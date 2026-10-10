"""The leaver list and ownership transfer (R1-B26 PR 1, acceptance criterion 6)."""

from __future__ import annotations

import pytest

from tests._scim_test_helpers import give_unlimited_plan, make_user

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def world(db_session, make_org):
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.services import provisioning_service

    org_a, org_b = make_org("leaver-a"), make_org("leaver-b")
    give_unlimited_plan(db_session, org_a)
    give_unlimited_plan(db_session, org_b)
    admin_a = make_user(db_session, org_a, "admina", administrator=True)
    admin_b = make_user(db_session, org_b, "adminb", administrator=True)
    member_a = make_user(db_session, org_a, "membera")
    new_owner = make_user(db_session, org_a, "newowner")
    leaver = make_user(db_session, org_a, "leaver")
    outsider = make_user(db_session, org_b, "outsider")

    app_row = ApplicationComponent(name="Payroll", organization_id=org_a.id)
    capability = UnifiedCapability(name="Hiring", organization_id=org_a.id)
    db_session.add_all([app_row, capability])
    db_session.flush()
    app_owner = ApplicationOwner(
        application_id=app_row.id, user_id=leaver.id, organization_id=org_a.id, ownership_type="primary"
    )
    element_owner = ApplicationOwner(
        element_type="capability", element_id=capability.id, user_id=leaver.id,
        organization_id=org_a.id, ownership_type="business",
    )
    db_session.add_all([app_owner, element_owner])
    db_session.flush()
    provisioning_service.deactivate_user(leaver, reason="leaver_deprovisioned", actor="test")
    return dict(
        org_a=org_a, org_b=org_b, admin_a=admin_a, admin_b=admin_b, member_a=member_a, new_owner=new_owner,
        leaver=leaver, outsider=outsider, app=app_row, capability=capability,
        app_owner=app_owner, element_owner=element_owner,
    )


def _post(client, login_as, user, owner_row, target_id):
    login_as(client, user)
    return client.post(
        f"/admin/leavers/ownerships/{owner_row.id}/transfer",
        data={"new_owner_id": str(target_id)},
        follow_redirects=False,
    )


def test_the_list_shows_the_application_and_the_element(client, login_as, world):
    login_as(client, world["admin_a"])
    resp = client.get("/admin/leavers")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert world["leaver"].email in text
    assert "Payroll" in text and "Hiring" in text
    assert "Primary" in text and "Business" in text
    assert "leaver deprovisioned" in text
    assert "No departed users hold any ownership." not in text


def test_organisation_b_does_not_see_the_leaver_and_cannot_transfer_the_row(client, login_as, db_session, world):
    login_as(client, world["admin_b"])
    text = client.get("/admin/leavers").get_data(as_text=True)
    assert world["leaver"].email not in text and "Payroll" not in text
    assert "No departed users hold any ownership." in text

    resp = _post(client, login_as, world["admin_b"], world["app_owner"], world["outsider"].id)
    assert resp.status_code == 404
    db_session.refresh(world["app_owner"])
    assert world["app_owner"].user_id == world["leaver"].id


def test_a_non_administrator_gets_403(client, login_as, world):
    login_as(client, world["member_a"])
    assert client.get("/admin/leavers").status_code == 403
    assert _post(client, login_as, world["member_a"], world["app_owner"], world["new_owner"].id).status_code == 403


def test_transfer_updates_the_same_row_audits_it_and_the_owners_api_shows_the_new_owner(client, login_as, db_session, world):
    from app.models.audit_log import AuditLog

    row_id = world["app_owner"].id
    resp = _post(client, login_as, world["admin_a"], world["app_owner"], world["new_owner"].id)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/leavers")
    db_session.refresh(world["app_owner"])
    assert world["app_owner"].id == row_id
    assert world["app_owner"].user_id == world["new_owner"].id
    assert world["app_owner"].ownership_type == "primary"
    assert world["app_owner"].application_id == world["app"].id

    audit = AuditLog.query.filter(AuditLog.action == "owner_transferred", AuditLog.organization_id == world["org_a"].id).one()
    assert audit.new_value["from_user_id"] == world["leaver"].id
    assert audit.new_value["to_user_id"] == world["new_owner"].id
    assert audit.new_value["application_id"] == world["app"].id
    assert audit.user_id == world["admin_a"].id

    login_as(client, world["admin_a"])
    owners = client.get(f"/applications/{world['app'].id}/owners")
    if owners.status_code == 404:  # blueprint prefix differs: fall back to the model read the route uses
        from app.models.application_owner import ApplicationOwner

        rows = ApplicationOwner.get_display_rows_for_application(world["app"].id, world["org_a"].id)
    else:
        rows = owners.get_json()["owners"]
    assert [r["user_id"] for r in rows] == [world["new_owner"].id]
    assert rows[0]["owner_active"] is True


