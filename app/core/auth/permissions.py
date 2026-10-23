"""One permission matrix per organisation: who may do what to which object.

``can(user, action, object_type)`` is the single answer the server (through
``require_permission``) and the templates (through the ``can`` Jinja global)
both read. Resolution order:

1. the platform ``ADMINISTER`` permission (always allowed);
2. a per-user override row in this organisation;
3. a per-role row in this organisation (role from ``OrgRole``, falling back to
   the platform role name);
4. the defaults in ``DEFAULTS`` below.

Rows live in ``permission_grants``, which is tenant-scoped: every ORM query on
it is filtered to the ambient organisation by the tenant hook. The organisation
is also named in the query (taken from the ambient binding, then from the
account itself, never from request input), so a query made outside a request
cannot read across organisations.
"""

from __future__ import annotations

from typing import Optional

ACTIONS = ("create", "read", "edit", "delete", "bulk_delete")
OBJECT_TYPES = ("application", "relationship", "motivation_element", "enterprise_record", "review_workflow")

_ALL = frozenset(ACTIONS)
# Architects create, read, edit and delete one application at a time; deleting
# many at once (``bulk_delete``) stays administrators only by default.
_ARCHITECT = frozenset({"create", "read", "edit", "delete"})
_READ = frozenset({"read"})

# Defaults per object type and role key. For ``application`` these are what the
# route gates allowed before the matrix existed: create, edit and delete were
# ``require_roles("admin", "architect")``, reading was open to every signed-in
# account. The one change in practice: an architect with an organisation
# role may now delete one application (the control used to be drawn only for
# ``role_archetype == 'architect'``). Bulk delete stays administrators only
# (``bulk_delete``). The organisation's own role strings
# (``org_admin``, ``architect``, ``viewer``) are what the matrix keys on. Any
# role key not listed gets ``_FALLBACK`` (read only), which is what
# ``require_roles`` gave it.
DEFAULTS = {
    "application": {
        "org_admin": _ALL,
        "administrator": _ALL,
        "admin": _ALL,
        "architect": _ARCHITECT,
        "viewer": _READ,
    },
    # Relationships between elements: administrators and architects create and
    # edit, everyone else reads (the route carried only a sign-in check before).
    "relationship": {
        "org_admin": _ALL,
        "administrator": _ALL,
        "admin": _ALL,
        "architect": _ARCHITECT,
        "viewer": _READ,
    },
    # Goals, drivers, requirements and other motivation elements: administrators and
    # architects create, edit and delete them, everyone else reads.
    "motivation_element": {
        "org_admin": _ALL,
        "administrator": _ALL,
        "admin": _ALL,
        "architect": _ARCHITECT,
        "viewer": _READ,
    },
    # The knowledge graph, ArchiMate models, capabilities, vendors and roadmap
    # read through /api/v2/enterprise. The retired RBAC decorator gave every
    # signed-in account read on these and administrators everything.
    "enterprise_record": {
        "org_admin": _ALL,
        "administrator": _ALL,
        "admin": _ALL,
        "architect": _ARCHITECT,
        "viewer": _READ,
    },
    # Architecture review board workflows. The retired decorator let only
    # administrators create one; every other account could read.
    "review_workflow": {
        "org_admin": _ALL,
        "administrator": _ALL,
        "admin": _ALL,
        "architect": _READ,
        "viewer": _READ,
    },
}
_FALLBACK = _READ

# Governance abilities. These are not drawn on the Permissions tab (that lists
# OBJECT_TYPES above); they are the one answer to "may this person vote on the
# board, edit a solution, edit the model, manage the portfolio". They key on the
# persona an ADMINISTRATOR assigned (``enterprise_role``, written only by the
# admin role page, an invitation or single sign-on group mapping; no route lets
# a person set their own), plus organisation-administrator status, which
# ``can`` answers first. A person's own persona preference is stored apart
# (``User.persona_lens``) and is never read here.
GOVERNANCE_ACTIONS = {
    "solution": ("edit", "view_all", "access_any"),
    "archimate": ("edit",),
    "arb_decision": ("vote", "decide"),
    "portfolio": ("manage",),
}
_GOVERNANCE_DEFAULTS = {
    "solution": {
        "edit": frozenset({"solution_architect", "enterprise_architect", "business_architect", "platform_admin"}),
        "view_all": frozenset({"enterprise_architect", "cto", "platform_admin"}),
        "access_any": frozenset({"platform_admin"}),
    },
    "archimate": {
        "edit": frozenset({"enterprise_architect", "business_architect", "platform_admin"}),
    },
    "arb_decision": {
        "vote": frozenset({"arb_member", "enterprise_architect", "business_architect", "platform_admin"}),
        "decide": frozenset({"arb_member", "cto", "enterprise_architect", "platform_admin"}),
    },
    "portfolio": {
        "manage": frozenset({"portfolio_manager", "enterprise_architect", "business_architect", "platform_admin"}),
    },
}

# Role keys shown on the Permissions tab that the matrix cannot restrict.
LOCKED_ROLES = ("org_admin",)


def _matrix_org(user, organization_id: Optional[int]) -> Optional[int]:
    if organization_id is not None:
        return int(organization_id)
    from app.middleware.tenant_context import current_org_id

    org_id = current_org_id()
    if org_id is not None:
        return int(org_id)
    value = getattr(user, "organization_id", None)
    return int(value) if value is not None else None


