"""R1-B88: quarterly access recertification."""

import csv
import io
from datetime import datetime, timedelta

from tests.test_team_invite_acceptance import _make_org as _make_org_base
from tests.test_team_invite_acceptance import _make_user


def _make_org(db_session, label):
    """An organisation on a plan that admits more than three people."""
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    org = _make_org_base(db_session, label)
    db_session.add(
        Subscription(
            organization_id=org.id,
            plan=SubscriptionPlan["enterprise"],
            status=SubscriptionStatus.active,
            seats_purchased=0,
        )
    )
    db_session.flush()
    return org


def _member(db_session, org, *, first_name=None):
    from app.models.org_role import OrgRole
    from app.models.user import Role

    user = _make_user(db_session, org)
    user.role = Role.query.filter_by(name="Architect").first()
    user.is_org_admin = False
    if first_name:
        user.first_name = first_name
    OrgRole.set_role(org.id, user.id, "architect")
    db_session.flush()
    return user


def _active(db_session, org, user, days_ago):
    from app.models.audit_log import AuditLog

    db_session.add(
        AuditLog(
            organization_id=org.id,
            user_id=user.id,
            action="update",
            table_name="applications",
            created_at=datetime.utcnow() - timedelta(days=days_ago),
        )
    )
    db_session.flush()


def _world(db_session):
    """An organisation with an administrator and three members: one active 10 days
    ago, one last active 120 days ago, one never."""
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    recent = _member(db_session, org)
    stale = _member(db_session, org)
    never = _member(db_session, org)
    _active(db_session, org, admin, 1)
    _active(db_session, org, recent, 10)
    _active(db_session, org, stale, 120)
    db_session.commit()
    return org, admin, recent, stale, never


def _open(client, login_as, admin):
    login_as(client, admin)
    return client.post("/admin/access/reviews")


def _cycle(org_id, status=None):
    from app.models.access_review import AccessReviewCycle

    query = AccessReviewCycle.query.filter_by(organization_id=org_id)
    if status:
        query = query.filter_by(status=status)
    return query.order_by(AccessReviewCycle.id.desc()).first()


def _item(cycle, user):
    from app.models.access_review import AccessReviewItem

    return AccessReviewItem.query.filter_by(cycle_id=cycle.id, user_id=user.id).one()


def _decide(client, login_as, admin, cycle_id, item_id, decision, note=""):
    login_as(client, admin)
    return client.post(
        "/admin/access/reviews/%d/items/%d" % (cycle_id, item_id),
        data={"decision": decision, "note": note},
    )


def test_opening_a_cycle_lists_every_member_once_with_unused_flags(app, db_session, login_as, client):
    org, admin, recent, stale, never = _world(db_session)
    org_id = org.id

    resp = _open(client, login_as, admin)

    assert resp.status_code == 302
    cycle = _cycle(org_id)
    assert cycle.status == "open" and cycle.unused_days == 90
    assert cycle.due_at - cycle.opened_at == timedelta(days=30)
    assert len(cycle.items) == 4
    assert {i.user_id for i in cycle.items} == {admin.id, recent.id, stale.id, never.id}
    assert _item(cycle, recent).unused is False
    assert _item(cycle, stale).unused is True
    assert _item(cycle, never).unused is True
    assert _item(cycle, never).last_activity_at is None
    assert _item(cycle, admin).unused is False
    assert _item(cycle, admin).org_role == "org_admin"
    assert _item(cycle, recent).org_role == "architect"
    assert all(i.decision == "pending" for i in cycle.items)

    login_as(client, admin)
    html = client.get("/admin/access/reviews/%d" % cycle.id).get_data(as_text=True)
    assert html.count("data-unused-flag") == 2
    # Unused grants are listed first.
    assert html.index('data-item-unused="yes"') < html.index('data-item-unused="no"')


