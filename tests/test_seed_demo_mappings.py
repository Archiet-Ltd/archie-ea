"""The demo mapping seeder must stay inside the organisation it is given.

This seeder exists to make a demonstration tenant navigable: entities alone
(applications, capabilities, value streams) render as disconnected lists, and
every coverage view reads empty. It writes *sample* relationships, so the two
properties that matter are that it never escapes the organisation it was given,
and that it never invents an entity to hang a relationship on — an unknown name
is skipped and reported, not created.
"""

import pytest

from app.commands.seed_demo_mappings import seed_demo_mappings


@pytest.fixture
def tenant_with_entities(db_session, make_org, tenant_ctx):
    """An org holding two applications and two capabilities by known name.

    Built inside `tenant_ctx` and **committed**, for two separate reasons.
    Inside, because these models carry after-insert listeners that mirror the
    row into `archimate_elements`, whose `organization_id` is NOT NULL and is
    resolved from the request context. Committed, because leaving the context
    pops the app context and Flask-SQLAlchemy's teardown removes the session —
    a merely flushed row is gone by the time a CLI-style call reads it. Under
    the `db_session` fixture a commit is a SAVEPOINT release, so this still
    leaves no residue in the shared test database.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    def _build(label):
        org = make_org(label)
        with tenant_ctx(org.id):
            for name in ("Blue Yonder WMS", "Tadley Despatch Tool"):
                db_session.add(ApplicationComponent(name=name, organization_id=org.id))
            for name in ("Warehouse Management", "Transport & Distribution"):
                db_session.add(BusinessCapability(name=name, organization_id=org.id))
            db_session.commit()
        return org

    return _build


def _mapping_count(db_session, org_id):
    from app.models.application_capability import ApplicationCapabilityMapping

    return (
        db_session.query(ApplicationCapabilityMapping)
        .filter(ApplicationCapabilityMapping.organization_id == org_id)
        .count()
    )


def test_seeding_links_applications_to_capabilities(db_session, tenant_with_entities):
    org = tenant_with_entities("seed-basic")

    stats = seed_demo_mappings(org.id)

    assert stats["app_capability_created"] > 0
    assert _mapping_count(db_session, org.id) == stats["app_capability_created"]


def test_seeding_is_idempotent(db_session, tenant_with_entities):
    org = tenant_with_entities("seed-idempotent")

    seed_demo_mappings(org.id)
    after_first = _mapping_count(db_session, org.id)
    second = seed_demo_mappings(org.id)

    assert second["app_capability_created"] == 0, "a second run duplicated rows"
    assert _mapping_count(db_session, org.id) == after_first


def test_seeding_stays_inside_the_organisation_it_is_given(
    db_session, tenant_with_entities, make_org
):
    """The decisive property, and the reason the seeder filters explicitly.

    Only ONE org is populated. The seeder is then pointed at a DIFFERENT,
    empty org. If its lookups were not scoped, the populated org's applications
    and capabilities would be found by name and mapped into the empty one.

    Two details are load-bearing. It runs outside `tenant_ctx`, because the
    command runs from the CLI where app/middleware/tenant_isolation.py is a
    no-op — inside a context the middleware would do the scoping and prove
    nothing about the seeder. And it asserts on rows created, not on rows
    stamped with an org: mappings are always stamped with the org passed in, so
    counting by `organization_id` passes even when the foreign keys point across
    the tenant boundary. Verified by deleting the filter: this test fails, the
    count-based version did not.
    """
    populated = tenant_with_entities("seed-populated")
    empty = make_org("seed-empty-target")

    stats = seed_demo_mappings(empty.id)

    assert stats["app_capability_created"] == 0, (
        "the seeder mapped another organisation's applications into an org that "
        "has none of its own"
    )
    assert _mapping_count(db_session, empty.id) == 0
    assert _mapping_count(db_session, populated.id) == 0


def test_cli_command_runs_with_a_tenant_context(app, db_session, tenant_with_entities):
    """The command must establish the tenant, not just pass the id along.

    Writing an ApplicationCapabilityMapping fires the ArchiMate relationship
    sync, which inserts into `archimate_relationships` — NOT NULL
    organization_id, defaulted from `g.current_org_id`. Invoked from a bare CLI
    there is no request context, so on a database with more than one
    organisation the default resolves to None and the entire seed aborts on the
    constraint. That is exactly how this failed against production, and the
    function-level tests could not see it because they never went through the
    command.
    """
    org = tenant_with_entities("seed-cli")

    result = app.test_cli_runner().invoke(args=["seed-demo-mappings", "--org-id", str(org.id)])

    assert result.exit_code == 0, (
        f"command failed: {result.output}\n{result.exception!r}"
    )
    assert "linked" in result.output


def test_unknown_names_are_skipped_not_invented(db_session, make_org):
    """An org with none of the named entities gets no rows and no new entities."""
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("seed-empty")
    stats = seed_demo_mappings(org.id)

    apps = (
        db_session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == org.id)
        .count()
    )

    assert stats["app_capability_created"] == 0
    assert stats["skipped"] > 0, "unmatched names should be reported, not silently dropped"
    assert apps == 0, "the seeder created an application that did not exist"


def test_dry_run_writes_nothing_but_still_reports_what_it_would_do(
    db_session, tenant_with_entities
):
    """A preview that reports zero is worse than no preview.

    The first version of this test asserted the *reported count* was 0, which
    passed against a dry run that zeroed its own counters — so the command
    printed "would link 0" for a tenant it would have linked dozens of mappings
    into. The count must reflect the work; only the database must be untouched.
    """
    org = tenant_with_entities("seed-dryrun")

    stats = seed_demo_mappings(org.id, dry_run=True)

    assert _mapping_count(db_session, org.id) == 0, "dry run wrote rows"
    assert stats["app_capability_created"] > 0, (
        "dry run reported no work for a tenant it would have seeded"
    )

    applied = seed_demo_mappings(org.id)
    assert applied["app_capability_created"] == stats["app_capability_created"], (
        "the dry run's preview did not match what the real run actually did"
    )


# ── the table the Investment Map and capability screens read ─────────────────


def _unified_count(db_session, org_id):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping as U,
    )

    return (
        db_session.query(U)
        .join(ApplicationComponent, ApplicationComponent.id == U.application_component_id)
        .filter(ApplicationComponent.organization_id == org_id)
        .count()
    )


def test_seeding_writes_the_rows_the_investment_map_reads(db_session, tenant_with_entities):
    """Fails on the base: the seed wrote only the legacy table, so the screens read 0."""
    org = tenant_with_entities("seed-unified")

    stats = seed_demo_mappings(org.id)

    assert stats["unified_created"] > 0
    assert _unified_count(db_session, org.id) == stats["unified_created"]
    assert stats["unified_created"] == stats["app_capability_created"]


def test_unified_rows_point_at_the_projection_of_the_same_organisation(
    db_session, tenant_with_entities
):
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping as U,
    )

    org = tenant_with_entities("seed-unified-org")
    seed_demo_mappings(org.id)

    from app.models.application_portfolio import ApplicationComponent

    mine = (
        db_session.query(U)
        .join(ApplicationComponent, ApplicationComponent.id == U.application_component_id)
        .filter(ApplicationComponent.organization_id == org.id)
        .all()
    )
    assert mine
    assert all(r.unified_capability.organization_id == org.id for r in mine)
    assert all(r.unified_capability.source_table == "business_capability" for r in mine)


def test_unified_seeding_is_idempotent(db_session, tenant_with_entities):
    org = tenant_with_entities("seed-unified-idem")

    seed_demo_mappings(org.id)
    first = _unified_count(db_session, org.id)
    second = seed_demo_mappings(org.id)

    assert second["unified_created"] == 0
    assert second["unified_already_present"] == first
    assert _unified_count(db_session, org.id) == first


def test_unified_dry_run_writes_nothing_and_matches_the_real_run(
    db_session, tenant_with_entities
):
    org = tenant_with_entities("seed-unified-dry")

    preview = seed_demo_mappings(org.id, dry_run=True)
    assert _unified_count(db_session, org.id) == 0
    assert preview["unified_created"] > 0

    applied = seed_demo_mappings(org.id)
    assert applied["unified_created"] == preview["unified_created"]


def test_unified_seeding_one_organisation_adds_nothing_for_the_other(
    db_session, tenant_with_entities, tenant_ctx
):
    a = tenant_with_entities("seed-unified-a")
    b = tenant_with_entities("seed-unified-b")

    # The command runs inside the organisation it is given (tenant_scope).
    with tenant_ctx(a.id):
        seed_demo_mappings(a.id)

    assert _unified_count(db_session, a.id) > 0
    assert _unified_count(db_session, b.id) == 0
