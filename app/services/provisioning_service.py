"""Provisioning, leaver removal and the leaver transfer list (R1-B26 PR 1, TB-0143).

The one place that creates a user from an identity provider, deactivates and
reactivates one, revokes their credentials, records SCIM group membership,
issues and revokes the per-organisation SCIM bearer token, and hands a
departed user's ownerships to someone else. The SCIM routes, the SSO sign-in
(``SSOService.provision_user``) and the administrator screens are thin
adapters over these functions; nothing else writes ``users.deactivated_at``,
``users.deactivation_reason``, ``users.provisioned_via``, ``scim_tokens`` or
``scim_group_memberships``.

Three callers sign users in from an identity provider, and all three come
through ``create_or_update_user``: the per-organisation SSO sign-in
(``SSOService.provision_user``) and the two global SSO callbacks of the
account blueprints (``link_only=True``). No other code constructs a ``User``
from an identity provider.

Tenancy: ``User`` is not a ``TenantMixin`` model, so every ``User`` query here
carries an explicit ``User.organization_id == org_id`` predicate, and an id
from another organisation is answered exactly as a missing one.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.account_token import AccountToken, digest
from app.models.application_owner import ApplicationOwner
from app.models.miscellaneous import SSOGroupRoleMapping
from app.models.scim import ScimGroupMembership, ScimToken
from app.models.user import ROLE_NON_TECHNICAL_OWNER, ROLE_PLATFORM_ADMIN, VALID_ROLES, User
from app.middleware.tenant_context import accessible_organizations
from app.middleware.tenant_decorators import is_platform_admin
from app.services import auth_audit, session_registry
from app.services.rbac_service import rbac_service

logger = logging.getLogger(__name__)

SOURCE_SCIM = "scim"
SOURCE_SSO = "sso"

#: Reason strings fit ``user_sessions.revoked_reason`` / ``users.deactivation_reason`` (String(32)).
REASON_LEAVER = "leaver_deprovisioned"

#: At most this many unrevoked SCIM tokens per organisation (rotation: issue the
#: second, switch the IdP, revoke the first).
MAX_ACTIVE_SCIM_TOKENS = 2

_TOKEN_PREFIX_LENGTH = 12
_LAST_USED_THROTTLE = timedelta(minutes=1)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProvisioningError(Exception):
    """A refused provisioning request; carries the SCIM status and scimType."""

    status = 400
    scim_type = None

    def __init__(self, detail, *, status=None, scim_type=None):
        super().__init__(detail)
        self.detail = detail
        if status is not None:
            self.status = status
        if scim_type is not None:
            self.scim_type = scim_type


class NotFoundError(ProvisioningError):
    status = 404


class EmailInOtherOrganisation(ProvisioningError):
    """The email belongs to an account elsewhere. Never names the other organisation."""

    status = 409
    scim_type = "uniqueness"


class ProtectedUserError(ProvisioningError):
    """Platform administrators cannot be changed through provisioning."""

    status = 403


class TokenLimitReached(ProvisioningError):
    status = 409


def _now():
    """Naive UTC, matching how the other timestamp columns are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------


def get_user(org_id, user_id):
    """The user with ``user_id`` in ``org_id``, else ``None`` (never another organisation's)."""
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    if org_id is None:
        return None
    return User.query.filter(User.id == user_id, User.organization_id == org_id).first()


def users_of_org(org_id):
    return User.query.filter(User.organization_id == org_id)


def _admin_email():
    return User.normalize_email(current_app.config.get("ADMIN_EMAIL"))


def is_admin_anywhere(user):
    """True when ``user`` administers any organisation they belong to, or the platform."""
    if is_platform_admin(user):
        return True
    return any(rbac_service.is_org_admin(user, org.id) for org in accessible_organizations(user))