def test_unactivated_invitees_and_platform_administrators_are_not_reviewed(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    invitee = _member(db_session, org)
    invitee.password_hash = None
    invitee.confirmed = False
    platform = _make_user(db_session, org, org_admin=True)
    platform.is_platform_admin = True
    db_session.commit()
    org_id = org.id

    _open(client, login_as, admin)

    cycle = _cycle(org_id)
    assert {i.user_id for i in cycle.items} == {admin.id}
    login_as(client, admin)
    assert "platform administrator(s) are not listed" in client.get(
        "/admin/access/reviews/%d" % cycle.id
    ).get_data(as_text=True)


def test_a_second_open_cycle_is_refused(app, db_session, login_as, client):
    org, admin, *_ = _world(db_session)
    org_id = org.id

    assert _open(client, login_as, admin).status_code == 302
    assert _open(client, login_as, admin).status_code == 409

    from app.models.access_review import AccessReviewCycle

    assert AccessReviewCycle.query.filter_by(organization_id=org_id).count() == 1


def test_removing_the_unused_member_reduces_them_to_view_only_and_signs_them_out(
    app, db_session, login_as, client
):
    from app.models.org_role import OrgRole
    from app.models.user_session import UserSession
    from tests._session_test_helpers import mint_test_sid

    org, admin, recent, stale, never = _world(db_session)
    sid = mint_test_sid(stale.id, organization_id=org.id)
    db_session.commit()
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)
    item = _item(cycle, stale)

    resp = _decide(client, login_as, admin, cycle.id, item.id, "removed", "left the project")

    assert resp.status_code == 302
    db_session.expire_all()
    item = _item(cycle, stale)
    assert item.decision == "removed" and item.decided_by_id == admin.id
    assert item.decided_at is not None and item.note == "left the project"
    assert OrgRole.get_role(org_id, stale.id) is None
    row = UserSession.query.filter_by(sid=sid).one()
    assert row.revoked_at is not None and row.revoked_reason == "access_review_removed"
    # The account itself stays.
    from app.models.user import User

    assert User.query.filter(User.id == stale.id, User.organization_id == org_id).count() == 1

    login_as(client, admin)
    html = client.get("/admin/access/reviews/%d" % cycle.id).get_data(as_text=True)
    assert "Access reduced to view only and signed out" in html
    assert "removed from the organisation" not in html


def test_keeping_a_grant_changes_nothing_but_the_record(app, db_session, login_as, client):
    from app.models.org_role import OrgRole

    org, admin, recent, stale, never = _world(db_session)
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)

    resp = _decide(client, login_as, admin, cycle.id, _item(cycle, recent).id, "kept")

    assert resp.status_code == 302
    db_session.expire_all()
    assert _item(cycle, recent).decision == "kept"
    assert OrgRole.get_role(org_id, recent.id) == "architect"


def test_the_reviewer_cannot_remove_themselves(app, db_session, login_as, client):
    from app.models.org_role import OrgRole

    org, admin, *_ = _world(db_session)
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)
    item = _item(cycle, admin)

    resp = _decide(client, login_as, admin, cycle.id, item.id, "removed")

    assert resp.status_code == 400
    db_session.expire_all()
    assert _item(cycle, admin).decision == "pending"
    assert OrgRole.get_role(org_id, admin.id) == "org_admin"


def test_close_is_refused_while_pending_then_succeeds_and_summarises(app, db_session, login_as, client):
    from app.models.audit_log import AuditLog

    org, admin, recent, stale, never = _world(db_session)
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)

    login_as(client, admin)
    refused = client.post("/admin/access/reviews/%d/close" % cycle.id)
    assert refused.status_code == 409
    assert "still need a decision" in refused.get_data(as_text=True)

    _decide(client, login_as, admin, cycle.id, _item(cycle, admin).id, "kept")
    _decide(client, login_as, admin, cycle.id, _item(cycle, recent).id, "kept")
    _decide(client, login_as, admin, cycle.id, _item(cycle, stale).id, "removed")
    _decide(client, login_as, admin, cycle.id, _item(cycle, never).id, "removed")
    login_as(client, admin)
    assert client.post("/admin/access/reviews/%d/close" % cycle.id).status_code == 302

    db_session.expire_all()
    cycle = _cycle(org_id)
    assert cycle.status == "closed" and cycle.closed_by_id == admin.id and cycle.closed_at is not None
    assert cycle.summary_json == {"total": 4, "kept": 2, "removed": 2, "unused": 2}
    actions = [
        a.action
        for a in AuditLog.query.filter(AuditLog.org_predicate(org_id)).order_by(AuditLog.id).all()
        if a.action.startswith("access_")
    ]
    assert actions.count("access_review_open") == 1
    assert actions.count("access_item_decided") == 4
    assert actions.count("access_review_close") == 1
    assert actions.count("access_restricted") == 2

    # A closed review cannot be changed or closed again, and another can start.
    login_as(client, admin)
    assert client.post("/admin/access/reviews/%d/close" % cycle.id).status_code == 409
    assert _decide(client, login_as, admin, cycle.id, _item(cycle, recent).id, "removed").status_code == 409
    assert _open(client, login_as, admin).status_code == 302


