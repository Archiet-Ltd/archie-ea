"""The ARB conformance gate must judge all four architecture domains.

TOGAF ADM treats Business (Phase B), Data and Application (Phase C) and
Technology (Phase D) as peers. The reviewer shipped with four rule categories —
integration, clean_core, technology, deployment — of which only one, a binary
"are there any technology elements at all", concerned an architecture domain.
Business and Data had **no rules whatsoever**, so a solution could reach
"board-ready" while naming no business process it changes and no data it
creates, classifies or retains.

These tests pin the two missing domains. They follow the reviewer's existing
convention deliberately: a solution with nothing modelled yet is not in breach
(see `_technology_findings`, which returns [] when total == 0) — the gate judges
incomplete designs, not empty ones.
"""

import pytest

from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
    ConformanceReviewer,
)


def _categories(solution_id):
    review = ConformanceReviewer.review(solution_id)
    assert review["success"], review
    return {f["category"] for f in review["findings"]}


@pytest.fixture
def solution(db_session, make_org, tenant_ctx):
    """A solution owned by a fresh org, created inside that tenant context."""
    from app.models.solution_models import Solution

    org = make_org("conformance")
    with tenant_ctx(org.id):
        s = Solution(name="Order Capture Replacement")
        db_session.add(s)
        db_session.flush()
        yield s


def _add_element(db_session, solution_id, layer_type, element_table, name, etype="TestElement"):
    """Attach one ArchiMate element to a solution.

    A real ArchiMateElement row is created first: `solution_archimate_elements`
    carries a FK on `element_id` and a UniqueConstraint on
    (solution_id, element_id), so invented ids either violate the FK or collide.
    """
    from app.models.models import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement

    element = ArchiMateElement(name=name, type=etype, layer=layer_type)
    db_session.add(element)
    db_session.flush()

    db_session.add(
        SolutionArchiMateElement(
            solution_id=solution_id,
            layer_type=layer_type,
            element_id=element.id,
            element_table=element_table,
            element_name=name,
        )
    )
    db_session.flush()


def test_solution_with_no_elements_is_not_flagged_for_business_or_data(
    db_session, solution
):
    """Nothing modelled yet is not a breach — matches the technology rule."""
    categories = _categories(solution.id)
    assert "business" not in categories
    assert "data" not in categories


def test_application_only_design_is_flagged_for_missing_business_layer(
    db_session, solution
):
    """A design that models applications but no business layer is incomplete."""
    _add_element(db_session, solution.id, "application", "application_components", "SAP")

    assert "business" in _categories(solution.id), (
        "a solution modelling only application elements was not flagged for "
        "having no business-layer content; the ARB gate ignores TOGAF Phase B"
    )


def test_application_only_design_is_flagged_for_missing_data_architecture(
    db_session, solution
):
    """A design that names no data object cannot be assessed for GDPR or lineage."""
    _add_element(db_session, solution.id, "application", "application_components", "SAP")

    assert "data" in _categories(solution.id), (
        "a solution modelling only application elements was not flagged for "
        "having no data architecture; the ARB gate ignores TOGAF Phase C-Data"
    )


def test_business_layer_element_clears_the_business_finding(db_session, solution):
    from app.models.solution_models import SolutionArchiMateElement  # noqa: F401

    _add_element(db_session, solution.id, "application", "application_components", "SAP")
    _add_element(db_session, solution.id, "business", "business_processes", "Capture Order")

    assert "business" not in _categories(solution.id)


def test_business_layer_match_is_case_insensitive(db_session, solution):
    """`layer_type` is written in both casings across the codebase."""
    _add_element(db_session, solution.id, "application", "application_components", "SAP")
    _add_element(db_session, solution.id, "Business", "business_processes", "Capture Order")

    assert "business" not in _categories(solution.id)


def test_data_object_clears_the_data_finding(db_session, solution):
    _add_element(db_session, solution.id, "application", "application_components", "SAP")
    _add_element(
        db_session, solution.id, "application", "application_data_objects", "Sales Order"
    )

    assert "data" not in _categories(solution.id)


def test_business_object_also_counts_as_data_architecture(db_session, solution):
    """A Business Object is data content in ArchiMate terms."""
    _add_element(db_session, solution.id, "application", "application_components", "SAP")
    _add_element(db_session, solution.id, "business", "business_objects", "Customer")

    assert "data" not in _categories(solution.id)


def _readiness_labels(solution_id):
    from app.modules.solutions_strategic.v2.services.chief_architect_service import (
        ChiefArchitectService,
    )

    packet = ChiefArchitectService.solution_packet(solution_id)
    assert packet["success"], packet
    return {row["label"]: row["ok"] for row in packet["readiness"]}


# The board-readiness checklist is the design score's ten checks; these are
# the rows that answer "has the design addressed data and technology".
DOMAIN_ROWS = (
    "Data named",
    "Where it runs named",
)


def test_board_packet_reports_domain_coverage(db_session, solution):
    """The board must see which architecture domains are unaddressed.

    Every readiness row shipped was process-only — owner, technical lead, ADR.
    A solution could be "board-ready" having said nothing about business
    behaviour, data, or technology.
    """
    _add_element(db_session, solution.id, "application", "application_components", "SAP")

    labels = _readiness_labels(solution.id)
    for row in DOMAIN_ROWS:
        assert row in labels, f"board packet has no readiness row for {row!r}"
        assert labels[row] is False, f"{row!r} reported ok on an application-only design"


def test_domain_rows_turn_green_when_the_domains_are_modelled(db_session, solution):
    _add_element(db_session, solution.id, "application", "application_components", "SAP")
    _add_element(db_session, solution.id, "business", "business_processes", "Capture Order")
    _add_element(db_session, solution.id, "application", "application_data_objects", "Sales Order")
    _add_element(db_session, solution.id, "technology", "technology_nodes", "SAP HANA node")
    _add_element(db_session, solution.id, "technology", "archimate_elements", "SAP HANA", etype="Node")
    _add_element(db_session, solution.id, "application", "archimate_elements", "Order", etype="DataObject")

    labels = _readiness_labels(solution.id)
    for row in DOMAIN_ROWS:
        assert labels[row] is True, f"{row!r} still False after the domain was modelled"


def test_findings_carry_evidence_and_recommendation(db_session, solution):
    """Findings must be actionable and sourced, never bare assertions."""
    _add_element(db_session, solution.id, "application", "application_components", "SAP")

    review = ConformanceReviewer.review(solution.id)
    for finding in review["findings"]:
        if finding["category"] in ("business", "data"):
            assert finding["evidence"], finding
            assert finding["recommendation"], finding
            assert finding["severity"] in ("critical", "high", "info")
