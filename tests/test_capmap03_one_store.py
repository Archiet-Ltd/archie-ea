"""One capability-to-application store: every screen that counts or lists a capability's applications agrees.

Seeded the way the demonstration company is: business capabilities (which project into the unified store),
applications, and mappings written to the unified mapping table the map reads.
"""

import json
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="cm3.%s@example.test" % uuid.uuid4().hex[:8], first_name="Map", last_name="Owner",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


def _seed(db_session, org):
    """Two top-level and one second-level capability, three applications, mappings in the unified table only."""
    from app.models.application_layer import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    tag = uuid.uuid4().hex[:6]
    bcs = {}
    for key, name, level, parent in (
        ("cal", "Sensor Calibration", 1, None),
        ("log", "Field Logistics", 1, None),
        ("sub", "Drift Correction", 2, "cal"),
    ):
        bc = BusinessCapability(
            name="%s %s" % (name, tag), code="CM3-%s-%s" % (key, tag), level=level, category="strategic",
            organization_id=org.id, parent_capability_id=bcs[parent].id if parent else None,
        )
        db_session.add(bc)
        db_session.flush()
        bcs[key] = bc
    unified = {
        k: UnifiedCapability.query.filter_by(source_table="business_capability", source_id=str(v.id)).one()
        for k, v in bcs.items()
    }
    apps = {}
    for key in ("ledger", "bench", "route"):
        comp = ApplicationComponent(name="App %s %s" % (key, tag), lifecycle_status="active", organization_id=org.id)
        db_session.add(comp)
        db_session.flush()
        apps[key] = comp
    for cap_key, app_keys in (("cal", ("ledger", "bench")), ("log", ("route",))):
        for app_key in app_keys:
            db_session.add(UnifiedApplicationCapabilityMapping(
                application_component_id=apps[app_key].id, unified_capability_id=unified[cap_key].id,
                support_level="full", coverage_percentage=80,
            ))
    db_session.flush()
    return bcs, unified, apps


def _flat(rows):
    for row in rows:
        yield row
        yield from _flat(row.get("children", []))


def _map_rows(client):
    reply = client.get("/capability-map/api/capabilities/hierarchy")
    assert reply.status_code == 200, reply.get_data(as_text=True)
    return {c["name"]: c for c in _flat([c for d in reply.get_json()["domains"] for c in d["capabilities"]])}


def _catalog(hierarchy):
    found = re.search(r"\bcatalog:\s*(\{.*\}),\s*\n", hierarchy)
    assert found, "the hierarchy page no longer carries its catalog"
    return json.loads(found.group(1))


def _mapping_page(client):
    reply = client.get("/enterprise/capability-map/mapping")
    assert reply.status_code == 200
    html = reply.get_data(as_text=True)
    rows = {}
    for block in re.split(r'(?=<div class="[^"]*cap-row")', html)[1:]:
        name = re.search(r'data-cap-name="([^"]*)"', block).group(1)
        names = re.findall(r'data-testid="mapped-application"[^>]*>([^<]+)<', block.split("cap-row", 1)[0] + block[:3000])
        rows[name] = sorted(n.strip() for n in names)
    return html, rows


def test_every_screen_lists_the_same_applications_and_counts(client, login_as, db_session, make_org):
    org = make_org("cm3-a")
    user = _owner(db_session, org)
    bcs, unified, apps = _seed(db_session, org)
    login_as(client, user)

    api = _map_rows(client)
    cal, log, sub = (bcs[k].name for k in ("cal", "log", "sub"))
    expected = {
        cal: sorted(apps[k].name for k in ("ledger", "bench")),
        log: [apps["route"].name],
        sub: [],
    }
    for name, names in expected.items():
        assert sorted(a["name"] for a in api[name]["applications"]) == names
        assert api[name]["applicationCoverage"] == len(names)

    # The mapping page lists the same applications for the same capabilities.
    login_as(client, user)
    html, page_rows = _mapping_page(client)
    for name, names in expected.items():
        assert page_rows.get(name) == names, (name, page_rows.get(name), names)
    assert "Capabilities with an application" in html

    # The capability record lists the same applications.
    for key, name in (("cal", cal), ("log", log), ("sub", sub)):
        login_as(client, user)
        record = client.get("/enterprise/capabilities/%s/fact-sheet" % bcs[key].id).get_data(as_text=True)
        for app_name in expected[name]:
            assert app_name in record, (name, app_name)
        for other in set(a.name for a in apps.values()) - set(expected[name]):
            assert other not in record, (name, other)

    # The hierarchy page reports the same per-capability count.
    login_as(client, user)
    hierarchy = client.get("/capability-map/hierarchy").get_data(as_text=True)
    for name, names in expected.items():
        node = next(c for c in _flat(_catalog(hierarchy)["children"]) if c["name"] == name)
        assert node["mapping_count"] == len(names), (name, node["mapping_count"])


def test_capability_totals_and_level_counts_agree(client, login_as, db_session, make_org):
    org = make_org("cm3-b")
    user = _owner(db_session, org)
    _, unified, _ = _seed(db_session, org)
    # As in the demonstration company: the second-level row carries no parent link in the unified store.
    unified["sub"].parent_capability_id = None
    db_session.flush()
    login_as(client, user)
    map_total = sum(1 for _ in _map_rows(client))
    assert map_total == 3

    login_as(client, user)
    caps = client.get("/enterprise/capability-map/capabilities").get_data(as_text=True)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", caps))
    total = int(re.search(r"Total Capabilities (\d+)", text).group(1))
    level1 = int(re.search(r"Level 1 (\d+)", text).group(1))
    deeper = int(re.search(r"Level 2\+ (\d+)", text).group(1))
    assert (total, level1, deeper) == (3, 2, 1)

    login_as(client, user)
    hierarchy = client.get("/capability-map/hierarchy").get_data(as_text=True)
    found = re.search(r"\bcatalog:\s*(\{.*\}),\s*\n", hierarchy)
    catalog = json.loads(found.group(1)) if found else None
    assert catalog is not None, "the hierarchy page no longer carries its catalog"
    flat = list(_flat(catalog["children"]))
    assert len(flat) == map_total
    assert sum(1 for c in flat if c["level"] == 1) == level1
    assert sum(1 for c in flat if c["level"] > 1) == deeper


def test_orphan_second_level_capability_is_on_the_hierarchy(client, login_as, db_session, make_org):
    """A projected second-level capability whose parent link is missing is listed, as the map lists it."""
    from app.models.unified_capability import UnifiedCapability

    org = make_org("cm3-c")
    user = _owner(db_session, org)
    bcs, unified, _ = _seed(db_session, org)
    unified["sub"].parent_capability_id = None
    db_session.flush()
    login_as(client, user)
    assert bcs["sub"].name in _map_rows(client)
    login_as(client, user)
    assert bcs["sub"].name in client.get("/capability-map/hierarchy").get_data(as_text=True)
    assert UnifiedCapability.query.count() >= 3


def test_validation_message_names_the_field_as_the_form_labels_it(client, login_as, db_session, make_org):
    org = make_org("cm3-d")
    user = _owner(db_session, org)
    bcs, _, _ = _seed(db_session, org)
    login_as(client, user)
    reply = client.put("/enterprise/capabilities/%s" % bcs["cal"].id, json={"name": bcs["cal"].name, "type": ""})
    assert reply.status_code == 400
    text = json.dumps(reply.get_json())
    assert "Category" in text and "capability type" not in text.lower()
