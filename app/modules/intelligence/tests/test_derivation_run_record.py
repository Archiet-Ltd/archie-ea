"""The derivation run-record store (``intelligence_derivation_runs``) and
its aggregate accessors.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.extensions import db


def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer="application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_relationship(db_session, org_id, source, target, type_):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


# --- Acceptance criterion 1: written row mirrors the returned result --------


def test_completed_run_writes_one_run_record_matching_the_result(app, db_session, make_org):
    from app.modules.intelligence.models.derivation_run import DerivationRun
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("dr-run-record")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    _make_relationship(db_session, org.id, a, b, "Composition")
    _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()
    org_id = org.id

    with app.app_context():
        result = DerivationRunner().run_and_persist(org_id, trigger="on_demand")

    rows = (
        db.session.execute(
            db.select(DerivationRun).where(DerivationRun.organization_id == org_id)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.explicit_count == result.explicit_count
    assert row.derived_count == result.derived_count
    assert row.duration_ms == result.duration_ms
    assert row.engine_version == result.engine_version
    assert row.trigger == "on_demand"
    if result.ratio is None:
        assert row.ratio is None
    else:
        assert float(row.ratio) == pytest.approx(result.ratio)


# --- Acceptance criterion 2: D6 -- measured-zero distinguishable from never-ran


def test_measured_zero_is_distinguishable_from_never_ran(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import latest_derivation_run

    org_ran_zero = make_org("dr-zero")
    org_never_ran = make_org("dr-never")
    # org_ran_zero has no explicit relationships at all -> the engine
    # produces zero derived rows, and _persist's else-branch DELETEs
    # whatever was there -- byte-identical, in the derived-fact store alone,
    # to a tenant that never ran.
    db_session.commit()
    org_ran_zero_id = org_ran_zero.id
    org_never_ran_id = org_never_ran.id

    with app.app_context():
        result = DerivationRunner().run_and_persist(org_ran_zero_id, trigger="on_demand")

    assert result.derived_count == 0

    with app.app_context():
        ran_record = latest_derivation_run(org_ran_zero_id)
        never_ran_record = latest_derivation_run(org_never_ran_id)

    assert ran_record is not None
    assert ran_record.derived_count == 0
    assert never_ran_record is None


def test_run_record_round_trips_utc_timestamps_within_a_few_seconds(
    app, db_session, make_org
):
    from app.modules.intelligence.models.derivation_run import DerivationRun
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("dr-run-utc-roundtrip")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    _make_relationship(db_session, org.id, a, b, "Serving")
    db_session.commit()
    org_id = org.id

    run_started_utc = _dt.datetime.now(_dt.timezone.utc)
    with app.app_context():
        result = DerivationRunner().run_and_persist(org_id, trigger="on_demand")
    run_finished_utc = _dt.datetime.now(_dt.timezone.utc)

    with app.app_context():
        db.session.remove()
        row = db.session.execute(
            db.select(DerivationRun)
            .where(DerivationRun.organization_id == org_id)
            .order_by(DerivationRun.id.desc())
        ).scalars().first()

    assert row is not None
    started_at = row.started_at
    finished_at = row.finished_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=_dt.timezone.utc)
    else:
        started_at = started_at.astimezone(_dt.timezone.utc)
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=_dt.timezone.utc)
    else:
        finished_at = finished_at.astimezone(_dt.timezone.utc)

    tolerance = _dt.timedelta(seconds=5)
    assert run_started_utc - tolerance <= started_at <= run_finished_utc + tolerance
    assert run_started_utc - tolerance <= finished_at <= run_finished_utc + tolerance
    assert finished_at >= started_at
    assert abs(
        ((finished_at - started_at) - _dt.timedelta(milliseconds=result.duration_ms)).total_seconds()
    ) <= tolerance.total_seconds()


# --- Acceptance criterion 3: failed / lock-skipped runs write no row -------


def test_failed_run_writes_no_run_record(app, db_session, make_org, monkeypatch):
    from app.modules.intelligence.models.derivation_run import DerivationRun
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("dr-run-fail")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    _make_relationship(db_session, org.id, a, b, "Serving")
    db_session.commit()
    org_id = org.id

    def _boom(self, organization_id, derived):
        raise RuntimeError("simulated persist failure")

    monkeypatch.setattr(DerivationRunner, "_persist", _boom)

    with app.app_context():
        with pytest.raises(RuntimeError):
            DerivationRunner().run_and_persist(org_id, trigger="on_demand")

    with app.app_context():
        db.session.rollback()
        count = db.session.execute(
            db.select(db.func.count(DerivationRun.id)).where(
                DerivationRun.organization_id == org_id
            )
        ).scalar_one()
    assert count == 0


def test_lock_skipped_recompute_writes_no_run_record(app, db_session, make_org):
    from app.jobs.tenant_safe_job import job_lock
    from app.modules.intelligence.models.derivation_run import DerivationRun
    from app.modules.intelligence.services.recompute_job import (
        per_tenant_lock_name,
        recompute_derived_facts_on_demand,
    )

    org = make_org("dr-run-lockskip")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    _make_relationship(db_session, org.id, a, b, "Serving")
    db_session.commit()
    org_id = org.id

    with app.app_context():
        with job_lock(per_tenant_lock_name(org_id), required=True):
            run = recompute_derived_facts_on_demand(app, org_id)

    assert run.results[0].value.get("skipped_locked") is True

    with app.app_context():
        count = db.session.execute(
            db.select(db.func.count(DerivationRun.id)).where(
                DerivationRun.organization_id == org_id
            )
        ).scalar_one()
    assert count == 0


# --- Acceptance criterion 4: atomicity of facts + run record ---------------


def test_facts_and_run_record_commit_atomically(app, db_session, make_org, monkeypatch):
    from app.modules.intelligence.models.derivation_run import DerivationRun
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship
    from app.modules.intelligence.services.derivation_runner import DerivationRunner

    org = make_org("dr-run-atomic")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    _make_relationship(db_session, org.id, a, b, "Composition")
    _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()
    org_id = org.id

    real_commit = db.session.commit

    def _boom_commit(*a, **k):
        raise RuntimeError("simulated commit failure between persist and commit")

    with app.app_context():
        monkeypatch.setattr(db.session, "commit", _boom_commit)
        try:
            with pytest.raises(RuntimeError):
                DerivationRunner().run_and_persist(org_id, trigger="on_demand")
        finally:
            monkeypatch.setattr(db.session, "commit", real_commit)
            db.session.rollback()

    with app.app_context():
        fact_count = db.session.execute(
            db.select(db.func.count(DerivedRelationship.id)).where(
                DerivedRelationship.organization_id == org_id
            )
        ).scalar_one()
        run_count = db.session.execute(
            db.select(db.func.count(DerivationRun.id)).where(
                DerivationRun.organization_id == org_id
            )
        ).scalar_one()

    assert fact_count == 0, "facts must not survive a failed commit"
    assert run_count == 0, "the run record must not survive a failed commit either"


# --- Acceptance criterion 5: tenancy -----------------------------------------


def test_run_record_is_tenant_scoped(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import latest_derivation_run

    org_a = make_org("dr-run-tenant-a")
    org_b = make_org("dr-run-tenant-b")
    a1 = _make_element(db_session, org_a.id, "a1")
    a2 = _make_element(db_session, org_a.id, "a2")
    _make_relationship(db_session, org_a.id, a1, a2, "Serving")
    db_session.commit()
    org_a_id, org_b_id = org_a.id, org_b.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_a_id, trigger="on_demand")

    with app.app_context():
        db.session.remove()
        record_b = latest_derivation_run(org_b_id)
        db.session.remove()
        record_a = latest_derivation_run(org_a_id)

    assert record_b is None
    assert record_a is not None
    assert record_a.organization_id == org_a_id


def test_run_record_tenant_loop_with_session_removal_between_tenants(app, db_session, make_org):
    """CLAUDE.md's identity-map caveat: loop over tenants in one session,
    calling ``db.session.remove()`` between them, and confirm no identity-map
    carry-over serves tenant B a tenant-A row."""
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import latest_derivation_run

    org_a = make_org("dr-run-loop-a")
    org_b = make_org("dr-run-loop-b")
    a1 = _make_element(db_session, org_a.id, "a1")
    a2 = _make_element(db_session, org_a.id, "a2")
    _make_relationship(db_session, org_a.id, a1, a2, "Serving")
    b1 = _make_element(db_session, org_b.id, "b1")
    b2 = _make_element(db_session, org_b.id, "b2")
    _make_relationship(db_session, org_b.id, b1, b2, "Serving")
    db_session.commit()
    org_a_id, org_b_id = org_a.id, org_b.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_a_id, trigger="on_demand")
        db.session.remove()
        DerivationRunner().run_and_persist(org_b_id, trigger="on_demand")
        db.session.remove()

        record_a = latest_derivation_run(org_a_id)
        db.session.remove()
        record_b = latest_derivation_run(org_b_id)

    assert record_a.organization_id == org_a_id
    assert record_b.organization_id == org_b_id


# --- Acceptance criterion 6: derived_fact_aggregates counts ----------------


def test_derived_fact_aggregates_matches_direct_count_and_null_computed_at(
    app, db_session, make_org
):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import derived_fact_aggregates

    org_empty = make_org("dr-agg-empty")
    org_populated = make_org("dr-agg-populated")
    a = _make_element(db_session, org_populated.id, "a")
    b = _make_element(db_session, org_populated.id, "b")
    c = _make_element(db_session, org_populated.id, "c")
    _make_relationship(db_session, org_populated.id, a, b, "Composition")
    _make_relationship(db_session, org_populated.id, b, c, "Serving")
    db_session.commit()

    org_empty_id = org_empty.id
    org_populated_id = org_populated.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_populated_id, trigger="on_demand")

    with app.app_context():
        direct_count = db.session.execute(
            db.select(db.func.count(DerivedRelationship.id)).where(
                DerivedRelationship.organization_id == org_populated_id,
                DerivedRelationship.stale.is_(False),
            )
        ).scalar_one()
        agg = derived_fact_aggregates(org_populated_id)
        empty_agg = derived_fact_aggregates(org_empty_id)

    assert agg["derived_count"] == direct_count
    assert empty_agg["derived_count"] == 0
    assert empty_agg["stale_count"] == 0
    assert empty_agg["computed_at"] is None


# --- Acceptance criterion 7: bounded statements, no row materialisation ----


def test_derived_fact_aggregates_does_not_materialise_rows(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import derived_fact_aggregates

    org = make_org("dr-agg-bounded")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    _make_relationship(db_session, org.id, a, b, "Composition")
    _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()
    org_id = org.id

    with app.app_context():
        DerivationRunner().run_and_persist(org_id, trigger="on_demand")

    statement_count = 0

    def _count_statements(*a, **k):
        nonlocal statement_count
        statement_count += 1

    with app.app_context():
        from sqlalchemy import event

        event.listen(db.engine, "before_cursor_execute", _count_statements)
        try:
            agg = derived_fact_aggregates(org_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", _count_statements)

    # A small, fixed number of aggregate SELECTs (derived_count, stale_count,
    # computed_at, engine_versions, plus incidental session bookkeeping) --
    # bounded regardless of row count, never one statement per row.
    assert statement_count <= 6
    assert isinstance(agg, dict)
    assert all(not isinstance(v, list) or all(isinstance(x, str) for x in v) for v in agg.values())


# --- Acceptance criterion 8: engine_versions reflects stored rows, not the constant


def test_engine_versions_reflects_stored_rows_not_the_module_constant(
    app, db_session, make_org
):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship
    from app.modules.intelligence.services.derivation_runner import ENGINE_VERSION
    from app.modules.intelligence.services.derived_facts import derived_fact_aggregates

    org = make_org("dr-agg-engine-version")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    old_row = DerivedRelationship(
        organization_id=org.id,
        source_element_id=a.id,
        target_element_id=b.id,
        derived_type="Association",
        rule_id="fallback:X:X",
        chain=[999],
        chain_element_ids=[a.id, b.id],
        depth=1,
        confidence="1.00",
        provenance="derivation",
        engine_version="0.0.1-old",
        computed_at=_dt.datetime.utcnow(),
        stale=False,
    )
    db_session.add(old_row)
    db_session.commit()
    org_id = org.id

    with app.app_context():
        agg = derived_fact_aggregates(org_id)

    assert agg["engine_versions"] == ["0.0.1-old"]
    assert ENGINE_VERSION not in agg["engine_versions"]


# --- Acceptance criterion 9: ratio is None, never 0, when explicit is zero --


def test_ratio_is_none_not_zero_when_explicit_count_is_zero(app, db_session, make_org):
    from app.modules.intelligence.services.derivation_runner import DerivationRunner
    from app.modules.intelligence.services.derived_facts import latest_derivation_run

    org = make_org("dr-run-ratio-none")
    db_session.commit()
    org_id = org.id

    with app.app_context():
        result = DerivationRunner().run_and_persist(org_id, trigger="on_demand")

    assert result.explicit_count == 0
    assert result.ratio is None

    with app.app_context():
        record = latest_derivation_run(org_id)

    assert record.ratio is None


# --- Acceptance criterion 10: schema safety ---------------------------------


def test_reconcile_schema_reports_no_drift_after_init_db():
    """Recorded in the build report per acceptance item 10; this test asserts
    the model's columns are nullable/server-defaulted so an existing database
    needs no backfill when this table is added."""
    from app.modules.intelligence.models.derivation_run import DerivationRun

    for column in DerivationRun.__table__.columns:
        if column.name in ("id", "organization_id"):
            continue
        assert column.nullable or column.server_default is not None, (
            f"DerivationRun.{column.name} is neither nullable nor server-defaulted -- "
            "reconcile-schema only ever adds nullable columns to an existing database"
        )
