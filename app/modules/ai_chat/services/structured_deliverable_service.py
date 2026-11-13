"""
-> app.modules.ai_chat.services

Structured Deliverable Service (AIC-010)

Wires existing architecture services into the AI chat pipeline as a
generator of structured, persistable deliverables. Each method returns
a typed Dict so routes can return it directly as JSON.

Deliverables:
  - Solution Analysis      (SolutionArchitectOrchestrator)
  - SAD Sections           (SADAutoPopulationService — sections 03, 04, 06)
  - Architecture Diagrams  (VisualGenerationService)
  - Implementation Roadmap (RoadmapGenerator)
  - Risk Register          (NEW — creates RiskSnapshot records)
  - Org Impact Assessment  (NEW — creates SolutionOrgImpact records)
  - Benefit Baseline       (NEW — creates SolutionBenefitRealization records)
  - Feasibility Review     (NEW — creates SolutionFeasibilityReview records)
  - Full Package           (orchestrates all of the above)
"""
from app.services.archimate_backbone import sync_archimate_element
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app import db

logger = logging.getLogger(__name__)


def _load_requirements_context(solution_id) -> List[Dict[str, Any]]:
    """Load SolutionRequirement records for a solution as a plain-dict list.

    Raises on a load failure rather than returning []. Every caller is already
    inside a handler that logs with a traceback and answers
    ``{"success": False, "error": ...}``; swallowing the error here instead put
    an empty list into the deliverable's ``requirements`` field, and made
    ``generate_test_cases`` report the specific, false claim "No requirements
    with acceptance criteria found".
    """
    if not solution_id:
        return []
    from app.models.solution_architect_models import SolutionRequirement
    requirements = SolutionRequirement.query.filter(
        SolutionRequirement.solution_id == solution_id,
        SolutionRequirement.deleted_at == None,  # noqa: E711
    ).all()
    return [
        {
            "name": r.requirement_name,
            "type": getattr(r, "requirement_type", None),
            "priority": getattr(r, "moscow_priority", None),
            "description": r.description,
            "acceptance_criteria": r.acceptance_criteria,
        }
        for r in requirements
    ]


