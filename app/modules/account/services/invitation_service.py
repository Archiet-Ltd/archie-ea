"""
Invitations to join an organisation, by e-mail. ``PendingInvitation`` is the
one record of every invitation not yet taken up (ADR 0008); the Team page reads
it through ``invitations_for`` and nothing else.

Two kinds of invitee, one link (``/account/join/<token>``):

* A person with no account is invited by address: an account is opened for
  them in the INVITER'S organisation, with no password, and the link lets them
  set one. Only then is the organisation role granted and the address counted
  as confirmed. The link is bound to that organisation, so it can put its
  holder nowhere else.
* A person who already has an account gets the same kind of link (when e-mail
  is available); they sign in as that account and accept or decline there.

An account opened by an invitation holds its address only while an invitation
for it is open, and never against the person at the address. Withdrawing the
last invitation removes the account; once they have all expired, another
organisation's invitation may take the address over; and the person may always
register the address themselves, which withdraws the invitations for it.
"""
import logging

from flask import url_for
from sqlalchemy import delete

from app.extensions import db
from app.flask_email import deliver_email, mail_available
from app.models import User
from app.models.org_role import VALID_ORG_ROLES, OrgRole
from app.models.pending_invitation import PendingInvitation
from app.models.user import ADMINISTRATOR_ROLES, ROLE_SOLUTION_ARCHITECT, VALID_ROLES

_log = logging.getLogger(__name__)

MAIL_UNAVAILABLE = (
    "E-mail is not available on this server, so no invitation can be sent. "
    "Nothing was created."
)

# Personas an organisation administrator may give a teammate. Platform
# administration is not the organisation's to hand out.
INVITABLE_PERSONAS = [r for r in VALID_ROLES if r not in ADMINISTRATOR_ROLES]


class InvitationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def is_unactivated(user):
    """An account opened by an invitation that has not been taken up yet."""
    return (
        user.password_hash is None
        and not user.confirmed
        and not getattr(user, "external_id", None)
    )


def organisation_name(org_id):
    from app.models.organization import Organization

    # org_id is the caller's own organisation, or the one a redeemed invitation is bound to.
    # tenant-scoping-ok: only that organisation's display name is read
    org = db.session.get(Organization, org_id)
    return org.name if org is not None else "your organisation"


def _invitations_of(user_id):
    # Every organisation's invitations for one account, read only to decide
    # whether an account opened by an invitation still holds its address.
    # tenant-scoping-ok: one account's invitations across organisations, never shown to a caller
    return PendingInvitation.query.filter(PendingInvitation.user_id == user_id).all()


def _send(invitation, raw, inviter):
    """E-mail ``invitation``'s newest link and record what the mail server said."""
    link = url_for("account.join", token=raw, _external=True)
    org_name = organisation_name(invitation.organization_id)
    delivered, error = deliver_email(
        recipient=invitation.user.email,
        subject="You are invited to join {}".format(org_name),
        template="account/email/invite",
        user=invitation.user,
        invite_link=link,
        inviter=inviter,
        organisation_name=org_name,
        expires_at=invitation.expires_at,
        has_account=not is_unactivated(invitation.user),
    )
    invitation.record_delivery(delivered, error)
    return delivered, error


def _retire(user):
    """Remove an account opened by an invitation that was never taken up,
    with every invitation for it, so its address is free again."""
    from app.models.account_token import AccountToken

    for row in _invitations_of(user.id):
        db.session.delete(row)
    db.session.execute(
        delete(AccountToken)
        .where(AccountToken.user_id == user.id)
        .execution_options(synchronize_session=False)
    )
    db.session.execute(
        delete(OrgRole).where(OrgRole.user_id == user.id).execution_options(synchronize_session=False)
    )
    db.session.flush()
    db.session.delete(user)
    db.session.flush()


def release_for_registration(email):
    """Free an address held only by an unactivated account, before registering it.

    The person at the address chose to open their own account, so the open
    invitations for it are withdrawn rather than handed to an account that
    has not yet proved it controls the address; the inviter can invite the
    new account. Returns True when an address was freed.
    """
    user = User.find_by_email(email)
    if user is None or not is_unactivated(user):
        return False
    _retire(user)
    _log.info("invitation-only account released for registration")
    return True


