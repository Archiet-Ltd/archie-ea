"""R1-B88: two organisations. One organisation's reviews, items and export flags
are invisible to, and untouchable by, the other."""

from datetime import datetime, timedelta

from tests.test_access_review import _active, _cycle, _item, _make_org, _member, _open
from tests.test_export_anomaly import _flag_for, _flags, _seed_history, _seed_recent
from tests.test_team_invite_acceptance import _make_user


def _two_orgs(db_session):
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    admin_b = _make_user(db_session, org_b, org_admin=True)
    stale_a = _member(db_session, org_a)
    stale_b = _member(db_session, org_b)
    for org, admin in ((org_a, admin_a), (org_b, admin_b)):
        _active(db_session, org, admin, 1)
    db_session.commit()
    return org_a, org_b, admin_a, admin_b, stale_a, stale_b


def _read_as(org_id):
    """Read as organisation ``org_id`` would: the test's own app context keeps the
    last request's organisation on ``g``, which would filter these reads."""
    from flask import g

    g.current_org_id = org_id


def _snapshot(org_id):
    from app.models.access_review import AccessReviewCycle, AccessReviewItem

    _read_as(org_id)
    from app.models.org_role import OrgRole

    return (
        [(c.id, c.status) for c in AccessReviewCycle.query.filter_by(organization_id=org_id).order_by("id")],
        [(i.id, i.decision) for i in AccessReviewItem.query.filter_by(organization_id=org_id).order_by("id")],
        sorted((r.user_id, r.role) for r in OrgRole.query.filter_by(organization_id=org_id).all()),
    )


def test_b_cannot_list_open_decide_close_or_download_as_a(app, db_session, login_as, client):
    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)
    a_id, b_id = org_a.id, org_b.id
    _open(client, login_as, admin_a)
    cycle_a = _cycle(a_id)
    item_a = _item(cycle_a, stale_a)
    cycle_a_id, item_a_id = cycle_a.id, item_a.id
    before = _snapshot(a_id)

    login_as(client, admin_b)
    listing = client.get("/admin/access/reviews").get_data(as_text=True)
    assert 'data-review-id="%d"' % cycle_a_id not in listing
    assert "No reviews yet." in listing

    for method, url, data in (
        ("get", "/admin/access/reviews/%d" % cycle_a_id, None),
        ("post", "/admin/access/reviews/%d/items/%d" % (cycle_a_id, item_a_id), {"decision": "removed"}),
        ("post", "/admin/access/reviews/%d/close" % cycle_a_id, None),
        ("get", "/admin/access/reviews/%d/evidence.csv" % cycle_a_id, None),
    ):
        login_as(client, admin_b)
        resp = getattr(client, method)(url, data=data) if data else getattr(client, method)(url)
        assert resp.status_code == 404, (method, url, resp.status_code)

    # B opens its own review; A's item id under B's own cycle is still a 404.
    login_as(client, admin_b)
    assert client.post("/admin/access/reviews").status_code == 302
    cycle_b = _cycle(b_id)
    login_as(client, admin_b)
    cross = client.post(
        "/admin/access/reviews/%d/items/%d" % (cycle_b.id, item_a_id), data={"decision": "removed"}
    )
    assert cross.status_code == 404

    db_session.expire_all()
    assert _snapshot(a_id) == before
    assert _cycle(a_id).status == "open"


def test_a_closed_cycle_evidence_is_not_downloadable_by_the_other_organisation(app, db_session, login_as, client):
    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)
    a_id = org_a.id
    _open(client, login_as, admin_a)
    cycle_a = _cycle(a_id)
    for item in list(cycle_a.items):
        login_as(client, admin_a)
        client.post(
            "/admin/access/reviews/%d/items/%d" % (cycle_a.id, item.id), data={"decision": "kept"}
        )
    login_as(client, admin_a)
    client.post("/admin/access/reviews/%d/close" % cycle_a.id)
    db_session.expire_all()
    cycle_a = _cycle(a_id)
    assert cycle_a.status == "closed"

    login_as(client, admin_a)
    assert client.get("/admin/access/reviews/%d/evidence.csv" % cycle_a.id).status_code == 200
    login_as(client, admin_b)
    assert client.get("/admin/access/reviews/%d/evidence.csv" % cycle_a.id).status_code == 404


