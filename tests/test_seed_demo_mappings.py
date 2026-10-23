"""The demo mapping seeder must stay inside the organisation it is given.

This seeder exists to make a demonstration tenant navigable: entities alone
(applications, capabilities, value streams) render as disconnected lists, and
every coverage view reads empty. It writes *sample* relationships, so the two
properties that matter are that it never escapes the organisation it was given,
and that it never invents an entity to hang a relationship on — an unknown name
is skipped and reported, not created.
"""

import pytest
from sqlalchemy import text

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


# ---------------------------------------------------------------------------
# Unified rows. The Investment Map and the capability screens read
# `UnifiedApplicationCapabilityMapping`, so the seeder writes those rows beside
# the old ones, and projects a capability with no unified row through the one
# canonical projection rather than a copy of it.
# ---------------------------------------------------------------------------


def _unified_mapping_count(db_session, org_id):
    """Unified mappings of org_id, reached through the mapped application (the
    table has no organisation column)."""
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
    """Pairs the fixture organisation's two applications and two capabilities resolve."""
    from app.commands.seed_demo_mappings import APP_CAPABILITIES

    apps = {"Blue Yonder WMS", "Tadley Despatch Tool"}
    caps = {"Warehouse Management", "Transport & Distribution"}
    return sum(
        1
        for app_name, names in APP_CAPABILITIES.items()
        if app_name in apps
        for name in names
        if name in caps
    )


def _strip_unified_capabilities(db_session, tenant_ctx, org_id):
    """Leave the business capabilities, drop their unified rows: the state of an
    organisation seeded before the projection existed."""
    from app.models.unified_capability import UnifiedCapability

    with tenant_ctx(org_id):
        db_session.query(UnifiedCapability).filter(
            UnifiedCapability.organization_id == org_id,
            UnifiedCapability.source_table == "business_capability",
        ).delete()
        db_session.commit()


def test_seed_writes_one_unified_row_per_resolvable_pair(db_session, tenant_with_entities):
    org = tenant_with_entities("seed-unified")

    stats = seed_demo_mappings(org.id)

    assert _unified_mapping_count(db_session, org.id) == _resolvable_pairs() > 0
    assert stats["unified_app_capability_created"] == _resolvable_pairs()
    assert _mapping_count(db_session, org.id) == _resolvable_pairs()


def test_unified_rows_carry_plain_words_not_a_command_name(db_session, tenant_with_entities):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    org = tenant_with_entities("seed-notes")
    seed_demo_mappings(org.id)

    notes = {
        row.notes
        for row in db_session.query(UnifiedApplicationCapabilityMapping)
        .join(
            ApplicationComponent,
            UnifiedApplicationCapabilityMapping.application_component_id
            == ApplicationComponent.id,
        )
        .filter(ApplicationComponent.organization_id == org.id)
    }
    assert notes, "no unified rows to read the notes of"
    for note in notes:
        assert note and "seed" not in note.lower() and "--" not in note and "flask" not in note.lower()


def test_second_run_and_dry_run_write_no_unified_rows(db_session, tenant_with_entities):
    org = tenant_with_entities("seed-unified-idem")

    preview = seed_demo_mappings(org.id, dry_run=True)
    assert _unified_mapping_count(db_session, org.id) == 0
    assert _mapping_count(db_session, org.id) == 0
    assert preview["unified_app_capability_created"] == _resolvable_pairs()

    seed_demo_mappings(org.id)
    second = seed_demo_mappings(org.id)

    assert second["app_capability_created"] == 0
    assert second["unified_app_capability_created"] == 0
    assert _unified_mapping_count(db_session, org.id) == _resolvable_pairs()


