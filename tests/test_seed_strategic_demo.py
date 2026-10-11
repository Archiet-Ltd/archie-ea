"""The T-S1 demonstration data set seeder: tenancy and idempotency.

`flask seed-strategic-demo` exists because no existing seeder can create a
value stream -- `seed-demo-mappings` maps entities that already exist and is
pinned never to create one; the capability seeders create no value stream
and set no per-tenant maturity. Without it, T-S1's answer is correct on day
one but has nothing to show a reviewer.

Two safety properties matter, mirroring `tests/test_seed_demo_mappings.py`'s
own two: it stays inside the organisation it is given, and re-running it
creates nothing new and changes nothing.

Every id used after a `seed_strategic_demo(...)` call below is a plain int
captured *before* that call, never an attribute read off the original ORM
object afterwards. The command runs inside `tenant_scope()` (required by the
brief), whose `_reset_session()` calls `db.session.remove()` on entry and
exit -- by design for a CLI/job's own session lifecycle (see
`derived_facts.py`'s module docstring for the same hazard documented against
a live request). That detaches every object the `db_session` fixture had
already loaded, so touching `org.id` again afterwards raises
`DetachedInstanceError` -- a plain int captured earlier has no such problem
and every assertion below re-queries fresh rows by that int.
"""

from __future__ import annotations

from app.commands.seed_strategic_demo import seed_strategic_demo


def _counts_for_org(org_id):
    from app.models.unified_capability import (
        CapabilityValueStreamMapping,
        UnifiedCapability,
        ValueStream,
        ValueStreamStage,
    )

    value_streams = ValueStream.query.filter_by(organization_id=org_id).all()
    vs_ids = [vs.id for vs in value_streams]
    stages = (
        ValueStreamStage.query.filter(ValueStreamStage.organization_id == org_id).count()
        if vs_ids
        else 0
    )
    capabilities = UnifiedCapability.query.filter_by(organization_id=org_id).count()
    mappings = CapabilityValueStreamMapping.query.filter(
        CapabilityValueStreamMapping.organization_id == org_id
    ).count()
    return {
        "value_streams": len(value_streams),
        "stages": stages,
        "capabilities": capabilities,
        "mappings": mappings,
    }


# --- Acceptance item 15: fresh organisation -----------------------------------


def test_fresh_organisation_gets_two_streams_four_stages_six_capabilities_eight_mappings(
    db_session, make_org
):
    org = make_org("strategic-demo-fresh")
    org_id = org.id
    db_session.commit()

    stats = seed_strategic_demo(org_id)

    assert stats["value_streams_created"] == 2
    assert stats["stages_created"] == 4
    assert stats["capabilities_created"] == 6
    assert stats["mappings_created"] == 8

    assert _counts_for_org(org_id) == {
        "value_streams": 2,
        "stages": 4,
        "capabilities": 6,
        "mappings": 8,
    }


def test_seeded_capabilities_include_both_below_and_at_or_above_default_threshold(
    db_session, make_org
):
    from app.models.unified_capability import UnifiedCapability

    org = make_org("strategic-demo-spread")
    org_id = org.id
    db_session.commit()

    seed_strategic_demo(org_id)

    levels = [
        cap.current_maturity_level
        for cap in UnifiedCapability.query.filter_by(organization_id=org_id).all()
    ]
    assert any(level is not None and level < 3 for level in levels)
    assert any(level is not None and level >= 3 for level in levels)


def test_route_shows_both_an_at_risk_and_a_safe_capability_after_seeding(
    app, db_session, make_org
):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategic-demo-route")
    org_id = org.id
    db_session.commit()

    seed_strategic_demo(org_id)

    result = IntelligenceQueryService.value_streams_at_risk(org_id)
    at_risk_flags = {cap["at_risk"] for row in result["rows"] for cap in row["capabilities"]}
    assert True in at_risk_flags, "expected at least one at_risk: true capability"
    assert False in at_risk_flags, "expected at least one at_risk: false capability"


# --- Acceptance item 15: idempotency --------------------------------------------


def test_rerun_creates_nothing_new(db_session, make_org):
    org = make_org("strategic-demo-rerun")
    org_id = org.id
    db_session.commit()

    seed_strategic_demo(org_id)
    before = _counts_for_org(org_id)

    second = seed_strategic_demo(org_id)

    assert second["value_streams_created"] == 0
    assert second["stages_created"] == 0
    assert second["capabilities_created"] == 0
    assert second["mappings_created"] == 0
    assert _counts_for_org(org_id) == before


def test_rerun_does_not_change_mapping_field_values(db_session, make_org):
    """`upsert_mapping_cell` bumps `updated_at` on every call; re-seeding must
    not even call it a second time for an unchanged row (constraint: a
    re-run changes nothing, not just "creates nothing")."""
    from app.models.unified_capability import CapabilityValueStreamMapping

    org = make_org("strategic-demo-rerun-fields")
    org_id = org.id
    db_session.commit()

    seed_strategic_demo(org_id)
    mappings_before = {
        m.id: m.updated_at
        for m in CapabilityValueStreamMapping.query.filter_by(organization_id=org_id).all()
    }

    seed_strategic_demo(org_id)
    mappings_after = {
        m.id: m.updated_at
        for m in CapabilityValueStreamMapping.query.filter_by(organization_id=org_id).all()
    }

    assert mappings_after == mappings_before


# --- Acceptance item 15: --dry-run ----------------------------------------------


