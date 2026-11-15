"""Duplicate detection and its clean-up refuse to run with no organisation,
and delete nothing of any organisation when they do."""
from __future__ import annotations

from flask import g
from sqlalchemy import text

from tests.test_detection_consolidation_isolation import _world


def _counts(db_session, run_ids, entry_ids):
    runs = db_session.execute(
        text("SELECT count(*) FROM unified_detection_runs WHERE id = ANY(:i)"), {"i": list(run_ids)}
    ).scalar()
    entries = db_session.execute(
        text("SELECT count(*) FROM consolidation_list_entries WHERE id = ANY(:i)"), {"i": list(entry_ids)}
    ).scalar()
    return runs, entries


def _no_organisation(app):
    ctx = app.test_request_context("/")
    ctx.push()
    g.current_org_id = None
    return ctx


def test_detection_with_no_organisation_returns_an_error_and_deletes_nothing(app, db_session, make_org):
    _ua, _ub, run_a, run_b, entry_a, entry_b = _world(db_session, make_org)
    before = _counts(db_session, (run_a, run_b), (entry_a, entry_b))
    assert before == (2, 2)
    ctx = _no_organisation(app)
    try:
        from app.modules.duplicate_detection.services.unified_duplicate_service import UnifiedDuplicateService

        result = UnifiedDuplicateService.run_detection(strategy="fast")
    finally:
        ctx.pop()
    assert result["success"] is False
    assert "organisation" in result["error"]
    assert _counts(db_session, (run_a, run_b), (entry_a, entry_b)) == before


def test_cleanup_with_no_organisation_returns_an_error_and_deletes_nothing(app, db_session, make_org):
    _ua, _ub, run_a, run_b, entry_a, entry_b = _world(db_session, make_org)
    before = _counts(db_session, (run_a, run_b), (entry_a, entry_b))
    ctx = _no_organisation(app)
    try:
        from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
            UnifiedDuplicateDetectionService,
        )

        result = UnifiedDuplicateDetectionService.cleanup_stale_data()
    finally:
        ctx.pop()
    assert result["success"] is False
    assert "organisation" in result["error"]
    assert _counts(db_session, (run_a, run_b), (entry_a, entry_b)) == before


def test_ai_detection_with_no_organisation_returns_an_error_and_writes_nothing(app, db_session, make_org):
    _ua, _ub, run_a, run_b, entry_a, entry_b = _world(db_session, make_org)
    before = _counts(db_session, (run_a, run_b), (entry_a, entry_b))
    total_runs = db_session.execute(text("SELECT count(*) FROM unified_detection_runs")).scalar()
    ctx = _no_organisation(app)
    try:
        from app.modules.duplicate_detection.services.ai_duplicate_detection_service import (
            AIDuplicateDetectionService,
        )

        result = AIDuplicateDetectionService().detect_duplicates()
    finally:
        ctx.pop()
    assert result["success"] is False
    assert "needs an organisation" in result["error"]
    assert db_session.execute(text("SELECT count(*) FROM unified_detection_runs")).scalar() == total_runs
    assert _counts(db_session, (run_a, run_b), (entry_a, entry_b)) == before
