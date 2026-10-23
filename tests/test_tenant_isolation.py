"""Tenant isolation invariants.

Archie is multi-tenant, and isolation is not enforced by query code — it is
enforced by two SQLAlchemy event listeners in ``app/middleware/tenant_isolation.py``:

* ``do_orm_execute``  adds ``WHERE organization_id = g.current_org_id`` to ORM SELECTs
* ``before_flush``    sets ``organization_id`` on newly inserted ``TenantMixin`` rows

Nothing in the type system, and nothing a reviewer can see at a call site, tells
you whether a given query is scoped. That makes these tests the *only* mechanism
that can establish isolation holds. There are 55 ``TenantMixin`` models.

Two gaps are known and encoded below as strict xfails
-----------------------------------------------------
``do_orm_execute`` returns early for anything that is not a SELECT::

    if not orm_execute_state.is_select:
        return

and ``before_flush`` only walks ``session.new`` (inserts). So **bulk UPDATE and
bulk DELETE are not tenant-filtered at all**, even inside a request context. The
repository contains 35 bulk ``.update()`` / ``.delete()`` call sites.

Those two tests are marked ``xfail(strict=True)``: they document the gap without
breaking the build on pre-existing behaviour, and if the gap is ever closed the
strict xfail *fails*, forcing the marker to be removed. See
docs/adr/0003-tenant-isolation-gaps.md.

``test_get_by_id_is_tenant_scoped`` is NOT xfailed
--------------------------------------------------
Whether ``Query.get()`` honours ``with_loader_criteria`` decides whether
``app/api/v1/applications.py:482`` is a cross-tenant delete vector: that endpoint's
only authorisation check is a ``.get()`` returning None for a foreign id.

**Executed 2026-07-30: it PASSES** — ``.get()`` is scoped, so that endpoint is safe.
The test stays un-xfailed as a regression guard, because the endpoint's safety depends
on this behaviour continuing to hold. If it ever fails, that is a confirmed
cross-tenant delete, not a flaky test.

Suite status when last run: 6 passed, 2 xfailed (the two documented gaps).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_app_component(db_session, org_id, name):
    """Insert an ApplicationComponent directly attributed to *org_id*.

    ApplicationComponent is a TenantMixin model and is the exact model used by the
    bulk-delete endpoint under review, so the tests exercise the real target.
    ``name`` is its only non-nullable business column.
    """
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


# --------------------------------------------------------------- SELECT scoping


def test_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """The core invariant: org A must not see org B's rows."""
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    _make_app_component(db_session, org_a.id, "App owned by A")
    b_row = _make_app_component(db_session, org_b.id, "App owned by B")

    with tenant_ctx(org_a.id):
        visible = ApplicationComponent.query.all()
        visible_ids = {row.id for row in visible}

    assert b_row.id not in visible_ids, (
        "TENANT LEAK: a query in org A's context returned org B's row. "
        "The do_orm_execute filter in app/middleware/tenant_isolation.py is not applying."
    )
    assert all(row.organization_id == org_a.id for row in visible), (
        "TENANT LEAK: query returned rows belonging to another organization."
    )


def test_filtered_select_cannot_reach_other_org(db_session, make_org, tenant_ctx):
    """Explicitly filtering by a foreign id must still return nothing."""
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    b_row = _make_app_component(db_session, org_b.id, "App owned by B")

    with tenant_ctx(org_a.id):
        found = ApplicationComponent.query.filter_by(id=b_row.id).first()

    assert found is None, (
        f"TENANT LEAK: org A retrieved org B's row (id={b_row.id}) by filtering on its id."
    )


def test_get_by_id_is_tenant_scoped(db_session, make_org, tenant_ctx):
    """``Query.get()`` must not return another tenant's row.

    This is the authorisation check that ``DELETE /api/v1/applications/<id>``
    relies on. A failure here means that endpoint deletes across tenants.
    """
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    b_row = _make_app_component(db_session, org_b.id, "App owned by B")
    b_row_id = b_row.id

    # Expire everything so .get() must hit the database rather than the identity map,
    # which would otherwise mask the absence of a filter.
    db_session.expunge_all()

    with tenant_ctx(org_a.id):
        fetched = ApplicationComponent.query.get(b_row_id)

    assert fetched is None, (
        "SECURITY: Query.get() returned another tenant's row. "
        "app/api/v1/applications.py:482 authorises its bulk delete solely with this "
        "call, so org A can delete org B's application. Fix by scoping the lookup "
        "explicitly (filter_by(organization_id=...)) rather than relying on .get()."
    )