def test_dry_run_writes_nothing_but_reports_what_it_would_do(db_session, make_org):
    org = make_org("strategic-demo-dryrun")
    org_id = org.id
    db_session.commit()

    stats = seed_strategic_demo(org_id, dry_run=True)

    assert stats["value_streams_created"] == 2
    assert stats["stages_created"] == 4
    assert stats["capabilities_created"] == 6
    assert stats["mappings_created"] == 8

    assert _counts_for_org(org_id) == {
        "value_streams": 0, "stages": 0, "capabilities": 0, "mappings": 0,
    }, "dry run wrote rows"

    applied = seed_strategic_demo(org_id)
    assert applied["value_streams_created"] == stats["value_streams_created"]
    assert applied["stages_created"] == stats["stages_created"]
    assert applied["capabilities_created"] == stats["capabilities_created"]
    assert applied["mappings_created"] == stats["mappings_created"]


# --- Acceptance item 15: stays inside the organisation it is given -------------


def test_command_writes_only_inside_the_organisation_it_is_given(db_session, make_org):
    org_a = make_org("strategic-demo-scope-a")
    org_b = make_org("strategic-demo-scope-b")
    org_a_id, org_b_id = org_a.id, org_b.id
    db_session.commit()

    seed_strategic_demo(org_a_id)

    assert _counts_for_org(org_a_id) == {
        "value_streams": 2, "stages": 4, "capabilities": 6, "mappings": 8,
    }
    assert _counts_for_org(org_b_id) == {
        "value_streams": 0, "stages": 0, "capabilities": 0, "mappings": 0,
    }, "seeding one organisation must never write into another"


def test_command_never_touches_a_null_owner_capability_row(db_session, make_org):
    """Pinned safety property: the command creates no capability it did not
    create itself and never touches a row whose organization_id is null."""
    from app.models.unified_capability import UnifiedCapability

    org = make_org("strategic-demo-null-owner")
    org_id = org.id
    shared = UnifiedCapability(
        name="Pre-existing shared capability",
        code="STRATEGIC-DEMO-SHARED-PREEXISTING",
        organization_id=None,
        scope="reference",
        level=1,
        current_maturity_level=4,
        target_maturity_level=4,
    )
    db_session.add(shared)
    db_session.flush()
    shared_id = shared.id
    db_session.commit()

    seed_strategic_demo(org_id)

    refreshed = UnifiedCapability.query.filter_by(id=shared_id).first()
    assert refreshed.organization_id is None
    assert refreshed.current_maturity_level == 4, "a null-owner row must never be modified"


def test_cli_command_runs_end_to_end(app, db_session, make_org):
    org = make_org("strategic-demo-cli")
    org_id = org.id
    db_session.commit()

    result = app.test_cli_runner().invoke(args=["seed-strategic-demo", "--org-id", str(org_id)])

    assert result.exit_code == 0, f"command failed: {result.output}\n{result.exception!r}"
    assert "created" in result.output
    assert _counts_for_org(org_id) == {
        "value_streams": 2, "stages": 4, "capabilities": 6, "mappings": 8,
    }


def test_cli_command_requires_org_id(app):
    result = app.test_cli_runner().invoke(args=["seed-strategic-demo"])
    assert result.exit_code != 0


def test_cli_command_rejects_unknown_organisation(app):
    result = app.test_cli_runner().invoke(args=["seed-strategic-demo", "--org-id", "999999999"])
    assert result.exit_code != 0


# --- --dry-run on an ALREADY-SEEDED organisation --------------------------------


def test_dry_run_on_seeded_organisation_reports_nothing_to_create(db_session, make_org):
    """The mapping section used to short-circuit on
    ``dry_run`` before the existing-mapping lookup, so a dry-run against an
    already-seeded organisation reported "would create 8 mapping rows" it
    would not actually create. Seed for real first, then dry-run the SAME
    organisation: every created count is 0, every row is already present,
    and nothing changes.
    """
    org = make_org("strategic-demo-dryrun-seeded")
    org_id = org.id
    db_session.commit()

    seed_strategic_demo(org_id)
    before = _counts_for_org(org_id)

    stats = seed_strategic_demo(org_id, dry_run=True)

    assert stats["value_streams_created"] == 0
    assert stats["stages_created"] == 0
    assert stats["capabilities_created"] == 0
    assert stats["mappings_created"] == 0
    assert stats["already_present"] == 20
    assert _counts_for_org(org_id) == before, "dry run on a seeded organisation changed rows"


# --- Two organisations seeded on one database -----------------------------------


def test_two_organisations_seed_independently_on_one_database(db_session, make_org):
    """The seed looks up by ``(organization_id, code)``, so
    on a database whose per-organisation unique index was never applied a
    second organisation's seed still creates its own rows with the same
    codes -- intended, and untested until now. Each organisation ends up
    with exactly its own 2 value streams, 6 capabilities and 8 mappings, and
    each answer's capability id set is disjoint from the other's.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("strategic-demo-d18-a")
    org_b = make_org("strategic-demo-d18-b")
    org_a_id, org_b_id = org_a.id, org_b.id
    db_session.commit()

    seed_strategic_demo(org_a_id)
    seed_strategic_demo(org_b_id)

    assert _counts_for_org(org_a_id) == {
        "value_streams": 2, "stages": 4, "capabilities": 6, "mappings": 8,
    }
    assert _counts_for_org(org_b_id) == {
        "value_streams": 2, "stages": 4, "capabilities": 6, "mappings": 8,
    }

    result_a = IntelligenceQueryService.value_streams_at_risk(org_a_id)
    result_b = IntelligenceQueryService.value_streams_at_risk(org_b_id)
    assert result_a["summary"]["capabilities_considered"] == 6
    assert result_b["summary"]["capabilities_considered"] == 6

    ids_a = {c["id"] for row in result_a["rows"] for c in row["capabilities"]}
    ids_b = {c["id"] for row in result_b["rows"] for c in row["capabilities"]}
    assert ids_a.isdisjoint(ids_b)
