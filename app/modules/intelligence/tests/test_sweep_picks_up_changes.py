"""The scheduled sweep brings an organisation up to date when it was never worked out,
or when relationships were added or changed after its last run (an import, a new link)."""

from __future__ import annotations

import os

from app.extensions import db

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "tests", "fixtures", "oef", "archiet_shaped.xml"
)


def _chain(db_session, org_id, tag):
    from app.models import ArchiMateElement, ArchiMateRelationship

    nodes = []
    for name in ("a", "b", "c"):
        e = ArchiMateElement(name=f"{tag}-{name}", type="ApplicationComponent", layer="application", organization_id=org_id)
        db_session.add(e)
        nodes.append(e)
    db_session.flush()
    for src, tgt, kind in ((nodes[0], nodes[1], "Composition"), (nodes[1], nodes[2], "Serving")):
        db_session.add(ArchiMateRelationship(source_id=src.id, target_id=tgt.id, type=kind, organization_id=org_id))
    db_session.flush()
    return nodes


def _derived(org_id):
    from app.modules.intelligence.services.derived_facts import list_derived_facts

    return list_derived_facts(org_id, include_stale=True)


def _runs(org_id):
    from app.modules.intelligence.models.derivation_run import DerivationRun

    return db.session.execute(
        db.select(db.func.count(DerivationRun.id)).where(DerivationRun.organization_id == org_id)
    ).scalar_one()


def _ran_an_hour_ago(org_id):
    """The fixture holds one outer transaction, which pins a relationship's stamp to the
    start of the test; in service each write is its own, later, transaction. Moving the
    run back an hour stands in for "the model changed after that run"."""
    db.session.execute(
        db.text(
            "UPDATE intelligence_derivation_runs SET started_at = started_at - interval '1 hour', "
            "finished_at = finished_at - interval '1 hour' WHERE organization_id = :o"
        ),
        {"o": org_id},
    )
    db.session.commit()


def _visited(run):
    return {r.organization_id for r in run.results}


def test_an_organisation_with_relationships_and_no_run_is_worked_out_by_one_sweep(app, db_session, make_org):
    from app.modules.intelligence.services.recompute_job import recompute_derived_facts

    never = make_org("sw-never")
    _chain(db_session, never.id, "n")
    db_session.commit()
    never_id = never.id
    with app.app_context():
        assert _derived(never_id) == []

    run = recompute_derived_facts(app)

    assert never_id in _visited(run)
    with app.app_context():
        rows = _derived(never_id)
        assert rows and not any(r["stale"] for r in rows)
        assert _runs(never_id) == 1


