"""Phase gate validation service — checks data completeness before phase transition.

Queries REAL junction/child records to determine whether a Solution has met
the minimum data requirements for the current ADM phase before allowing
advancement to the next phase.
"""
from app.services.kanban_projection_service import normalize_phase_code
from app.models.solution_models import Solution, SolutionCapabilityMapping

# Phase ordering for navigation
PHASE_ORDER = list("ABCDEFGH")

# Human-readable labels per phase
PHASE_LABELS = {
    "A": "Architecture Vision",
    "B": "Business Architecture",
    "C": "Information Systems Architecture",
    "D": "Technology Architecture",
    "E": "Opportunities & Solutions",
    "F": "Migration Planning",
    "G": "Implementation Governance",
    "H": "Architecture Change Management",
}

def _count_drivers(solution):
    """Count SolutionDriver records linked via analysis session."""
    if not solution.analysis_session_id:
        return 0
    from app.models.solution_architect_models import (
        SolutionProblemDefinition,
        SolutionDriver,
    )

    problem = SolutionProblemDefinition.query.filter_by(
        session_id=solution.analysis_session_id
    ).first()
    if not problem:
        return 0
    return SolutionDriver.query.filter_by(problem_id=problem.id).count()


def _count_goals(solution):
    """Count SolutionGoal records linked via analysis session."""
    if not solution.analysis_session_id:
        return 0
    from app.models.solution_architect_models import (
        SolutionProblemDefinition,
        SolutionGoal,
    )

    problem = SolutionProblemDefinition.query.filter_by(
        session_id=solution.analysis_session_id
    ).first()
    if not problem:
        return 0
    return SolutionGoal.query.filter_by(problem_id=problem.id).count()


def _count_stakeholder_mappings(solution):
    """Count SolutionStakeholderMapping records for this solution."""
    from app.models.solution_stakeholder import SolutionStakeholderMapping

    return SolutionStakeholderMapping.query.filter_by(
        solution_id=solution.id
    ).count()


def _count_capabilities(solution):
    """Count capability mappings (direct or via session problem)."""
    direct = SolutionCapabilityMapping.query.filter_by(
        solution_id=solution.id
    ).count()
    if direct > 0:
        return direct
    # Fallback: via analysis session problem
    if solution.analysis_session_id:
        from app.models.solution_architect_models import SolutionProblemDefinition

        problem = SolutionProblemDefinition.query.filter_by(
            session_id=solution.analysis_session_id
        ).first()
        if problem:
            return SolutionCapabilityMapping.query.filter_by(
                problem_id=problem.id
            ).count()
    return 0


def _count_applications(solution):
    """Count applications linked to solution via junction table."""
    return solution.applications.count()


def _count_vendor_products(solution):
    """Count vendor products linked to solution via junction table."""
    return solution.vendor_products.count()


def _count_archimate_by_layer(solution, layer_type):
    """Count ArchiMate elements of a given layer_type for this solution."""
    from app.models.solution_models import SolutionArchiMateElement

    return SolutionArchiMateElement.query.filter_by(
        solution_id=solution.id, layer_type=layer_type
    ).count()


def _count_work_packages(solution):
    """Count work packages linked to this solution.

    This used to import `SolutionWorkPackage`, which has never existed in this
    codebase. The ImportError was swallowed and every call answered 0, so phase
    F's *critical* "Work packages defined" check was unpassable for every
    solution, with no trace in any log.

    There are two real stores, and which one holds the rows depends on whether
    the ArchiMate sync has run:
      - `RoadmapWorkPackage` (`source_type='solution'`) is where the solution's
        own work packages are written; and
      - `WorkPackage` (`context='solution'`) is the ArchiMate mirror that
        `solution_archimate_sync_service` creates from them.
    Count the native rows, falling back to the mirror — the same direct-then-
    fallback shape as `_count_capabilities` above.
    """
    from app.models.roadmap_models import RoadmapWorkPackage

    direct = RoadmapWorkPackage.query.filter_by(
        source_type="solution", source_id=solution.id
    ).count()
    if direct > 0:
        return direct

    from app.models.implementation_migration import WorkPackage

    return WorkPackage.query.filter_by(
        context="solution", context_id=solution.id
    ).count()