def test_the_evidence_pack_lists_every_item_and_neutralises_formulas(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    sneaky = _member(db_session, org, first_name="=HYPERLINK(\"http://evil.test\")")
    quiet = _member(db_session, org)
    _active(db_session, org, admin, 1)
    _active(db_session, org, sneaky, 2)
    db_session.commit()
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)

    login_as(client, admin)
    assert client.get("/admin/access/reviews/%d/evidence.csv" % cycle.id).status_code == 409

    _decide(client, login_as, admin, cycle.id, _item(cycle, admin).id, "kept")
    _decide(client, login_as, admin, cycle.id, _item(cycle, sneaky).id, "kept", "=1+1 checked with manager")
    _decide(client, login_as, admin, cycle.id, _item(cycle, quiet).id, "removed")
    login_as(client, admin)
    client.post("/admin/access/reviews/%d/close" % cycle.id)

    login_as(client, admin)
    resp = client.get("/admin/access/reviews/%d/evidence.csv" % cycle.id)

    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith("attachment")
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
    header, body = rows[0], rows[1:]
    assert len(body) == 3
    col = {name: i for i, name in enumerate(header)}
    by_email = {r[col["Email"]]: r for r in body}
    assert set(by_email) == {admin.email, sneaky.email, quiet.email}
    assert by_email[quiet.email][col["Decision"]] == "removed"
    assert by_email[quiet.email][col["Decided by"]] == admin.email
    assert by_email[quiet.email][col["Decided at"]]
    assert by_email[quiet.email][col["Unused for the review period"]] == "yes"
    assert by_email[sneaky.email][col["Member"]].startswith("'=HYPERLINK")
    assert by_email[sneaky.email][col["Note"]] == "'=1+1 checked with manager"
    for row in body:
        for cell in row:
            assert not cell.startswith(("=", "+", "@")), cell


def test_a_member_who_is_not_an_administrator_cannot_read_or_act(app, db_session, login_as, client):
    org, admin, recent, stale, never = _world(db_session)
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)

    login_as(client, recent)
    assert client.get("/admin/access/reviews").status_code == 403
    login_as(client, recent)
    assert client.get("/admin/access/reviews/%d" % cycle.id).status_code == 403
    login_as(client, recent)
    assert client.post("/admin/access/reviews").status_code == 403
    login_as(client, recent)
    assert client.post("/admin/access/reviews/%d/items/%d" % (cycle.id, _item(cycle, stale).id),
                       data={"decision": "removed"}).status_code == 403
    login_as(client, recent)
    assert client.post("/admin/access/reviews/%d/close" % cycle.id).status_code == 403


def test_a_security_architect_can_read_but_not_act(app, db_session, login_as, client):
    org, admin, recent, stale, never = _world(db_session)
    reader = _member(db_session, org)
    reader.enterprise_role = "security_architect"
    db_session.commit()
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)

    login_as(client, reader)
    assert client.get("/admin/access/reviews/%d" % cycle.id).status_code == 200
    login_as(client, reader)
    assert client.post("/admin/access/reviews/%d/items/%d" % (cycle.id, _item(cycle, stale).id),
                       data={"decision": "removed"}).status_code == 403
    login_as(client, reader)
    assert client.post("/admin/access/reviews/%d/close" % cycle.id).status_code == 403


def test_bad_decisions_are_refused(app, db_session, login_as, client):
    org, admin, recent, stale, never = _world(db_session)
    org_id = org.id
    _open(client, login_as, admin)
    cycle = _cycle(org_id)

    assert _decide(client, login_as, admin, cycle.id, _item(cycle, recent).id, "maybe").status_code == 400
    assert _decide(client, login_as, admin, cycle.id, 999999, "kept").status_code == 404
    db_session.expire_all()
    assert _item(cycle, recent).decision == "pending"
