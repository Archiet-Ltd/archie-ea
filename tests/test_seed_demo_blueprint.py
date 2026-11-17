"""The demonstration solutions carry a written blueprint and risks that the
solution's own pages read back, and a second organisation carries none of it."""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company


@pytest.fixture
def seeded(app, db_session, make_org):
    """Seed the demonstration organisation twice, plus a second real organisation."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        first = seed_demo_company()
        second = seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    demo_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    other = make_org("blueprint-other")
    db_session.commit()
    return {"demo": demo_id, "other": other.id, "first": first, "second": second}


def _solution(org_id, name):
    from app import db
    from app.models.solution_models import Solution

    return db.session.query(Solution).filter(
        Solution.organization_id == org_id, Solution.name == name).one()


def test_field_calibration_platform_has_all_fifteen_sections_written(seeded):
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BLUEPRINT_SECTION_IDS, compute_blueprint_completeness,
    )

    solution = _solution(seeded["demo"], "Field Calibration Platform")
    figures = compute_blueprint_completeness(solution.section_scores)
    assert figures["total_sections"] == 15 == len(BLUEPRINT_SECTION_IDS)
    assert figures["sections_complete"] == 15
    assert figures["percent"] > 0
    narratives = solution.section_narratives
    for section in ("executive_summary", "architecture_decisions"):
        assert len(narratives[section].split()) >= 100, section
    # Governance follows from the blueprint being ready, not from a typed status.
    assert solution.governance_status == "proposed"


def test_the_other_solutions_are_partly_written_and_say_so(seeded):
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        compute_blueprint_completeness,
    )

    for name in ("Supplier Collaboration Hub", "Event-Driven Operations Backbone"):
        figures = compute_blueprint_completeness(_solution(seeded["demo"], name).section_scores)
        assert 0 < figures["percent"] < 100, name
        assert 0 < figures["sections_complete"] < figures["total_sections"], name


def test_solution_risks_are_the_ones_the_solution_lists_and_the_heat_map_plots(seeded):
    from app.services import risk_service

    solution = _solution(seeded["demo"], "Field Calibration Platform")
    canonical, legacy = risk_service.solution_risk_sources(solution.id)
    assert len(canonical) == 6 and legacy == []
    cells = sorted((r.likelihood, r.impact) for r in canonical)
    assert (5, 5) in cells and (5, 1) in cells
    # A likely risk with a small impact is not critical; the top right is.
    assert risk_service.risk_band(5 * 1) == "medium"
    assert risk_service.risk_band(5 * 5) == "critical"
    assert risk_service.risk_band(1 * 1) == "low"


def test_a_second_run_changes_nothing_and_another_organisation_has_none_of_it(seeded):
    from app import db
    from app.models.risk import Risk
    from app.models.solution_models import Solution

    second = seeded["second"]
    for key in ("blueprint_elements_created", "blueprint_links_created",
                "blueprint_relationships_created", "blueprint_narratives_written",
                "solution_risks_created"):
        assert second[key] == 0, key
    assert seeded["first"]["solution_risks_created"] in (0, 12)
    assert db.session.query(Solution).filter(Solution.organization_id == seeded["other"]).count() == 0
    assert db.session.query(Risk).filter(Risk.organization_id == seeded["other"]).count() == 0
