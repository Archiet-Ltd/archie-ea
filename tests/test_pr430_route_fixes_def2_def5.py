"""review-pr430-v3.md's remaining rulings for the route-fixes split PR:

DEF-2: ``run_detection``/``run_detection_hybrid`` wiped every organisation's
duplicate-detection groups before creating new ones (neither
``unified_group_members`` nor ``unified_duplicate_groups`` carries an
``organization_id`` column). Scoped the cleanup to only the caller's own
organisation's group memberships (via each member's ``ApplicationComponent``,
which is TenantMixin), dropping only groups left with zero members rather
than guessing at ones with a remaining foreign member.

DEF-5: ``GET /api/security/audit/events`` had no organisation filter at all,
so any org admin could read another organisation's full audit trail (emails,
IPs, user agents). Scoped to the caller's own organisation's users unless
they are a genuine platform admin.

DEF-3 (platform_admin_required on the v2 roles API) was already fixed on
main before this split landed -- see
tests/test_admin_org_member_idor.py::TestRolesApiPlatformAdminOnly, which
this file does not duplicate.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False, org_admin=True):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator" if org_admin else "Architect").first()
    if role is None:
        pytest.skip("no seeded role matches in this database")
    user = User(
        email=f"def2def5-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Def2Def5",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = org_admin
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


# ---------------------------------------------------------------------------
# DEF-2: run_detection must not wipe another organisation's detection groups
# ---------------------------------------------------------------------------


def _component(db_session, org, *, name):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(comp)
    db_session.flush()
    return comp


def _group_with_apps(db_session, applications, *, name):
    from app.models.unified_duplicate_detection import UnifiedDuplicateGroup

    group = UnifiedDuplicateGroup(name=name, similarity_score=0.9, status="pending")
    db_session.add(group)
    db_session.flush()
    for app in applications:
        group.applications.append(app)
    db_session.flush()
    return group


def test_def2_run_detection_does_not_wipe_another_orgs_groups(
    app, db_session, make_org, client, login_as
):
    from sqlalchemy import text

    org_a = make_org("def2-a")
    org_b = make_org("def2-b")

    # org B's own pre-existing group -- must survive org A running detection.
    b_app1 = _component(db_session, org_b, name=f"B1 {uuid.uuid4().hex[:6]}")
    b_app2 = _component(db_session, org_b, name=f"B2 {uuid.uuid4().hex[:6]}")
    victim_group = _group_with_apps(db_session, [b_app1, b_app2], name="def2-victim-group")

    admin_a = _user(db_session, org_a)
    db_session.commit()

    victim_group_id = victim_group.id
    admin_a_id = admin_a.id

    _login(db_session, client, login_as, admin_a_id)
    response = client.post(
        "/duplicate-detection/simple/run-detection",
        json={"strategy": "fast", "similarity_threshold": 0.6},
    )
    assert response.status_code == 200, response.get_json()

    row = db_session.execute(
        text("SELECT id FROM unified_duplicate_groups WHERE id = :id"),
        {"id": victim_group_id},
    ).first()
    assert row is not None, (
        "org A running detection for its own portfolio must not delete "
        "org B's pre-existing duplicate-detection group"
    )

    members = db_session.execute(
        text("SELECT application_id FROM unified_group_members WHERE group_id = :id"),
        {"id": victim_group_id},
    ).fetchall()
    assert len(members) == 2, (
        "org B's group must keep both of its own members after org A's run"
    )


def test_def2_run_detection_still_cleans_up_the_callers_own_stale_groups(
    app, db_session, make_org, client, login_as
):
    """No-regression control: the caller's OWN stale groups must still be
    cleaned up (the whole point of the pre-run cleanup step), not just left
    alone out of over-caution."""
    from sqlalchemy import text

    org_a = make_org("def2-own")
    a_app1 = _component(db_session, org_a, name=f"A1 {uuid.uuid4().hex[:6]}")
    a_app2 = _component(db_session, org_a, name=f"A2 {uuid.uuid4().hex[:6]}")
    stale_group = _group_with_apps(db_session, [a_app1, a_app2], name="def2-stale-own-group")

    admin_a = _user(db_session, org_a)
    db_session.commit()

    stale_group_id = stale_group.id
    admin_a_id = admin_a.id

    _login(db_session, client, login_as, admin_a_id)
    response = client.post(
        "/duplicate-detection/simple/run-detection",
        json={"strategy": "fast", "similarity_threshold": 0.6},
    )
    assert response.status_code == 200, response.get_json()

    row = db_session.execute(
        text("SELECT id FROM unified_duplicate_groups WHERE id = :id"),
        {"id": stale_group_id},
    ).first()
    assert row is None, (
        "the caller's own stale group from a previous run must still be "
        "cleaned up before the new run"
    )


# ---------------------------------------------------------------------------
# DEF-5: GET /api/security/audit/events must not leak another org's trail
# ---------------------------------------------------------------------------


def _audit_event(db_session, *, user_id, event_type="data_access", resource_type="probe"):
    from app.security.audit import AuditEvent

    event = AuditEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        severity="low",
        user_id=user_id,
        resource_type=resource_type,
        action="read",
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_def5_org_admin_cannot_read_another_orgs_audit_events(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("def5-a")
    org_b = make_org("def5-b")
    admin_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b, org_admin=False)
    db_session.flush()

    marker = f"def5-probe-{uuid.uuid4().hex[:8]}"
    _audit_event(db_session, user_id=user_b.id, resource_type=marker)
    db_session.commit()

    admin_a_id = admin_a.id

    _login(db_session, client, login_as, admin_a_id)
    response = client.get(f"/api/security/audit/events?resource_type={marker}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 0, (
        "an org A admin must not see org B's audit events in the response"
    )


def test_def5_org_admin_can_still_read_their_own_orgs_audit_events(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("def5-own")
    admin_a = _user(db_session, org_a)
    db_session.flush()

    marker = f"def5-own-probe-{uuid.uuid4().hex[:8]}"
    _audit_event(db_session, user_id=admin_a.id, resource_type=marker)
    db_session.commit()

    admin_a_id = admin_a.id

    _login(db_session, client, login_as, admin_a_id)
    response = client.get(f"/api/security/audit/events?resource_type={marker}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1, "an admin must still see their own organisation's audit events"


def test_def5_platform_admin_sees_every_orgs_audit_events(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("def5-platform-a")
    org_b = make_org("def5-platform-b")
    platform_admin = _user(db_session, org_a, platform=True)
    user_b = _user(db_session, org_b, org_admin=False)
    db_session.flush()

    marker = f"def5-platform-probe-{uuid.uuid4().hex[:8]}"
    _audit_event(db_session, user_id=user_b.id, resource_type=marker)
    db_session.commit()

    platform_admin_id = platform_admin.id

    _login(db_session, client, login_as, platform_admin_id)
    response = client.get(f"/api/security/audit/events?resource_type={marker}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1, "a genuine platform admin must still see every organisation's events"
