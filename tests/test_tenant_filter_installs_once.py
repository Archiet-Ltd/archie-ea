"""install_tenant_filter registers its session hooks once per process.

The hooks sit on the process-global ``db.session``; the test suite (and any
process that builds more than one app) calls ``install_tenant_filter`` many
times. Before the once-only guard each call stacked another copy of every hook,
so every ORM statement issued one ``set_config`` per copy. Tenant filtering
itself must be unchanged: a foreign organisation's row stays invisible.
"""

from __future__ import annotations

import threading

from sqlalchemy import event

from app.extensions import db

HOOKS = ("after_begin", "do_orm_execute", "before_flush")


def _hook_counts():
    probe = db.session.session_factory()
    out = {}
    for name in HOOKS:
        for fn in list(getattr(probe.dispatch, name)):
            key = (name, getattr(fn, "__name__", repr(fn)))
            out[key] = out.get(key, 0) + 1
    probe.close()
    return out


def test_calling_install_tenant_filter_again_adds_no_hooks(app):
    from app.middleware.tenant_isolation import install_tenant_filter

    before = _hook_counts()
    assert before, "the tenant hooks are not registered at all"
    # Every named hook is present exactly once.
    for key in (
        ("after_begin", "_set_database_tenant_after_begin"),
        ("do_orm_execute", "_add_soft_delete_filter"),
        ("do_orm_execute", "_add_tenant_filter"),
        ("before_flush", "_set_tenant_on_new"),
    ):
        assert before.get(key) == 1, (key, before)
    for _ in range(5):
        install_tenant_filter(app)
    assert _hook_counts() == before


def test_one_orm_read_issues_one_tenant_set_config_per_transaction(app, db_session, make_org):
    from flask import g

    from app.middleware.tenant_isolation import install_tenant_filter
    from app.models import ArchiMateElement

    org = make_org("once-a")
    db_session.add(ArchiMateElement(name="Once", type="ApplicationComponent", layer="application", organization_id=org.id))
    db_session.commit()

    def set_config_statements():
        seen = []
        me = threading.get_ident()

        def listener(conn, cursor, statement, params, context, executemany):
            if threading.get_ident() == me and "set_config('archie.organization_id'" in statement:
                seen.append(statement)

        with app.test_request_context("/"):
            g.current_org_id = org.id
            event.listen(db.engine, "before_cursor_execute", listener)
            try:
                db.session.execute(db.select(ArchiMateElement)).scalars().all()
            finally:
                event.remove(db.engine, "before_cursor_execute", listener)
            db.session.rollback()
        return len(seen)

    baseline = set_config_statements()
    for _ in range(5):
        install_tenant_filter(app)
    assert set_config_statements() == baseline
    # One read is the transaction's begin plus the per-execute reassertion, never one per copy.
    assert baseline <= 2


def test_a_foreign_organisations_row_is_still_invisible(app, db_session, make_org):
    from flask import g

    from app.middleware.tenant_isolation import install_tenant_filter
    from app.models import ArchiMateElement

    org_a = make_org("once-own")
    org_b = make_org("once-foreign")
    db_session.add(ArchiMateElement(name="OwnRow", type="ApplicationComponent", layer="application", organization_id=org_a.id))
    db_session.add(ArchiMateElement(name="ForeignRow", type="ApplicationComponent", layer="application", organization_id=org_b.id))
    db_session.commit()

    for _ in range(3):
        install_tenant_filter(app)

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        names = {row.name for row in db.session.execute(db.select(ArchiMateElement)).scalars().all()}
        db.session.rollback()
    assert "OwnRow" in names
    assert "ForeignRow" not in names