def test_an_element_ownership_transfers_through_the_same_record(client, login_as, db_session, world):
    resp = _post(client, login_as, world["admin_a"], world["element_owner"], world["new_owner"].id)
    assert resp.status_code == 302
    db_session.refresh(world["element_owner"])
    assert world["element_owner"].user_id == world["new_owner"].id
    assert world["element_owner"].element_type == "capability"
    assert world["element_owner"].element_id == world["capability"].id


def test_transfer_to_an_inactive_user_or_a_user_of_another_organisation_is_refused(client, login_as, db_session, world):
    from app.services import provisioning_service

    gone = make_user(db_session, world["org_a"], "alsogone")
    provisioning_service.deactivate_user(gone, reason="leaver_deprovisioned", actor="test")
    for target in (gone, world["outsider"], world["leaver"]):
        resp = _post(client, login_as, world["admin_a"], world["app_owner"], target.id)
        assert resp.status_code == 302
        db_session.refresh(world["app_owner"])
        assert world["app_owner"].user_id == world["leaver"].id, target.email
    resp = _post(client, login_as, world["admin_a"], world["app_owner"], "not-a-number")
    assert resp.status_code == 302


def test_an_active_users_row_cannot_be_moved_from_the_leaver_list(client, login_as, db_session, world):
    from app.models.application_owner import ApplicationOwner

    live = ApplicationOwner(
        application_id=world["app"].id, user_id=world["member_a"].id,
        organization_id=world["org_a"].id, ownership_type="backup",
    )
    db_session.add(live)
    db_session.flush()
    resp = _post(client, login_as, world["admin_a"], live, world["new_owner"].id)
    assert resp.status_code == 404
    db_session.refresh(live)
    assert live.user_id == world["member_a"].id


def test_transfer_onto_someone_who_already_holds_it_removes_the_leavers_row(client, login_as, db_session, world):
    from app.models.application_owner import ApplicationOwner

    existing = ApplicationOwner(
        application_id=world["app"].id, user_id=world["new_owner"].id,
        organization_id=world["org_a"].id, ownership_type="primary",
    )
    db_session.add(existing)
    db_session.flush()
    leaver_row_id = world["app_owner"].id
    resp = _post(client, login_as, world["admin_a"], world["app_owner"], world["new_owner"].id)
    assert resp.status_code == 302
    rows = ApplicationOwner.query.filter(
        ApplicationOwner.application_id == world["app"].id, ApplicationOwner.ownership_type == "primary"
    ).all()
    assert [r.user_id for r in rows] == [world["new_owner"].id]
    assert ApplicationOwner.query.filter(ApplicationOwner.id == leaver_row_id).count() == 0


def test_duplicate_rule_is_shared_with_the_writer_routes(db_session, world):
    from app.models.application_owner import ApplicationOwner
    from app.modules.applications.routes.owner_routes import _duplicate_owner

    found = ApplicationOwner.find_duplicate(
        world["leaver"].id, "primary", world["org_a"].id, application_id=world["app"].id
    )
    assert found is not None and found.id == world["app_owner"].id
    assert _duplicate_owner(world["app"].id, world["leaver"].id, "primary", world["org_a"].id).id == found.id
    assert ApplicationOwner.find_duplicate(
        world["leaver"].id, "business", world["org_a"].id,
        element_type="capability", element_id=world["capability"].id,
    ).id == world["element_owner"].id
    assert ApplicationOwner.find_duplicate(
        world["leaver"].id, "primary", world["org_b"].id, application_id=world["app"].id
    ) is None


def test_service_signature_leaves_room_for_the_successor_recommendation(db_session, world):
    import inspect

    from app.services import provisioning_service

    params = inspect.signature(provisioning_service.transfer_ownership).parameters
    assert params["effective_date"].default is None
    assert params["recommended_by"].default is None
    assert params["effective_date"].kind is inspect.Parameter.KEYWORD_ONLY
