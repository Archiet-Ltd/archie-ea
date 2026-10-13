"""The demo mapping seeder must stay inside the organisation it is given.

This seeder exists to make a demonstration tenant navigable: entities alone
(applications, capabilities, value streams) render as disconnected lists, and
every coverage view reads empty. It writes *sample* relationships, so the two
properties that matter are that it never escapes the organisation it was given,
and that it never invents an entity to hang a relationship on — an unknown name
is skipped and reported, not created.
"""

import contextlib
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


def _unified_mapping_count(db_session, org_id):
    """Unified mappings belonging to org_id.

    ``UnifiedApplicationCapabilityMapping`` carries no ``organization_id``
    column (it is keyed by ``application_component_id`` and
    ``unified_capability_id``), so the org is reached through the mapped
    application, which is a ``TenantMixin`` row.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    return (
        db_session.query(UnifiedApplicationCapabilityMapping)
        .join(
            ApplicationComponent,
            UnifiedApplicationCapabilityMapping.application_component_id
            == ApplicationComponent.id,
        )
        .filter(ApplicationComponent.organization_id == org_id)
        .count()
    )


def _resolvable_pairs():
    """The (application, capability) pairs the fixture org's names resolve."""
    from app.commands.seed_demo_mappings import APP_CAPABILITIES

    fixture_apps = {"Blue Yonder WMS", "Tadley Despatch Tool"}
    fixture_caps = {"Warehouse Management", "Transport & Distribution"}
    return sum(
        1
        for app_name, capability_names in APP_CAPABILITIES.items()
        if app_name in fixture_apps
        for capability_name in capability_names
        if capability_name in fixture_caps
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


# ---------------------------------------------------------------------------
# Unified rows: the Investment Map and the Capability Map read
# `UnifiedApplicationCapabilityMapping`, so the demonstration seeder must
# write those rows too — the same pair, same defaults the screen's own
# writer uses — beside the legacy rows it already writes.
# ---------------------------------------------------------------------------


def test_seed_writes_unified_rows_for_every_resolvable_pair(
    db_session, tenant_with_entities
):
    """Criterion: after the seed, `UnifiedApplicationCapabilityMapping` holds
    one row per resolvable (application, capability) pair, and more than 0."""
    org = tenant_with_entities("seed-unified")

    stats = seed_demo_mappings(org.id)

    unified_rows = _unified_mapping_count(db_session, org.id)
    assert unified_rows > 0, "the seed wrote no unified mapping rows"
    assert stats["app_capability_created"] > 0
    assert unified_rows == stats["app_capability_created"], (
        "the unified rows must mirror the legacy rows, one per resolvable pair"
    )
    assert unified_rows == _resolvable_pairs()
    assert (
        stats["unified_app_capability_created"] == unified_rows
    ), "the reported unified count must match the rows actually written"


def test_investment_map_prerequisite_reports_ready_after_seed(
    db_session, tenant_with_entities
):
    """Criterion: the Investment Map's prerequisite query — the one that
    decides between the real matrix and the 'Map your first application to a
    business capability' screen — reports the organisation as ready once the
    seed has run. It must report *not ready* before the seed, so the test
    proves the seed is what unlocked it."""
    from app.modules.architecture.routes.architecture_routes import (
        _assemble_investment_priorities_context,
    )

    org = tenant_with_entities("seed-investment")

    analysis, mapping_count = _assemble_investment_priorities_context()
    assert analysis is None, "the map should start on the prerequisite screen"
    assert mapping_count == 0

    seed_demo_mappings(org.id)

    analysis, mapping_count = _assemble_investment_priorities_context()
    assert mapping_count > 0, "the seed left the map's mapping store empty"
    assert analysis is not None, (
        "the map still renders 'map your first application' after the seed ran"
    )


def test_second_run_creates_no_new_unified_or_legacy_rows(
    db_session, tenant_with_entities
):
    """Criterion: idempotence extends to the unified rows — a second run
    creates nothing in either table and reports the pairs as already present."""
    org = tenant_with_entities("seed-unified-idem")

    seed_demo_mappings(org.id)
    unified_after_first = _unified_mapping_count(db_session, org.id)
    legacy_after_first = _mapping_count(db_session, org.id)
    assert unified_after_first > 0

    second = seed_demo_mappings(org.id)

    assert second["app_capability_created"] == 0, "a second run duplicated legacy rows"
    assert second["unified_app_capability_created"] == 0, (
        "a second run duplicated unified rows"
    )
    assert _unified_mapping_count(db_session, org.id) == unified_after_first
    assert _mapping_count(db_session, org.id) == legacy_after_first
    assert second["already_present"] == unified_after_first, (
        "pairs that needed no work should be reported as already_present"
    )


def test_dry_run_writes_no_unified_rows(db_session, tenant_with_entities):
    """Criterion: dry-run extends to the unified store — nothing written in
    either table, while the preview still reports the unified work."""
    org = tenant_with_entities("seed-unified-dry")

    stats = seed_demo_mappings(org.id, dry_run=True)

    assert _unified_mapping_count(db_session, org.id) == 0, "dry run wrote unified rows"
    assert _mapping_count(db_session, org.id) == 0, "dry run wrote legacy rows"
    assert stats["app_capability_created"] > 0
    assert stats["unified_app_capability_created"] > 0, (
        "dry run reported no unified work for a tenant it would have seeded"
    )

    applied = seed_demo_mappings(org.id)
    assert applied["unified_app_capability_created"] == stats["unified_app_capability_created"], (
        "the dry run's unified preview did not match what the real run did"
    )
    assert applied["app_capability_created"] == stats["app_capability_created"]


def _references_only_own_unified_caps(db_session, org_id):
    """Every unified mapping row touching org_id's apps must reference org_id's
    own unified capabilities, and vice versa — a row whose two sides belong to
    different organisations is a cross-org write, invisible to the count-based
    assertions."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    own_app_ids = {
        row.id
        for row in db_session.query(ApplicationComponent.id).filter(
            ApplicationComponent.organization_id == org_id
        )
    }
    own_unified_ids = {
        row.id
        for row in db_session.query(UnifiedCapability.id).filter(
            UnifiedCapability.organization_id == org_id
        )
    }
    rows = db_session.query(
        UnifiedApplicationCapabilityMapping.application_component_id,
        UnifiedApplicationCapabilityMapping.unified_capability_id,
    ).all()
    return not [
        (app_id, cap_id)
        for app_id, cap_id in rows
        if (app_id in own_app_ids) != (cap_id in own_unified_ids)
    ]


def _references_only_own_business_caps(db_session, org_id):
    """The legacy sibling of `_references_only_own_unified_caps`."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    own_app_ids = {
        row.id
        for row in db_session.query(ApplicationComponent.id).filter(
            ApplicationComponent.organization_id == org_id
        )
    }
    own_bc_ids = {
        row.id
        for row in db_session.query(BusinessCapability.id).filter(
            BusinessCapability.organization_id == org_id
        )
    }
    rows = db_session.query(
        ApplicationCapabilityMapping.application_component_id,
        ApplicationCapabilityMapping.business_capability_id,
    ).all()
    return not [
        (app_id, cap_id)
        for app_id, cap_id in rows
        if (app_id in own_app_ids) != (cap_id in own_bc_ids)
    ]


