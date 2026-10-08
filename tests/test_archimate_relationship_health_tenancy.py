"""E2E-I: the Capability Map's relationship-health panel must not leak
counts across tenants.

Confirmed live 6 Sep 2026 (Archie-E2E-Workflow-Test-Report.md E2E-I): a
tenant built from empty with 1 application and 1 composer element read
"2 elements . 34 relationships . 2 unconnected . 17 avg/el" -- both
elements reported unconnected yet 34 relationships were claimed, a figure
close to another tenant's real total.

Root cause: ArchitectureInferenceRelationship carries no organization_id
(no TenantMixin, no fast-init/full-runtime split -- a single, always-active
definition), so a raw count() on it summed every tenant's rows, not just
the caller's, while ArchiMateElement (TenantMixin) was correctly scoped --
hence elements and relationships disagreeing about how many tenants' data
they represented.

(ArchiMateRelationship itself turned out already correct in normal
runtime -- app/models/archimate_core.py re-exports the real, TenantMixin
class from app.models.models unless APP_FAST_INIT=1; the lightweight
class defined directly in archimate_core.py is a test/E2E-speed-only
substitute.)

Corrected: ArchitectureInferenceRelationship has since gained TenantMixin
of its own (a nullable organization_id, backfilled from its source/target
elements' agreeing organisation) rather than relying solely on the
join-through-ArchiMateElement workaround this test originally existed to
prove. A raw, unjoined ``func.count()`` is therefore natively scoped now
too -- SQLAlchemy's ``with_loader_criteria`` applies to any ORM-enabled
query referencing a TenantMixin-mapped class, not only a full-entity
``SELECT`` -- so the leak this test pins is closed at the column, not only
at the join. The join-based query the production route still uses is kept
under test below since it remains correct and is now doubly-scoped
(through both tables' own filters), and a genuinely unscoped raw-SQL count
(bypassing the ORM entirely) is added so the underlying row count is still
verified directly rather than through a query that itself now filters.
"""
from sqlalchemy import func, or_, text


def test_inference_relationship_count_join_excludes_another_tenants_rows(
    app, db_session, make_org, tenant_ctx,
):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    org_a = make_org("relhealth-a")
    org_b = make_org("relhealth-b")

    # Written outside any tenant_ctx, with organization_id set explicitly
    # (the proven pattern from test_tenant_isolation.py's
    # _make_app_component) -- entering AND exiting a tenant_ctx around a
    # write rolls back the flush on request-context teardown, since
    # tenant_ctx only ever promised read-time scoping via g.current_org_id.
    #
    # Tenant B: a real portfolio with several inferred relationships -- the
    # noisy neighbour whose numbers must not leak into tenant A's reading.
    # organization_id is set explicitly on each relationship too, matching
    # how every real writer (ArchitectureGraphFacade.get_or_create_relationship,
    # JourneyOrchestrator's wiring passes, the wizard's capability-realization
    # writer) now attributes these rows -- a NULL-organisation row is invisible
    # to every tenant by design, not a stand-in for "any tenant".
    b_elements = [
        ArchiMateElement(name=f"B element {i}", type="ApplicationComponent",
                          layer="application", organization_id=org_b.id)
        for i in range(6)
    ]
    db_session.add_all(b_elements)
    db_session.flush()
    for i in range(len(b_elements) - 1):
        db_session.add(ArchitectureInferenceRelationship(
            architecture_id=1, source_type="element", source_id=b_elements[i].id,
            target_type="element", target_id=b_elements[i + 1].id, rel_type="serving",
            organization_id=org_b.id,
        ))
    db_session.flush()

    # Tenant A: exactly the reported scenario -- one element, zero
    # inferred relationships of its own.
    a_element = ArchiMateElement(
        name="A element", type="ApplicationComponent", layer="application",
        organization_id=org_a.id,
    )
    db_session.add(a_element)
    db_session.flush()

    # A genuinely unscoped count, bypassing the ORM (and therefore
    # with_loader_criteria) entirely -- confirms tenant B's 5 rows actually
    # exist in the shared table, independent of anyone's tenant context.
    raw_count = db.session.execute(
        text("SELECT COUNT(*) FROM architecture_inference_relationship")
    ).scalar() or 0
    assert raw_count >= 5, (
        "sanity check: tenant B's 5 inferred relationships must actually "
        f"exist in the shared table for this test to mean anything, got {raw_count}"
    )

    with tenant_ctx(org_a.id):
        # The once-buggy query: a raw ORM count with no join to the
        # tenant-scoped element table. It no longer leaks -- AIR's own
        # organization_id column is now filtered by with_loader_criteria
        # the same way a full-entity query would be.
        unjoined_count = db.session.query(
            func.count(ArchitectureInferenceRelationship.id)
        ).scalar() or 0
        assert unjoined_count == 0, (
            "tenant A has zero inferred relationships of its own -- seeing "
            f"any is tenant B's data leaking. Got: {unjoined_count}"
        )

        # The production query (archimate_cap_routes.api_archimate_relationship_health):
        # join through ArchiMateElement so the ORM's automatic tenant filter
        # scopes the count through the element side too. Still correct, now
        # redundant with AIR's own filter rather than load-bearing alone.
        scoped_count = (
            db.session.query(func.count(func.distinct(ArchitectureInferenceRelationship.id)))
            .join(ArchiMateElement, or_(
                ArchitectureInferenceRelationship.source_id == ArchiMateElement.id,
                ArchitectureInferenceRelationship.target_id == ArchiMateElement.id,
            ))
            .scalar() or 0
        )
        assert scoped_count == 0, (
            "tenant A has zero inferred relationships of its own -- seeing "
            f"any is tenant B's data leaking through the join. Got: {scoped_count}"
        )
