"""D-01 (PR 430 review v2): the write-guard must not refuse a backref-only dirty.

``session.dirty`` includes an object whenever SQLAlchemy's unit-of-work has it
on its "modified" list at all, including a BACKREF-only change: assigning
``user.role = some_role`` back-populates the ``Role.users`` collection, which
makes SQLAlchemy report the ``Role`` row dirty even though no column on
``Role`` itself changed. Before this fix, the platform-write guard
(``app/middleware/tenant_isolation.py::_refuse_unscoped_platform_write``)
treated that as a real write to the global ``Role`` table and refused it —
the exact cause of every invite/admin-add-user regression PR 430 round 2's
write-path test batch found (review-pr430-v2.md, D-01).

The fix checks ``session.is_modified(obj, include_collections=False)`` for
each dirty object, which answers "did one of THIS object's own column
attributes change" and ignores collection/relationship-only changes. Both
halves need proving: the false positive must be gone (role assignment no
longer refused), and the guard must still catch a GENUINE column edit to an
otherwise-global table by a non-platform-admin.
"""
from __future__ import annotations

import uuid

import pytest


def _make_org_admin(db_session, make_org, label):
    from app.models.user import Role, User

    org = make_org(f"backref-dirty-{label}")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"backref-dirty-{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Backref",
        last_name="Dirty",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = "x" * 12
    user.is_org_admin = True
    user.is_platform_admin = False
    db_session.add(user)
    db_session.flush()
    return org, user, role


def test_assigning_a_role_no_longer_refuses_on_the_backref_only_dirty_role_row(
    app, db_session, make_org
):
    """BEFORE this fix: this raised AuthorizationError on the Role row."""
    from flask_login import login_user

    from app.models.user import Role, User

    org, admin, role = _make_org_admin(db_session, make_org, "assign")

    other_role = Role.query.filter_by(name="Viewer").first() or role
    target = User(
        email=f"backref-dirty-target-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Target",
        last_name="User",
        organization_id=org.id,
        confirmed=True,
        role=other_role,
    )
    target.password = "x" * 12
    db_session.add(target)
    db_session.flush()

    with app.test_request_context("/"):
        login_user(admin)
        # Reassigning the role dirties Role via the Role.users backref with
        # no column on Role itself changing -- this must not raise.
        target.role = role
        db_session.flush()

    db_session.refresh(target)
    assert target.role_id == role.id


def test_a_genuine_column_edit_to_role_by_a_non_admin_is_still_refused(
    app, db_session, make_org
):
    """AFTER this fix: a real write to the global Role table is still caught.

    Proves the fix narrows the false-positive, it does not disable the guard:
    is_modified(obj, include_collections=False) is True here because `name`
    is Role's own column attribute, not a relationship/collection.
    """
    from flask_login import login_user

    from app.exceptions import AuthorizationError
    from app.models.user import Role

    _, admin, role = _make_org_admin(db_session, make_org, "mutate")

    with app.test_request_context("/"):
        login_user(admin)
        role.name = f"renamed-{uuid.uuid4().hex[:8]}"
        with pytest.raises(AuthorizationError):
            db_session.flush()
    db_session.rollback()
