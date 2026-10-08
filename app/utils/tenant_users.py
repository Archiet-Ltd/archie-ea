"""Look up a user by id, but only inside one organisation.

``User`` carries an ``organization_id`` column but no ``TenantMixin``, so the ORM tenant
filter does not apply to it. A lookup such as ``db.session.get(User, user_id)`` with an id
that came from a request or from stored JSON therefore returns a user of ANY organisation.
Use this wherever a user is resolved from such an id (to show a name, an e-mail, an
assignee) so the organisation check cannot be forgotten.
"""

from __future__ import annotations

from typing import Optional

from app.extensions import db


def escape_like_literal(value: str) -> str:
    """Escape SQL LIKE wildcards so ``%`` and ``_`` match literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def user_in_org(user_id, org_id) -> Optional[object]:
    """The user with ``user_id`` who belongs to ``org_id``, else ``None``.

    Fails closed: a missing organisation, a non-numeric id or a user of another
    organisation all return ``None``.
    """
    from app.models.user import User

    if user_id in (None, "") or org_id is None:
        return None
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.execute(
        db.select(User).where(User.id == user_id).where(User.organization_id == org_id)
    ).scalar_one_or_none()


def same_user_id(submitted, stored) -> bool:
    """True when a submitted user id names the same user as a stored one.

    An edit form usually sends the stored value back unchanged. Request bodies
    carry ids as numbers or strings, so compare them as text (``7`` matches
    ``"7"``); an empty value matches only another empty value. Use this to
    skip re-validating an unchanged stored id, which may predate an
    organisation check and so must not block an edit of other fields.
    """
    submitted_empty = submitted in (None, "")
    stored_empty = stored in (None, "")
    if submitted_empty or stored_empty:
        return submitted_empty and stored_empty
    return str(submitted).strip() == str(stored).strip()
