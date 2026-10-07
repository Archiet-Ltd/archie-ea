"""The Ask page's read-only account of worked-out connections, for one tenant.

Recent runs newest first, current and out-of-date counts, and how many rows each
stale reason accounts for -- read from the run records and the derived-fact
store, scoped to the one organisation asked about.
"""

import datetime as dt
import itertools

_TARGETS = itertools.count(1000)


def _derived(db_session, org, *, stale, reason=None):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    row = DerivedRelationship(
        organization_id=org.id, source_element_id=1, target_element_id=next(_TARGETS),
        derived_type="Serving", rule_id="table:serving:serving", chain=[1, 2],
        chain_element_ids=[1, 3, 2], depth=2, confidence=1.0, provenance="derivation",
        engine_version="1.0.0", computed_at=dt.datetime.utcnow(), stale=stale,
        stale_since=dt.datetime.utcnow() if stale else None, stale_reason=reason,
    )
    db_session.add(row)
    return row


def _run(db_session, org, minutes_ago, derived):
    from app.modules.intelligence.models.derivation_run import DerivationRun

    db_session.add(DerivationRun(
        organization_id=org.id, trigger="on_demand", explicit_count=2, derived_count=derived,
        duration_ms=12, finished_at=dt.datetime.utcnow() - dt.timedelta(minutes=minutes_ago),
    ))


def test_never_run_reads_as_no_runs_not_zero(app, db_session, make_org):
    from app.modules.intelligence.services.derived_facts import derivation_status

    org = make_org("derivation-never")
    status = derivation_status(org.id)
    assert status["runs"] == [] and status["last_run"] is None
    assert status["stale_reasons"] == []


def test_runs_counts_and_stale_reasons_for_one_tenant(app, db_session, make_org):
    from app.modules.intelligence.services.derived_facts import derivation_status

    org = make_org("derivation-status")
    other = make_org("derivation-other")
    _run(db_session, org, 30, 1)
    _run(db_session, org, 5, 2)
    _run(db_session, other, 1, 9)
    _derived(db_session, org, stale=False)
    _derived(db_session, org, stale=True, reason="relationship_updated")
    _derived(db_session, org, stale=True, reason="relationship_updated")
    _derived(db_session, org, stale=True, reason="element_deleted")
    _derived(db_session, other, stale=True, reason="relationship_deleted")
    db_session.flush()

    status = derivation_status(org.id)
    assert [r.derived_count for r in status["runs"]] == [2, 1]
    assert status["last_run"].derived_count == 2
    assert (status["current_count"], status["stale_count"]) == (1, 3)
    assert status["stale_reasons"] == [("element_deleted", 1), ("relationship_updated", 2)]