def require_sole_organisation(user, org_id):
    """Refuse (403) a SCIM write to a person who belongs to any organisation but ``org_id``."""
    if any(org.id != org_id for org in accessible_organizations(user)):
        raise ProtectedUserError(
            "This user belongs to another organisation and cannot be changed through SCIM."
        )


def _has_sso_protocol(org_id, provider):
    """Whether ``org_id`` has an enabled per-organisation SSO whose protocol is ``provider``."""
    from app.models.sso_config import SSOConfig

    if org_id is None:
        return False
    return SSOConfig.query.filter(
        SSOConfig.organization_id == org_id,
        SSOConfig.enabled.is_(True),
        SSOConfig.protocol == provider,
    ).first() is not None


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------


def create_or_update_user(org_id, attrs, *, source, user=None, actor=None, link_only=False):
    """Create or update a user of ``org_id`` from identity-provider attributes.

    ``attrs`` keys (any may be absent, meaning "not provided"): ``email``,
    ``first_name``, ``last_name``, ``external_id``, ``sso_provider`` and, for
    SCIM, ``active``. ``user`` is the user being updated (looked up by the
    caller inside ``org_id``); without it the user is found by email.

    Returns ``(user, created, changed)`` where ``changed`` is a list of the
    attribute names that changed (never values). Commits.

    Raises :class:`EmailInOtherOrganisation` when the email belongs to another
    organisation's account (the rule the SSO sign-in has always applied, now
    shared), and, for SCIM only, :class:`ProtectedUserError` for platform
    administrators and a 409 for the configured ``ADMIN_EMAIL``; SCIM also
    refuses to change the ``userName`` of an organisation administrator.

    ``link_only`` is for the global SSO callbacks: without a ``user`` the
    account is looked up by the (``external_id``, ``sso_provider``) pair first
    (inside ``org_id`` when one is given), then by email, and ``external_id``
    and ``sso_provider`` are only ever filled when empty, never replaced.
    """
    scim = source == SOURCE_SCIM
    actor = actor or (f"scim:{source}" if scim else "sso")
    email = User.normalize_email(attrs.get("email")) if attrs.get("email") is not None else None
    if scim:
        _check_lengths(email, attrs)
    if scim and email is not None and not _EMAIL_RE.match(email):
        raise ProvisioningError("userName must be an email address.", scim_type="invalidValue")
    if scim and email is not None and email == _admin_email():
        raise ProvisioningError(
            "That userName is reserved.", status=409, scim_type="uniqueness"
        )

    changed = []
    created = False

    if user is None:
        existing = None
        if link_only and attrs.get("external_id") and attrs.get("sso_provider"):
            pair = User.query.filter(  # tenant-scoping-ok: pre-auth SSO callback, no org context yet; the pair is unique per IdP.
                User.external_id == attrs["external_id"],
                User.sso_provider == attrs["sso_provider"],
            )
            if org_id is not None:
                pair = pair.filter(User.organization_id == org_id)
            # A user the identity provider's own SCIM feed set the id on is never
            # matched by a global sign-in, unless their organisation's SSO is
            # the provider being named (N-01).
            existing = next(
                (
                    u for u in pair.all()
                    if u.provisioned_via != SOURCE_SCIM
                    or _has_sso_protocol(u.organization_id, attrs["sso_provider"])
                ),
                None,
            )
        if email is None and existing is None:
            raise ProvisioningError("userName is required.", scim_type="invalidValue")
        if existing is None and email is not None:
            # tenant-scoping-ok: an email is the globally unique sign-in identity and the
            # whole purpose of this lookup is to find out whether another organisation holds it.
            existing = User.find_by_email(email)
        if existing is not None and org_id is not None and existing.organization_id != org_id:
            raise EmailInOtherOrganisation(
                "This email address belongs to a different organisation's "
                "account and cannot be used here."
            )
        user = existing
        if user is None:
            user = _construct_user(org_id, email, attrs, scim)
            created = True
            changed = [n for n in ("userName", "name.givenName", "name.familyName", "externalId")
                       if _provided(attrs, n)]
        elif scim:
            if user.provisioned_via == SOURCE_SCIM:
                raise EmailInOtherOrganisation(
                    "A user with that userName already exists.", status=409
                )
            if user.is_platform_admin:
                raise ProtectedUserError("Platform administrators cannot be provisioned through SCIM.")
            _check_scim_may_touch(user, org_id, attrs)
            user.provisioned_via = SOURCE_SCIM
            changed.append("provisionedVia")
    elif scim and user.is_platform_admin:
        raise ProtectedUserError("Platform administrators cannot be provisioned through SCIM.")
    elif scim:
        _check_scim_may_touch(user, org_id, attrs)

    # A global sign-in never rewrites the stored email; a difference is audited (N-03).
    email_mismatch = (
        link_only and not created and email is not None
        and email != User.normalize_email(user.email)
    )

    if not created:
        changed.extend(_apply_attrs(org_id, user, email, attrs, scim, link_only))
    elif scim:
        user.provisioned_via = SOURCE_SCIM

    try:
        db.session.commit()
    except IntegrityError as exc:
        # Two requests for the same address raced: the second loses.
        db.session.rollback()
        raise EmailInOtherOrganisation(
            "A user with that userName already exists.", status=409
        ) from exc
    except Exception:
        db.session.rollback()
        raise

    if email_mismatch:
        auth_audit.record_sso_email_mismatch(user.organization_id, user, attrs.get("sso_provider"))

    if scim:
        _apply_active(user, attrs, actor, changed)
        if created:
            auth_audit.record_scim_user_created(org_id, user, actor, changed)
        elif changed:
            auth_audit.record_scim_user_updated(org_id, user, actor, changed)
    return user, created, changed


