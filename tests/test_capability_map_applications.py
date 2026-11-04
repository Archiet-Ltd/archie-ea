"""The capability map lists the applications its counts say are mapped, for the signed-in organisation only."""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="map.%s@example.test" % uuid.uuid4().hex[:8], first_name="Map", last_name="Owner",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


def _seed(db_session, org, code, app_names):
    from app.models.application_layer import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name="Capability %s" % code, code=code, level=1, capability_type="strategic",
        organization_id=org.id, scope="tenant",
    )
    db_session.add(cap)
    db_session.flush()
    for name in app_names:
        comp = ApplicationComponent(name=name, lifecycle_status="active", organization_id=org.id)
        db_session.add(comp)
        db_session.flush()
        db_session.add(UnifiedApplicationCapabilityMapping(
            application_component_id=comp.id, unified_capability_id=cap.id, support_level="full",
            coverage_percentage=80,
        ))
    db_session.flush()
    return cap


def _rows(client, login_as, user):
    login_as(client, user)
    reply = client.get("/capability-map/api/capabilities/hierarchy")
    assert reply.status_code == 200, reply.get_data(as_text=True)
    return [c for d in reply.get_json()["domains"] for c in d["capabilities"]]


def test_each_capability_lists_the_applications_its_count_says(client, login_as, db_session, make_org, caplog):
    org = make_org("capmap-a")
    owner = _owner(db_session, org)
    _seed(db_session, org, "SHARED-1", ["CRM System", "Marketing Hub"])
    _seed(db_session, org, "EMPTY-1", [])
    with caplog.at_level("ERROR"):
        rows = {c["code"]: c for c in _rows(client, login_as, owner)}
    mapped = rows["SHARED-1"]
    assert mapped["applicationCoverage"] == 2
    assert sorted(a["name"] for a in mapped["applications"]) == ["CRM System", "Marketing Hub"]
    assert len(mapped["applications"]) == mapped["applicationCoverage"]
    for entry in mapped["applications"]:
        assert client.get(entry["url"]).status_code == 200, entry["url"]
    assert rows["EMPTY-1"]["applications"] == [] and rows["EMPTY-1"]["applicationCoverage"] == 0
    assert "_api_hierarchy_context" not in caplog.text


def test_another_organisation_never_lists_these_applications(client, login_as, db_session, make_org):
    org_a, org_b = make_org("capmap-a"), make_org("capmap-b")
    owner_a, owner_b = _owner(db_session, org_a), _owner(db_session, org_b)
    _seed(db_session, org_a, "SAME-CODE", ["Only A App"])
    _seed(db_session, org_b, "SAME-CODE", ["Only B App"])
    a = {c["code"]: c for c in _rows(client, login_as, owner_a)}["SAME-CODE"]
    b = {c["code"]: c for c in _rows(client, login_as, owner_b)}["SAME-CODE"]
    assert [x["name"] for x in a["applications"]] == ["Only A App"]
    assert [x["name"] for x in b["applications"]] == ["Only B App"]
    assert a["applicationCoverage"] == b["applicationCoverage"] == 1
