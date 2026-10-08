"""Stakeholders linked to a transformation programme, and the owners suggested for it.

A programme's stakeholders are ordinary stakeholder rows linked through the
existing stakeholder mapping, so the map for a programme reads only its own;
suggestions come only from owners recorded on the capabilities the
programme's options affect, and say where each came from.
"""

import pytest


def _programme(db_session, org, name="Programme"):
    from app.models.strategic import StrategicInitiative

    programme = StrategicInitiative(
        name=name, organization_id=org.id, record_kind="transformation_programme"
    )
    db_session.add(programme)
    db_session.flush()
    return programme


def _option_affecting(db_session, org, programme, capability_ids):
    from app.models.transformation_decision import TransformationOption
    from app.models.transformation_programme import ProgrammeWorkstream

    workstream = ProgrammeWorkstream(
        organization_id=org.id, programme_id=programme.id, workstream_type="process",
        objective="Simplify order handling", scope_expression={},
    )
    db_session.add(workstream)
    db_session.flush()
    option = TransformationOption(
        organization_id=org.id, workstream_id=workstream.id, title="Consolidate",
        action_type="consolidate", description="One order platform",
        affected_capability_ids=list(capability_ids), affected_value_stream_ids=[],
    )
    db_session.add(option)
    db_session.flush()
    return option


def _capability(db_session, org, name, business_owner=None, it_owner=None):
    from app.models.business_capabilities import BusinessCapability

    capability = BusinessCapability(
        name=name, organization_id=org.id, business_owner=business_owner, it_owner=it_owner
    )
    db_session.add(capability)
    db_session.flush()
    return capability


def _solution(db_session, org, name="Solution"):
    from app.models.solution_models import Solution

    solution = Solution(name=name, organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def test_a_programme_with_nothing_affected_has_no_suggestions(app, db_session, make_org, tenant_ctx):
    from app.modules.architecture.services.stakeholder_service import programme_owner_suggestions

    org = make_org("stk-empty")
    programme = _programme(db_session, org)
    with tenant_ctx(org.id):
        assert programme_owner_suggestions(programme.id) == {"affected_count": 0, "suggestions": []}


def test_owners_of_affected_capabilities_are_suggested_with_their_basis(
    app, db_session, make_org, tenant_ctx
):
    from app.models.solution_stakeholder import SolutionStakeholder, SolutionStakeholderMapping
    from app.modules.architecture.services.stakeholder_service import programme_owner_suggestions

    org = make_org("stk-suggest")
    programme = _programme(db_session, org)
    orders = _capability(db_session, org, "Order Management", "Dana Price", "Ravi Shah")
    billing = _capability(db_session, org, "Billing", "Dana Price", None)
    _capability(db_session, org, "Unaffected", "Someone Else", None)
    _option_affecting(db_session, org, programme, [orders.id, billing.id])

    # Already on the programme's map: not suggested again.
    on_map = SolutionStakeholder(name="Ravi Shah", organization_id=org.id)
    db_session.add(on_map)
    db_session.flush()
    db_session.add(SolutionStakeholderMapping(stakeholder_id=on_map.id, programme_id=programme.id))
    db_session.flush()

    with tenant_ctx(org.id):
        result = programme_owner_suggestions(programme.id)
    assert result["affected_count"] == 2
    assert result["suggestions"] == [{
        "name": "Dana Price",
        "basis": ["Business owner of Billing", "Business owner of Order Management"],
    }]


@pytest.fixture
def signed_in(app, db_session, make_org, client, login_as):
    from app.models.user import User

    def _as(label):
        org = make_org(label)
        user = User(email="%s-%s@example.com" % (label, org.id), first_name="Stk",
                    last_name=label, organization_id=org.id, confirmed=True)
        user.password = "a-long-test-password-1"
        db_session.add(user)
        db_session.flush()
        login_as(client, user)
        return org

    return _as


def test_a_stakeholder_added_to_a_programme_is_on_its_map_only(
    app, db_session, client, signed_in
):
    org = signed_in("stk-route")
    programme = _programme(db_session, org, "Route Programme")
    other_programme = _programme(db_session, org, "Other Programme")

    created = client.post("/api/stakeholders/", json={
        "name": "Programme Sponsor", "influence_level": 5, "interest_level": 5,
        "programme_id": programme.id})
    assert created.status_code == 201, created.get_json()

    mine = client.get("/api/stakeholders/map-data?programme_id=%d" % programme.id).get_json()
    assert [(s["name"], s["quadrant"]) for s in mine] == [("Programme Sponsor", "manage_closely")]
    # A programme with no stakeholders shows none, never somebody else's.
    assert client.get("/api/stakeholders/map-data?programme_id=%d" % other_programme.id).get_json() == []


def test_another_organisations_programme_cannot_be_read_or_written(
    app, db_session, client, signed_in
):
    theirs = signed_in("stk-theirs")
    programme = _programme(db_session, theirs, "Their Programme")
    signed_in("stk-mine")

    assert client.get("/api/stakeholders/map-data?programme_id=%d" % programme.id).status_code == 404
    assert client.post("/api/stakeholders/", json={
        "name": "Intruder", "programme_id": programme.id}).status_code == 404
    assert client.get(
        "/api/stakeholders/programme-suggestions?programme_id=%d" % programme.id).status_code == 404


def test_another_organisations_solution_cannot_be_read_or_written(
    app, db_session, client, signed_in
):
    theirs = signed_in("stk-sol-theirs")
    foreign_solution = _solution(db_session, theirs, "Foreign Solution")
    signed_in("stk-sol-mine")

    read_resp = client.get("/api/stakeholders/map-data?solution_id=%d" % foreign_solution.id)
    assert read_resp.status_code == 404

    write_resp = client.post(
        "/api/stakeholders/",
        json={"name": "Intruder Mapping", "solution_id": foreign_solution.id},
    )
    assert write_resp.status_code == 404


def test_a_solution_with_no_mappings_returns_an_empty_list(
    app, db_session, client, signed_in
):
    org = signed_in("stk-sol-empty")
    solution = _solution(db_session, org, "Empty Solution")

    resp = client.get("/api/stakeholders/map-data?solution_id=%d" % solution.id)

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_people_search_matches_full_display_name_for_a_user(
    app, db_session, client, signed_in
):
    signed_in("stk-sol-search")

    resp = client.get("/api/stakeholders/search-people?q=Stk stk-sol-search")

    assert resp.status_code == 200
    assert [item["name"] for item in resp.get_json()] == ["Stk stk-sol-search"]