#: Column widths on ``users`` (email, first_name, last_name, external_id).
_MAX_LENGTHS = {"email": 64, "first_name": 64, "last_name": 64, "external_id": 255}


def _check_lengths(email, attrs):
    values = dict(attrs)
    if email is not None:
        values["email"] = email
    for field, limit in _MAX_LENGTHS.items():
        value = values.get(field)
        if isinstance(value, str) and len(value) > limit:
            raise ProvisioningError(
                f"{field} is longer than {limit} characters.", scim_type="invalidValue"
            )


_ATTR_FIELDS = {
    "userName": "email",
    "name.givenName": "first_name",
    "name.familyName": "last_name",
    "externalId": "external_id",
}


def _provided(attrs, scim_name):
    return attrs.get(_ATTR_FIELDS[scim_name]) is not None


def _construct_user(org_id, email, attrs, scim):
    """The one place a ``User`` is constructed from an identity provider."""
    kwargs = dict(
        email=email,
        first_name=attrs.get("first_name") or "",
        last_name=attrs.get("last_name") or "",
        confirmed=True,
        external_id=attrs.get("external_id"),
    )
    if scim:
        # Never rely on the column default ('platform_admin'); a mapped group
        # may raise this later, a SCIM call never grants an administrator.
        kwargs["enterprise_role"] = ROLE_NON_TECHNICAL_OWNER
    else:
        kwargs["sso_provider"] = attrs.get("sso_provider") or "oidc"
    user = User(**kwargs)
    if org_id is not None:
        user.organization_id = org_id
    db.session.add(user)
    return user