class StructuredDeliverableService:
    """
    Generates structured architecture deliverables from natural language
    problem descriptions or solution IDs.

    All methods return ``{"success": bool, "deliverable_type": str, ...}``
    so they can be serialised directly as JSON API responses.
    """

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id

    # ------------------------------------------------------------------
    # 1. Solution Analysis — Buy / Build / Reuse + vendor options + gaps
    # ------------------------------------------------------------------

    def generate_solution_analysis(
        self,
        problem_description: str,
        capability_id: Optional[int] = None,
        budget_min: Optional[float] = None,
        budget_max: Optional[float] = None,
        timeline_months: Optional[int] = None,
        user_count: Optional[int] = None,
        is_critical: bool = False,
        organization_size: str = "enterprise",
        industry_vertical: str = "",
        existing_constraints: Optional[List[str]] = None,
        compliance_requirements: Optional[List[str]] = None,
        *,
        show_costs: bool,
    ) -> Dict[str, Any]:
        """Run Buy/Build/Reuse analysis and return ranked recommendations."""
        try:
            from app.modules.architecture.services.solution_architect_orchestrator import (
                SolutionArchitectOrchestrator,
            )

            orchestrator = SolutionArchitectOrchestrator()
            result = orchestrator.analyze_problem(
                problem_description=problem_description,
                capability_id=capability_id,
                budget_min=Decimal(str(budget_min)) if budget_min is not None else None,
                budget_max=Decimal(str(budget_max)) if budget_max is not None else None,
                timeline_months=timeline_months,
                user_count=user_count,
                is_critical=is_critical,
                organization_size=organization_size,
                industry_vertical=industry_vertical,
                existing_constraints=existing_constraints or [],
                compliance_requirements=compliance_requirements or [],
                show_costs=show_costs,
            )
            result["deliverable_type"] = "solution_analysis"
            return result
        except Exception as e:
            logger.error(f"generate_solution_analysis error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "solution_analysis", "error": str(e)}

    # ------------------------------------------------------------------
    # 2. SAD Sections — auto-populate sections 03 (App Arch), 04 (Data), 06 (Integration)
    # ------------------------------------------------------------------

    def generate_sad_sections(self, solution_id: int) -> Dict[str, Any]:
        """Auto-populate SAD phase-C sections for an existing solution."""
        try:
            from app.services.sad_auto_population_service import SADAutoPopulationService

            requirements_context = _load_requirements_context(solution_id)

            svc = SADAutoPopulationService()
            sections = svc.draft_phase_c_sections(solution_id)
            return {
                "success": True,
                "deliverable_type": "sad_sections",
                "solution_id": solution_id,
                "sections": sections,
                "requirements": requirements_context,
                "generated_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"generate_sad_sections error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "sad_sections", "error": str(e)}

    # ------------------------------------------------------------------
    # 3. Architecture Diagram — Mermaid / PlantUML visual output
    # ------------------------------------------------------------------

    def generate_visual(
        self,
        viz_type: str = "archimate_diagram",
        output_format: str = "mermaid",
        context: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate a visual diagram (ArchiMate, heat map, roadmap, dependency graph, etc.)."""
        try:
            from app.services.ai_chat_extensions import VisualGenerationService

            svc = VisualGenerationService()
            ctx = context or {}
            result = svc.generate_visualization(
                viz_type=viz_type,
                context=ctx,
                output_format=output_format,
                options=options or {},
            )
            result["deliverable_type"] = "visual"

            # Persist a SavedDiagram in the ArchiMate Composer when a solution_id
            # is in context and the solution has linked ArchiMate elements.
            solution_id = ctx.get("solution_id")
            if result.get("success") and solution_id:
                try:
                    from app.models.solution_models import SolutionArchiMateElement
                    from app.services.archimate_composer_service import (
                        create_diagram, full_composer_url,
                    )
                    el_ids = [
                        r.archimate_element_id
                        for r in SolutionArchiMateElement.query.filter_by(
                            solution_id=solution_id
                        ).all()
                        if r.archimate_element_id
                    ]
                    if el_ids:
                        diag_name = f"AI Visual — {viz_type} (Sol-{solution_id})"
                        rel = create_diagram(
                            el_ids[:30],  # cap at 30 to keep diagram readable
                            name=diag_name,
                            created_by=self.user_id,
                            solution_id=solution_id,
                        )
                        if rel:
                            result["composer_url"] = full_composer_url(rel)
                            result["composer_relative_url"] = rel
                except Exception as exc:
                    logger.debug("generate_visual: composer persist skipped: %s", exc)

            return result
        except Exception as e:
            logger.error(f"generate_visual error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "visual", "error": str(e)}

    # ------------------------------------------------------------------
    # 4. Implementation Roadmap — work packages + plateaus + Gantt
    # ------------------------------------------------------------------

    def generate_roadmap(
        self,
        gap_ids: Optional[List[int]] = None,
        architecture_id: Optional[int] = None,
        timeline_months: int = 18,
        priority_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate an ArchiMate Implementation & Migration roadmap from capability gaps."""
        try:
            from app.modules.architecture.services.roadmap_generator import RoadmapGenerator

            gen = RoadmapGenerator()
            result = gen.generate_roadmap_from_gaps(
                gap_ids=gap_ids,
                architecture_id=architecture_id,
                priority_filter=priority_filter,
                include_plateaus=True,
                timeline_months=timeline_months,
            )
            result["deliverable_type"] = "roadmap"
            return result
        except Exception as e:
            logger.error(f"generate_roadmap error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "roadmap", "error": str(e)}

    # ------------------------------------------------------------------
    # 5. Risk Register — NEW: create RiskSnapshot records from analysis
    # ------------------------------------------------------------------

    def generate_risk_register(
        self,
        solution_id: int,
        analysis_result: Optional[Dict[str, Any]] = None,
        risks: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Derive and persist a risk register for a solution.

        If ``analysis_result`` from generate_solution_analysis is provided,
        risks are extracted from its gap_analysis and recommendations.
        If ``risks`` list is provided directly, those records are used.
        """
        try:
            from app.models.solution_sad_models import RiskSnapshot

            requirements_context = _load_requirements_context(solution_id)
            created_ids = []
            risk_records = risks or []

            # Extract risks from SolutionArchitectOrchestrator output
            if analysis_result and not risk_records:
                gap_analysis = analysis_result.get("gap_analysis", {})
                for gap in gap_analysis.get("gaps", []):
                    risk_records.append({
                        "risk_name": f"Gap: {gap.get('name', 'Unknown')}",
                        "risk_category": "capability_gap",
                        "impact": gap.get("severity", "medium"),
                        "probability": "medium",
                        "risk_score": 0.5,
                        "trend": "stable",
                        "mitigation_status": "open",
                        "adm_phase": "C",
                    })
                for rec in analysis_result.get("recommendations", []):
                    if rec.get("risk_level") in ("high", "critical"):
                        risk_records.append({
                            "risk_name": f"Implementation risk: {rec.get('title', 'Unknown')}",
                            "risk_category": "implementation",
                            "impact": rec.get("risk_level", "medium"),
                            "probability": "medium",
                            "risk_score": 0.6,
                            "trend": "stable",
                            "mitigation_status": "open",
                            "adm_phase": "F",
                        })

            # Derive risks from requirements (must-have / high-priority requirements as delivery risks)
            for req in requirements_context:
                if req.get("priority") in ("must_have", "M", "must"):
                    risk_records.append({
                        "risk_name": f"Requirement delivery risk: {(req.get('name') or 'Unknown')[:80]}",
                        "risk_category": "requirements",
                        "impact": "high",
                        "probability": "medium",
                        "risk_score": 0.6,
                        "trend": "stable",
                        "mitigation_status": "open",
                        "adm_phase": "B",
                    })

            for r in risk_records:
                snap = RiskSnapshot(
                    solution_id=solution_id,
                    risk_name=r.get("risk_name", "Unnamed Risk"),
                    risk_category=r.get("risk_category", "general"),
                    adm_phase=r.get("adm_phase", ""),
                    impact=r.get("impact", "medium"),
                    probability=r.get("probability", "medium"),
                    risk_score=r.get("risk_score", 0.5),
                    trend=r.get("trend", "stable"),
                    mitigation_status=r.get("mitigation_status", "open"),
                    snapshot_date=date.today(),
                )
                db.session.add(snap)
                db.session.flush()
                created_ids.append(snap.id)

            db.session.commit()
            return {
                "success": True,
                "deliverable_type": "risk_register",
                "solution_id": solution_id,
                "risks_created": len(created_ids),
                "risk_ids": created_ids,
                "requirements": requirements_context,
                "generated_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"generate_risk_register error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "risk_register", "error": str(e)}

    # ------------------------------------------------------------------
    # 6. Org Impact Assessment — NEW: create SolutionOrgImpact records
    # ------------------------------------------------------------------

    def generate_org_impact(
        self,
        solution_id: int,
        impact_areas: Optional[List[Dict[str, Any]]] = None,
        analysis_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Derive and persist an organisational impact assessment.

        Generates default impact areas (People, Process, Technology, Culture)
        if none are provided explicitly.
        """
        try:
            from app.models.solution_sad_models import SolutionOrgImpact

            requirements_context = _load_requirements_context(solution_id)
            created_ids = []
            areas = impact_areas or []

            if not areas:
                # Default org impact areas inferred from analysis
                context = analysis_result or {}
                context["requirements"] = requirements_context
                areas = [
                    {
                        "impact_area": "People",
                        "description": "Workforce capability changes required for the solution",
                        "current_state": "Existing skill set",
                        "target_state": "Upskilled for " + context.get("problem_description", "new solution")[:80],
                        "reskilling_required": True,
                        "change_readiness": "medium",
                        "timeline_months": context.get("timeline_months", 12),
                    },
                    {
                        "impact_area": "Process",
                        "description": "Business process redesign required",
                        "current_state": "Current manual or legacy processes",
                        "target_state": "Automated / optimised target processes",
                        "reskilling_required": False,
                        "change_readiness": "medium",
                        "timeline_months": context.get("timeline_months", 12),
                    },
                    {
                        "impact_area": "Technology",
                        "description": "Technology platform and integration changes",
                        "current_state": "Existing technology stack",
                        "target_state": "Target architecture per solution design",
                        "reskilling_required": True,
                        "change_readiness": "high",
                        "timeline_months": context.get("timeline_months", 12),
                    },
                    {
                        "impact_area": "Culture",
                        "description": "Organisational culture and working practice shifts",
                        "current_state": "Current operating model",
                        "target_state": "Digitally enabled operating model",
                        "reskilling_required": False,
                        "change_readiness": "low",
                        "timeline_months": context.get("timeline_months", 18),
                    },
                ]

            for a in areas:
                impact = SolutionOrgImpact(
                    solution_id=solution_id,
                    impact_area=a.get("impact_area", "General"),
                    description=a.get("description", ""),
                    current_state=a.get("current_state", ""),
                    target_state=a.get("target_state", ""),
                    reskilling_required=a.get("reskilling_required", False),
                    change_readiness=a.get("change_readiness", "medium"),
                    timeline_months=a.get("timeline_months", 12),
                )
                db.session.add(impact)
                db.session.flush()
                created_ids.append(impact.id)

            db.session.commit()
            return {
                "success": True,
                "deliverable_type": "org_impact",
                "solution_id": solution_id,
                "impacts_created": len(created_ids),
                "impact_ids": created_ids,
                "requirements": requirements_context,
                "generated_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"generate_org_impact error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "org_impact", "error": str(e)}

    # ------------------------------------------------------------------
    # 7. Benefit Baseline — NEW: create SolutionBenefitRealization records
    # ------------------------------------------------------------------

    def generate_benefit_baseline(
        self,
        solution_id: int,
        benefits: Optional[List[Dict[str, Any]]] = None,
        analysis_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create benefit realization baseline records for a solution.

        Generates standard benefit categories if none are provided.
        """
        try:
            from app.models.solution_sad_models import SolutionBenefitRealization

            requirements_context = _load_requirements_context(solution_id)
            created_ids = []
            benefit_list = benefits or []

            if not benefit_list:
                benefit_list = [
                    {"benefit_name": "Cost Reduction", "benefit_type": "financial", "metric_name": "Total Cost of Ownership", "baseline_value": 0.0, "target_value": None, "status": "baseline_set", "measurement_frequency": "quarterly"},
                    {"benefit_name": "Productivity Improvement", "benefit_type": "operational", "metric_name": "Process Cycle Time", "baseline_value": 0.0, "target_value": None, "status": "baseline_set", "measurement_frequency": "monthly"},
                    {"benefit_name": "User Adoption", "benefit_type": "operational", "metric_name": "Active Users %", "baseline_value": 0.0, "target_value": 80.0, "status": "baseline_set", "measurement_frequency": "monthly"},
                    {"benefit_name": "Risk Reduction", "benefit_type": "risk", "metric_name": "Critical Risk Count", "baseline_value": 0.0, "target_value": None, "status": "baseline_set", "measurement_frequency": "quarterly"},
                ]

            for b in benefit_list:
                record = SolutionBenefitRealization(
                    solution_id=solution_id,
                    benefit_name=b.get("benefit_name", "Unnamed Benefit"),
                    benefit_type=b.get("benefit_type", "operational"),
                    metric_name=b.get("metric_name", ""),
                    baseline_value=b.get("baseline_value", 0.0),
                    baseline_date=date.today(),
                    target_value=b.get("target_value"),
                    status=b.get("status", "baseline_set"),
                    measurement_frequency=b.get("measurement_frequency", "quarterly"),
                )
                db.session.add(record)
                db.session.flush()
                created_ids.append(record.id)

            db.session.commit()
            return {
                "success": True,
                "deliverable_type": "benefit_baseline",
                "solution_id": solution_id,
                "benefits_created": len(created_ids),
                "benefit_ids": created_ids,
                "requirements": requirements_context,
                "generated_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"generate_benefit_baseline error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "benefit_baseline", "error": str(e)}

    # ------------------------------------------------------------------
    # 8. Feasibility Review — NEW: create SolutionFeasibilityReview record
    # ------------------------------------------------------------------

    def generate_feasibility_review(
        self,
        solution_id: int,
        analysis_result: Optional[Dict[str, Any]] = None,
        review_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create an initial feasibility review record for a solution.

        Derives feasibility from SolutionArchitectOrchestrator output
        when review_data is not explicitly provided.
        """
        try:
            from app.models.solution_sad_models import SolutionFeasibilityReview

            requirements_context = _load_requirements_context(solution_id)
            data = review_data or {}

            # Infer feasibility from analysis result if available
            feasible = data.get("feasible", True)
            confidence_level = data.get("confidence_level", "medium")
            technical_risks = data.get("technical_risks", "")
            mitigation_plan = data.get("mitigation_plan", "")
            recommendation = data.get("recommendation", "proceed_with_conditions")
            constraints_violated = data.get("constraints_violated", [])

            if analysis_result and not review_data:
                recs = analysis_result.get("recommendations", [])
                high_risk = any(r.get("risk_level") in ("high", "critical") for r in recs)
                feasible = not high_risk or bool(recs)
                confidence_level = "low" if high_risk else "medium"
                technical_risks = "; ".join(
                    r.get("title", "") for r in recs if r.get("risk_level") in ("high", "critical")
                )[:500]
                recommendation = "proceed_with_conditions" if high_risk else "proceed"

            review = SolutionFeasibilityReview(
                solution_id=solution_id,
                review_phase=data.get("review_phase", "A"),
                review_type=data.get("review_type", "initial"),
                reviewer_id=self.user_id,
                reviewed_at=datetime.utcnow(),
                feasible=feasible,
                confidence_level=confidence_level,
                constraints_violated=constraints_violated,
                technical_risks=technical_risks,
                mitigation_plan=mitigation_plan,
                recommendation=recommendation,
            )
            db.session.add(review)
            db.session.commit()

            return {
                "success": True,
                "deliverable_type": "feasibility_review",
                "solution_id": solution_id,
                "review_id": review.id,
                "feasible": feasible,
                "confidence_level": confidence_level,
                "recommendation": recommendation,
                "requirements": requirements_context,
                "generated_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"generate_feasibility_review error: {e}", exc_info=True)
            return {"success": False, "deliverable_type": "feasibility_review", "error": str(e)}

    # ------------------------------------------------------------------
    # 9. Requirements Backlog — LLM-generated SolutionRequirement records
    # ------------------------------------------------------------------

    def generate_requirements(
        self,
        solution_id: int,
        capability_id: Optional[int] = None,
        count: int = 5,
    ) -> Dict[str, Any]:
        """Generate requirements for a solution using LLM."""
        import json
        import re

        try:
            from app.models.solution_architect_models import Solution, SolutionRequirement

            solution = Solution.query.get(solution_id)
            if not solution:
                return {"success": False, "error": "Solution not found", "deliverable_type": "requirements"}

            from app.services.llm_service import LLMService

            llm = LLMService()
            prompt = (
                f"Generate {count} functional requirements for the solution: '{solution.name}'.\n"
                f"Description: {solution.description or 'N/A'}\n\n"
                f"For each requirement, provide:\n"
                f"1. requirement_name (EARS format: 'The system shall...')\n"
                f"2. description (1-2 sentences)\n"
                f"3. requirement_type (functional/non-functional/constraint)\n"
                f"4. moscow_priority (MUST/SHOULD/COULD/WONT)\n\n"
                f"Return as a JSON array of objects with these exact keys."
            )

            result = llm.generate_from_prompt(prompt=prompt, use_cache=False)
            content = result.get("content") or result.get("text") or result.get("response") or ""

            json_match = re.search(r"\[.*?\]", content, re.DOTALL)
            requirements_data = []
            if json_match:
                try:
                    requirements_data = json.loads(json_match.group())
                except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                    logger.exception("Failed to JSON parsing")
                    pass
            # Accumulator for the rows created below. Its initialisation had been
            # removed while `created.append(...)`, `len(created)` and the closing
            # comprehension over it all remained, so this raised NameError on the
            # first requirement.
            created = []
            for req_data in requirements_data:
                req = SolutionRequirement(
                    solution_id=solution_id,
                    capability_id=capability_id,
                    name=req_data.get("requirement_name", "").strip(),
                    description=req_data.get("description"),
                    requirement_type=req_data.get("requirement_type", "functional"),
                    moscow_priority=req_data.get("moscow_priority", "SHOULD"),
                )
                db.session.add(req)
                sync_archimate_element(req)
                created.append(req)
            db.session.commit()

            return {
                "success": True,
                "deliverable_type": "requirements",
                "solution_id": solution_id,
                "count": len(created),
                "requirements": [
                    {
                        "id": r.id,
                        "requirement_name": r.name,
                        "requirement_type": r.requirement_type,
                        "moscow_priority": r.moscow_priority,
                    }
                    for r in created
                ],
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"generate_requirements error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "deliverable_type": "requirements"}

    # ------------------------------------------------------------------
    # 10. Test Cases — BDD scenarios from acceptance criteria
    # ------------------------------------------------------------------

    def generate_test_cases(self, solution_id: int, options: dict = None) -> dict:
        """Generate BDD test cases from acceptance criteria for a solution's requirements."""
        try:
            from app.models.solution_architect_models import Solution

            solution = Solution.query.get(solution_id)
            if not solution:
                return {"success": False, "error": "Solution not found", "deliverable_type": "test-cases"}

            reqs = _load_requirements_context(solution_id)
            reqs_with_ac = [r for r in reqs if r.get("acceptance_criteria")]
            if not reqs_with_ac:
                return {"success": False, "error": "No requirements with acceptance criteria found", "deliverable_type": "test-cases"}

            test_cases = []
            for req in reqs_with_ac:
                test_cases.append({
                    "requirement": req["name"],
                    "bdd_scenario": (
                        f"Given the system is in a valid state\n"
                        f"When {req['name']}\n"
                        f"Then {req['acceptance_criteria']}"
                    ),
                })

            return {
                "success": True,
                "deliverable_type": "test-cases",
                "solution_id": solution_id,
                "solution_name": solution.name,
                "test_cases": test_cases,
                "count": len(test_cases),
            }
        except Exception as e:
            logger.error(f"generate_test_cases error: {e}", exc_info=True)
            return {"success": False, "error": str(e), "deliverable_type": "test-cases"}

    # ------------------------------------------------------------------
    # 11. Full Package — orchestrates all deliverables for a solution
    # ------------------------------------------------------------------

    def generate_full_package(
        self,
        solution_id: int,
        problem_description: str,
        capability_id: Optional[int] = None,
        budget_min: Optional[float] = None,
        budget_max: Optional[float] = None,
        timeline_months: int = 12,
        organization_size: str = "enterprise",
        industry_vertical: str = "",
        existing_constraints: Optional[List[str]] = None,
        compliance_requirements: Optional[List[str]] = None,
        *,
        show_costs: bool,
    ) -> Dict[str, Any]:
        """
        Generate the complete set of structured deliverables for a solution:
        solution analysis, SAD sections, risk register, org impact,
        benefit baseline, feasibility review, and an ArchiMate diagram.
        """
        results: Dict[str, Any] = {
            "success": True,
            "deliverable_type": "full_package",
            "solution_id": solution_id,
            "generated_at": datetime.utcnow().isoformat(),
            "deliverables": {},
        }

        # 1. Solution analysis (Buy/Build/Reuse)
        analysis = self.generate_solution_analysis(
            problem_description=problem_description,
            capability_id=capability_id,
            budget_min=budget_min,
            budget_max=budget_max,
            timeline_months=timeline_months,
            organization_size=organization_size,
            industry_vertical=industry_vertical,
            existing_constraints=existing_constraints,
            compliance_requirements=compliance_requirements,
            show_costs=show_costs,
        )
        results["deliverables"]["solution_analysis"] = analysis

        # 2. SAD auto-population
        results["deliverables"]["sad_sections"] = self.generate_sad_sections(solution_id)

        # 3. ArchiMate diagram (overview)
        results["deliverables"]["visual"] = self.generate_visual(
            viz_type="archimate_diagram", output_format="mermaid"
        )

        # 4. Roadmap
        results["deliverables"]["roadmap"] = self.generate_roadmap(
            timeline_months=timeline_months
        )

        # 5. Risk register (derived from analysis)
        results["deliverables"]["risk_register"] = self.generate_risk_register(
            solution_id=solution_id, analysis_result=analysis
        )

        # 6. Org impact
        results["deliverables"]["org_impact"] = self.generate_org_impact(
            solution_id=solution_id, analysis_result={"problem_description": problem_description, "timeline_months": timeline_months}
        )

        # 7. Benefit baseline
        results["deliverables"]["benefit_baseline"] = self.generate_benefit_baseline(
            solution_id=solution_id, analysis_result=analysis
        )

        # 8. Feasibility review
        results["deliverables"]["feasibility_review"] = self.generate_feasibility_review(
            solution_id=solution_id, analysis_result=analysis
        )

        # Summarise
        failures = [k for k, v in results["deliverables"].items() if not v.get("success")]
        if failures:
            results["warnings"] = f"Some deliverables failed: {failures}"
        results["deliverables_generated"] = len(results["deliverables"]) - len(failures)
        results["deliverables_failed"] = len(failures)

        return results

    # ------------------------------------------------------------------
    # Stub fallback (AIC-010)
    # ------------------------------------------------------------------

    @staticmethod
    def _stub(deliverable_type: str, solution_id: Optional[int] = None) -> Dict[str, Any]:
        """Return a stub response when the underlying service is unavailable."""
        return {
            "success": False,
            "deliverable_type": deliverable_type,
            "solution_id": solution_id,
            "status": "stub",
            "message": f"{deliverable_type} service not available — configure LLM provider to enable",
        }
