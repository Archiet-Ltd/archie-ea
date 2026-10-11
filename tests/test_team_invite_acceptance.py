"""Acceptance tests for team invitation requiring user consent.

An invitation must not grant a role or membership until the invited user accepts.
"""

import uuid

import pytest


PASSWORD = "test-password-123"


def _make_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Inv {label} {suffix}", slug=f"inv-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _make_user(db_session, org, *, org_admin=False, email=None):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    admin_role = Role.query.filter_by(name="Administrator").first()
    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="Test",
        last_name=f"User-{suffix}",
        email=email or f"user-{suffix}@example.test",
        password=PASSWORD,
        confirmed=True,
        organization_id=org.id,
        role=admin_role,
        is_org_admin=org_admin,
        is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin" if org_admin else "viewer",
                     granted_by_id=user.id)
    db_session.flush()
    return user


# ── Reason-code tests (team_invite error paths) ──────────────────────────


def test_invite_missing_email(app, db_session, login_as, client):
    """Inviting without an email returns 400."""
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": "", "role": "viewer"})
    assert resp.status_code == 400
    assert "email required" in resp.get_data(as_text=True)


def test_invite_invalid_role(app, db_session, login_as, client):
    """Inviting with an invalid role returns 400."""
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite",
                       data={"email": "someone@example.test", "role": "superadmin"})
    assert resp.status_code == 400
    assert "invalid role" in resp.get_data(as_text=True)


def test_invite_unknown_address_without_mail_server_creates_nothing(app, db_session, login_as, client, monkeypatch):
    """An address with no account is invited by e-mail; with no mail server
    configured the page says so and nothing is created.

    (Inviting an address with no account used to answer 404 "No user found";
    it now opens an invitation by e-mail -- see tests/test_account_mail_flows.py.)
    """
    from app.models.user import User

    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", None)
    monkeypatch.setitem(app.config, "MAIL_USERNAME", None)
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite",
                       data={"email": "nobody@example.test", "role": "viewer"})
    assert resp.status_code == 503
    assert "E-mail is not available on this server" in resp.get_data(as_text=True)
    assert User.find_by_email("nobody@example.test") is None


def test_duplicate_invite_refused(app, db_session, login_as, client):
    """Inviting the same user twice returns 409."""
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin = _make_user(db_session, org_a, org_admin=True)
    _make_user(db_session, org_b, email="target@example.test")
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite",
                       data={"email": "target@example.test", "role": "viewer"})
    assert resp.status_code == 302

    resp = client.post("/admin/team/invite",
                       data={"email": "target@example.test", "role": "viewer"})
    assert resp.status_code == 409
    assert "already exists" in resp.get_data(as_text=True)


# ── Acceptance flow tests ─────────────────────────────────────────────────


def test_invite_does_not_grant_role_until_acceptance(app, db_session, login_as, client):
    """Inviting an existing user creates a pending invitation, NOT an OrgRole."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    target = _make_user(db_session, org_b, email="target@example.test")
    db_session.commit()

    assert OrgRole.get_role(org_a.id, target.id) is None

    login_as(client, admin_a)
    resp = client.post("/admin/team/invite",
                       data={"email": "target@example.test", "role": "viewer"})
    assert resp.status_code == 302

    assert OrgRole.get_role(org_a.id, target.id) is None

    invitation = PendingInvitation.query.filter_by(
        organization_id=org_a.id, user_id=target.id
    ).first()
    assert invitation is not None
    assert invitation.role == "viewer"
    assert invitation.invited_by == admin_a.id


def test_accept_invitation_grants_role(app, db_session, login_as, client):
    """Accepting a pending invitation creates the OrgRole and removes the invitation."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    target = _make_user(db_session, org_b, email="target@example.test")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    db_session.commit()

    # Create invitation directly so we control the DB state
    invitation, _ = PendingInvitation.create_for(
        org_a.id, target.id, "viewer", invited_by_id=admin_a.id
    )
    invitation_id = invitation.id
    assert invitation is not None

    login_as(client, target)
    accept_url = f"/account/invitation/{invitation_id}/accept"
    resp = client.post(accept_url)
    assert resp.status_code == 302

    assert OrgRole.get_role(org_a.id, target.id) == "viewer"
    assert PendingInvitation.query.get(invitation_id) is None