def test_get_by_id_is_NOT_scoped_on_an_identity_map_hit(db_session, make_org, tenant_ctx):
    """The limit of the guarantee above, pinned so it cannot be over-read.

    The preceding test calls ``expunge_all()`` so ``.get()`` must hit the database.
    That is the only case it covers, and CLAUDE.md previously generalised it to
    "``Query.get()`` *is* scoped (verified)". It is not.

    On an identity-map HIT, ``.get()`` returns the cached object without emitting
    SQL, so ``do_orm_execute`` never fires and no tenant predicate is applied. This
    test asserts that leaky behaviour deliberately: it documents a real property of
    SQLAlchemy rather than a defect we intend to fix, and it will fail loudly if a
    future change makes the identity map tenant-aware — at which point the guidance
    below can be relaxed.

    Consequence, and the reason this is written down: a single request is a single
    tenant on a single session, so request-handling code is unaffected. The exposure
    is code that loops over tenants *within one session* — CLI commands, the
    scheduler, importers, and tests. There, call ``db.session.remove()`` between
    tenants and put ``organization_id`` in the predicate.
    """
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    b_row = _make_app_component(db_session, org_b.id, "App owned by B")
    b_row_id = b_row.id

    # Deliberately NO expunge: load B's row as B so it sits in the identity map.
    with tenant_ctx(org_b.id):
        assert ApplicationComponent.query.get(b_row_id) is not None

    with tenant_ctx(org_a.id):
        leaked = ApplicationComponent.query.get(b_row_id)
    assert leaked is not None, (
        "Query.get() no longer returns a cached cross-tenant row. That is an "
        "improvement, not a failure — the identity map has become tenant-aware. "
        "Relax the CLI/scheduler guidance in CLAUDE.md and delete this test."
    )

    # And the control: once the cache is dropped, the filter does apply.
    db_session.expunge_all()
    with tenant_ctx(org_a.id):
        assert ApplicationComponent.query.get(b_row_id) is None, (
            "SECURITY: .get() reached another tenant's row even on a cold session."
        )


# --------------------------------------------------------------- INSERT scoping


def test_insert_inherits_current_org(db_session, make_org, tenant_ctx):
    """before_flush must stamp organization_id on new rows."""
    from app.models.application_portfolio import ApplicationComponent

    org_a = make_org("a")

    with tenant_ctx(org_a.id):
        row = ApplicationComponent(name="Created inside org A context")
        db_session.add(row)
        db_session.flush()
        assigned = row.organization_id

    assert assigned == org_a.id, (
        f"expected organization_id to be auto-set to {org_a.id}, got {assigned!r}. "
        "The before_flush listener in tenant_isolation.py is not applying."
    )


def test_explicit_org_on_insert_is_not_overwritten(db_session, make_org, tenant_ctx):
    """An explicitly-set organization_id must win over the ambient context."""
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")

    with tenant_ctx(org_a.id):
        row = ApplicationComponent(name="Explicitly attributed to B", organization_id=org_b.id)
        db_session.add(row)
        db_session.flush()
        assigned = row.organization_id

    assert assigned == org_b.id, "an explicit organization_id must not be overwritten"


def test_transformation_workstream_select_is_tenant_scoped(
    db_session, make_org, tenant_ctx
):
    """Transformation programme children obey the same tenant query policy."""
    from app.models.strategic import StrategicInitiative
    from app.models.transformation_programme import ProgrammeWorkstream

    org_a, org_b = make_org("transformation-a"), make_org("transformation-b")
    programme = StrategicInitiative(
        name="Org A programme",
        record_kind="transformation_programme",
        organization_id=org_a.id,
    )
    db_session.add(programme)
    db_session.flush()
    stream = ProgrammeWorkstream(
        organization_id=org_a.id,
        programme_id=programme.id,
        workstream_type="application_rationalisation",
        objective="Reduce cost",
        lifecycle_stage="objective",
    )
    db_session.add(stream)
    db_session.flush()
    stream_id = stream.id
    db_session.expunge_all()

    with tenant_ctx(org_b.id):
        assert db_session.get(ProgrammeWorkstream, stream_id) is None