def _release_if_abandoned(user):
    """Remove an unactivated account whose invitations have all expired."""
    if not is_unactivated(user):
        return False
    if any(not row.is_expired() for row in _invitations_of(user.id)):
        return False
    _retire(user)
    return True


def invite_new_person(org_id, inviter, email, org_role="viewer", persona=None,
                      first_name=None, last_name=None, platform_role=None):
    """Invite an address with no activated account into ``org_id``.

    Returns ``(invitation, delivered, error)``. Raises InvitationError when the
    invitation cannot be made, and PlanLimitReached when opening the account
    would take the organisation past its plan; nothing is written in either
    case.
    """
    if org_role not in VALID_ORG_ROLES:
        raise InvitationError("Invalid role '{}'.".format(org_role))
    persona = persona or ROLE_SOLUTION_ARCHITECT
    if persona not in INVITABLE_PERSONAS:
        raise InvitationError("Invalid persona '{}'.".format(persona))

    user = User.find_by_email(email)
    if user is not None and not is_unactivated(user):
        raise InvitationError("This address already has an account.", status=409)
    if user is None or user.organization_id != org_id:
        # A new member of this organisation would be opened. Say a full plan
        # is full before anything else; the save itself is still checked
        # (billing_plans), which also settles two admins taking the last place.
        from app.services.billing_plans import PlanLimitReached, user_limit_status

        status = user_limit_status(org_id)
        if status.get("limit_reached"):
            raise PlanLimitReached(status)
    if not mail_available():
        raise InvitationError(MAIL_UNAVAILABLE, status=503)

    if user is not None:
        if user.organization_id != org_id:
            # Another organisation opened this account. Its address is free to
            # take over only once every invitation for it has expired.
            if not _release_if_abandoned(user):
                raise InvitationError(
                    "This address has an open invitation from another organisation.",
                    status=409,
                )
            user = None
    if user is not None:
        open_invite = PendingInvitation.find_one_or_none(org_id, user.id)
        if open_invite is not None and not open_invite.is_expired():
            raise InvitationError(
                "An invitation for this address is still open. Use Resend to send it again.",
                status=409,
            )
        user.enterprise_role = persona
    else:
        user = User(
            first_name=first_name or None,
            last_name=last_name or None,
            email=email,
            organization_id=org_id,
            confirmed=False,
            enterprise_role=persona,
        )
        if platform_role is not None:
            user.role = platform_role
        db.session.add(user)
        db.session.flush()

    invitation, _ = PendingInvitation.create_for(
        org_id, user.id, org_role, invited_by_id=getattr(inviter, "id", None)
    )
    raw = invitation.issue_link()
    delivered, error = _send(invitation, raw, inviter)
    db.session.commit()
    return invitation, delivered, error


def invite_existing_account(org_id, inviter, user, org_role):
    """Invite someone who already has an account; they accept after signing in.

    Returns ``(invitation, delivered, error)``; ``delivered`` is None when no
    message could be sent because e-mail is not available here. Raises
    InvitationError when an invitation for them is already open.
    """
    invitation, created = PendingInvitation.create_for(
        org_id, user.id, org_role, invited_by_id=inviter.id
    )
    if not created:
        raise InvitationError("An invitation for this user already exists.", status=409)
    delivered, error = None, None
    if mail_available():
        raw = invitation.issue_link()
        delivered, error = _send(invitation, raw, inviter)
    db.session.commit()
    return invitation, delivered, error


def invitations_for(org_id):
    """Every invitation this organisation has sent that nobody has taken up."""
    return (
        PendingInvitation.query.filter(PendingInvitation.organization_id == org_id)
        .order_by(PendingInvitation.created_at.desc())
        .all()
    )


def _invitation_in_org(org_id, invitation_id):
    row = PendingInvitation.query.filter_by(id=invitation_id, organization_id=org_id).first()
    if row is None:
        raise InvitationError("Invitation not found.", status=404)
    return row


