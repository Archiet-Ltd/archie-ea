"""Every organisation keeps at least one Administrator.

One module answers three questions, so the migration, the pages, the API and the
verification check can never disagree:

* which organisations have members and no Administrator
  (``organisations_without_administrator``);
* the repair for them (``repair_missing_administrators``): the earliest member
  becomes Administrator, the change is reported and written to the audit trail
  through the one sealed writer (``chain_insert``); a second run changes nothing;
* whether a change would take away an organisation's last Administrator
  (``ensure_keeps_administrator``), which the team page, the account pages and
  the API call before they write.

"Administrator of an organisation" is whatever ``rbac_service.is_org_admin``
says, the same helper ``admin_required`` uses. The SQL below is that helper's
rule written for set-based reads: an OrgRole row of ``org_admin`` for the
organisation, or a member of the organisation whose Role carries
Permission.ADMINISTER. Nothing here ever grants platform administration.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

LAST_ADMINISTRATOR_MESSAGE = (
    "Every organisation needs at least one Administrator. Make another person an "
    "Administrator first, then change or remove this one."
)

#: Permission.ADMINISTER, repeated here so a migration never imports the models.
_ADMINISTER = 0xFF

_MEMBER = "COALESCE(u.is_service_user, FALSE) = FALSE"

# Organisations that have people in them and no one who administers them.
_WITHOUT_ADMIN_SQL = f"""
SELECT o.id, o.name
FROM organizations o
WHERE (
        EXISTS (SELECT 1 FROM users u WHERE u.organization_id = o.id AND {_MEMBER})
        OR EXISTS (SELECT 1 FROM org_roles m WHERE m.organization_id = o.id)
      )
  AND NOT EXISTS (
        SELECT 1 FROM org_roles x
        WHERE x.organization_id = o.id AND x.role = 'org_admin'
      )
  AND NOT EXISTS (
        SELECT 1 FROM users u JOIN roles r ON r.id = u.role_id
        WHERE u.organization_id = o.id AND {_MEMBER}
          AND r.permissions IS NOT NULL AND (r.permissions & {_ADMINISTER}) = {_ADMINISTER}
      )
