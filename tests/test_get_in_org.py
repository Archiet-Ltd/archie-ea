"""A lookup by id inside the caller's organisation runs a real, filtered query.

``Model.query.get(id)`` and ``db.session.get(Model, id)`` answer from the session's identity map
without running a query when the object is already loaded, so the tenant filter in
``do_orm_execute`` never runs. ``TenantMixin.get_in_org`` always runs the query, with the
organisation predicate written into it.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"GIO {label} {suffix}", slug=f"gio-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _record(db_session, org):
    from app.models.business_capability import BusinessCapability

    row = BusinessCapability(name=f"Cap {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def test_an_object_in_the_identity_map_from_org_a_is_not_returned_to_org_b(app, db_session, tenant_ctx):
    from app.models.business_capability import BusinessCapability

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    row = _record(db_session, org_a)
    assert row in db_session  # loaded in this session: the identity map holds it

    with tenant_ctx(org_b.id):
        # The shortcut the helper replaces: no query runs, so the foreign row comes back.
        assert db_session.get(BusinessCapability, row.id) is row
        # The helper runs the filtered query: org B cannot see org A's row.
        assert BusinessCapability.get_in_org(row.id) is None

    with tenant_ctx(org_a.id):
        assert BusinessCapability.get_in_org(row.id) is row


def test_a_missing_id_and_no_tenant_context_answer_none(app, db_session, tenant_ctx):
    from app.models.business_capability import BusinessCapability

    org = _org(db_session, "a")
    row = _record(db_session, org)
    with tenant_ctx(org.id):
        assert BusinessCapability.get_in_org(10**9) is None
        assert BusinessCapability.get_in_org(None) is None
    with tenant_ctx(None):
        # Outside a tenant request nothing is in "the caller's organisation".
        assert BusinessCapability.get_in_org(row.id) is None


def test_the_helper_runs_a_query_that_names_the_organisation(app, db_session, tenant_ctx):
    from sqlalchemy import event

    from app.models.business_capability import BusinessCapability

    org = _org(db_session, "a")
    row = _record(db_session, org)
    seen = []

    def grab(conn, cursor, statement, *a, **k):
        seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", grab)
    try:
        with tenant_ctx(org.id):
            assert BusinessCapability.get_in_org(row.id) is row
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    assert any("organization_id" in s and "business_capabilit" in s.lower() for s in seen)