def test_seeding_one_org_never_names_another_orgs_rows(
    db_session, tenant_with_entities
):
    """Criterion: with two organisations holding *identically named*
    applications and capabilities, seeding A writes nothing for B and seeding
    B afterwards resolves B's own rows rather than reading A's as already
    present. If any lookup escaped the org, A's rows would be found by name
    and either mapped into B or shadow B's own seeding.

    The seed runs with the ambient tenant context cleared, the shape the
    module docstring promises: the command itself scopes every read and write
    to ``org_id``, so it must be correct even where the request tenant
    middleware is a no-op (a bare CLI, or a test). The verification queries
    also run with the ambient context cleared so they read what is truly in
    the tables, not what the middleware's criteria would show.
    """
    from flask import g

    a = tenant_with_entities("seed-unified-a")
    b = tenant_with_entities("seed-unified-b")

    @contextlib.contextmanager
    def _cleared():
        had = hasattr(g, "current_org_id")
        saved = getattr(g, "current_org_id", None) if had else None
        if had:
            delattr(g, "current_org_id")
        try:
            yield
        finally:
            if had:
                g.current_org_id = saved

    # Seed A with no ambient tenant: only the command's own org filters apply.
    with _cleared():
        seed_demo_mappings(a.id)

    with _cleared():
        assert _unified_mapping_count(db_session, b.id) == 0, (
            "seeding A wrote unified rows for B"
        )
        assert _mapping_count(db_session, b.id) == 0, (
            "seeding A wrote legacy rows for B"
        )
        assert _unified_mapping_count(db_session, a.id) == _resolvable_pairs()
        # Content-level: every unified row A wrote references A's own unified
        # capability, never B's same-named one, and every legacy row A wrote
        # references A's own business capability.
        assert _references_only_own_unified_caps(db_session, a.id)
        assert _references_only_own_business_caps(db_session, a.id)

    # Seed B afterwards: B's same-named entities must resolve to B's own rows,
    # and A's existing rows must not be counted as already present for B.
    with _cleared():
        b_stats = seed_demo_mappings(b.id)
    assert b_stats["unified_app_capability_created"] > 0, (
        "seeding B read A's rows as already present and created nothing"
    )
    assert b_stats["unified_app_capability_created"] == _resolvable_pairs()
    with _cleared():
        a_unified_after_b = _unified_mapping_count(db_session, a.id)
        assert a_unified_after_b == _resolvable_pairs(), "seeding B touched A's rows"
        assert _unified_mapping_count(db_session, b.id) == _resolvable_pairs()
        assert _references_only_own_unified_caps(db_session, b.id)
        assert _references_only_own_business_caps(db_session, b.id)

    # A second run on B stays idempotent and still never reads A's rows.
    with _cleared():
        b_second = _unified_mapping_count(db_session, b.id)
        seed_demo_mappings(b.id)
        assert _unified_mapping_count(db_session, a.id) == a_unified_after_b
        assert _unified_mapping_count(db_session, b.id) == b_second