def _apply_attrs(org_id, user, email, attrs, scim, link_only=False):
    changed = []
    if email is not None and not link_only and email != User.normalize_email(user.email):
        if scim and is_admin_anywhere(user):
            # A changed email plus a password reset is an account takeover.
            raise ProtectedUserError(
                "The userName of an organisation administrator cannot be changed through SCIM."
            )
        # tenant-scoping-ok: globally unique identity; checking for a collision anywhere.
        other = User.find_by_email(email)
        if other is not None and other.id != user.id:
            raise EmailInOtherOrganisation("A user with that userName already exists.")
        user.email = email
        changed.append("userName")
    for scim_name in ("name.givenName", "name.familyName"):
        field = _ATTR_FIELDS[scim_name]
        value = attrs.get(field)
        if value is None:
            continue
        # SSO sign-in only ever fills a name that the identity provider sent;
        # SCIM may blank one.
        if not scim and not value:
            continue
        if (getattr(user, field) or "") != value:
            setattr(user, field, value)
            changed.append(scim_name)
    external_id = attrs.get("external_id")
    if external_id is not None:
        # An SSO sign-in never overwrites the id the identity provider's own
        # SCIM feed set (decision 8); a link-only sign-in never replaces any set id.
        if link_only:
            allowed = not user.external_id
        else:
            allowed = scim or user.provisioned_via != SOURCE_SCIM
        if allowed and user.external_id != external_id:
            user.external_id = external_id
            changed.append("externalId")
    if not scim:
        if attrs.get("sso_provider") and not (link_only and user.sso_provider):
            user.sso_provider = attrs["sso_provider"]
        if org_id is not None and not user.organization_id:
            user.organization_id = org_id
    return changed


def _check_scim_may_touch(user, org_id, attrs):
    """Refuse a SCIM write to someone outside this organisation alone (N-02), and
    an externalId set or change on anyone who administers anywhere (N-01)."""
    require_sole_organisation(user, org_id)
    external_id = attrs.get("external_id")
    if external_id is not None and external_id != user.external_id and is_admin_anywhere(user):
        raise ProtectedUserError(
            "The externalId of an administrator cannot be set or changed through SCIM."
        )


def _apply_active(user, attrs, actor, changed):
    if "active" not in attrs or attrs["active"] is None:
        return
    wanted = bool(attrs["active"])
    if wanted and not user.is_active:
        reactivate_user(user, actor=actor)
        changed.append("active")
    elif not wanted and user.is_active:
        deactivate_user(user, reason=REASON_LEAVER, actor=actor)
        changed.append("active")


# ---------------------------------------------------------------------------
# Deactivate / reactivate / revoke
# ---------------------------------------------------------------------------


def deactivate_user(user, *, reason, actor, actor_id=None):
    """Deactivate ``user``: refused on the next request and at every sign-in
    path, sessions and outstanding account tokens revoked. Keeps the row and
    every ownership they hold (flagged on the leaver list, never reassigned).
    Idempotent. Commits.
    """
    if user.is_platform_admin:
        raise ProtectedUserError("Platform administrators cannot be deactivated through provisioning.")
    already = not user.is_active
    if not already:
        user.deactivated_at = _now()
        user.deactivation_reason = (reason or REASON_LEAVER)[:32]
        db.session.commit()
    # Even when already deactivated, make sure nothing is left alive.
    revoke_user_credentials(user, reason or REASON_LEAVER)
    if not already:
        auth_audit.record_user_deactivated(
            user.organization_id, user, actor, user.deactivation_reason, actor_id=actor_id
        )
    return user


def reactivate_user(user, *, actor, actor_id=None):
    """Clear the deactivation state. Ownerships already transferred stay
    transferred. Commits."""
    if user.is_active:
        return user
    user.deactivated_at = None
    user.deactivation_reason = None
    db.session.commit()
    auth_audit.record_user_reactivated(user.organization_id, user, actor, actor_id=actor_id)
    return user


def revoke_user_credentials(user, reason):
    """Revoke every credential the user holds: all sessions, and every
    outstanding account token. The single hook later token stores call into.

    # OAuth access and refresh tokens (PR 347, ``app/modules/oauth_provider``)
    # are not on main yet; when that store lands, revoke the user's tokens here.
    """
    reason = (reason or REASON_LEAVER)[:32]
    session_registry.revoke_all_for_user(user.id, reason)
    now = _now()
    tokens = AccountToken.query.filter(
        AccountToken.user_id == user.id,
        AccountToken.organization_id == user.organization_id,
        AccountToken.revoked_at.is_(None),
    ).all()
    for token in tokens:
        token.revoked_at = now
    db.session.commit()
    return len(tokens)