def test_decline_invitation_grants_nothing(app, db_session, login_as, client):
    """Declining a pending invitation does not create an OrgRole."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    target = _make_user(db_session, org_b, email="target@example.test")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    db_session.commit()

    invitation, _ = PendingInvitation.create_for(
        org_a.id, target.id, "viewer", invited_by_id=admin_a.id
    )
    invitation_id = invitation.id
    assert invitation is not None

    login_as(client, target)
    decline_url = f"/account/invitation/{invitation_id}/decline"
    resp = client.post(decline_url)
    assert resp.status_code == 302

    assert OrgRole.get_role(org_a.id, target.id) is None
    assert PendingInvitation.query.get(invitation_id) is None


def test_cross_org_no_move_without_acceptance(app, db_session, login_as, client):
    """A user in org B cannot be moved into org A without accepting."""
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    user_b = _make_user(db_session, org_b, email="user@example.test")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    db_session.commit()

    OrgRole.set_role(org_b.id, user_b.id, "org_admin", granted_by_id=user_b.id)
    assert OrgRole.get_role(org_b.id, user_b.id) == "org_admin"
    assert OrgRole.get_role(org_a.id, user_b.id) is None

    # Admin of org A invites user_b via POST
    login_as(client, admin_a)
    resp = client.post("/admin/team/invite",
                       data={"email": "user@example.test", "role": "viewer"})
    assert resp.status_code == 302

    assert OrgRole.get_role(org_b.id, user_b.id) == "org_admin"
    assert OrgRole.get_role(org_a.id, user_b.id) is None

    invitation = PendingInvitation.query.filter_by(
        organization_id=org_a.id, user_id=user_b.id
    ).first()
    assert invitation is not None

    # user_b declines
    login_as(client, user_b)
    decline_url = f"/account/invitation/{invitation.id}/decline"
    resp = client.post(decline_url)
    assert resp.status_code == 302

    assert OrgRole.get_role(org_b.id, user_b.id) == "org_admin"
    assert OrgRole.get_role(org_a.id, user_b.id) is None
    assert PendingInvitation.query.get(invitation.id) is None


def test_accept_wrong_user_refused(app, db_session, login_as, client):
    """A different user cannot accept someone else's invitation."""
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    org_c = _make_org(db_session, "C")
    target = _make_user(db_session, org_b, email="target@example.test")
    other = _make_user(db_session, org_c, email="other@example.test")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    db_session.commit()

    invitation, _ = PendingInvitation.create_for(
        org_a.id, target.id, "viewer", invited_by_id=admin_a.id
    )
    invitation_id = invitation.id
    assert invitation is not None

    login_as(client, other)
    accept_url = f"/account/invitation/{invitation_id}/accept"
    resp = client.post(accept_url)
    assert resp.status_code == 302
    assert PendingInvitation.query.get(invitation_id) is not None


def test_cross_org_read_own_invitations_only(app, db_session, login_as, client):
    """A user can only see invitations addressed to them."""
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    org_c = _make_org(db_session, "C")
    user_c = _make_user(db_session, org_c, email="userc@example.test")
    other = _make_user(db_session, org_c, email="other@example.test")
    admin_a = _make_user(db_session, org_a, org_admin=True)
    admin_b = _make_user(db_session, org_b, org_admin=True)
    db_session.commit()

    # Create two invitations for user_c
    inv1, _ = PendingInvitation.create_for(org_a.id, user_c.id, "viewer", invited_by_id=admin_a.id)
    inv2, _ = PendingInvitation.create_for(org_b.id, user_c.id, "architect", invited_by_id=admin_b.id)

    invs_for_c = PendingInvitation.query.filter_by(user_id=user_c.id).all()
    assert len(invs_for_c) == 2

    invs_for_other = PendingInvitation.query.filter_by(user_id=other.id).all()
    assert len(invs_for_other) == 0

    for inv in invs_for_c:
        assert inv.user_id == user_c.id

    login_as(client, other)
    accept_url = f"/account/invitation/{inv1.id}/accept"
    resp = client.post(accept_url)
    assert resp.status_code == 302
    assert PendingInvitation.query.get(inv1.id) is not None


def test_accept_nonexistent_invitation(app, db_session, login_as, client):
    """Accepting a non-existent invitation returns an error."""
    org = _make_org(db_session, "A")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.post("/account/invitation/99999/accept")
    assert resp.status_code == 302


def test_decline_nonexistent_invitation(app, db_session, login_as, client):
    """Declining a non-existent invitation returns an error."""
    org = _make_org(db_session, "A")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.post("/account/invitation/99999/decline")
    assert resp.status_code == 302