def test_seed_projects_capabilities_that_have_no_unified_row_yet(
    db_session, tenant_with_entities, tenant_ctx
):
    """A capability that exists but was never projected into the unified store
    (an organisation seeded before the capability projection existed) must be
    projected by the seed itself through the existing projection SQL — never
    invented by hand — and then linked."""
    from app.models.unified_capability import UnifiedCapability

    org = tenant_with_entities("seed-unified-unprojected")

    # Strip the unified rows the model listeners wrote, leaving the business
    # capabilities behind — the pre-projection state production actually had.
    with tenant_ctx(org.id):
        db_session.query(UnifiedCapability).filter(
            UnifiedCapability.organization_id == org.id,
            UnifiedCapability.source_table == "business_capability",
        ).delete()

    stats = seed_demo_mappings(org.id)

    assert stats["unified_app_capability_created"] > 0
    assert _unified_mapping_count(db_session, org.id) == _resolvable_pairs(), (
        "capabilities with no unified row were not projected before linking"
    )
    projected = (
        db_session.query(UnifiedCapability)
        .filter(
            UnifiedCapability.organization_id == org.id,
            UnifiedCapability.source_table == "business_capability",
        )
        .count()
    )
    assert projected == 2, "the seed should have re-projected the org's capabilities"


def test_dry_run_on_unprojected_org_previews_work_without_writing(
    db_session, tenant_with_entities, tenant_ctx
):
    """Even for an organisation whose capabilities have no unified rows yet,
    a dry run previews the unified work that a real run would do — and writes
    nothing, not even the projection."""
    from app.models.unified_capability import UnifiedCapability

    org = tenant_with_entities("seed-unified-unprojected-dry")

    with tenant_ctx(org.id):
        db_session.query(UnifiedCapability).filter(
            UnifiedCapability.organization_id == org.id,
            UnifiedCapability.source_table == "business_capability",
        ).delete()

    stats = seed_demo_mappings(org.id, dry_run=True)

    assert stats["unified_app_capability_created"] > 0, (
        "dry run hid the unified work an unprojected org needs"
    )
    assert _unified_mapping_count(db_session, org.id) == 0, "dry run wrote unified rows"
    assert _mapping_count(db_session, org.id) == 0, "dry run wrote legacy rows"

    applied = seed_demo_mappings(org.id)
    assert applied["unified_app_capability_created"] == stats["unified_app_capability_created"], (
        "the dry run's preview did not match what the real run did"
    )
