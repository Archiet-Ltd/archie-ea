"""Wizard Context Assembler — shared context for all wizard AI layers.

Extends SolutionContextAssembler with wizard-specific fields:
journey_state, codegen genome status, budget/timeline, constraints,
and capability summaries needed by Copilot, Auto-Complete, Quality Gate,
and Genome Perfector layers.

Pure database querying — no AI.
"""

import logging
from typing import Any, Dict, List

from app import db

logger = logging.getLogger(__name__)


class WizardContextAssembler:
    """Assemble solution + wizard context for AI enhancement layers."""

    def assemble(self, solution_id: int) -> Dict[str, Any]:
        """Gather full context for a wizard AI layer call.

        Returns a dict safe for JSON serialization and LLM prompt injection.
        Includes: solution metadata, problem statement, capabilities summary,
        ArchiMate elements summary, budget/timeline, constraints, journey_state,
        and genome status.
        """
        from app.models.solution_models import Solution

        solution = Solution.query.get(solution_id)
        if solution is None:
            logger.warning("Solution %d not found", solution_id)
            return self._empty_context()

        journey_state = solution.journey_state or {}

        return {
            "solution_id": solution.id,
            "solution_name": getattr(solution, "name", ""),
            "problem_statement": self._extract_problem(solution, journey_state),
            "business_domain": getattr(solution, "business_domain", "") or "",
            "solution_type": getattr(solution, "solution_type", "") or "",
            "organization_size": journey_state.get("organization_size", ""),
            "budget_range": self._extract_budget(journey_state),
            "timeline_months": journey_state.get("timeline_months"),
            "constraints": self._extract_constraints(journey_state),
            "capabilities_summary": self._summarize_capabilities(solution_id, journey_state),
            "elements_summary": self._summarize_elements(solution_id),
            "build_buy_summary": self._summarize_build_buy(solution_id),
            "journey_state": journey_state,
            "genome_status": self._get_genome_status(solution_id),
            "section_narratives": solution.section_narratives or {},
            "section_scores": solution.section_scores or {},
        }

    def _extract_problem(self, solution, journey_state: dict) -> str:
        """Get the best problem statement available."""
        # Enriched brief from Step 1 is best
        enriched = journey_state.get("enriched_brief", "")
        if enriched and len(enriched) > 20:
            return enriched
        # Fall back to solution description
        return getattr(solution, "description", "") or ""

    def _extract_budget(self, journey_state: dict) -> str:
        """Extract budget range as readable string."""
        budget_min = journey_state.get("budget_min")
        budget_max = journey_state.get("budget_max")
        if budget_min and budget_max:
            return f"{budget_min}-{budget_max}"
        if budget_min:
            return f"{budget_min}+"
        if budget_max:
            return f"up to {budget_max}"
        return ""

    def _extract_constraints(self, journey_state: dict) -> List[str]:
        """Extract constraints from structured intake or journey state."""
        constraints = []
        intake = journey_state.get("structured_intake", {})
        if isinstance(intake, dict):
            for key in ("compliance_requirements", "technology_constraints",
                        "integration_constraints", "security_requirements"):
                val = intake.get(key)
                if val:
                    if isinstance(val, list):
                        constraints.extend(val)
                    else:
                        constraints.append(str(val))
        return constraints

    def _summarize_capabilities(self, solution_id: int, journey_state: dict) -> List[Dict[str, Any]]:
        """Summarize capabilities from journey state or DB."""
        # Try journey_state first (accepted capabilities from Step 2)
        accepted = journey_state.get("accepted_capabilities", [])
        if accepted:
            return [
                {
                    "name": cap.get("name", ""),
                    "description": cap.get("description", ""),
                    "maturity_current": cap.get("maturity_current"),
                    "maturity_target": cap.get("maturity_target"),
                    "strategic_importance": cap.get("strategic_importance"),
                    "acm_domain": cap.get("acm_domain", ""),
                }
                for cap in accepted
                if isinstance(cap, dict)
            ]

        # Fall back to DB junction
        from app.utils.tenant_sql import org_scope
        _org_clause, _org_params = org_scope(prefix="bcm.")
        rows = db.session.execute(db.text(
            "SELECT bcm.capability_id "
            "FROM solution_capability_mappings bcm "
            "WHERE bcm.solution_id = :sid" + _org_clause
        ), {"sid": solution_id, **_org_params}).fetchall()
        cap_ids = [r[0] for r in rows]
        if not cap_ids:
            return []

        from app.models.business_capabilities import BusinessCapability
        caps = BusinessCapability.query.filter(
            BusinessCapability.id.in_(cap_ids)
        ).all()
        return [
            {"name": c.name, "description": getattr(c, "description", "") or ""}
            for c in caps
        ]

    def _summarize_elements(self, solution_id: int) -> List[Dict[str, str]]:
        """Summarize linked ArchiMate elements."""
        from app.models.archimate_core import ArchiMateElement

        from app.utils.tenant_sql import org_scope
        _org_clause, _org_params = org_scope()
        rows = db.session.execute(db.text(
            "SELECT element_id FROM solution_archimate_elements "
            "WHERE solution_id = :sid" + _org_clause
        ), {"sid": solution_id, **_org_params}).fetchall()
        element_ids = [r[0] for r in rows if r[0]]
        if not element_ids:
            return []

        elements = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(element_ids)
        ).all()
        return [
            {
                "name": e.name,
                "type": e.type,
                "layer": e.layer,
                "description": getattr(e, "description", "") or "",
            }
            for e in elements
        ]

    def _summarize_build_buy(self, solution_id: int) -> List[Dict[str, str]]:
        """Summarize build/buy decisions from ArchiMate element properties."""
        from app.models.archimate_core import ArchiMateElement

        from app.utils.tenant_sql import org_scope
        _org_clause, _org_params = org_scope()
        rows = db.session.execute(db.text(
            "SELECT element_id FROM solution_archimate_elements "
            "WHERE solution_id = :sid" + _org_clause
        ), {"sid": solution_id, **_org_params}).fetchall()
        element_ids = [r[0] for r in rows if r[0]]
        if not element_ids:
            return []

        elements = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(element_ids)
        ).all()

        decisions = []
        for e in elements:
            props = getattr(e, "properties", None) or {}
            if isinstance(props, dict) and props.get("build_buy"):
                decisions.append({
                    "element": e.name,
                    "type": e.type,
                    "build_buy": props["build_buy"],
                    "rationale": props.get("build_buy_rationale", ""),
                })
        return decisions

    def _get_genome_status(self, solution_id: int) -> Dict[str, Any]:
        """Check if a genome exists and its quality score."""
        try:
            from app.modules.codegen.models import CodegenGeneration
            gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
            if gen is None:
                return {"exists": False, "quality_score": None}
            return {
                "exists": getattr(gen, "genome", None) is not None,
                "quality_score": getattr(gen, "genome_quality_score", None),
                "language": getattr(gen, "language", None),
                "mode": getattr(gen, "mode", None),
            }
        except Exception:
            return {"exists": False, "quality_score": None}

    def _empty_context(self) -> Dict[str, Any]:
        return {
            "solution_id": None,
            "solution_name": "",
            "problem_statement": "",
            "business_domain": "",
            "solution_type": "",
            "organization_size": "",
            "budget_range": "",
            "timeline_months": None,
            "constraints": [],
            "capabilities_summary": [],
            "elements_summary": [],
            "build_buy_summary": [],
            "journey_state": {},
            "genome_status": {"exists": False, "quality_score": None},
            "section_narratives": {},
            "section_scores": {},
        }