# ---------------------------------------------------------------------------
# SCIM groups (an SSOGroupRoleMapping row is a SCIM Group)
# ---------------------------------------------------------------------------


def get_group(org_id, group_id):
    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        return None
    if org_id is None:
        return None
    return SSOGroupRoleMapping.query.filter(
        SSOGroupRoleMapping.id == group_id,
        SSOGroupRoleMapping.organization_id == org_id,
    ).first()


def list_groups(org_id, display_name=None):
    query = SSOGroupRoleMapping.query.filter(SSOGroupRoleMapping.organization_id == org_id)
    if display_name is not None:
        query = query.filter(SSOGroupRoleMapping.sso_group_name == display_name)
    return query.order_by(SSOGroupRoleMapping.id).all()


def group_member_ids(org_id, group_id):
    rows = ScimGroupMembership.query.filter(
        ScimGroupMembership.organization_id == org_id,
        ScimGroupMembership.group_mapping_id == group_id,
    ).order_by(ScimGroupMembership.id).all()
    return [r.user_id for r in rows]


def create_group(org_id, display_name, actor):
    """Return ``(mapping, created)``. A new group is created inactive with the
    lowest-privilege role, so it grants nothing until an administrator
    activates and maps it on the SSO settings screen."""
    display_name = (display_name or "").strip()
    if not display_name:
        raise ProvisioningError("displayName is required.", scim_type="invalidValue")
    existing = SSOGroupRoleMapping.query.filter(
        SSOGroupRoleMapping.organization_id == org_id,
        SSOGroupRoleMapping.sso_group_name == display_name,
    ).first()
    if existing is not None:
        return existing, False
    mapping = SSOGroupRoleMapping(
        organization_id=org_id,
        sso_group_name=display_name[:200],
        role_name=ROLE_NON_TECHNICAL_OWNER,
        description="Created by SCIM",
        is_active=False,
    )
    db.session.add(mapping)
    db.session.commit()
    auth_audit.record_scim_group_changed(org_id, mapping.id, actor, ["group"], {"event": "created"})
    return mapping, True


def _resolve_members(org_id, user_ids):
    """The users for ``user_ids``, all inside ``org_id``; any other id is refused."""
    users = []
    seen = set()
    for raw in user_ids:
        user = get_user(org_id, raw)
        if user is None:
            raise ProvisioningError("A member is not a user of this organisation.", scim_type="invalidValue")
        if user.is_platform_admin:
            raise ProtectedUserError("Platform administrators cannot be placed in groups through SCIM.")
        require_sole_organisation(user, org_id)
        if user.id not in seen:
            seen.add(user.id)
            users.append(user)
    return users


def add_group_members(org_id, group, user_ids, actor):
    users = _resolve_members(org_id, user_ids)
    held = set(group_member_ids(org_id, group.id))
    for user in users:
        if user.id not in held:
            db.session.add(ScimGroupMembership(
                organization_id=org_id, group_mapping_id=group.id, user_id=user.id,
            ))
    group.updated_at = _now()
    db.session.commit()
    recompute_roles(org_id, [u.id for u in users])
    auth_audit.record_scim_group_changed(
        org_id, group.id, actor, ["members"], {"added": [u.id for u in users]}
    )


def remove_group_members(org_id, group, user_ids, actor):
    ids = []
    for raw in user_ids:
        user = get_user(org_id, raw)
        if user is not None:
            require_sole_organisation(user, org_id)
            ids.append(user.id)
    if ids:
        ScimGroupMembership.query.filter(
            ScimGroupMembership.organization_id == org_id,
            ScimGroupMembership.group_mapping_id == group.id,
            ScimGroupMembership.user_id.in_(ids),
        ).delete(synchronize_session=False)
        group.updated_at = _now()
        db.session.commit()
        recompute_roles(org_id, ids)
    auth_audit.record_scim_group_changed(org_id, group.id, actor, ["members"], {"removed": ids})