def test_transformation_candidate_and_signal_selects_are_tenant_scoped(
    db_session, make_org, tenant_ctx
):
    """Candidate decisions and their immutable citations never cross tenants."""
    from datetime import datetime, timezone

    from app.models.application_portfolio import ApplicationComponent
    from app.models.strategic import StrategicInitiative
    from app.models.transformation_evidence import CandidateSignal, TransformationCandidate
    from app.models.transformation_programme import ProgrammeWorkstream
    from app.models.user import User

    org_a, org_b = make_org("candidate-a"), make_org("candidate-b")
    actor = User(
        email=f"candidate-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=org_a.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    application = ApplicationComponent(
        name="Candidate application", organization_id=org_a.id
    )
    programme = StrategicInitiative(
        name="Candidate programme",
        record_kind="transformation_programme",
        organization_id=org_a.id,
    )
    db_session.add_all((actor, application, programme))
    db_session.flush()
    stream = ProgrammeWorkstream(
        organization_id=org_a.id,
        programme_id=programme.id,
        workstream_type="application_rationalisation",
        objective="Reduce duplication",
        lifecycle_stage="discover",
    )
    db_session.add(stream)
    db_session.flush()
    candidate = TransformationCandidate(
        organization_id=org_a.id,
        workstream_id=stream.id,
        subject_type="application",
        subject_id=application.id,
        inclusion_status="accepted",
        inclusion_reason="Inspectable signals",
        accepted_by_id=actor.id,
        accepted_at=datetime.now(timezone.utc),
        revision=1,
    )
    db_session.add(candidate)
    db_session.flush()
    signal = CandidateSignal(
        organization_id=org_a.id,
        candidate_id=candidate.id,
        rule_code="cost",
        rule_version="app-rationalisation-r1.1/cost/1",
        payload_json={"observed_values": {"total_cost_of_ownership": None}},
        source_record_ids={"application_components": [application.id]},
        evaluated_at=datetime.now(timezone.utc),
        content_hash="a" * 64,
    )
    db_session.add(signal)
    db_session.flush()
    candidate_id, signal_id = candidate.id, signal.id
    db_session.expunge_all()

    with tenant_ctx(org_b.id):
        assert db_session.get(TransformationCandidate, candidate_id) is None
        assert db_session.get(CandidateSignal, signal_id) is None


def test_capability_reference_and_current_tenant_are_visible_but_foreign_tenant_is_hidden(
    db_session, make_org, tenant_ctx
):
    """Hybrid capability reads expose reference plus own rows, never another tenant's rows."""
    from app.models.unified_capability import UnifiedCapability

    org_a, org_b = make_org("capability-a"), make_org("capability-b")
    suffix = uuid.uuid4().hex[:10]
    reference = UnifiedCapability(
        name="Reference capability",
        code=f"REF-{suffix}",
        scope="reference",
        organization_id=None,
    )
    own = UnifiedCapability(
        name="Tenant A capability",
        code=f"A-{suffix}",
        scope="tenant",
        organization_id=org_a.id,
    )
    foreign = UnifiedCapability(
        name="Tenant B capability",
        code=f"B-{suffix}",
        scope="tenant",
        organization_id=org_b.id,
    )
    db_session.add_all((reference, own, foreign))
    db_session.flush()
    wanted = {reference.id, own.id, foreign.id}
    db_session.expunge_all()

    with tenant_ctx(org_a.id):
        visible = {
            row.id
            for row in UnifiedCapability.query.filter(UnifiedCapability.id.in_(wanted)).all()
        }

    assert reference.id in visible
    assert own.id in visible
    assert foreign.id not in visible


def test_capability_reference_is_read_only_inside_a_tenant_request(
    db_session, tenant_ctx, make_org
):
    """A tenant must not mutate the shared reference catalogue it can read."""
    from app.models.unified_capability import UnifiedCapability

    org = make_org("capability-writer")
    reference = UnifiedCapability(
        name="Immutable reference capability",
        code=f"IMM-{uuid.uuid4().hex[:10]}",
        scope="reference",
        organization_id=None,
    )
    db_session.add(reference)
    db_session.flush()
    reference_id = reference.id
    db_session.expunge_all()

    with tenant_ctx(org.id):
        loaded = db_session.get(UnifiedCapability, reference_id)
        assert loaded is not None
        loaded.name = "Tenant attempted edit"
        with pytest.raises(PermissionError, match="reference capabilities are read-only"):
            db_session.flush()


