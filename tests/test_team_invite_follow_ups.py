"""Follow-ups to the team invitation flow: expiry, a race-safe duplicate, and
refusing to invite someone who is already a member.
"""

from datetime import timedelta
from unittest import mock

import pytest

from tests.test_team_invite_acceptance import _make_org, _make_user


def _invite(db_session, org, user, *, role="architect", age_days=0, expires_at="default"):
    """Insert a pending invitation directly, optionally aged."""
    from app.models.pending_invitation import PendingInvitation, _utcnow

    created = _utcnow() - timedelta(days=age_days)
    row = PendingInvitation(
        organization_id=org.id,
        user_id=user.id,
        role=role,
        created_at=created,
        expires_at=created + PendingInvitation.LIFETIME if expires_at == "default" else expires_at,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _invitation_count(org_id, user_id):
    from app.models.pending_invitation import PendingInvitation

    return PendingInvitation.query.filter_by(organization_id=org_id, user_id=user_id).count()


# -- expiry ---------------------------------------------------------------


def test_an_expired_invitation_cannot_be_accepted_and_grants_nothing(app, db_session, login_as, client):
    from app.models.org_role import OrgRole

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    invitee = _make_user(db_session, org_b)
    invitation = _invite(db_session, org_a, invitee, age_days=15)
    invitation_id = invitation.id
    db_session.commit()

    login_as(client, invitee)
    resp = client.post(f"/account/invitation/{invitation_id}/accept", follow_redirects=True)

    assert resp.status_code == 200
    assert b"has expired" in resp.data
    assert OrgRole.get_role(org_a.id, invitee.id) is None
    assert _invitation_count(org_a.id, invitee.id) == 0


def test_an_invitation_just_inside_its_lifetime_can_still_be_accepted(app, db_session, login_as, client):
    from app.models.org_role import OrgRole

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    invitee = _make_user(db_session, org_b)
    invitation = _invite(db_session, org_a, invitee, age_days=13, role="architect")
    invitation_id = invitation.id
    db_session.commit()

    login_as(client, invitee)
    client.post(f"/account/invitation/{invitation_id}/accept")

    assert OrgRole.get_role(org_a.id, invitee.id) == "architect"


def test_a_row_created_before_expiry_existed_expires_from_its_creation_date(app, db_session):
    org = _make_org(db_session, "A")
    other = _make_org(db_session, "B")
    user_old = _make_user(db_session, other)
    user_new = _make_user(db_session, other)
    old = _invite(db_session, org, user_old, age_days=15, expires_at=None)
    new = _invite(db_session, org, user_new, age_days=1, expires_at=None)

    assert old.is_expired() is True
    assert new.is_expired() is False


def test_an_expired_invitation_can_still_be_declined_and_is_removed(app, db_session, login_as, client):
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    invitee = _make_user(db_session, org_b)
    invitation_id = _invite(db_session, org_a, invitee, age_days=30).id
    db_session.commit()

    login_as(client, invitee)
    client.post(f"/account/invitation/{invitation_id}/decline")

    assert _invitation_count(org_a.id, invitee.id) == 0


def test_listing_a_users_invitations_leaves_out_expired_ones(app, db_session):
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    invitee = _make_user(db_session, org_b)
    _invite(db_session, org_a, invitee, age_days=20)
    valid_org = _make_org(db_session, "C")
    valid = _invite(db_session, valid_org, invitee, age_days=2)

    assert [i.id for i in PendingInvitation.find_for_user(invitee.id)] == [valid.id]


def test_inviting_again_after_expiry_renews_the_invitation(app, db_session, login_as, client):
    from app.models.pending_invitation import PendingInvitation, _utcnow

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin = _make_user(db_session, org_a, org_admin=True)
    invitee = _make_user(db_session, org_b)
    stale = _invite(db_session, org_a, invitee, age_days=20, role="viewer")
    stale_id = stale.id
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": invitee.email, "role": "architect"})

    assert resp.status_code == 302
    rows = PendingInvitation.query.filter_by(organization_id=org_a.id, user_id=invitee.id).all()
    assert [r.id for r in rows] == [stale_id]
    assert rows[0].role == "architect" and rows[0].invited_by == admin.id
    assert rows[0].is_expired() is False
    assert rows[0].expires_at > _utcnow() + timedelta(days=13)


def test_inviting_again_while_the_first_is_valid_is_still_refused(app, db_session, login_as, client):
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin = _make_user(db_session, org_a, org_admin=True)
    invitee = _make_user(db_session, org_b)
    _invite(db_session, org_a, invitee, age_days=1)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": invitee.email, "role": "viewer"})

    assert resp.status_code == 409
    assert _invitation_count(org_a.id, invitee.id) == 1


def test_a_new_invitation_is_given_an_expiry_in_the_future(app, db_session):
    from app.models.pending_invitation import PendingInvitation, _utcnow

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    invitee = _make_user(db_session, org_b)

    invitation, created = PendingInvitation.create_for(org_a.id, invitee.id, "viewer")

    assert created is True
    assert invitation.expires_at > _utcnow() + timedelta(days=13)


# -- the duplicate race -----------------------------------------------------


def test_two_requests_racing_to_create_the_same_invitation_do_not_error(app, db_session, login_as, client):
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin = _make_user(db_session, org_a, org_admin=True)
    invitee = _make_user(db_session, org_b)
    winner = _invite(db_session, org_a, invitee, age_days=0)
    winner_id = winner.id
    db_session.commit()

    real = PendingInvitation.find_one_or_none.__func__
    calls = {"n": 0}

    def losing_first_read(cls, org_id, user_id):
        # The loser's read happened before the winner's insert was visible.
        calls["n"] += 1
        return None if calls["n"] == 1 else real(cls, org_id, user_id)

    with mock.patch.object(PendingInvitation, "find_one_or_none", classmethod(losing_first_read)):
        login_as(client, admin)
        resp = client.post("/admin/team/invite", data={"email": invitee.email, "role": "viewer"})

    assert resp.status_code == 409
    assert "already exists" in resp.get_data(as_text=True)
    assert [r.id for r in PendingInvitation.query.filter_by(organization_id=org_a.id, user_id=invitee.id)] == [
        winner_id
    ]


def test_create_for_hands_the_loser_the_winners_row(app, db_session):
    from app.models.pending_invitation import PendingInvitation

    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    invitee = _make_user(db_session, org_b)
    winner = _invite(db_session, org_a, invitee)
    db_session.flush()

    real = PendingInvitation.find_one_or_none.__func__
    calls = {"n": 0}

    def losing_first_read(cls, org_id, user_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(cls, org_id, user_id)

    with mock.patch.object(PendingInvitation, "find_one_or_none", classmethod(losing_first_read)):
        row, created = PendingInvitation.create_for(org_a.id, invitee.id, "viewer")

    assert created is False and row.id == winner.id
    # The session is still usable after the refused insert.
    assert _invitation_count(org_a.id, invitee.id) == 1


# -- already a member -------------------------------------------------------


def test_inviting_someone_who_is_already_a_member_is_refused_without_creating_a_row(
    app, db_session, login_as, client
):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    member = _make_user(db_session, org)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": member.email, "role": "architect"})

    assert resp.status_code == 409
    assert "already a member" in resp.get_data(as_text=True)
    assert _invitation_count(org.id, member.id) == 0


def test_an_admin_cannot_invite_themselves(app, db_session, login_as, client):
    org = _make_org(db_session, "A")
    admin = _make_user(db_session, org, org_admin=True)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": admin.email, "role": "viewer"})

    assert resp.status_code == 409
    assert _invitation_count(org.id, admin.id) == 0


def test_a_member_of_another_organisation_can_still_be_invited(app, db_session, login_as, client):
    org_a = _make_org(db_session, "A")
    org_b = _make_org(db_session, "B")
    admin = _make_user(db_session, org_a, org_admin=True)
    outsider = _make_user(db_session, org_b)
    db_session.commit()

    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": outsider.email, "role": "viewer"})

    assert resp.status_code == 302
    assert _invitation_count(org_a.id, outsider.id) == 1
