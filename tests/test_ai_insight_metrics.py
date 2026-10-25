"""Regression tests for the QA register's M-01 / M-02 / M-04 AI-insight defects.

All three share one disease: a metric that rewards the absence of data.

- M-01: Chief Architect Synthesis scored empty solutions 100/100 (nothing to
  be non-conformant about), so an estate of empty solutions read as healthy.
- M-02: EA Briefing never detected duplicate elements, orphaned elements,
  missing descriptions, or duplicate/empty solutions — those checks did not
  exist in the finding gatherers at all.
- M-04: Data Stewardship's real findings (semantic duplicate match, PII with
  no classification, zero data lineage) were all graded INFO regardless of
  the risk stated in their own text.
"""

import pytest

from app.modules.solutions_strategic.v2.services.chief_architect_service import (
    ChiefArchitectService,
)
from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
    ConformanceReviewer,
)
from app.modules.solutions_strategic.v2.services.data_stewardship_reviewer import (
    DataStewardshipReviewer,
)
from app.modules.solutions_strategic.v2.services.enterprise_briefing_service import (
    EnterpriseBriefingService,
)


@pytest.fixture
def org_ctx(db_session, make_org, tenant_ctx):
    org = make_org("ai-insights")
    with tenant_ctx(org.id):
        yield org


def _solution(db_session, name="Empty Solution"):
    from app.models.solution_models import Solution

    s = Solution(name=name)
    db_session.add(s)
    db_session.flush()
    return s


def _element(db_session, name, type_="TestElement", layer="application", description=None):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, description=description)
    db_session.add(el)
    db_session.flush()
    return el


# --------------------------------------------------------------------- #
# M-01 — empty solutions must not score as conformant                    #
# --------------------------------------------------------------------- #

def test_empty_solution_is_unassessed_not_perfect(db_session, org_ctx):
    s = _solution(db_session, "Empty Solution")

    review = ConformanceReviewer.review(s.id)

    assert review["success"], review
    assert review["score"] is None, (
        "an empty solution scored a number — it must be unassessed, never "
        "a computed conformance score (see M-01)"
    )
    assert review.get("unassessed") is True


def test_portfolio_average_excludes_unassessed_solutions(db_session, org_ctx):
    from app.models.solution_models import Solution, SolutionArchiMateElement

    # Four empty solutions + one with real, flagged content — the exact
    # shape the auditor found: (4 x 100 + 64) / 5 = 93 was the bug.
    for i in range(4):
        _solution(db_session, f"Empty Solution {i}")

    real = _solution(db_session, "Real Solution")
    el = _element(db_session, "SAP", layer="application")
    db_session.add(SolutionArchiMateElement(
        solution_id=real.id, layer_type="application",
        element_id=el.id, element_table="application_components",
        element_name="SAP",
    ))
    db_session.flush()

    synthesis = ChiefArchitectService.portfolio_synthesis()

    assert synthesis["success"], synthesis
    # Only the real solution is scored; the four empty ones are excluded
    # and the exclusion is reported, not silently dropped.
    assert synthesis["solutions_reviewed"] == 1
    assert synthesis["solutions_unassessed"] == 4
    # The review counts findings and calculates no score, so no average of
    # scores is published: the fabricated 93 cannot come back. The one real
    # solution's missing business/data/technology content shows as findings.
    assert synthesis["avg_conformance"] is None
    assert synthesis["flagged_total"] >= 1


# --------------------------------------------------------------------- #
# M-02 — EA Briefing must detect known, present defects                  #
# --------------------------------------------------------------------- #

def test_briefing_flags_duplicate_named_elements(db_session, org_ctx):
    _element(db_session, "BlockReason")
    _element(db_session, "BlockReason")  # exact duplicate name

    briefing = EnterpriseBriefingService.generate(user_id=1, source="test")

    categories = {f["category"] for f in briefing.findings}
    assert "duplicates" in categories, briefing.findings


def test_briefing_flags_orphaned_elements(db_session, org_ctx):
    _element(db_session, "Orphan Element A")
    _element(db_session, "Orphan Element B")

    briefing = EnterpriseBriefingService.generate(user_id=1, source="test")

    categories = {f["category"] for f in briefing.findings}
    assert "orphans" in categories, briefing.findings