ORDER BY o.id
"""

_EARLIEST_HOME_MEMBER_SQL = f"""
SELECT u.id, u.email, u.role_id, COALESCE(u.is_org_admin, FALSE) AS flag, r.name AS role_name
FROM users u LEFT JOIN roles r ON r.id = u.role_id
WHERE u.organization_id = :org AND {_MEMBER}
ORDER BY u.id
LIMIT 1
"""

_EARLIEST_ORG_ROLE_MEMBER_SQL = """
SELECT u.id, u.email, m.role AS org_role
FROM org_roles m JOIN users u ON u.id = m.user_id
WHERE m.organization_id = :org
ORDER BY m.id
LIMIT 1
"""


def organisations_without_administrator(connection, limit: int | None = None) -> list[dict]:
    """[{"id", "name"}] for every organisation with members and no Administrator."""
    connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    sql = _WITHOUT_ADMIN_SQL + (" LIMIT :limit" if limit else "")
    rows = connection.execute(text(sql), {"limit": limit} if limit else {}).fetchall()
    return [{"id": row[0], "name": row[1]} for row in rows]


def _administrator_role_id(connection) -> int | None:
    found = connection.execute(text("SELECT id FROM roles WHERE name = 'Administrator'")).scalar()
    if found is not None:
        return int(found)
    # A database that has organisations but was never given its roles: add the one
    # row the repair needs, exactly as Role.insert_roles defines it.
    return connection.execute(
        text(
            'INSERT INTO roles (name, index, "default", permissions) '
            "VALUES ('Administrator', 'admin', FALSE, :perm) RETURNING id"
        ),
        {"perm": _ADMINISTER},
    ).scalar()


def _audit(connection, org_id: int, user_id: int, old: dict, new: dict) -> None:
    from app.models.audit_log import chain_insert

    chain_insert(
        connection,
        organization_id=org_id,
        user_id=None,
        action="update",
        table_name="users",
        record_id=user_id,
        old_value=old,
        new_value=new,
        extra_json={
            "reason": "organisation had members and no Administrator",
            "change": "earliest member made Administrator",
            "platform_admin_granted": False,
        },
    )


def repair_missing_administrators(connection, batch_size: int = 200) -> list[dict]:
    """Make the earliest member Administrator in every organisation that has none.

    Idempotent: an organisation that has an Administrator is never selected, so a
    second run returns an empty list and writes nothing. Batched: each pass takes
    at most ``batch_size`` organisations. Returns one dict per organisation
    repaired: ``organization_id``, ``organization``, ``user_id``, ``email``,
    ``was`` (the role the person held).

    Platform administration is never touched: ``is_platform_admin`` is not read
    or written here.
    """
    repaired: list[dict] = []
    seen: set[int] = set()
    # Row-level security fences tenant tables by organisation; this repair reads and
    # writes across organisations, for this transaction only.
    connection.execute(text("SELECT set_config('archie.platform_scope', 'on', true)"))
    while True:
        batch = [o for o in organisations_without_administrator(connection, batch_size) if o["id"] not in seen]
        if not batch:
            break
        admin_role_id = _administrator_role_id(connection)
        for org in batch:
            seen.add(org["id"])
            member = connection.execute(text(_EARLIEST_HOME_MEMBER_SQL), {"org": org["id"]}).fetchone()
            if member is not None:
                user_id, email, role_id, flag, role_name = member
                prior_org_role = connection.execute(
                    text("SELECT role FROM org_roles WHERE organization_id = :o AND user_id = :u"),
                    {"o": org["id"], "u": user_id},
                ).scalar()
                connection.execute(
                    text("UPDATE users SET role_id = :r, is_org_admin = TRUE WHERE id = :u"),
                    {"r": admin_role_id, "u": user_id},
                )
                was = role_name or "no role"
            else:
                # Members only through an invitation into this organisation: the
                # per-organisation grant is the whole change; their own home
                # organisation's Role is not touched.
                member = connection.execute(text(_EARLIEST_ORG_ROLE_MEMBER_SQL), {"org": org["id"]}).fetchone()
                if member is None:
                    continue
                user_id, email, prior_org_role = member
                flag, role_name = None, None
                was = "no role"
            connection.execute(
                text(
                    "INSERT INTO org_roles (organization_id, user_id, role, granted_by, created_at) "
                    "VALUES (:o, :u, 'org_admin', NULL, NOW()) "
                    "ON CONFLICT ON CONSTRAINT uq_org_role_user DO UPDATE SET role = 'org_admin'"
                ),
                {"o": org["id"], "u": user_id},
            )
            _audit(
                connection,
                org["id"],
                user_id,
                {"role": role_name, "organisation_role": prior_org_role, "is_org_admin": flag},
                {"role": "Administrator", "organisation_role": "org_admin", "is_org_admin": True},
            )
            repaired.append(
                {
                    "organization_id": org["id"],
                    "organization": org["name"],
                    "user_id": user_id,
                    "email": email,
                    "was": was,
                }
            )
    return repaired


def report_line(item: dict) -> str:
    return (
        "Organisation {organization_id} ({organization}): {email} "
        "(user {user_id}, was {was}) is now Administrator".format(**item)
    )


# ---------------------------------------------------------------- refusing the last one


class LastAdministratorError(Exception):
    """The change would leave an organisation with no Administrator."""

    def __init__(self, message: str = LAST_ADMINISTRATOR_MESSAGE):
        super().__init__(message)
        self.message = message


def administrator_ids(org_id: int) -> set[int]:
    """Ids of everyone who administers ``org_id``, by the one helper ``admin_required`` uses."""
    from app.extensions import db
    from app.models.org_role import OrgRole
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    people = {}
    # tenant-scoping-ok: one organisation's own members, by its id
    for user in User.query.filter(User.organization_id == org_id).all():
        if not user.is_service_user:
            people[user.id] = user
    for row in OrgRole.query.filter_by(organization_id=org_id).all():
        if row.user_id not in people:
            user = db.session.get(User, row.user_id)
            if user is not None:
                people[user.id] = user
    return {uid for uid, user in people.items() if rbac_service.is_org_admin(user, org_id)}


def member_ids(org_id: int) -> set[int]:
    """Ids of everyone who belongs to ``org_id``: its own people and anyone invited in."""
    from app.models.org_role import OrgRole
    from app.models.user import User

    # tenant-scoping-ok: one organisation's own members, by its id
    ids = {u.id for u in User.query.filter(User.organization_id == org_id).all() if not u.is_service_user}
    ids.update(row.user_id for row in OrgRole.query.filter_by(organization_id=org_id).all())
    return ids


def ensure_keeps_administrator(org_id: int, losing_ids, leaving: bool = False) -> None:
    """Raise ``LastAdministratorError`` when removing or demoting ``losing_ids``
    would leave ``org_id`` without an Administrator it has now.

    ``leaving`` says the people are being removed from the organisation (deleted,
    or taken out of it) rather than demoted. An organisation they were the only
    members of is left empty, and an empty organisation has no one to be without
    an Administrator, so that is allowed.

    An organisation that already has none is not this function's concern (the
    repair and the verification check own that), so it never blocks a change there.
    """
    from app.extensions import db
    from app.models.user import User

    asked = {int(i) for i in losing_ids if i is not None}
    if leaving and not (member_ids(org_id) - asked):
        return
    losing = set()
    for uid in asked:
        user = db.session.get(User, uid)
        # A platform administrator keeps the Administrator role through any
        # organisation-scoped revoke (User.revoke_org_admin is a no-op for them),
        # so such a change does not take an Administrator away.
        if user is not None and user.organization_id == org_id and getattr(user, "is_platform_admin", False):
            continue
        losing.add(uid)
    current = administrator_ids(org_id)
    if current and not (current - losing):
        raise LastAdministratorError()
