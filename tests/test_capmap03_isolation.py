"""Two organisations: the application picker offers only the signed-in organisation's applications, and the write
refuses another organisation's application or capability."""

import pytest

from tests.test_capmap03_one_store import _owner, _seed

pytestmark = pytest.mark.usefixtures("db_session")


def test_picker_offers_only_own_applications(client, login_as, db_session, make_org):
    org_a, org_b = make_org("cm3-iso-a"), make_org("cm3-iso-b")
    user_a, user_b = _owner(db_session, org_a), _owner(db_session, org_b)
    _, _, apps_a = _seed(db_session, org_a)
    _, _, apps_b = _seed(db_session, org_b)
    login_as(client, user_a)
    names = {a["name"] for a in client.get("/capability-map/api/applications").get_json()["applications"]}
    assert {a.name for a in apps_a.values()} <= names
    assert not names & {a.name for a in apps_b.values()}


def test_write_refuses_a_foreign_application_and_a_foreign_capability(client, login_as, db_session, make_org):
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping as Mapping

    org_a, org_b = make_org("cm3-iso-a"), make_org("cm3-iso-b")
    user_a = _owner(db_session, org_a)
    _owner(db_session, org_b)
    _, unified_a, apps_a = _seed(db_session, org_a)
    _, unified_b, apps_b = _seed(db_session, org_b)
    login_as(client, user_a)

    reply = client.post("/capability-map/api/mappings", json={
        "unified_capability_id": unified_a["sub"].id,
        "applications": [{"application_id": apps_b["ledger"].id, "mapping": {"support_level": "full"}}],
    })
    body = reply.get_json()
    assert reply.status_code == 200 and body["created"] == 0 and body["errors"], body
    assert Mapping.query.filter_by(unified_capability_id=unified_a["sub"].id).count() == 0

    login_as(client, user_a)
    reply = client.post("/capability-map/api/mappings", json={
        "unified_capability_id": unified_b["sub"].id,
        "applications": [{"application_id": apps_a["ledger"].id}],
    })
    assert reply.status_code == 404
    assert Mapping.query.filter_by(unified_capability_id=unified_b["sub"].id).count() == 0

    login_as(client, user_a)
    reply = client.delete(
        "/capability-map/api/mappings/by-unified-capability/%s/application/%s"
        % (unified_b["cal"].id, apps_b["ledger"].id))
    assert reply.status_code == 404
    assert Mapping.query.filter_by(unified_capability_id=unified_b["cal"].id).count() == 2


def test_a_mapping_written_from_the_drawer_reaches_every_screen(client, login_as, db_session, make_org):
    org = make_org("cm3-write")
    user = _owner(db_session, org)
    bcs, unified, apps = _seed(db_session, org)
    login_as(client, user)
    reply = client.post("/capability-map/api/mappings", json={
        "unified_capability_id": unified["sub"].id,
        "applications": [{"application_id": apps["route"].id, "mapping": {"support_level": "partial"}}],
    })
    assert reply.status_code == 200 and reply.get_json()["created"] == 1, reply.get_json()
    login_as(client, user)
    page = client.get("/enterprise/capability-map/mapping").get_data(as_text=True)
    assert apps["route"].name in page
    login_as(client, user)
    record = client.get("/enterprise/capabilities/%s/fact-sheet" % bcs["sub"].id).get_data(as_text=True)
    assert apps["route"].name in record
    login_as(client, user)
    reply = client.delete(
        "/capability-map/api/mappings/by-unified-capability/%s/application/%s" % (unified["sub"].id, apps["route"].id))
    assert reply.status_code == 200


def test_assess_a_capability_with_no_business_record_is_fenced_to_the_organisation(client, login_as, db_session, make_org):
    from app.models.unified_capability import UnifiedCapability

    org_a, org_b = make_org("cm3-as-a"), make_org("cm3-as-b")
    user_a = _owner(db_session, org_a)
    _owner(db_session, org_b)
    own = UnifiedCapability(name="Own", code="AS-A", level=1, capability_type="strategic",
                            organization_id=org_a.id, scope="tenant")
    foreign = UnifiedCapability(name="Foreign", code="AS-B", level=1, capability_type="strategic",
                                organization_id=org_b.id, scope="tenant")
    db_session.add_all([own, foreign])
    db_session.flush()
    login_as(client, user_a)
    assert client.put("/capability-map/api/unified-capabilities/%s/maturity" % own.id,
                      json={"current_maturity_level": 3}).status_code == 200
    assert own.current_maturity_level == 3 and own.maturity_assessment_date is not None
    login_as(client, user_a)
    assert client.put("/capability-map/api/unified-capabilities/%s/maturity" % foreign.id,
                      json={"current_maturity_level": 3}).status_code == 404
    assert foreign.current_maturity_level is None
    login_as(client, user_a)
    assert client.put("/capability-map/api/unified-capabilities/%s/maturity" % own.id,
                      json={"current_maturity_level": 9}).status_code == 400