def replace_group_member(org_id, group, old_user_id, new_user_ids, actor):
    """Swap one member (``old_user_id``) for ``new_user_ids`` in a single
    commit; every other member is left alone."""
    users = _resolve_members(org_id, new_user_ids)
    old = get_user(org_id, old_user_id)
    if old is not None:
        require_sole_organisation(old, org_id)
    new_ids = {u.id for u in users}
    held = set(group_member_ids(org_id, group.id))
    gone = [old.id] if old is not None and old.id in held and old.id not in new_ids else []
    if gone:
        ScimGroupMembership.query.filter(
            ScimGroupMembership.organization_id == org_id,
            ScimGroupMembership.group_mapping_id == group.id,
            ScimGroupMembership.user_id.in_(gone),
        ).delete(synchronize_session=False)
    for user in users:
        if user.id not in held:
            db.session.add(ScimGroupMembership(
                organization_id=org_id, group_mapping_id=group.id, user_id=user.id,
            ))
    group.updated_at = _now()
    db.session.commit()
    recompute_roles(org_id, sorted(new_ids | set(gone)))
    auth_audit.record_scim_group_changed(
        org_id, group.id, actor, ["members"], {"removed": gone, "added": sorted(new_ids - held)}
    )


def set_group_members(org_id, group, user_ids, actor):
    """Replace the membership with exactly ``user_ids``."""
    users = _resolve_members(org_id, user_ids)
    wanted = {u.id for u in users}
    held = set(group_member_ids(org_id, group.id))
    gone = sorted(held - wanted)
    for gone_id in gone:
        gone_user = get_user(org_id, gone_id)
        if gone_user is not None:
            require_sole_organisation(gone_user, org_id)
    if gone:
        ScimGroupMembership.query.filter(
            ScimGroupMembership.organization_id == org_id,
            ScimGroupMembership.group_mapping_id == group.id,
            ScimGroupMembership.user_id.in_(gone),
        ).delete(synchronize_session=False)
    for user in users:
        if user.id not in held:
            db.session.add(ScimGroupMembership(
                organization_id=org_id, group_mapping_id=group.id, user_id=user.id,
            ))
    group.updated_at = _now()
    db.session.commit()
    recompute_roles(org_id, sorted(wanted | set(gone)))
    auth_audit.record_scim_group_changed(
        org_id, group.id, actor, ["members"], {"replaced_with": sorted(wanted)}
    )


def delete_group(org_id, group, actor):
    """Remove the group's memberships only; the mapping row stays for the administrator."""
    ids = group_member_ids(org_id, group.id)
    ScimGroupMembership.query.filter(
        ScimGroupMembership.organization_id == org_id,
        ScimGroupMembership.group_mapping_id == group.id,
    ).delete(synchronize_session=False)
    group.updated_at = _now()
    db.session.commit()
    recompute_roles(org_id, ids)
    auth_audit.record_scim_group_changed(org_id, group.id, actor, ["members"], {"event": "memberships_removed"})


def recompute_roles(org_id, user_ids):
    """Recompute each user's ``enterprise_role`` from their active groups
    through the one resolver (``map_groups_to_role``). Mappings that grant
    ``platform_admin`` are ignored; when nothing maps the role is left alone."""
    from app.auth.sso import sso_service

    changed = False
    for uid in user_ids:
        user = get_user(org_id, uid)
        if user is None or user.is_platform_admin:
            continue
        rows = (
            db.session.query(SSOGroupRoleMapping)
            .join(ScimGroupMembership, ScimGroupMembership.group_mapping_id == SSOGroupRoleMapping.id)
            .filter(
                ScimGroupMembership.organization_id == org_id,
                ScimGroupMembership.user_id == user.id,
                SSOGroupRoleMapping.organization_id == org_id,
                SSOGroupRoleMapping.is_active.is_(True),
                SSOGroupRoleMapping.role_name != ROLE_PLATFORM_ADMIN,
            )
            .all()
        )
        names = [r.sso_group_name for r in rows if r.role_name in VALID_ROLES]
        role = sso_service.map_groups_to_role(names, org_id) if names else None
        if role and role != ROLE_PLATFORM_ADMIN and role in VALID_ROLES and role != user.enterprise_role:
            user.enterprise_role = role
            changed = True
    if changed:
        db.session.commit()