def _count_risks(solution):
    """Count risks for this solution."""
    from app.models.solution_lifecycle_models import SolutionRisk

    return SolutionRisk.query.filter_by(solution_id=solution.id).count()


def _count_metrics(solution):
    """Count success metrics for this solution."""
    from app.models.solution_lifecycle_models import SolutionMetric

    return SolutionMetric.query.filter_by(solution_id=solution.id).count()


# ─── Gate definitions ─────────────────────────────────────────────────────────
# Each phase defines checks that must pass before the phase can be completed
# and the solution advanced to the next phase.
#
# Format per check:
#   {
#       "key": unique identifier,
#       "label": human-readable description,
#       "counter": callable(solution) -> int,
#       "min": minimum count required,
#       "severity": "critical" | "warning"
#   }
#
# "critical" items block advancement.  "warning" items are advisory.

GATE_DEFINITIONS = {
    "A": [
        {
            "key": "drivers",
            "label": "Business drivers defined",
            "counter": _count_drivers,
            "min": 1,
            "severity": "critical",
        },
        {
            "key": "goals",
            "label": "Solution goals defined",
            "counter": _count_goals,
            "min": 1,
            "severity": "critical",
        },
        {
            "key": "stakeholders",
            "label": "Stakeholder mappings created",
            "counter": _count_stakeholder_mappings,
            "min": 1,
            "severity": "warning",
        },
    ],
    "B": [
        {
            "key": "capabilities",
            "label": "Business capabilities mapped",
            "counter": _count_capabilities,
            "min": 1,
            "severity": "critical",
        },
        {
            "key": "archimate_business",
            "label": "Business-layer ArchiMate elements linked",
            "counter": lambda s: _count_archimate_by_layer(s, "business"),
            "min": 1,
            "severity": "warning",
        },
    ],
    "C": [
        {
            "key": "applications",
            "label": "Applications linked",
            "counter": _count_applications,
            "min": 1,
            "severity": "warning",
        },
        {
            "key": "vendor_products",
            "label": "Vendor products evaluated",
            "counter": _count_vendor_products,
            "min": 1,
            "severity": "warning",
        },
        {
            "key": "archimate_application",
            "label": "Application-layer ArchiMate elements linked",
            "counter": lambda s: _count_archimate_by_layer(s, "application"),
            "min": 1,
            "severity": "warning",
        },
    ],
    "D": [
        {
            "key": "archimate_technology",
            "label": "Technology-layer ArchiMate elements linked",
            "counter": lambda s: _count_archimate_by_layer(s, "technology"),
            "min": 1,
            "severity": "warning",
        },
    ],
    "E": [
        {
            "key": "archimate_strategy",
            "label": "Strategy-layer elements (courses of action) linked",
            "counter": lambda s: _count_archimate_by_layer(s, "strategy"),
            "min": 1,
            "severity": "warning",
        },
    ],
    "F": [
        {
            "key": "work_packages",
            "label": "Work packages defined",
            "counter": _count_work_packages,
            "min": 1,
            "severity": "critical",
        },
        {
            "key": "risks",
            "label": "Risks identified",
            "counter": _count_risks,
            "min": 1,
            "severity": "warning",
        },
    ],
    "G": [
        {
            "key": "description",
            "label": "Problem statement defined (>20 chars)",
            "counter": lambda s: 1 if s.description and len(s.description) > 20 else 0,
            "min": 1,
            "severity": "critical",
        },
        {
            "key": "solution_owner",
            "label": "Solution owner assigned",
            "counter": lambda s: 1 if s.solution_owner else 0,
            "min": 1,
            "severity": "critical",
        },
    ],
    "H": [
        {
            "key": "metrics",
            "label": "Success metrics defined",
            "counter": _count_metrics,
            "min": 1,
            "severity": "warning",
        },
    ],
}


