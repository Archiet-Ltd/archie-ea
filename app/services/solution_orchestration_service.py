"""
SolutionOrchestrationService -- Central glue layer for EA/SA platform wiring.

Connects recommendations to solutions, AI suggestions to DB records,
phase advancement to workflow triggers, and chat to solution state.
"""
from app.services.archimate_backbone import sync_archimate_element
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app import db

logger = logging.getLogger(__name__)

# Recommendation option_type -> Solution solution_type mapping
OPTION_TYPE_MAP = {
    "BUY": "Product",
    "BUILD": "Platform",
    "REUSE": "Service",
    "PARTNER": "Integration",
    "HYBRID": "Platform",
}


class SolutionOrchestrationService:
    """Central orchestration service for EA/SA platform wiring."""

    def accept_recommendation(
        self,
        session_id: int,
        recommendation_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """
        Accept an analysis recommendation and auto-create a Solution.

        Creates Solution with pre-populated fields from the recommendation
        and problem definition. Links vendor products (BUY), existing apps
        (REUSE), copies capability mappings, and creates risk records.

        Args:
            session_id: SolutionAnalysisSession ID
            recommendation_id: SolutionRecommendation ID to accept
            user_id: Current user ID

        Returns:
            {"success": True, "solution_id": int} or
            {"success": False, "error": str}
        """
        from app.models.solution_architect_models import (
            SolutionAnalysisSession,
            SolutionRecommendation,
        )
        from app.models.truly_missing_models import Solution

        try:
            # Load session and recommendation
            session = SolutionAnalysisSession.query.get(session_id)
            if not session:
                return {"success": False, "error": "Analysis session not found"}

            rec = SolutionRecommendation.query.get(recommendation_id)
            if not rec or rec.session_id != session_id:
                return {"success": False, "error": "Recommendation not found"}

            # Load problem definition for budget/timeline
            problem = session.problem_definition

            # Resolve option_type to an uppercase string label.
            # option_type is an Enum(RecommendationOptionType) whose .value
            # is lowercase (e.g. "buy"). Normalise to uppercase for the map.
            raw_option = rec.option_type
            if hasattr(raw_option, "value"):
                option_label = str(raw_option.value).upper()
            else:
                option_label = str(raw_option).upper()

            # Build solution name
            name = f"{session.name} - {option_label}"
            if len(name) > 255:
                name = name[:252] + "..."

            # Map option type to solution type
            solution_type = OPTION_TYPE_MAP.get(option_label, "Platform")

            # Calculate planned end date from timeline
            planned_end = None
            timeline = rec.timeline_months
            if not timeline and problem:
                timeline = problem.timeline_months
            if timeline:
                planned_end = datetime.utcnow() + timedelta(days=timeline * 30)

            # Create Solution
            solution = Solution(
                name=name,
                description=rec.justification or (session.description or ""),
                solution_type=solution_type,
                estimated_cost=rec.estimated_cost_min,
                planned_start_date=datetime.utcnow().date(),
                planned_end_date=planned_end.date() if planned_end else None,
                analysis_session_id=session.id,
                governance_status="draft",
                adm_phase="A",
                status="planned",
                created_by_id=user_id,
            )
            db.session.add(solution)
            db.session.flush()

            # Create risk records from recommendation risks via the canonical
            # risk register (app/services/risk_service.py) -- the one writer
            # -- instead of SolutionRisk directly, so the superseded store
            # gets no new row. risk_service.create_risk commits per call, so
            # (unlike the single transaction this loop used to share with the
            # Solution insert above) one malformed risk string can no longer
            # roll back the Solution or an already-created risk; it is
            # logged and skipped instead, the same per-item shape already
            # used by the solution risk CSV import.
            from app.modules.solutions_strategic.v2.routes.solution_routes import _level_to_int
            from app.services import risk_service

            risks_data = rec.risks or []
            risks_created = 0
            for risk_text in risks_data:
                if isinstance(risk_text, str) and risk_text.strip():
                    try:
                        text = risk_text.strip()
                        risk = risk_service.create_risk(
                            solution_id=solution.id,
                            title=text[:255],
                            description=text[:500],
                            likelihood=_level_to_int("medium"),
                            impact=_level_to_int("medium"),
                            owner=None,
                            mitigation_plan=None,
                        )
                        risk_service.add_risk_link(risk.id, "solution", solution.id)
                        risks_created += 1
                    except Exception as exc:
                        logger.warning(
                            "Error creating risk from recommendation %d: %s", rec.id, exc
                        )

            db.session.commit()

            logger.info(
                "Created solution %d from recommendation %d (session %d, type=%s)",
                solution.id,
                rec.id,
                session.id,
                option_label,
            )

            return {
                "success": True,
                "solution_id": solution.id,
                "solution_name": solution.name,
                "solution_type": solution_type,
                "risks_created": risks_created,
            }

        except Exception as e:
            db.session.rollback()
            logger.error("Error accepting recommendation: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def analyze_problem(self, session_id: int) -> Dict[str, Any]:
        """
        Run AI analysis on a problem definition to populate motivation layer.

        Creates SolutionDriver, SolutionGoal, SolutionRequirement,
        SolutionConstraint records from a single LLM call.
        """
        from app.models.solution_architect_models import (
            ConstraintType,
            DriverType,
            RequirementType,
            SolutionAnalysisSession,
            SolutionConstraint,
            SolutionDriver,
            SolutionGoal,
            SolutionRequirement,
        )

        try:
            session = SolutionAnalysisSession.query.get(session_id)
            if not session:
                return {"success": False, "error": "Session not found"}

            problem = session.problem_definition
            if not problem or not problem.problem_description:
                return {"success": False, "error": "Problem definition is required"}

            # Build prompt
            prompt = self._build_motivation_prompt(problem)

            # Call LLM
            from app.modules.ai_chat.services.llm_service_impl import LLMService

            llm = LLMService()
            raw_response = llm.generate_from_prompt(prompt=prompt, use_cache=False)

            # Parse JSON from response
            parsed = self._parse_json_from_llm(raw_response)
            if not parsed:
                return {"success": False, "error": "Failed to parse AI response"}

            # Create records
            counts = {
                "drivers_created": 0,
                "goals_created": 0,
                "requirements_created": 0,
                "constraints_created": 0,
                "principles_created": 0,
                "assessments_created": 0,
            }

            # Helper to safely resolve an enum member from a string key
            def _resolve_enum(enum_cls, raw_value, default):
                key = str(raw_value).upper()
                try:
                    return enum_cls[key]
                except KeyError:
                    return default

            for d in parsed.get("drivers", []):
                driver = SolutionDriver(
                    problem_id=problem.id,
                    name=str(d.get("name", ""))[:200],
                    description=str(d.get("description", ""))[:2000] or None,
                    driver_type=_resolve_enum(
                        DriverType, d.get("type", "INTERNAL"), DriverType.INTERNAL
                    ),
                    impact_level=min(int(d.get("impact", 3)), 5),
                    urgency=min(int(d.get("urgency", 3)), 5),
                    ai_generated=True,
                    ai_confidence=float(d.get("confidence", 0.7)),
                )
                db.session.add(driver)
                sync_archimate_element(driver)
                counts["drivers_created"] += 1

            for g in parsed.get("goals", []):
                goal = SolutionGoal(
                    problem_id=problem.id,
                    name=str(g.get("name", ""))[:200],
                    description=str(g.get("description", ""))[:2000] or None,
                    priority=min(int(g.get("priority", 3)), 5),
                    ai_generated=True,
                    ai_confidence=float(g.get("confidence", 0.7)),
                )
                db.session.add(goal)
                sync_archimate_element(goal)
                counts["goals_created"] += 1

            for r in parsed.get("requirements", []):
                req = SolutionRequirement(
                    problem_id=problem.id,
                    name=str(r.get("name", ""))[:200],
                    description=str(r.get("description", ""))[:2000] or "See name",
                    requirement_type=_resolve_enum(
                        RequirementType,
                        r.get("type", "FUNCTIONAL"),
                        RequirementType.FUNCTIONAL,
                    ),
                    priority=min(int(r.get("priority", 3)), 5),
                    is_mandatory=bool(r.get("mandatory", False)),
                    ai_generated=True,
                    ai_confidence=float(r.get("confidence", 0.7)),
                )
                db.session.add(req)
                sync_archimate_element(req)
                counts["requirements_created"] += 1

            for c in parsed.get("constraints", []):
                constraint = SolutionConstraint(
                    problem_id=problem.id,
                    name=str(c.get("name", ""))[:200],
                    description=str(c.get("description", ""))[:2000] or "See name",
                    constraint_type=_resolve_enum(
                        ConstraintType,
                        c.get("type", "TECHNICAL"),
                        ConstraintType.TECHNICAL,
                    ),
                    severity=min(int(c.get("severity", 3)), 5),
                    ai_generated=True,
                )
                db.session.add(constraint)
                sync_archimate_element(constraint)
                counts["constraints_created"] += 1

            db.session.commit()
            counts["success"] = True
            logger.info("Analyzed problem for session %d: %s", session_id, counts)
            return counts

        except Exception as e:
            db.session.rollback()
            logger.error("Error analyzing problem: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def apply_element_suggestions(
        self,
        solution_id: int,
        elements: list,
    ) -> Dict[str, Any]:
        """
        Apply AI-suggested ArchiMate elements to a solution.

        Creates SolutionArchiMateElement junction records for each suggested
        element. These serve as bookmarks that can later be linked to real
        ArchiMate records through the CRUD workflow.

        Args:
            solution_id: Solution ID to attach elements to
            elements: List of dicts with keys: layer, type, name, description

        Returns:
            {"success": True, "created": int} or error
        """
        from app.models.truly_missing_models import Solution, SolutionArchiMateElement

        try:
            solution = Solution.query.get(solution_id)
            if not solution:
                return {"success": False, "error": "Solution not found"}

            created = 0
            for elem in elements:
                layer = str(elem.get("layer", "business")).lower()
                str(elem.get("type", ""))
                name = str(elem.get("name", ""))[:255]

                if not name:
                    continue

                # Map layer to table name for polymorphic reference
                layer_table_map = {
                    "business": "business_processes",
                    "application": "application_components",
                    "technology": "technology_services",
                    "motivation": "solution_drivers",
                    "strategy": "business_capabilities",
                    "implementation": "implementation_components",
                }
                element_table = layer_table_map.get(layer, "business_processes")

                junction = SolutionArchiMateElement(
                    solution_id=solution.id,
                    layer_type=layer,
                    element_id=0,
                    element_table=element_table,
                    element_name=name,
                    notes=str(elem.get("description", ""))[:2000] or None,
                    is_new_element=True,
                )
                db.session.add(junction)
                created += 1

            db.session.commit()

            logger.info(
                "Applied %d element suggestions to solution %d",
                created,
                solution_id,
            )

            return {"success": True, "created": created}

        except Exception as e:
            db.session.rollback()
            logger.error("Error applying element suggestions: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def apply_requirement_suggestions(
        self,
        session_id: int,
        requirements: list,
    ) -> Dict[str, Any]:
        """
        Apply AI-suggested requirements to a session's problem definition.

        Creates SolutionRequirement records from the suggestions.

        Args:
            session_id: SolutionAnalysisSession ID
            requirements: List of dicts with keys: name, description, type, priority, mandatory

        Returns:
            {"success": True, "created": int} or error
        """
        from app.models.solution_architect_models import (
            RequirementType,
            SolutionAnalysisSession,
            SolutionRequirement,
        )

        try:
            session = SolutionAnalysisSession.query.get(session_id)
            if not session:
                return {"success": False, "error": "Session not found"}

            problem = session.problem_definition
            if not problem:
                return {"success": False, "error": "No problem definition for session"}

            def _resolve_enum(enum_cls, raw_value, default):
                key = str(raw_value).upper()
                try:
                    return enum_cls[key]
                except KeyError:
                    return default

            created = 0
            for r in requirements:
                name = str(r.get("name", ""))[:200]
                if not name:
                    continue

                req = SolutionRequirement(
                    problem_id=problem.id,
                    name=name,
                    description=str(r.get("description", ""))[:2000] or "See name",
                    requirement_type=_resolve_enum(
                        RequirementType,
                        r.get("type", "FUNCTIONAL"),
                        RequirementType.FUNCTIONAL,
                    ),
                    priority=min(int(r.get("priority", 3)), 5),
                    is_mandatory=bool(r.get("mandatory", False)),
                    ai_generated=True,
                    ai_confidence=float(r.get("confidence", 0.7)),
                )
                db.session.add(req)
                sync_archimate_element(req)
                created += 1

            db.session.commit()

            logger.info(
                "Applied %d requirement suggestions to session %d",
                created,
                session_id,
            )

            return {"success": True, "created": created}

        except Exception as e:
            db.session.rollback()
            logger.error("Error applying requirement suggestions: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def apply_roadmap_suggestions(
        self,
        solution_id: int,
        roadmap_items: list,
    ) -> Dict[str, Any]:
        """
        Apply AI-suggested roadmap items to a solution.

        Creates SolutionPlateau records representing transition
        architecture plateaus (TOGAF Phase F).

        Args:
            solution_id: Solution ID
            roadmap_items: List of dicts with keys: name, description, target_date, order

        Returns:
            {"success": True, "created": int} or error
        """
        from app.models.solution_lifecycle_models import SolutionPlateau
        from app.models.truly_missing_models import Solution

        try:
            solution = Solution.query.get(solution_id)
            if not solution:
                return {"success": False, "error": "Solution not found"}

            created = 0
            for idx, item in enumerate(roadmap_items):
                name = str(item.get("name", ""))[:255]
                if not name:
                    continue

                # Parse target_date if provided
                target_date = None
                raw_date = item.get("target_date")
                if raw_date:
                    try:
                        from datetime import datetime as dt_cls

                        target_date = dt_cls.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        pass

                plateau = SolutionPlateau(
                    solution_id=solution.id,
                    name=name,
                    description=str(item.get("description", ""))[:2000] or None,
                    target_date=target_date,
                    order=int(item.get("order", idx)),
                )
                db.session.add(plateau)
                created += 1

            db.session.commit()

            logger.info(
                "Applied %d roadmap items to solution %d",
                created,
                solution_id,
            )

            return {"success": True, "created": created}

        except Exception as e:
            db.session.rollback()
            logger.error("Error applying roadmap suggestions: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def populate_tco_from_vendor(
        self,
        solution_id: int,
        vendor_product_id: "Optional[int]",
        vendor_data: "Optional[Dict]" = None,
        user_count: int = 50,
    ) -> Dict[str, Any]:
        """
        Populate TCO line items from vendor product data.

        Creates 4 standard line items:
        - Software Licensing (recurring, year 1)
        - Implementation Services (non-recurring, year 1)
        - Annual Maintenance (recurring, year 1 at 18% of license)
        - Training (non-recurring, year 1 at 500/user)

        Args:
            solution_id: Solution ID
            vendor_product_id: Optional VendorProduct ID to load data from
            vendor_data: Optional dict with keys: price_range_min, pricing_model,
                         implementation_time_months
            user_count: Number of users for per-user pricing

        Returns:
            {"success": True, "line_items_created": int} or error
        """
        from app.models.solution_lifecycle_models import SolutionTCOItem
        from app.models.truly_missing_models import Solution

        try:
            solution = Solution.query.get(solution_id)
            if not solution:
                return {"success": False, "error": "Solution not found"}

            # Get pricing data
            base_price = 0
            impl_months = 3

            if vendor_data:
                base_price = float(vendor_data.get("price_range_min", 0))
                impl_months = int(vendor_data.get("implementation_time_months", 3))
            elif vendor_product_id:
                # Try loading from VendorProduct model
                try:
                    from app.models.vendor.vendor_organization import VendorProduct

                    product = VendorProduct.query.get(vendor_product_id)
                    if product:
                        base_price = float(getattr(product, "price_range_min", 0) or 0)
                        impl_months = int(
                            getattr(product, "implementation_time_months", 3) or 3
                        )
                except Exception as vendor_err:
                    logger.warning("Could not load VendorProduct %d: %s", vendor_product_id, vendor_err)

            if base_price <= 0:
                return {"success": False, "error": "No pricing data available"}

            items_created = 0
            pricing_model = str(vendor_data.get("pricing_model", "")) if vendor_data else ""

            # 1. Software Licensing (recurring, year 1)
            license_cost = (
                base_price * user_count if "per_user" in pricing_model else base_price
            )
            db.session.add(
                SolutionTCOItem(
                    solution_id=solution.id,
                    cost_category="Software Licensing",
                    is_recurring=True,
                    year=1,
                    amount=license_cost,
                    notes=(
                        f"Based on {user_count} users"
                        if "per_user" in pricing_model
                        else "Flat rate"
                    ),
                )
            )
            items_created += 1

            # 2. Implementation Services (non-recurring, year 1)
            impl_cost = base_price * impl_months * 0.5
            db.session.add(
                SolutionTCOItem(
                    solution_id=solution.id,
                    cost_category="Implementation Services",
                    is_recurring=False,
                    year=1,
                    amount=impl_cost,
                    notes=f"Estimated {impl_months}-month implementation",
                )
            )
            items_created += 1

            # 3. Annual Maintenance (recurring, year 1 at 18%)
            maintenance_cost = license_cost * 0.18
            db.session.add(
                SolutionTCOItem(
                    solution_id=solution.id,
                    cost_category="Annual Maintenance",
                    is_recurring=True,
                    year=1,
                    amount=maintenance_cost,
                    notes="18% of annual licensing",
                )
            )
            items_created += 1

            # 4. Training (non-recurring, year 1 at 500/user)
            training_cost = 500 * user_count
            db.session.add(
                SolutionTCOItem(
                    solution_id=solution.id,
                    cost_category="Training",
                    is_recurring=False,
                    year=1,
                    amount=training_cost,
                    notes=f"{user_count} users at 500/user",
                )
            )
            items_created += 1

            db.session.commit()

            logger.info(
                "Populated %d TCO items for solution %d",
                items_created,
                solution_id,
            )

            return {"success": True, "line_items_created": items_created}

        except Exception as e:
            db.session.rollback()
            logger.error("Error populating TCO: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def advance_phase(
        self,
        solution_id: int,
        user_id: int,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Advance a Solution through its ADM phase lifecycle.

        Validates phase-gate requirements (ArchiMate artifact minimums),
        stamps completion timestamp, advances to next phase, and optionally
        triggers a workflow and updates the kanban board.

        Args:
            solution_id: Solution ID to advance
            user_id: Current user ID performing the advancement
            force: If True, skip phase-gate validation (backward compat)

        Returns:
            {"success": True, "previous_phase": str, "new_phase": str,
             "validation": dict, "workflow_triggered": bool,
             "kanban_updated": bool}
            or
            {"success": False, "error": str, "validation": dict}
        """
        from app.models.truly_missing_models import Solution

        try:
            solution = Solution.query.get(solution_id)
            if not solution:
                return {"success": False, "error": "Solution not found", "validation": {}}

            current_phase = solution.adm_phase or "A"

            # Validate phase gate requirements
            validation = solution.validate_phase_gate(current_phase)

            if not force and not validation.get("valid", False):
                logger.warning(
                    "Phase gate validation failed for solution %d phase %s: %s",
                    solution_id,
                    current_phase,
                    validation.get("errors", []),
                )
                return {
                    "success": False,
                    "error": "Phase gate validation failed",
                    "validation": validation,
                }

            # Complete the current phase (stamps timestamp, advances adm_phase)
            result = solution.complete_adm_phase(current_phase, force=force)

            if not result.get("completed"):
                return {
                    "success": False,
                    "error": "Phase completion failed",
                    "validation": result,
                }

            new_phase = solution.adm_phase
            db.session.commit()

            logger.info(
                "Advanced solution %d from phase %s to %s (force=%s)",
                solution_id,
                current_phase,
                new_phase,
                force,
            )

            # Best-effort: trigger EA workflow for new phase
            workflow_triggered = False
            try:
                from app.models.workflow_models import EAWorkflowDefinition
                from app.services.ea_workflow_engine import EAWorkflowEngine

                # Find a workflow definition matching the new phase
                # Convention: workflow_code starts with ADM_PHASE_{letter}_
                prefix = f"ADM_PHASE_{new_phase}_"
                wf_def = EAWorkflowDefinition.query.filter(
                    EAWorkflowDefinition.workflow_code.like(f"{prefix}%"),
                    EAWorkflowDefinition.is_active.is_(True),
                ).first()

                # Fallback: match by adm_phase field
                if not wf_def:
                    wf_def = EAWorkflowDefinition.query.filter_by(
                        adm_phase=new_phase, is_active=True
                    ).first()

                if wf_def:
                    engine = EAWorkflowEngine()
                    engine.start_workflow(
                        workflow_code=wf_def.workflow_code,
                        context={
                            "solution_id": solution_id,
                            "phase": new_phase,
                            "previous_phase": current_phase,
                        },
                        triggered_by="phase_advance",
                        user_id=user_id,
                    )
                    workflow_triggered = True
                    logger.info(
                        "Triggered workflow %s for solution %d entering phase %s",
                        wf_def.workflow_code,
                        solution_id,
                        new_phase,
                    )
            except Exception as wf_err:
                logger.warning(
                    "Could not trigger workflow for solution %d phase %s: %s",
                    solution_id,
                    new_phase,
                    wf_err,
                )

            # Best-effort: update/create kanban card for the solution
            kanban_updated = False
            try:
                from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard

                # Find ADMPhase record for the new phase
                adm_phase_record = ADMPhase.query.filter_by(code=new_phase).first()
                if not adm_phase_record:
                    raise ValueError(f"ADMPhase record not found for code={new_phase}")

                # Find default board (first available)
                board = KanbanBoard.query.order_by(KanbanBoard.id).first()
                if not board:
                    raise ValueError("No KanbanBoard found")

                # Look for existing card for this solution on this board
                existing_card = KanbanCard.query.filter_by(
                    board_id=board.id,
                    title=solution.name,
                ).first()

                if existing_card:
                    existing_card.adm_phase_id = adm_phase_record.id
                    existing_card.status = "in_progress"
                else:
                    card = KanbanCard(
                        board_id=board.id,
                        adm_phase_id=adm_phase_record.id,
                        title=solution.name,
                        description=f"Solution phase advanced to {new_phase}",
                        card_type="solution",
                        status="in_progress",
                        created_by_id=user_id,
                    )
                    db.session.add(card)

                db.session.commit()
                kanban_updated = True
                logger.info(
                    "Updated kanban card for solution %d on board %d (phase %s)",
                    solution_id,
                    board.id,
                    new_phase,
                )
            except Exception as kb_err:
                db.session.rollback()
                logger.warning(
                    "Could not update kanban for solution %d phase %s: %s",
                    solution_id,
                    new_phase,
                    kb_err,
                )

            return {
                "success": True,
                "previous_phase": current_phase,
                "new_phase": new_phase,
                "validation": validation,
                "workflow_triggered": workflow_triggered,
                "kanban_updated": kanban_updated,
            }

        except Exception as e:
            db.session.rollback()
            logger.error("Error advancing phase: %s", e, exc_info=True)
            return {"success": False, "error": str(e), "validation": {}}

    def _build_motivation_prompt(self, problem) -> str:
        """Build LLM prompt for motivation layer generation."""
        import json as json_mod

        parts = [f"Business Problem: {problem.problem_description}"]
        if hasattr(problem, "business_context") and problem.business_context:
            parts.append(f"Context: {problem.business_context}")
        if problem.budget_min or problem.budget_max:
            parts.append(
                f"Budget: {getattr(problem, 'budget_currency', 'GBP') or 'GBP'} "
                f"{problem.budget_min or 0:,.0f} - {problem.budget_max or 0:,.0f}"
            )
        if problem.timeline_months:
            parts.append(f"Timeline: {problem.timeline_months} months")
        if hasattr(problem, "compliance_requirements") and problem.compliance_requirements:
            parts.append(f"Compliance: {json_mod.dumps(problem.compliance_requirements)}")

        context = "\n".join(parts)

        return (
            "Analyze this enterprise architecture problem and generate "
            "structured motivation elements.\n\n"
            f"{context}\n\n"
            "Return a JSON object with these categories:\n"
            "{\n"
            '  "drivers": [\n'
            '    {"name": "...", "description": "...", '
            '"type": "TECHNOLOGY|STAKEHOLDER|EXTERNAL|INTERNAL", '
            '"impact": 1-5, "urgency": 1-5, "confidence": 0.0-1.0}\n'
            "  ],\n"
            '  "goals": [\n'
            '    {"name": "...", "description": "...", '
            '"priority": 1-5, "confidence": 0.0-1.0}\n'
            "  ],\n"
            '  "requirements": [\n'
            '    {"name": "...", "description": "...", '
            '"type": "FUNCTIONAL|QUALITY|CONSTRAINT", '
            '"priority": 1-5, "mandatory": true/false, "confidence": 0.0-1.0}\n'
            "  ],\n"
            '  "constraints": [\n'
            '    {"name": "...", "description": "...", '
            '"type": "BUDGET|TIMELINE|RESOURCE|COMPLIANCE|TECHNICAL|ORGANIZATIONAL", '
            '"severity": 1-5, "confidence": 0.0-1.0}\n'
            "  ],\n"
            '  "principles": [],\n'
            '  "assessments": []\n'
            "}\n\n"
            "Generate 2-4 drivers, 2-4 goals, 4-8 requirements, and 2-4 constraints.\n"
            "Return ONLY valid JSON, no markdown formatting."
        )

    @staticmethod
    def _parse_json_from_llm(raw: str) -> "Optional[Dict]":
        """Extract JSON from LLM response text."""
        import json as json_mod

        if not raw:
            return None
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            return json_mod.loads(raw[start:end])
        except (json_mod.JSONDecodeError, ValueError):
            return None