# ---------------------------------------------------------------------------
# SCIM bearer token
# ---------------------------------------------------------------------------


def issue_scim_token(org_id, created_by, *, actor_id=None):
    """Create a token and return ``(row, raw)``. The raw value is returned
    once and never stored or logged."""
    from sqlalchemy import select

    from app.models.organization import Organization

    # Serialise concurrent issues for one organisation (same lock check_capacity
    # takes) so the count below cannot be passed by two requests at once.
    db.session.execute(
        select(Organization.id).where(Organization.id == org_id).with_for_update()
    )
    active = ScimToken.query.filter(
        ScimToken.organization_id == org_id, ScimToken.revoked_at.is_(None)
    ).count()
    if active >= MAX_ACTIVE_SCIM_TOKENS:
        raise TokenLimitReached(
            "This organisation already has two active tokens. Revoke one before creating another."
        )
    raw = "scim_" + secrets.token_urlsafe(30)[:40]
    created_by_id = getattr(created_by, "id", created_by)
    row = ScimToken(
        organization_id=org_id,
        token_hash=digest(raw),
        token_prefix=raw[:_TOKEN_PREFIX_LENGTH],
        created_by_id=created_by_id,
    )
    db.session.add(row)
    db.session.commit()
    auth_audit.record_scim_token_issued(
        org_id, row.id, row.token_prefix, f"user:{created_by_id}",
        actor_id=actor_id if actor_id is not None else created_by_id,
    )
    return row, raw


def list_scim_tokens(org_id):
    return ScimToken.query.filter(ScimToken.organization_id == org_id).order_by(ScimToken.id.desc()).all()


def revoke_scim_token(org_id, token_id, actor, *, actor_id=None):
    """Revoke a token of ``org_id``. Returns the row, or ``None`` when no such
    token exists in that organisation (another organisation's id is the same
    as a missing one)."""
    try:
        token_id = int(token_id)
    except (TypeError, ValueError):
        return None
    row = ScimToken.query.filter(
        ScimToken.id == token_id, ScimToken.organization_id == org_id
    ).first()
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = _now()
        db.session.commit()
        auth_audit.record_scim_token_revoked(
            org_id, row.id, row.token_prefix, actor, actor_id=actor_id,
        )
    return row


def authenticate_scim_token(raw):
    """Resolve a presented bearer value to ``(row, reason)``.

    ``row`` is the unrevoked token of an active organisation, else ``None``
    with a ``reason`` (``unknown_token``, ``revoked_token``,
    ``organisation_inactive``). ``last_used_at`` is touched at most once a
    minute.
    """
    from app.models.organization import Organization

    if not raw:
        return None, "missing_token"
    row = ScimToken.query.filter(ScimToken.token_hash == digest(raw)).first()
    if row is None:
        return None, "unknown_token"
    if row.revoked_at is not None:
        return row, "revoked_token"
    org = Organization.query.filter(Organization.id == row.organization_id).first()
    if org is None or org.is_active is False:
        return row, "organisation_inactive"
    now = _now()
    if row.last_used_at is None or now - row.last_used_at >= _LAST_USED_THROTTLE:
        row.last_used_at = now
        db.session.commit()
    return row, None


# ---------------------------------------------------------------------------
# Leaver list and ownership transfer
# ---------------------------------------------------------------------------