def _is_administrator(user, org_id) -> bool:
    """Platform administrator, or administrator of THIS organisation.

    The same two checks ``require_roles("admin")`` makes (a global
    ``Permission.ADMINISTER`` flag alone must not make someone an administrator
    of an organisation they only belong to as a viewer).
    """
    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    try:
        return bool(is_platform_admin(user)) or bool(
            rbac_service.is_org_admin(user, org_id)
        )
    except Exception:  # fabricated-ok: an unreadable role is not administrator authority
        return False


def role_key(user, organization_id: Optional[int] = None) -> str:
    """The role name the matrix uses for ``user`` in the organisation."""
    from app.models.org_role import OrgRole

    org_id = _matrix_org(user, organization_id)
    key = None
    if org_id is not None and getattr(user, "id", None) is not None:
        key = OrgRole.get_role(org_id, user.id)
    if key:
        return str(key).lower()
    names = {str(getattr(user, "role_name", "") or "").lower()}
    archetype = getattr(user, "role_archetype", None)
    if archetype:
        # A global ``role_archetype`` of "admin"/"administrator" is NOT the
        # matrix "admin" key: administrator authority is answered first by
        # ``can`` through ``_is_administrator`` (scoped to the active
        # organisation), so reading this global flag as "admin" here would hand
        # matrix-administrator rights in any organisation the account merely
        # belongs to without a role row, with no active-organisation scoping.
        archetype_key = str(archetype).lower()
        if archetype_key not in ("admin", "administrator"):
            names.add(archetype_key)
    if "architect" in names:
        return "architect"
    # An account whose persona ends in "_architect" and whose platform role
    # carries write permission was admitted by require_roles("architect").
    persona = str(getattr(user, "enterprise_role", "") or "").lower()
    if persona.endswith("_architect"):
        try:
            from app.models.user import Permission

            if user.can(Permission.GENERAL):
                return "architect"
        except Exception:  # fabricated-ok: unreadable permission means no write persona
            pass
    names.discard("")
    names.discard("anonymous")
    return sorted(names)[0] if names else "viewer"


def persona_key(user) -> str:
    """The matrix role key of an administrator-assigned persona (may be empty)."""
    return str(getattr(user, "enterprise_role", "") or "").strip().lower()


def default_allowed(role: str, action: str, object_type: str) -> bool:
    if object_type in GOVERNANCE_ACTIONS:
        return str(role).lower() in _GOVERNANCE_DEFAULTS[object_type].get(action, frozenset())
    table = DEFAULTS.get(object_type, {})
    return action in table.get(str(role).lower(), _FALLBACK)


def _grant(org_id, subject_type, subject_key, object_type, action):
    from app.models.permission_grant import PermissionGrant

    return PermissionGrant.query.filter_by(
        organization_id=org_id,
        subject_type=subject_type,
        subject_key=str(subject_key),
        object_type=object_type,
        action=action,
    ).first()


def can(user, action: str, object_type: str, organization_id: Optional[int] = None) -> bool:
    """Whether ``user`` may ``action`` an ``object_type`` in the organisation."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    governance = object_type in GOVERNANCE_ACTIONS
    if governance:
        if action not in GOVERNANCE_ACTIONS[object_type]:
            return False
    elif action not in ACTIONS or object_type not in OBJECT_TYPES:
        return False
    org_id = _matrix_org(user, organization_id)
    if _is_administrator(user, org_id):
        return True
    if org_id is not None:
        row = _grant(org_id, "user", user.id, object_type, action)
        if row is not None:
            return bool(row.allowed)
    key = persona_key(user) if governance else role_key(user, org_id)
    if org_id is not None:
        row = _grant(org_id, "role", key, object_type, action)
        if row is not None:
            return bool(row.allowed)
    return default_allowed(key, action, object_type)


def role_cell(org_id: int, role: str, action: str, object_type: str):
    """(allowed, is_set) for one role cell: ``is_set`` when a row decides it."""
    row = _grant(org_id, "role", role, object_type, action)
    if row is not None:
        return bool(row.allowed), True
    return default_allowed(role, action, object_type), False


def set_grant(org_id, subject_type, subject_key, object_type, action, allowed, granted_by=None):
    """Upsert one matrix row. The caller commits."""
    from app import db
    from app.models.permission_grant import PermissionGrant

    if (
        subject_type not in ("role", "user")
        or (
            action not in GOVERNANCE_ACTIONS.get(object_type, ())
            if object_type in GOVERNANCE_ACTIONS
            else (action not in ACTIONS or object_type not in OBJECT_TYPES)
        )
    ):
        raise ValueError("unknown subject, action or object type")
    row = _grant(org_id, subject_type, subject_key, object_type, action)
    if row is None:
        row = PermissionGrant(
            organization_id=org_id,
            subject_type=subject_type,
            subject_key=str(subject_key),
            object_type=object_type,
            action=action,
        )
        db.session.add(row)
    row.allowed = bool(allowed)
    row.granted_by = granted_by
    db.session.flush()
    return row


def clear_grant(org_id, subject_type, subject_key, object_type, action) -> bool:
    """Remove one matrix row. The caller commits. True when a row existed."""
    from app import db

    row = _grant(org_id, subject_type, subject_key, object_type, action)
    if row is None:
        return False
    db.session.delete(row)
    db.session.flush()
    return True
