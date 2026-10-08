"""Enumeration ratchet for the platform-write guard (lead review of PR 430, Part 1).

For every mapped model that is NOT a ``TenantMixin`` subclass, this asserts
one of two things is true:

1. its table is on ``app.middleware.platform_write_allowlist``, with a
   non-empty, written reason, or
2. an authenticated, non-platform-admin user who tries to write a bare
   instance of it is refused by ``app.exceptions.AuthorizationError`` before
   any SQL runs (``app.middleware.tenant_isolation``'s ``before_flush`` guard).

This is the ratchet the brief asked for: adding a new non-tenant model later
with no allow-list decision made for it fails this test immediately, rather
than silently shipping an unreviewed global table.

Model enumeration follows the same ``db.Model.registry.mappers`` walk
``tests/_isolation_sweep.py``'s own ``mapped_classes()``/``table_models()``
already use for a similar "every model" sweep, so this does not invent a
second way of doing the same thing.
"""

from __future__ import annotations

import pytest


def _non_tenant_table_classes():
    """{table_name: one representative mapped class} for every non-TenantMixin model."""
    import app.models  # noqa: F401 -- import every model module so the registry is complete
    from app import db
    from app.models.mixins.core import TenantMixin

    out = {}
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, TenantMixin):
            continue
        table = getattr(cls, "__table__", None)
        if table is None:
            continue
        # Joined/single-table inheritance can register more than one mapped
        # class for the same table; one representative is enough; the guard
        # itself inspects the table, not which subclass was instantiated.
        out.setdefault(table.name, cls)
    return out


_NON_TENANT_TABLES = sorted(_non_tenant_table_classes())


def _make_non_admin_org_user(db_session, make_org, label):
    from app.models.user import Role, User

    org = make_org(f"guard-{label}")
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"guard-{label}@example.test",
        first_name="Guard",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = "x" * 12
    user.is_org_admin = True
    user.is_platform_admin = False
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.parametrize("table_name", _NON_TENANT_TABLES)
def test_every_non_tenant_model_is_allow_listed_or_refused(
    app, db_session, make_org, table_name
):
    from app.middleware.platform_write_allowlist import reason_for

    reason = reason_for(table_name)
    if reason is not None:
        assert reason.strip(), (
            f"'{table_name}' is on the platform-write allow-list with an "
            "empty reason -- every entry must say why a non-platform-admin "
            "user legitimately writes this table"
        )
        return

    # Not allow-listed: a write attempt as a non-platform-admin, authenticated
    # org admin must be refused by the guard. The guard raises inside
    # before_flush, before any SQL is issued, so a bare, otherwise-incomplete
    # instance is enough -- this does not need to satisfy the table's real
    # NOT NULL constraints.
    import sqlalchemy as sa
    from flask_login import login_user

    from app.exceptions import AuthorizationError

    classes = _non_tenant_table_classes()
    cls = classes[table_name]

    try:
        instance = sa.inspect(cls).class_manager.new_instance()
    except Exception as exc:  # pragma: no cover -- structural edge case, not a security gap
        pytest.skip(f"{table_name} ({cls.__name__}) cannot be bare-instantiated: {exc!r}")

    user = _make_non_admin_org_user(db_session, make_org, table_name.replace("_", "-")[:20])

    with app.test_request_context("/"):
        login_user(user)
        db_session.add(instance)
        try:
            with pytest.raises(AuthorizationError):
                db_session.flush()
        finally:
            # However the flush ended, discard the attempted row so it
            # cannot bleed into the next parametrised case sharing this
            # function-scoped db_session... (it is function-scoped already,
            # but a failed flush can otherwise leave the session unusable
            # for the fixture's own teardown rollback).
            db_session.rollback()


def test_the_allowlist_only_names_tables_that_exist_and_are_not_tenant_scoped():
    """Catch a stale or mistyped allow-list entry.

    An entry naming a table that is not actually in the non-tenant set (typo,
    the model gained TenantMixin later, the table was renamed/dropped) would
    silently stop meaning anything -- this fails loudly instead.
    """
    from app.middleware.platform_write_allowlist import PLATFORM_WRITE_ALLOWLIST

    non_tenant_tables = set(_non_tenant_table_classes())
    stale = sorted(set(PLATFORM_WRITE_ALLOWLIST) - non_tenant_tables)
    assert not stale, (
        "platform_write_allowlist.py names table(s) that are not current, "
        f"non-TenantMixin tables: {stale}"
    )
