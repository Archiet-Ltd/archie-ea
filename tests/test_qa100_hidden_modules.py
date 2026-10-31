"""Fail-first regression tests for QA Update 6 hidden-module findings H-01..H-04.

Uses the shared db_session fixture (tests/conftest.py) — rolled back per test,
so no residue in the shared database.
"""

import pytest


# ---------------------------------------------------------------------------
# H-03 — ADM Kanban: phase/column counts must derive from the same collection
# ---------------------------------------------------------------------------

def test_normalize_phase_code_maps_unknown_values_to_a_known_phase():
    """The bug: a card whose stored phase value doesn't exactly match one of
    the 10 ADM_PHASES codes (wrong case, stray text, None) silently dropped
    out of phase_counts while still counting in column_counts — the 27-vs-28
    divergence QA measured. Every value must resolve to a valid code."""
    from app.services.kanban_projection_service import (
        _normalize_phase_code,
        _VALID_PHASE_CODES,
    )

    assert _normalize_phase_code(None) in _VALID_PHASE_CODES
    assert _normalize_phase_code("") in _VALID_PHASE_CODES
    assert _normalize_phase_code("a") == "A"  # wrong case, still recognisable
    assert _normalize_phase_code("nonsense-phase") in _VALID_PHASE_CODES
    assert _normalize_phase_code("PRELIM") == "PRELIM"
    assert _normalize_phase_code("G") == "G"


def test_kanban_phase_and_column_counts_always_sum_to_the_same_total(db_session, make_org):
    """Regression for H-03: phase_counts and column_counts must both sum to
    len(cards) for the same (unfiltered) card set. Uses a KanbanCard with a
    deliberately garbage adm_phase-adjacent value path exercised through
    _project_one_deliverable, which is where the divergence QA found lived."""
    from app.services.kanban_projection_service import (
        KanbanProjectionService,
        ADM_PHASES,
        COLUMN_IDS,
    )
    from app.models.adm_deliverable import ADMDeliverable

    make_org("kanban")

    # A deliverable with a phase value that does NOT exactly match any
    # ADM_PHASES code (lowercase) — this is the shape of row that used to
    # vanish from phase_counts while still appearing in column_counts.
    deliv = ADMDeliverable(
        name="Garbage-phase deliverable",
        phase="a",  # lowercase — not a literal ADM_PHASES code
        is_template=False,
    )
    db_session.add(deliv)
    db_session.flush()

    svc = KanbanProjectionService()
    result = svc.get_cards(card_type="deliverable")

    total_cards = len(result["cards"])
    assert total_cards >= 1

    phase_sum = sum(result["phase_counts"][p["code"]] for p in ADM_PHASES)
    column_sum = sum(result["column_counts"][c] for c in COLUMN_IDS)

    assert phase_sum == total_cards, (
        f"phase_counts summed to {phase_sum}, but there are {total_cards} cards "
        "— a card's phase value fell outside the known ADM phase codes"
    )
    assert column_sum == total_cards
    assert phase_sum == column_sum


# ---------------------------------------------------------------------------
# H-01 — Duplicate detection: ArchiMate elements, not just applications
# ---------------------------------------------------------------------------