def resend(org_id, inviter, invitation_id):
    """Send a fresh link for an invitation in ``org_id``; the old link stops working."""
    row = _invitation_in_org(org_id, invitation_id)
    if not mail_available():
        raise InvitationError(MAIL_UNAVAILABLE.replace(" Nothing was created.", ""), status=503)
    row.invited_by = inviter.id
    raw = row.issue_link()
    delivered, error = _send(row, raw, inviter)
    db.session.commit()
    return row, delivered, error


def revoke(org_id, invitation_id):
    """Withdraw an invitation. Returns the invitee's address.

    An account the invitation opened, and that was never taken up, goes with
    it unless another organisation's invitation for it is still open, so the
    address is free again.
    """
    row = _invitation_in_org(org_id, invitation_id)
    user = row.user
    email = user.email
    db.session.delete(row)
    db.session.flush()
    if is_unactivated(user) and not any(
        not other.is_expired() for other in _invitations_of(user.id)
    ):
        _retire(user)
    db.session.commit()
    return email


def find_joinable(raw_token):
    """The open invitation behind a link, or None when the link cannot be used.

    An invitation that opened an account is refused unless that account is
    still unactivated and in the invitation's organisation.
    """
    row = PendingInvitation.find_by_link(raw_token)
    if row is None or row.user is None:
        return None
    if is_unactivated(row.user) and row.user.organization_id != row.organization_id:
        return None
    return row


def accept_new(raw_token, password):
    """Take up an invitation that opened an account: set the password, confirm, grant the role.

    Returns the new member, or None when the link cannot be used (expired,
    already used, withdrawn, or not bound to the account's organisation).
    """
    invitation = find_joinable(raw_token)
    if invitation is None or not is_unactivated(invitation.user):
        return None
    user = invitation.user
    offered = PendingInvitation.take_up(raw_token)
    if offered is None or offered["user_id"] != user.id or user.organization_id != offered["organization_id"]:
        db.session.rollback()
        return None
    user.password = password
    user.confirmed = True
    OrgRole.set_role(
        offered["organization_id"], user.id, offered["role"] or "viewer",
        granted_by_id=offered["invited_by"],
    )
    # The account this invitation opened lives in the invited organisation
    # (checked above against ``offered["organization_id"]``), so granting the
    # canonical Administrator role here is always a grant in the user's own
    # organisation — never a foreign one.
    if offered["role"] == "org_admin":
        user.grant_org_admin()
    db.session.commit()
    _log.info("invitation taken up into organisation %s", offered["organization_id"])
    return user


def answer_existing(raw_token, user, accept):
    """The signed-in invitee accepts or declines the invitation behind a link.

    Returns the organisation id on success, or None when the link cannot be
    used or is not this account's.
    """
    invitation = find_joinable(raw_token)
    if invitation is None or invitation.user_id != user.id or is_unactivated(invitation.user):
        return None
    offered = PendingInvitation.take_up(raw_token)
    if offered is None or offered["user_id"] != user.id:
        db.session.rollback()
        return None
    if accept:
        OrgRole.set_role(
            offered["organization_id"], user.id, offered["role"] or "viewer",
            granted_by_id=offered["invited_by"],
        )
        # This invitee already has an account, so the invited organisation
        # can be a DIFFERENT one from their own (``user.organization_id`` is
        # untouched here).  The Administrator role is global to the user, not
        # scoped to one organisation, so it must only be granted or revoked
        # when the invitation's organisation IS the user's own — otherwise
        # accepting an org-admin invite into organisation A would also make
        # the user an administrator of their own organisation B.  A grant
        # into a foreign organisation is carried by the OrgRole row above
        # alone; rbac_service.is_org_admin() reads that row first, before it
        # ever falls back to the user's own-organisation Administrator role.
        if offered["organization_id"] == user.organization_id:
            if offered["role"] == "org_admin":
                user.grant_org_admin()
            elif user.is_admin():
                user.revoke_org_admin()
    db.session.commit()
    return offered["organization_id"]