def test_a_sweep_leaves_another_organisation_that_is_up_to_date_alone(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.recompute_job import recompute_derived_facts

    never, settled = make_org("sw-iso-never"), make_org("sw-iso-settled")
    _chain(db_session, never.id, "n")
    _chain(db_session, settled.id, "s")
    db_session.commit()
    never_id, settled_id = never.id, settled.id
    with app.app_context():
        DerivationRunner().run_and_persist(settled_id, trigger="on_demand")
        before = sorted((r["id"], r["stale"]) for r in _derived(settled_id))
        runs_before = _runs(settled_id)

    run = recompute_derived_facts(app)

    assert never_id in _visited(run)
    assert settled_id not in _visited(run)
    with app.app_context():
        assert sorted((r["id"], r["stale"]) for r in _derived(settled_id)) == before
        assert _runs(settled_id) == runs_before


def test_an_import_into_a_worked_out_organisation_is_picked_up_by_the_next_sweep(app, db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.recompute_job import (
        organization_ids_needing_recompute,
        recompute_derived_facts,
    )
    from app.services.archimate_import_service import ArchiMateImportService

    org = make_org("sw-import")
    _chain(db_session, org.id, "i")
    db_session.commit()
    org_id = org.id
    with app.app_context():
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")
        assert org_id not in organization_ids_needing_recompute()
        runs_before = _runs(org_id)
        _ran_an_hour_ago(org_id)

    with open(FIXTURE, encoding="utf-8") as fh:
        xml = fh.read()
    with tenant_ctx(org_id):
        service = ArchiMateImportService()
        result = service.execute_import(service.parse_oef_xml(xml), strategy="skip_duplicates")
        assert result["relationships_created"] > 0
    db.session.commit()

    with app.app_context():
        assert org_id in organization_ids_needing_recompute()

    run = recompute_derived_facts(app)

    assert org_id in _visited(run)
    with app.app_context():
        assert org_id not in organization_ids_needing_recompute()
        assert _runs(org_id) == runs_before + 1


def test_the_panel_counts_relationships_added_since_the_last_run(app, db_session, make_org):
    from app.models import ArchiMateRelationship
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import derivation_status

    org = make_org("sw-count")
    a, b, c = _chain(db_session, org.id, "c")
    db_session.commit()
    org_id = org.id
    with app.app_context():
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")
        status = derivation_status(org_id)
        assert status["changed_since_count"] == 0

    from app.models import ArchiMateElement

    with app.app_context():
        from app.jobs.tenant_safe_job import tenant_scope

        with tenant_scope(org_id):
            ids = [e.id for e in ArchiMateElement.query.filter_by(organization_id=org_id).all()]
            for kind in ("Flow", "Triggering", "Association"):
                db.session.add(ArchiMateRelationship(source_id=ids[0], target_id=ids[2], type=kind, organization_id=org_id))
            # As in service, each addition is stamped when it is written, after the run.
            db.session.execute(
                db.text(
                    "UPDATE archimate_relationships SET created_at = clock_timestamp(), "
                    "updated_at = clock_timestamp() WHERE organization_id = :o "
                    "AND type IN ('Flow', 'Triggering', 'Association')"
                ),
                {"o": org_id},
            )
            db.session.commit()
            status = derivation_status(org_id)
    assert status["changed_since_count"] == 3
    assert status["stale_count"] == 0


def test_a_session_in_another_time_zone_does_not_change_what_counts_as_changed(app, db_session, make_org):
    """Relationship stamps hold the session's local time and runs hold UTC; the two are
    compared in one base, so a London or Auckland session sees the same answer as UTC."""
    from app.models import ArchiMateRelationship
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import derivation_status
    from app.modules.intelligence.services.recompute_job import changed_since_last_run_organization_ids

    org = make_org("sw-tz")
    _chain(db_session, org.id, "t")
    db_session.commit()
    org_id = org.id
    for zone in ("UTC", "Europe/London", "Pacific/Auckland", "America/Los_Angeles"):
        with app.app_context():
            db.session.execute(db.text("SET TIME ZONE '%s'" % zone))
            db.session.execute(db.text("DELETE FROM intelligence_derivation_runs WHERE organization_id = :o"), {"o": org_id})
            # Stamp the relationships as this session would have written them, an hour ago.
            db.session.execute(
                db.text(
                    "UPDATE archimate_relationships SET created_at = now() - interval '1 hour', "
                    "updated_at = now() - interval '1 hour' WHERE organization_id = :o"
                ),
                {"o": org_id},
            )
            db.session.commit()
            DerivationRunner().run_and_persist(org_id, trigger="on_demand")
            # Written before the run, in the session's own time: nothing has changed since.
            assert derivation_status(org_id)["changed_since_count"] == 0, zone
            assert org_id not in changed_since_last_run_organization_ids(), zone
            # Written after the run: it is counted, whatever the zone.
            db.session.execute(
                db.text(
                    "UPDATE archimate_relationships SET updated_at = clock_timestamp() "
                    "WHERE organization_id = :o AND type = 'Serving'"
                ),
                {"o": org_id},
            )
            db.session.commit()
            assert derivation_status(org_id)["changed_since_count"] == 1, zone
            assert org_id in changed_since_last_run_organization_ids(), zone
            db.session.execute(db.text("RESET TIME ZONE"))
            db.session.commit()
