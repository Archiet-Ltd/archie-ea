"""A gap made on the ADM board is a gap register record, and only a person who may change the model makes one."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _person(db_session, org, enterprise_role, role_name):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="gap.%s@example.com" % uuid.uuid4().hex[:10], first_name="G", last_name="W",
        organization_id=org.id, enterprise_role=enterprise_role, confirmed=True,
        role=Role.query.filter_by(name=role_name).first(),
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    return user


def _phase(db_session):
    from app.models.adm_kanban import ADMPhase

    phase = ADMPhase.query.filter_by(code="A").first()
    if phase is None:
        phase = ADMPhase(name="Architecture Vision", code="A", order=1)
        db_session.add(phase)
        db_session.flush()
    return phase


def test_a_reader_cannot_make_a_gap_and_an_editor_can(db_session, make_org, tenant_ctx, client, login_as):
    from app.services.gap_register_service import count_gaps

    mine, theirs = make_org("gap-writers"), make_org("gap-others")
    reader = _person(db_session, mine, "viewer", "Viewer")
    editor = _person(db_session, mine, "enterprise_architect", "User")
    _phase(db_session)
    body = {"title": "No single customer record", "phase": "A", "arch_element_type": "Gap"}

    with tenant_ctx(mine.id):
        before = count_gaps(mine.id)
    login_as(client, reader)
    refused = client.post("/api/adm-kanban/v2/cards", json=body)
    assert refused.status_code == 403
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before

    login_as(client, editor)
    made = client.post("/api/adm-kanban/v2/cards", json=body)
    assert made.status_code in (200, 201), made.get_data(as_text=True)
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before + 1
    with tenant_ctx(theirs.id):
        assert count_gaps(theirs.id) == 0


def test_a_reader_cannot_write_a_gap_through_the_capability_roadmap(db_session, make_org, tenant_ctx, client, login_as):
    from app.models.business_capabilities import BusinessCapability
    from app.services.gap_register_service import count_gaps

    mine, theirs = make_org("roadmap-writers"), make_org("roadmap-others")
    reader = _person(db_session, mine, "viewer", "Viewer")
    editor = _person(db_session, mine, "enterprise_architect", "User")
    capability = BusinessCapability(name="Billing %s" % uuid.uuid4().hex[:6], organization_id=mine.id)
    db_session.add(capability)
    db_session.flush()
    writes = [
        ("/capability-map/api/roadmap/gaps", {"name": "No single customer record", "gap_type": "coverage"}),
        ("/capability-map/api/roadmap/gaps/convert", {"gaps": [{
            "capability_id": capability.id, "capability_type": "business", "name": "Billing",
            "gap_types": ["coverage"], "priority": "high"}]}),
        ("/capability-map/api/roadmap/gaps/add-from-capability", {
            "capability_id": capability.id, "capability_type": "business", "capability_name": "Billing",
            "gap_type": "coverage", "priority": "high"}),
    ]

    with tenant_ctx(mine.id):
        before = count_gaps(mine.id)
    login_as(client, reader)
    for url, body in writes:
        assert client.post(url, json=body).status_code == 403, url
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before

    login_as(client, editor)
    made = client.post(writes[0][0], json=writes[0][1])
    assert made.status_code == 201, made.get_data(as_text=True)
    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before + 1
    with tenant_ctx(theirs.id):
        assert count_gaps(theirs.id) == 0


def test_a_reader_cannot_change_or_remove_what_the_capability_roadmap_holds(db_session, make_org, tenant_ctx, client, login_as):
    from app.models.implementation_migration import Gap, Plateau
    from app.services.gap_register_service import count_gaps

    mine, theirs = make_org("roadmap-edit"), make_org("roadmap-edit-other")
    reader = _person(db_session, mine, "viewer", "Viewer")
    editor = _person(db_session, mine, "enterprise_architect", "User")
    base = "/capability-map/api/roadmap"

    login_as(client, editor)
    made = client.post(base + "/gaps", json={"name": "Duplicate customer records", "gap_type": "coverage"})
    assert made.status_code == 201, made.get_data(as_text=True)
    gap_id = made.get_json()["gap"]["id"]
    plateau = client.post(base + "/plateaus", json={"name": "Target state", "sequence_order": 2})
    assert plateau.status_code in (200, 201), plateau.get_data(as_text=True)
    package = client.post(base + "/work-packages", json={"name": "Merge records"})
    assert package.status_code in (200, 201), package.get_data(as_text=True)
    wp_id = (package.get_json().get("work_package") or package.get_json())["id"]

    with tenant_ctx(mine.id):
        before = count_gaps(mine.id)
        plateaus = Plateau.query.count()

    login_as(client, reader)
    attempts = [
        ("put", "/gaps/%d" % gap_id, {"name": "Renamed by a reader"}),
        ("delete", "/gaps/%d" % gap_id, None),
        ("post", "/gaps/detect", {}),
        ("post", "/plateaus", {"name": "Reader plateau", "sequence_order": 3}),
        ("post", "/gaps/%d/work-packages" % gap_id, {"name": "Reader package"}),
        ("post", "/work-packages", {"name": "Reader standalone package"}),
        ("put", "/work-packages/%d" % wp_id, {"name": "Renamed by a reader"}),
        ("delete", "/work-packages/%d" % wp_id, None),
        ("post", "/work-packages/%d/children" % wp_id, {"name": "Reader child"}),
        ("post", "/ai-generate", {"gap_ids": [gap_id]}),
    ]
    for method, path, body in attempts:
        response = getattr(client, method)(base + path, json=body) if body is not None else getattr(client, method)(base + path)
        assert response.status_code == 403, (method, path, response.status_code)
        if "/work-packages" not in path or path.startswith("/work-packages"):
            assert "may read the roadmap but not change it" in response.get_data(as_text=True), (method, path)

    with tenant_ctx(mine.id):
        assert count_gaps(mine.id) == before
        assert Plateau.query.count() == plateaus
        assert Gap.query.get(gap_id).name == "Duplicate customer records"

    login_as(client, editor)
    renamed = client.put(base + "/gaps/%d" % gap_id, json={"name": "Customer records merged"})
    assert renamed.status_code == 200, renamed.get_data(as_text=True)
    with tenant_ctx(theirs.id):
        assert count_gaps(theirs.id) == 0