class SolutionPhaseGateService:
    """Validates phase gate requirements before allowing phase transitions."""

    def check_gate(self, solution_id, phase_letter):
        """
        Check phase gate requirements for the given phase.

        Args:
            solution_id: ID of the solution to check
            phase_letter: Current phase letter (A-H) — checks what is needed
                          to complete *this* phase and advance to the next.

        Returns:
            {
                'can_advance': bool,
                'phase': str,
                'next_phase': str | None,
                'critical_failures': [{'check': str, 'label': str, 'actual': int, 'required': int}],
                'warnings': [{'check': str, 'label': str, 'actual': int, 'required': int}],
                'passed': [{'check': str, 'label': str, 'actual': int, 'required': int}],
                'summary': {'total': int, 'passed_count': int, 'failed_count': int, 'warning_count': int}
            }
        """
        solution = Solution.query.get_or_404(solution_id)
        phase_letter = normalize_phase_code(phase_letter, valid_codes=PHASE_ORDER)

        checks = GATE_DEFINITIONS.get(phase_letter, [])
        idx = PHASE_ORDER.index(phase_letter)
        next_phase = PHASE_ORDER[idx + 1] if idx < len(PHASE_ORDER) - 1 else None

        critical_failures = []
        warnings = []
        passed = []

        for check_def in checks:
            actual = check_def["counter"](solution)
            item = {
                "check": check_def["key"],
                "label": check_def["label"],
                "actual": actual,
                "required": check_def["min"],
            }
            if actual >= check_def["min"]:
                passed.append(item)
            elif check_def["severity"] == "critical":
                critical_failures.append(item)
            else:
                warnings.append(item)

        total = len(checks)
        passed_count = len(passed)
        failed_count = len(critical_failures)
        warning_count = len(warnings)

        return {
            "can_advance": failed_count == 0,
            "phase": phase_letter,
            "phase_label": PHASE_LABELS.get(phase_letter, "Unknown"),
            "next_phase": next_phase,
            "next_phase_label": PHASE_LABELS.get(next_phase, "Complete") if next_phase else "Complete",
            "critical_failures": critical_failures,
            "warnings": warnings,
            "passed": passed,
            "summary": {
                "total": total,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "warning_count": warning_count,
            },
        }

    def get_gate_checklist(self, solution_id):
        """
        Get full gate checklist for the solution's current phase.

        Returns the same structure as check_gate() but uses the solution's
        current adm_phase automatically.
        """
        solution = Solution.query.get_or_404(solution_id)
        current_phase = normalize_phase_code(solution.adm_phase, valid_codes=PHASE_ORDER)
        return self.check_gate(solution_id, current_phase)

    def get_all_phases_status(self, solution_id):
        """
        Get gate status for ALL phases (for overview display).

        Returns list of {phase, phase_label, status, summary} where status
        is 'completed', 'current', or 'upcoming'.
        """
        solution = Solution.query.get_or_404(solution_id)
        current_phase = normalize_phase_code(solution.adm_phase, valid_codes=PHASE_ORDER)
        completed_phases = solution.adm_phases_completed

        results = []
        for i, letter in enumerate(PHASE_ORDER):
            gate_result = self.check_gate(solution_id, letter)
            if letter in completed_phases:
                status = "completed"
            elif letter == current_phase:
                status = "current"
            else:
                status = "upcoming"

            results.append({
                "phase": letter,
                "phase_label": PHASE_LABELS.get(letter, "Unknown"),
                "status": status,
                "can_advance": gate_result["can_advance"],
                "summary": gate_result["summary"],
            })

        return results
