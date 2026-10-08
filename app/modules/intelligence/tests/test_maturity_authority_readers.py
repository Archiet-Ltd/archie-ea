"""T-002 tasks 02-04: reader migration off the four duplicate maturity columns.

Grep-level assertions per the brief's AC-8 (and each task's own AC-1): a
current-value reader of the superseded columns must not exist outside the
projection, the defining model file itself, and the accessor.
CapabilityMaturityAssessment's historical/trend reads are excluded by name.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
APP_DIR = REPO_ROOT / "app"

# Files that are allowed to read the superseded attribute names because they
# either define them, are the projection itself, or are the historical
# CapabilityMaturityAssessment trail (excluded by name, per the brief).
ALLOWED_FILES = {
    "app/models/capabilities.py",
    "app/models/capability_models.py",
    "app/models/capability_gap_analysis.py",
    "app/models/business_capabilities.py",
    "app/models/unified_capability.py",
    "app/commands/project_capabilities.py",
    # IndustryProcessRecommendation.current_maturity is an unrelated column on
    # an unrelated model (industry process benchmarking), not one of T-002's
    # four duplicate maturity definitions — named exclusion, not an oversight.
    "app/models/industry_apqc.py",
    # Historical trend endpoints for CapabilityMaturityAssessment — excluded
    # by name per the brief's AC-8/AC-1: these answer "what did assessment X
    # record", not "what is the current value".
}


def _iter_py_files():
    for path in APP_DIR.rglob("*.py"):
        if "/tests/" in path.as_posix() or path.name.startswith("test_"):
            continue
        yield path


def _grep(pattern: str) -> list[str]:
    hits = []
    regex = re.compile(pattern)
    for path in _iter_py_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments/docstrings referencing the old names, not reads
            if regex.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


class TestStep2CapabilitiesModelReaders:
    """app/models/capabilities.py:118-119 — ArchiMateCapability.target_maturity/.current_maturity."""

    def test_no_current_value_reader_outside_allowed_files(self):
        # Deliberately narrow: `.target_maturity` / `.current_maturity` as
        # attribute access, not the (unrelated) `target_maturity_level` /
        # `current_maturity_level` names used by BusinessCapability and
        # UnifiedCapability.
        hits = _grep(r"\.target_maturity\b(?!_level)|\.current_maturity\b(?!_level)")
        assert hits == [], (
            "found a current-value read of ArchiMateCapability's superseded "
            f"maturity columns outside the allowed files: {hits}"
        )


class TestStep3ACapabilityModelsReaders:
    """app/models/capability_models.py:167,180 — CapabilityMaturityAssessment.

    maturity_level / target_maturity_level are per-EVENT columns and stay
    reachable for historical/trend reads; what must not exist is a caller
    reading them as the answer to "what is the current maturity".
    """

    def test_no_bare_maturity_level_attribute_read_outside_allowed_files(self):
        """R3-5: the original version of this test checked whether any file
        mentioned the STRING "CapabilityMaturityAssessment" — two real
        holes: (a) it MISSES a relationship-traversal read like
        `cap.maturity_assessments[-1].maturity_level`, which reads a
        current value off the assessment model without ever naming the
        class directly at that spot, and (b) it FALSE-FAILS on a file
        that merely mentions the class name in an unrelated comment
        (the old version searched raw file text, comments included).

        Fixed with two checks, both attribute/pattern-level like the
        working Step 2 / Step 4 assertions above, not a whole-file
        substring search for a class name:
        """
        # Check 1 — a bare relationship-traversal read of a current
        # maturity value off `CapabilityMaturityAssessment` via its
        # `maturity_assessments` backref (capability_models.py:213,
        # `BusinessCapability.maturity_assessments`) — this is the shape
        # a caller uses to read the assessment model WITHOUT ever naming
        # `CapabilityMaturityAssessment` in the same statement, so the
        # class-name search above could never have caught it. A repo-wide
        # bare `.maturity_level` grep would false-positive on the many
        # unrelated `maturity_level` columns on other models
        # (VendorProductCapability, TechnicalCapabilityVendorMapping,
        # UnifiedApplicationCapabilityMapping, InteractiveCoverageMatrix
        # cells, IndustryProcessRecommendation, etc) — requiring
        # `.maturity_assessments` to co-occur on the same statement is
        # specific enough to avoid that, since only this one relationship
        # name points at `CapabilityMaturityAssessment`.
        traversal_hits = _grep(
            r"\.maturity_assessments\b.*\.(maturity_level|current_maturity|target_maturity)\b"
        )
        assert traversal_hits == [], (
            "found a bare relationship-traversal read of a current maturity "
            "value off CapabilityMaturityAssessment via .maturity_assessments "
            f"outside the allowed files — route it through "
            f"UnifiedCapability.maturity_for_source instead: {traversal_hits}"
        )

        # Check 2 — every genuine CapabilityMaturityAssessment call site
        # (its class body, its event listener, its own
        # to_dict()/calculate_maturity_gap()) lives in ONE file,
        # app/models/capability_models.py (already in ALLOWED_FILES). A
        # new importer of the class elsewhere is a signal worth reviewing
        # even though it doesn't prove a bad read on its own — kept as a
        # secondary, non-comment-aware cross-reference, using the same
        # `_grep`-style comment-skipping `_iter_py_files()` walk as every
        # other check in this file (not a raw-text substring search) so a
        # comment mentioning the class name does not false-fail it.
        known_importers = {
            "app/models/capability_models.py",
            "app/models/__init__.py",
            "app/models/unified_capability.py",
            "app/modules/intelligence/tests/test_maturity_authority_readers.py",
        }
        pattern = re.compile(r"\bCapabilityMaturityAssessment\b")
        import_hits = []
        for path in _iter_py_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in known_importers:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # comment mention, not a real import/read
                if pattern.search(line):
                    import_hits.append(rel)
                    break
        assert import_hits == [], (
            "found a new reader of CapabilityMaturityAssessment outside the "
            f"known/reviewed set — verify it doesn't read .maturity_level as a "
            f"current-value answer instead of the authority accessor: {import_hits}"
        )


class TestStep4CapabilityGapAnalysisReaders:
    """app/models/capability_gap_analysis.py:208,213 — CapabilityGapDetail."""

    def test_no_current_value_reader_outside_allowed_files(self):
        hits = _grep(r"\.current_maturity\b(?!_level)|\.required_maturity_level\b")
        assert hits == [], (
            "found a current-value read of CapabilityGapDetail's superseded "
            f"maturity columns outside the allowed files: {hits}"
        )


class TestCapabilityDriftRegressionDoesNotFabricateZero:
    """D-3: architecture_monitoring_service.py's `_analyze_capability_drift`
    must not coerce a None (unassessed/cleared) maturity to 0 -- that would
    fabricate a CMM level 0 that does not exist on the scale and fire a fake
    regression alert. Pure-function test: no DB needed, `_analyze_capability_drift`
    only reads plain dicts.
    """

    def test_none_current_maturity_does_not_fire_a_fabricated_regression(self):
        from app.modules.architecture.services.architecture_monitoring_service import (
            ArchitectureMonitoringService,
        )

        service = ArchitectureMonitoringService.__new__(ArchitectureMonitoringService)
        baseline_caps = [{"id": 1, "name": "Cap A", "current_maturity": 3}]
        current_caps = [{"id": 1, "name": "Cap A", "current_maturity": None}]

        result = service._analyze_capability_drift(baseline_caps, current_caps)

        assert result["maturity_regressions"] == [], (
            "a capability whose maturity went from a real value to None "
            "(unassessed/cleared) must not be reported as a regression to "
            "level 0 -- that level does not exist on the scale"
        )

    def test_real_regression_still_fires(self):
        from app.modules.architecture.services.architecture_monitoring_service import (
            ArchitectureMonitoringService,
        )

        service = ArchitectureMonitoringService.__new__(ArchitectureMonitoringService)
        baseline_caps = [{"id": 1, "name": "Cap A", "current_maturity": 3}]
        current_caps = [{"id": 1, "name": "Cap A", "current_maturity": 1}]

        result = service._analyze_capability_drift(baseline_caps, current_caps)

        assert len(result["maturity_regressions"]) == 1
        assert result["maturity_regressions"][0]["baseline_maturity"] == 3
        assert result["maturity_regressions"][0]["current_maturity"] == 1


class TestAccessorBehaviour:
    def test_maturity_for_sources_batch_returns_no_maturity_recorded_for_missing_keys(self, app):
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            result = UnifiedCapability.maturity_for_sources(
                "business_capability", [-1, -2, -3]
            )
            assert set(result.keys()) == {"-1", "-2", "-3"}
            for value in result.values():
                assert value["reason_code"] == "no_maturity_recorded"
                assert value["current_maturity_level"] is None

    def test_maturity_for_sources_empty_input_returns_empty_dict(self, app):
        from app.models.unified_capability import UnifiedCapability

        with app.app_context():
            assert UnifiedCapability.maturity_for_sources("business_capability", []) == {}


class TestCapabilityGapDetailToDictRendersReasonCode:
    def test_gap_detail_with_no_capability_maturity_renders_reason_code_not_fabricated_default(
        self, app, db_session, make_org, tenant_ctx
    ):
        """AC-2 (task 04): the old default=1 / default=3 must not reach a caller."""
        from app.models.capability_gap_analysis import CapabilityGapAnalysis, CapabilityGapDetail
        from app.models.unified_capability import UnifiedCapability

        org = make_org("gap-detail")
        with tenant_ctx(org.id):
            cap = UnifiedCapability(
                name="Gap detail capability",
                code=f"GD-{org.id}",
                organization_id=org.id,
                level=1,
                current_maturity_level=None,
                target_maturity_level=None,
            )
            db_session.add(cap)
            db_session.flush()

            analysis = CapabilityGapAnalysis(
                organization_id=org.id,
                analysis_name="T-002 test analysis",
            )
            db_session.add(analysis)
            db_session.flush()

            detail = CapabilityGapDetail(
                analysis_id=analysis.id,
                capability_id=cap.id,
                # These columns still carry the old defaults at the DB level —
                # the point of this test is that to_dict() must not surface
                # them once the authority has no maturity recorded.
            )
            db_session.add(detail)
            db_session.flush()

            rendered = detail.to_dict()
            assert rendered["maturity_reason_code"] == "no_maturity_recorded"
            assert rendered["current_maturity"] is None
            assert rendered["current_maturity"] != 1
            assert rendered["required_maturity_level"] is None
            assert rendered["required_maturity_level"] != 3

    def test_gap_detail_with_recorded_maturity_renders_the_authoritys_value(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.capability_gap_analysis import CapabilityGapAnalysis, CapabilityGapDetail
        from app.models.unified_capability import UnifiedCapability

        org = make_org("gap-detail-2")
        with tenant_ctx(org.id):
            cap = UnifiedCapability(
                name="Gap detail capability 2",
                code=f"GD2-{org.id}",
                organization_id=org.id,
                level=1,
                current_maturity_level=2,
                target_maturity_level=4,
            )
            db_session.add(cap)
            db_session.flush()

            analysis = CapabilityGapAnalysis(
                organization_id=org.id,
                analysis_name="T-002 test analysis 2",
            )
            db_session.add(analysis)
            db_session.flush()

            detail = CapabilityGapDetail(analysis_id=analysis.id, capability_id=cap.id)
            db_session.add(detail)
            db_session.flush()

            rendered = detail.to_dict()
            assert rendered["current_maturity"] == 2
            assert rendered["required_maturity_level"] == 4
            assert rendered["maturity_reason_code"] is None


class TestEngineMaturityReadsGoThroughTheHelper:
    """ADR-005 (extended): ``CapabilityHeatmapService.maturity_for_elements``
    and ``CapabilityHeatmapService.maturity_for_capability_ids`` are the only
    two engine-facing maturity reads this codebase allows (ADR-MAT-1). An
    engine holding element or capability ids reaches a capability's current
    maturity, its target, whether it was assessed, whether it is under
    target and by how much, only through one of those two methods -- never
    by reading the authority's own current/target maturity columns off
    ``UnifiedCapability`` directly, never through either of the two
    source-provenance accessors, and never by calling the strict,
    capability-id-keyed accessor itself from outside the one file that hosts
    the two helpers (and, until a later task repoints it, the one existing
    caller named below).
    """

    ENGINE_FILES = [
        "app/modules/intelligence/services/query_service.py",
        "app/modules/ai_chat/services/ai_gap_detection_service.py",
        "app/services/gap_discovery_service.py",
        "app/modules/business_model_canvas/service.py",
        "app/modules/business_case/service.py",
    ]

    # The only files allowed to call the strict, capability-id-keyed
    # accessor directly. This list can only shrink: query_service.py's
    # value_streams_at_risk was repointed onto the batched helper, so only
    # capability_heatmap_service.py remains.
    ACCESSOR_CALLERS = {
        "app/modules/capabilities/services/capability_heatmap_service.py",
    }

    def test_no_engine_reads_the_authority_columns_or_the_source_accessors_directly(self):
        pattern = re.compile(
            r"\.current_maturity_level\b|\.target_maturity_level\b"
            r"|maturity_for_source\(|maturity_for_sources\("
        )
        hits = []
        for rel in self.ENGINE_FILES:
            path = REPO_ROOT / rel
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                if pattern.search(line):
                    hits.append(f"{rel}:{lineno}: {line.strip()}")
        assert hits == [], (
            "an engine read the maturity authority's columns, or a source-"
            "provenance accessor, directly instead of going through "
            f"CapabilityHeatmapService's batched helper: {hits}"
        )

    def test_accessor_is_called_only_from_its_allowed_callers(self):
        pattern = re.compile(r"UnifiedCapability\.maturity_for_capability_ids\(")
        hits = []
        for path in APP_DIR.rglob("*.py"):
            if "/tests/" in path.as_posix() or path.name.startswith("test_"):
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in self.ACCESSOR_CALLERS:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                if pattern.search(line):
                    hits.append(f"{rel}:{lineno}: {line.strip()}")
        assert hits == [], (
            "UnifiedCapability.maturity_for_capability_ids was called outside "
            "its allowed callers -- route an engine through "
            f"CapabilityHeatmapService.maturity_for_elements / "
            f".maturity_for_capability_ids instead: {hits}"
        )

    def test_accessor_callers_list_names_no_stale_entry(self):
        """ACCESSOR_CALLERS is an allow-list, not a record of history -- an
        entry that no longer calls the strict accessor (because a later
        change repointed it onto the batched helper, as query_service.py's
        value_streams_at_risk was) silently widens what the test above
        actually enforces without anyone noticing. Pins that every listed
        caller still earns its place."""
        pattern = re.compile(r"UnifiedCapability\.maturity_for_capability_ids\(")
        for rel in self.ACCESSOR_CALLERS:
            path = REPO_ROOT / rel
            text = path.read_text(encoding="utf-8")
            calls = [
                line for line in text.splitlines()
                if not line.strip().startswith("#") and pattern.search(line)
            ]
            assert calls, (
                f"{rel} is listed in ACCESSOR_CALLERS but no longer calls "
                "the strict accessor -- drop it from the list"
            )


class TestCapabilityMaturityAssessmentHistorySurvives:
    """D-2 / brief AC-9, task 03 AC-2/3/4/6: the per-event audit trail must not
    have been touched by this task. Plant two assessments in different
    periods, confirm both are retrievable with their identifying fields
    intact, confirm calculate_maturity_gap() still works, and record a
    before/after row count proving nothing was deleted by this diff.
    """

    def test_two_period_assessments_survive_with_gap_calculation_intact(
        self, app, db_session, make_org, tenant_ctx
    ):
        import datetime

        from app.models.business_capabilities import BusinessCapability
        from app.models.capability_models import CapabilityMaturityAssessment

        org = make_org("maturity-history")
        with tenant_ctx(org.id):
            cap = BusinessCapability(
                name="History capability",
                organization_id=org.id,
                current_maturity_level=3,
                target_maturity_level=4,
            )
            db_session.add(cap)
            db_session.flush()

            before_count = CapabilityMaturityAssessment.query.filter_by(
                organization_id=org.id
            ).count()

            period_1 = CapabilityMaturityAssessment(
                organization_id=org.id,
                business_capability_id=cap.id,
                assessment_date=datetime.date(2026, 1, 15),
                assessor_name="Jane Assessor",
                maturity_level=2,
                target_maturity_level=4,
            )
            period_2 = CapabilityMaturityAssessment(
                organization_id=org.id,
                business_capability_id=cap.id,
                assessment_date=datetime.date(2026, 6, 15),
                assessor_name="John Assessor",
                maturity_level=3,
                target_maturity_level=4,
            )
            db_session.add_all([period_1, period_2])
            db_session.flush()

            after_count = CapabilityMaturityAssessment.query.filter_by(
                organization_id=org.id
            ).count()
            assert after_count == before_count + 2, (
                "expected exactly the two newly-planted assessments to be "
                "added — a row disappearing here would mean the historical "
                "trail was damaged by this task's migration"
            )

            rows = (
                CapabilityMaturityAssessment.query.filter_by(organization_id=org.id)
                .order_by(CapabilityMaturityAssessment.assessment_date.asc())
                .all()
            )
            assert len(rows) == 2
            assert rows[0].assessment_date == datetime.date(2026, 1, 15)
            assert rows[0].assessor_name == "Jane Assessor"
            assert rows[0].maturity_level == 2
            assert rows[1].assessment_date == datetime.date(2026, 6, 15)
            assert rows[1].assessor_name == "John Assessor"
            assert rows[1].maturity_level == 3

            # calculate_maturity_gap() — the per-event method, unrelated to the
            # single-authority accessor — must still work on a planted row.
            rows[1].target_maturity_level = 5
            gap = rows[1].calculate_maturity_gap()
            assert gap == 2  # 5 - 3