def test_a_review_lists_only_its_own_organisations_members(app, db_session, login_as, client):
    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)
    a_id = org_a.id

    _open(client, login_as, admin_a)

    cycle = _cycle(a_id)
    assert {i.user_id for i in cycle.items} == {admin_a.id, stale_a.id}
    assert all(i.organization_id == a_id for i in cycle.items)


def test_b_cannot_see_or_act_on_a_flag_through_the_screen_or_the_service(app, db_session, login_as, client):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.org_role import OrgRole
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)
    flagged_a = _member(db_session, org_a)
    flag_id = _flag_for(db_session, org_a, flagged_a)
    db_session.commit()
    a_id = org_a.id

    login_as(client, admin_b)
    listing = client.get("/admin/access/exports").get_data(as_text=True)
    assert "downloaded 40 files" not in listing
    login_as(client, admin_b)
    assert client.get("/admin/access/exports/%d" % flag_id).status_code == 404
    login_as(client, admin_b)
    assert client.post("/admin/access/exports/%d/decision" % flag_id, data={"decision": "restrict"}).status_code == 404

    service = AIChatApprovalService(user_id=admin_b.id)
    approved = service.approve_and_execute(flag_id, approving_user_id=admin_b.id)
    rejected = service.reject_approval(flag_id, reason="not yours")
    assert approved["success"] is False and approved["code"] == "NOT_FOUND"
    assert rejected["success"] is False and rejected["code"] == "NOT_FOUND"

    db_session.expire_all()
    _read_as(a_id)
    assert _flags(a_id, flagged_a.id)[0].status == ApprovalStatus.PENDING
    assert OrgRole.get_role(a_id, flagged_a.id) == "architect"


def test_as_scan_never_reads_bs_audit_rows(app, db_session):
    from app.services.export_anomaly_service import member_activity, scan_organisation

    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org_b.id, stale_b.id, [2] * 28, now)
    _seed_recent(db_session, org_b.id, stale_b.id, 40, now)
    db_session.commit()

    assert scan_organisation(org_a.id, now=now) == []
    assert _flags(org_a.id) == []
    assert member_activity(org_a.id, now=now) == []

    created = scan_organisation(org_b.id, now=now)
    assert len(created) == 1
    assert _flags(org_a.id) == []
    assert [r["user"].id for r in member_activity(org_b.id, now=now)] == [stale_b.id]


def test_a_user_id_from_another_organisation_is_not_a_member_for_a_scan(app, db_session):
    """An audit row in A carrying B's user id (a stray reference) flags nobody."""
    from app.services.export_anomaly_service import scan_organisation

    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)
    now = datetime.utcnow()
    _seed_history(db_session, org_a.id, stale_b.id, [2] * 28, now)
    _seed_recent(db_session, org_a.id, stale_b.id, 40, now)
    db_session.commit()

    assert scan_organisation(org_a.id, now=now) == []


def test_revoking_across_organisations_is_refused(app, db_session):
    from app.models.org_role import OrgRole
    from app.services.rbac_service import MemberAccessError, rbac_service

    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)

    for end_sessions in (True, False):
        try:
            rbac_service.revoke_member_access(
                org_a.id, stale_b.id, actor_id=admin_a.id, reason="access_review_removed",
                end_sessions=end_sessions,
            )
        except MemberAccessError as exc:
            assert exc.status == 404
        else:
            raise AssertionError("a member of another organisation was reached")
    assert OrgRole.get_role(org_b.id, stale_b.id) == "architect"


def test_the_team_page_and_preview_show_only_the_callers_organisation(app, db_session, login_as, client):
    org_a, org_b, admin_a, admin_b, stale_a, stale_b = _two_orgs(db_session)

    login_as(client, admin_a)
    html_a = client.get("/admin/team").get_data(as_text=True)
    login_as(client, admin_b)
    html_b = client.get("/admin/team").get_data(as_text=True)

    assert stale_a.email in html_a and stale_b.email not in html_a
    assert stale_b.email in html_b and stale_a.email not in html_b

    login_as(client, admin_b)
    body = client.get("/admin/team/persona-preview?persona=finance&role=viewer").get_data(as_text=True)
    assert stale_a.email not in body and org_a.name not in body