def test_briefing_flags_missing_descriptions(db_session, org_ctx):
    _element(db_session, "Undocumented Element", description=None)

    briefing = EnterpriseBriefingService.generate(user_id=1, source="test")

    categories = {f["category"] for f in briefing.findings}
    assert "descriptions" in categories, briefing.findings


def test_briefing_flags_empty_and_duplicate_named_solutions(db_session, org_ctx):
    _solution(db_session, "HxGN EAM")
    _solution(db_session, "HxGN EAM")  # same name — the auditor's exact finding

    briefing = EnterpriseBriefingService.generate(user_id=1, source="test")

    solution_findings = [f for f in briefing.findings if f["category"] == "solutions"]
    assert solution_findings, briefing.findings
    joined = " ".join(f["title"] + f["detail"] for f in solution_findings)
    assert "HxGN EAM" in joined or "share a name" in joined.lower()


def test_briefing_all_clear_only_when_checks_actually_ran(db_session, org_ctx):
    """A briefing over an empty tenant should say clean, but the record must
    show that every check ran — not merely that nothing was found."""
    briefing = EnterpriseBriefingService.generate(user_id=1, source="test")

    assert briefing.checks_run, "checks_run must be recorded on every briefing"
    assert all(c["ran"] for c in briefing.checks_run), (
        "a check silently failed and is indistinguishable from 'ran clean' "
        "without checks_run — this is exactly the M-02 defect"
    )


def test_briefing_reproduces_the_auditors_fixture(db_session, org_ctx):
    """The 06:57 18-Aug run: duplicates, orphans, and a duplicate solution
    name were all present in the data and the briefing still said 0 findings.
    This is the exhaustive regression case."""
    _element(db_session, "BlockReason")
    _element(db_session, "BlockReason")
    _element(db_session, "Unconnected Node")
    _solution(db_session, "HxGN EAM")
    _solution(db_session, "HxGN EAM")

    briefing = EnterpriseBriefingService.generate(user_id=1, source="test")

    assert briefing.finding_count > 0, (
        "known, present defects (duplicate elements, an orphaned element, "
        "duplicate-named solutions) produced zero findings — M-02 regression"
    )


# --------------------------------------------------------------------- #
# M-04 — severity must track the finding's own stated risk               #
# --------------------------------------------------------------------- #

def test_pii_finding_is_not_info(db_session, org_ctx):
    _element(db_session, "Customer", type_="DataObject")

    review = DataStewardshipReviewer.review()

    pii = [f for f in review["findings"] if f["category"] == "classification"
           and "personal" in f["title"].lower()]
    assert pii, review["findings"]
    assert pii[0]["severity"] != "info", (
        "a finding whose own text calls unclassified PII a compliance/DPIA "
        "risk was graded info (see M-04)"
    )


def test_lineage_gap_is_not_info(db_session, org_ctx):
    _element(db_session, "Order", type_="DataObject")

    review = DataStewardshipReviewer.review()

    lineage = [f for f in review["findings"] if f["category"] == "lineage"]
    assert lineage, review["findings"]
    assert lineage[0]["severity"] != "info"


def test_semantic_duplicate_above_threshold_is_not_info(monkeypatch, db_session, org_ctx):
    """Reproduce the auditor's BlockReason/BlockRule case directly against
    the severity-mapping logic, independent of whether the live embedding
    service is reachable in this environment."""
    from app.modules.solutions_strategic.v2.services import data_stewardship_reviewer as mod

    _element(db_session, "BlockReason", type_="DataObject")
    _element(db_session, "BlockRule", type_="DataObject")

    monkeypatch.setattr(
        mod, "_semantic_pairs", lambda names: [("BlockReason", "BlockRule", 0.635)]
    )

    review = DataStewardshipReviewer.review()

    semantic = [f for f in review["findings"] if "semantic match" in f["title"]]
    assert semantic, review["findings"]
    assert semantic[0]["severity"] == "high", semantic[0]


def test_data_layer_with_real_risk_is_not_headlined_coherent(db_session, org_ctx):
    """The concrete manifestation: with PII and zero lineage present, flagged
    must be > 0 so the page cannot read 'Data layer coherent — 0 need
    attention' (the template derives its headline from review['flagged'])."""
    _element(db_session, "Customer", type_="DataObject")

    review = DataStewardshipReviewer.review()

    assert review["flagged"] > 0, (
        "genuine PII/lineage risk present but flagged==0 — the page would "
        "still headline 'Data layer coherent'"
    )