def test_capabilities_without_a_unified_row_go_through_the_canonical_projection(
    db_session, tenant_with_entities, tenant_ctx, monkeypatch
):
    from app.models import business_capabilities

    org = tenant_with_entities("seed-canonical")
    other = tenant_with_entities("seed-canonical-other")
    _strip_unified_capabilities(db_session, tenant_ctx, org.id)

    real = business_capabilities._project_capability_row
    projected = []

    def spy(connection, target):
        projected.append((target.id, target.organization_id))
        return real(connection, target)

    monkeypatch.setattr(business_capabilities, "_project_capability_row", spy)

    seed_demo_mappings(org.id)

    assert len(projected) == 2, "each unprojected capability goes through the one projection"
    assert {org_id for _id, org_id in projected} == {org.id}, (
        "the projection was called for a capability of another organisation"
    )
    assert _unified_mapping_count(db_session, org.id) == _resolvable_pairs()
    assert _unified_mapping_count(db_session, other.id) == 0


def test_seed_never_overwrites_another_organisations_capability_row(
    db_session, tenant_with_entities, tenant_ctx
):
    """A unified row that points at one of this organisation's capabilities but
    belongs to another organisation must be left exactly as it is: the canonical
    projection refuses a moved row instead of overwriting it, and the pair still
    gets its old mapping row."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    org = tenant_with_entities("seed-moved")
    other = tenant_with_entities("seed-moved-other")

    with tenant_ctx(org.id):
        bc = BusinessCapability.query.filter_by(
            organization_id=org.id, name="Warehouse Management"
        ).one()
        bc_id = bc.id
        row = UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(bc_id)
        ).one()
        row_id = row.id
    db_session.execute(
        text(
            "UPDATE unified_capabilities SET organization_id = :o, name = 'Belongs to the other organisation' "
            "WHERE id = :i"
        ),
        {"o": other.id, "i": row_id},
    )
    db_session.commit()

    stats = seed_demo_mappings(org.id)

    after = db_session.execute(
        text(
            "SELECT organization_id, name FROM unified_capabilities WHERE id = :i"
        ),
        {"i": row_id},
    ).one()
    assert after.organization_id == other.id
    assert after.name == "Belongs to the other organisation"
    # The old mapping row is still written for the pairs that use this capability.
    assert stats["app_capability_created"] == _resolvable_pairs()
    assert _mapping_count(db_session, org.id) == _resolvable_pairs()
    # And no unified mapping points at the other organisation's row.
    assert (
        db_session.execute(
            text(
                "SELECT count(*) FROM unified_application_capability_mapping "
                "WHERE unified_capability_id = :i"
            ),
            {"i": row_id},
        ).scalar()
        == 0
    )


def _user(db_session, org, label):
    import uuid

    from app.models.user import User

    user = User(
        email=f"seed-map-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Seed",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_investment_map_readiness_counts_only_the_callers_organisation(
    db_session, tenant_with_entities, client, login_as, tenant_ctx
):
    """Organisation A is seeded and sees its matrix; organisation B, which holds
    capabilities of the same names but no mappings, still gets Setup Required
    and none of A's rows."""
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        organisation_capability_mappings,
    )

    org_a = tenant_with_entities("seed-invest-a")
    org_b = tenant_with_entities("seed-invest-b")
    with tenant_ctx(org_a.id):
        seed_demo_mappings(org_a.id)
    user_a = _user(db_session, org_a, "a")
    user_b = _user(db_session, org_b, "b")
    db_session.commit()

    with tenant_ctx(org_a.id):
        assert organisation_capability_mappings(org_a.id).count() == _resolvable_pairs()
    with tenant_ctx(org_b.id):
        assert organisation_capability_mappings(org_b.id).count() == 0

    login_as(client, user_a)
    page_a = client.get("/architecture/investment-priorities")
    assert page_a.status_code == 200
    assert "Setup Required" not in page_a.get_data(as_text=True)

    login_as(client, user_b)
    page_b = client.get("/architecture/investment-priorities")
    assert page_b.status_code == 200
    html_b = page_b.get_data(as_text=True)
    assert "Setup Required" in html_b
    assert "Warehouse Management" not in html_b