def leaver_ownerships(org_id):
    """Deactivated users of ``org_id`` with every ownership row each holds.

    Returns a list of ``{"user": User, "ownerships": [dict, ...]}`` sorted by
    when they were deactivated; a deactivated user with no ownership still
    appears (with an empty list) so the administrator sees who left.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.services.capability_ownership_service import get_tenant_capability

    leavers = (
        User.query.filter(User.organization_id == org_id, User.deactivated_at.isnot(None))
        .order_by(User.deactivated_at.desc(), User.id)
        .all()
    )
    if not leavers:
        return []
    rows = ApplicationOwner.query.filter(
        ApplicationOwner.organization_id == org_id,
        ApplicationOwner.user_id.in_([u.id for u in leavers]),
    ).order_by(ApplicationOwner.id).all()
    by_user = {}
    for row in rows:
        entry = {
            "owner_id": row.id,
            "ownership_type": row.ownership_type,
            "ownership_label": ApplicationOwner.OWNERSHIP_LABELS.get(
                row.ownership_type, (row.ownership_type or "").capitalize()
            ),
            "kind": "application" if row.application_id is not None else (row.element_type or "element"),
            "item_id": row.application_id if row.application_id is not None else row.element_id,
            "item_name": None,
        }
        if row.application_id is not None:
            item = ApplicationComponent.query.filter(
                ApplicationComponent.id == row.application_id,
                ApplicationComponent.organization_id == org_id,
            ).first()
        elif row.element_type == "capability":
            item = get_tenant_capability(row.element_id, org_id)
        else:
            item = None
        entry["item_name"] = getattr(item, "name", None) or f"#{entry['item_id']}"
        by_user.setdefault(row.user_id, []).append(entry)
    return [{"user": u, "ownerships": by_user.get(u.id, [])} for u in leavers]


def active_users(org_id):
    """Active users of ``org_id`` an ownership may be handed to."""
    return (
        User.query.filter(User.organization_id == org_id, User.deactivated_at.is_(None))
        .order_by(User.first_name, User.last_name, User.email)
        .all()
    )


def transfer_ownership(org_id, owner_row_id, new_user_id, *, actor, effective_date=None, recommended_by=None):
    """Move one ownership row from a departed user to an active user of the
    same organisation, through the one ownership record.

    ``actor`` is the administrator's user id. ``effective_date`` and
    ``recommended_by`` are reserved for the successor recommendation (R1-B63)
    and are not used yet. Returns ``(row, removed_duplicate)``.

    Raises :class:`NotFoundError` for a row that is not in ``org_id`` (or whose
    holder is still active) and :class:`ProvisioningError` for a target that is
    not an active user of ``org_id``.
    """
    row = ApplicationOwner.query.filter(
        ApplicationOwner.id == owner_row_id, ApplicationOwner.organization_id == org_id
    ).first()
    if row is None:
        raise NotFoundError("Ownership record not found.")
    holder = get_user(org_id, row.user_id)
    if holder is None or holder.is_active:
        raise NotFoundError("Ownership record not found.")
    target = get_user(org_id, new_user_id)
    if target is None or not target.is_active:
        raise ProvisioningError("Choose an active user of your organisation.", scim_type="invalidValue")
    if target.id == holder.id:
        raise ProvisioningError("Choose someone other than the departed user.", scim_type="invalidValue")

    from_user_id = row.user_id
    application_id, element_type, element_id = row.application_id, row.element_type, row.element_id
    row_id = row.id
    survivor, removed = row.transfer_to(target.id, org_id)
    if not removed:
        row.assigned_by = actor
        row.assigned_at = _now()
    db.session.commit()
    auth_audit.record_owner_transferred(
        org_id, row_id, from_user_id, target.id, actor,
        application_id=application_id, element_type=element_type, element_id=element_id,
        removed_duplicate=removed,
    )
    return survivor, removed