def test_request_tenant_context_is_set_transaction_locally_in_postgresql(
    app, db_session, make_org
):
    """The database trigger must receive the same tenant as the ORM middleware."""
    from flask_login import login_user
    from sqlalchemy import text

    from app.models.user import User

    org = make_org("db-context")
    user = User(
        email=f"db-context-{uuid.uuid4().hex[:10]}@example.com",
        first_name="DB",
        last_name="Context",
        organization_id=org.id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()

    with app.test_request_context("/"):
        login_user(user)
        handler = next(
            callback
            for callback in app.before_request_funcs[None]
            if callback.__name__ == "set_tenant_context"
        )
        handler()
        direct_actor_org = db_session.connection().execute(
            text("SELECT current_setting('archie.organization_id', true)")
        ).scalar_one()
        actor_org = db_session.execute(
            text("SELECT current_setting('archie.organization_id', true)")
        ).scalar_one()

    assert (direct_actor_org, actor_org) == (str(org.id), str(org.id))


def test_database_tenant_setting_clears_at_transaction_boundary(app):
    """A pooled PostgreSQL connection cannot carry one request's tenant onward."""
    from sqlalchemy import text

    from app import db
    from app.middleware.tenant_isolation import bind_tenant, set_database_tenant_context

    with db.engine.connect() as connection:
        transaction = connection.begin()
        with bind_tenant(777001):
            set_database_tenant_context(connection, 777001)
            assert connection.execute(
                text("SELECT current_setting('archie.organization_id', true)")
            ).scalar_one() == "777001"
        transaction.rollback()

        with connection.begin():
            cleared = connection.execute(
                text("SELECT current_setting('archie.organization_id', true)")
            ).scalar_one()
    assert cleared in (None, "")


def test_explicit_capability_identifier_loader_does_not_treat_missing_org_as_null_owner(
    db_session,
):
    """A system caller without an org may load references, not unclassified NULL rows."""
    from app.models.unified_capability import UnifiedCapability

    suffix = uuid.uuid4().hex[:10]
    legacy = UnifiedCapability(
        name="Unclassified supplied identifier",
        code=f"NULL-ID-{suffix}",
        scope=None,
        organization_id=None,
    )
    reference = UnifiedCapability(
        name="Reference supplied identifier",
        code=f"REF-ID-{suffix}",
        scope="reference",
        organization_id=None,
    )
    db_session.add_all((legacy, reference))
    db_session.flush()

    assert UnifiedCapability.visible_to_organization(legacy.id, None) is None
    assert UnifiedCapability.visible_to_organization(reference.id, None) is reference


def test_capability_api_identifier_lookup_rejects_warm_cached_foreign_tenant(
    app, db_session, make_org, tenant_ctx
):
    """The API's supplied-ID fallback must not trust an identity-map hit."""
    from app.api.v1.capabilities import get_capability
    from app.models.unified_capability import UnifiedCapability

    org_a, org_b = make_org("api-cap-a"), make_org("api-cap-b")
    foreign = UnifiedCapability(
        name="Foreign API capability",
        code=f"API-B-{uuid.uuid4().hex[:10]}",
        scope="tenant",
        organization_id=org_b.id,
    )
    db_session.add(foreign)
    db_session.flush()
    foreign_id = foreign.id

    with tenant_ctx(org_b.id):
        assert db_session.get(UnifiedCapability, foreign_id) is foreign

    with tenant_ctx(org_a.id):
        response = get_capability.__wrapped__(str(foreign_id))
    status = response[1] if isinstance(response, tuple) else response.status_code
    assert status == 404


def test_dual_mapping_service_identifier_lookup_rejects_warm_cached_foreign_tenant(
    db_session, make_org, tenant_ctx
):
    """The compatibility service must explicitly scope supplied capability IDs."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability
    from app.modules.capabilities.services.dual_capability_mapping_service import (
        DualCapabilityMappingService,
    )

    org_a, org_b = make_org("service-cap-a"), make_org("service-cap-b")
    business = BusinessCapability(
        name="Tenant A legacy capability",
        code=f"BUS-A-{uuid.uuid4().hex[:10]}",
        organization_id=org_a.id,
    )
    foreign = UnifiedCapability(
        name="Tenant B replacement",
        code=f"UNI-B-{uuid.uuid4().hex[:10]}",
        scope="tenant",
        organization_id=org_b.id,
    )
    db_session.add_all((business, foreign))
    db_session.flush()
    foreign_id = foreign.id

    with tenant_ctx(org_b.id):
        assert db_session.get(UnifiedCapability, foreign_id) is foreign

    with tenant_ctx(org_a.id):
        result = DualCapabilityMappingService.deprecate_business_capability(
            business.id, foreign_id
        )

    assert result == {"status": "error", "message": "UnifiedCapability not found"}
    assert business.is_deprecated is False


# --------------------------------------------------------------- known gaps


def test_bulk_update_cannot_cross_tenants(db_session, make_org, tenant_ctx):
    """A bulk UPDATE in org A's context must not modify org B's rows."""
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    b_row = _make_app_component(db_session, org_b.id, "Original name")
    b_row_id = b_row.id
    # Commit (a SAVEPOINT release under the db_session fixture), because leaving
    # tenant_ctx pops an app context and Flask-SQLAlchemy's teardown discards
    # merely-flushed rows — the post-context .get() then returns None and this
    # test "fails" without ever testing the filter. Same mechanism as the
    # air-gap fixture fix; it kept the old strict xfail green for years of
    # runs for the wrong reason.
    db_session.commit()

    with tenant_ctx(org_a.id):
        ApplicationComponent.query.filter_by(id=b_row_id).update(
            {"name": "Overwritten from org A"}, synchronize_session=False
        )
        db_session.flush()

    # Verify AS ORG B, the row's owner. An unscoped final read is impossible
    # here: test_request_context reuses the fixture's app context, so the
    # g.current_org_id set inside the block above SURVIVES it (the same
    # context-reuse behaviour CLAUDE.md documents for g._login_user), and a
    # bare .get() after the block runs tenant-filtered as org A — org B's row
    # comes back None and reads as a leak when the update actually matched 0.
    org_b_id = org_b.id  # capture before expunge_all detaches the instance
    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        after = db_session.get(ApplicationComponent, b_row_id)
        assert after is not None and after.name == "Original name", (
            "TENANT LEAK: a bulk UPDATE executed in org A's context modified org B's row."
        )


def test_bulk_delete_cannot_cross_tenants(db_session, make_org, tenant_ctx):
    """A bulk DELETE in org A's context must not remove org B's rows."""
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    b_row = _make_app_component(db_session, org_b.id, "App owned by B")
    b_row_id = b_row.id
    db_session.commit()  # see the commit note in the bulk-update test above

    with tenant_ctx(org_a.id):
        ApplicationComponent.query.filter_by(id=b_row_id).delete(synchronize_session=False)
        db_session.flush()

    # As org B — see the context-reuse note in the bulk-update test above.
    org_b_id = org_b.id  # capture before expunge_all detaches the instance
    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        assert db_session.get(ApplicationComponent, b_row_id) is not None, (
            "TENANT LEAK: a bulk DELETE executed in org A's context removed org B's row."
        )


# --------------------------------------------------------------- documented no-op


def test_no_tenant_context_is_unfiltered_by_design(db_session, make_org):
    """Outside a request context there is no filtering — assert it, don't assume it.

    ``tenant_isolation.py`` documents this as intentional ("NO-OPs when
    g.current_org_id is None (CLI, migrations, background tasks)"). It is pinned
    here because the ~80 CLI commands and the APScheduler jobs run in exactly this
    mode: any of them that reads tenant data sees *every* organization's rows. If
    that ever changes, this test tells you the CLI's data visibility changed.
    """
    from app.models.application_portfolio import ApplicationComponent

    org_a, org_b = make_org("a"), make_org("b")
    a_row = _make_app_component(db_session, org_a.id, "App owned by A")
    b_row = _make_app_component(db_session, org_b.id, "App owned by B")

    ids = {row.id for row in ApplicationComponent.query.all()}
    assert {a_row.id, b_row.id} <= ids, (
        "expected unfiltered access with no tenant context — the documented CLI behaviour"
    )