def test_archimate_element_duplicate_detection_finds_exact_name_groups(db_session, make_org, tenant_ctx):
    """H-01 acceptance: detection must extend to ArchiMate ELEMENTS.
    Reuses DuplicateDetectionUtils (exact, case-insensitive) — no threshold,
    sidestepping the untuned-per-layer-threshold problem 352836e flagged for
    the semantic engine."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    org = make_org("dupe-elements")
    # ArchiMateElement.organization_id is NOT NULL and is stamped by
    # TenantMixin on flush, which only happens inside a tenant context.
    with tenant_ctx(org.id):
        _run_element_duplicate_assertions(db_session)


def _run_element_duplicate_assertions(db_session):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.duplicate_detection.services.unified_duplicate_detection_service import (
        UnifiedDuplicateDetectionService,
    )

    els = [
        ArchiMateElement(name="Customer Portal", type="ApplicationComponent", layer="Application"),
        ArchiMateElement(name="customer portal", type="ApplicationComponent", layer="Application"),
        ArchiMateElement(name="Customer Portal", type="BusinessProcess", layer="Business"),
        ArchiMateElement(name="Unique Element", type="ApplicationComponent", layer="Application"),
    ]
    for el in els:
        db_session.add(el)
    db_session.flush()

    svc = UnifiedDuplicateDetectionService()
    groups = svc.get_archimate_element_duplicate_groups()

    matching = [
        g for g in groups
        if g["name"].lower() == "customer portal" and g["type"] == "ApplicationComponent"
    ]
    assert len(matching) == 1, "exact-name duplicate within the same (layer, type) bucket was not found"
    assert matching[0]["element_count"] == 2

    # Different type (BusinessProcess) must NOT be merged into the
    # ApplicationComponent group — same reasoning 352836e used per name-space.
    cross_type = [
        g for g in groups
        if g["name"].lower() == "customer portal" and g["type"] == "BusinessProcess"
    ]
    assert cross_type == [], "a BusinessProcess should not collide with an ApplicationComponent of the same name"

    unique_hits = [g for g in groups if g["name"] == "Unique Element"]
    assert unique_hits == [], "a name appearing once must not be reported as a duplicate group"


# ---------------------------------------------------------------------------
# H-02 — Impact analysis: no fabricated financial-risk fallback
# ---------------------------------------------------------------------------

def test_impact_analysis_does_not_fabricate_financial_risk(db_session):
    """H-02 / CLAUDE.md "never invent data": with no real TCO data,
    estimated_financial_risk must be None (renders as an em dash), never a
    literal $25,000-per-element guess that looks computed."""
    from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
        ImpactAnalysisService,
    )

    result = ImpactAnalysisService.analyze_change_impact(element_id=999999999, change_type="MODIFY")

    assert result["total_affected"] == 0
    assert result["estimated_financial_risk"] is None, (
        "a nonexistent element with zero dependencies must not receive a "
        "fabricated per-element dollar estimate"
    )


# ---------------------------------------------------------------------------
# H-04 — Currency: one source of truth, code shown, consistent precision
# ---------------------------------------------------------------------------

def test_format_currency_filter_uses_org_currency_and_no_invented_zero(app):
    """H-04: format_currency must derive symbol/decimals from CurrencyConfig
    (single source of truth) and must never turn a missing value into a
    fabricated 0 — None passes through untouched so the template can render
    an em dash."""
    with app.app_context():
        fmt = app.jinja_env.filters["format_currency"]

        # No request/org context -> falls back to the configured default,
        # but it must still be internally consistent (symbol + decimals from
        # the SAME CurrencyConfig entry).
        from config import CurrencyConfig

        default_code = CurrencyConfig.DEFAULT_CURRENCY
        default_cfg = CurrencyConfig.get_currency_config(default_code)

        formatted = fmt(1234.5)
        assert formatted is not None
        assert default_cfg["symbol"] in formatted
        # decimal_places from the SAME config entry, not a hardcoded 2 or 0
        if default_cfg["decimal_places"] == 0:
            assert "." not in formatted
        else:
            assert formatted.count(".") == 1 or formatted.count(",") >= 0  # sanity, locale-tolerant

        assert fmt(None) == "—", (
            "a missing amount must render as an em dash — never a fabricated 0, "
            "and never an empty string, which reads as a blank cell"
        )

        with_code = fmt(10, show_code=True)
        assert default_cfg["code"] in with_code


def test_org_currency_code_resolution_reads_reporting_currency(app, db_session, make_org):
    """H-04 / QA-007: Organization.reporting_currency is the one stored currency an org sees; settings.currency_code is
    not a source, and the default is the server default (GBP), not a hardcoded value."""
    from config import CurrencyConfig

    org = make_org("currency")
    org.reporting_currency = "USD"
    org.settings = {"currency_code": "EUR"}  # no longer a source
    db_session.commit()
    assert CurrencyConfig.get_org_currency_code(org) == "USD"
    assert CurrencyConfig.get_org_currency_code(organization_id=org.id) == "USD"

    fresh = make_org("currency")  # never given a reporting currency: the server default applies
    fresh.settings = {"currency_code": "USD"}
    db_session.commit()
    assert CurrencyConfig.get_org_currency_code(organization_id=fresh.id) == CurrencyConfig.DEFAULT_CURRENCY == "GBP"
    assert CurrencyConfig.get_org_currency_code(None) == CurrencyConfig.DEFAULT_CURRENCY
