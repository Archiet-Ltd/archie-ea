"""Programme setup service — guided creation for greenfield/brownfield.

Provides wizard-driven programme creation, linking Solution records
with ArchiMate motivational-layer elements (drivers, goals) and
stakeholder mappings.  Brownfield mode leverages the ArchiMate OEF
import service from ENT-067.
"""
from app.services.archimate_backbone import sync_archimate_element

import logging

from app import db
from app.models.solution_models import Solution

logger = logging.getLogger(__name__)


class ProgrammeSetupService:
    """Orchestrates multi-step programme creation for the wizard UI."""

    _PREFILL_FIELDS = (
        "name", "objective", "workstream_type", "business_units",
        "outcome_statement", "direction", "metric_name", "unit",
        "baseline_value", "target_value", "target_date",
    )

    def ai_prefill_programme(self, description: str) -> dict:
        """Suggest programme-wizard field values from free-text via the LLM.

        Returns a dict with exactly the keys in _PREFILL_FIELDS, each either the
        model's extracted value or None. Never invents a value the description
        does not support (CLAUDE.md "Never invent data") — the prompt instructs
        the model to answer null rather than guess, and any key the model
        omits, or any value that fails a basic type check, is forced to None
        here rather than passed through.
        """
        import json

        from app.services.llm_service import LLMService
        from app.models.transformation_programme import WORKSTREAM_TYPES, IMPROVEMENT_DIRECTIONS

        prompt = f"""You are helping populate a transformation-programme intake form from a
free-text description written by an enterprise architect.

Description:
\"\"\"{description}\"\"\"

Extract ONLY what this description actually supports. If something is not
stated or cannot be confidently inferred, respond with null for that field —
never guess or invent a plausible-sounding value.

Return ONLY a JSON object with exactly these keys:
{{
  "name": string or null (a short programme name, not the whole description),
  "objective": string or null (one or two sentences on the business objective),
  "workstream_type": one of {list(WORKSTREAM_TYPES)} or null,
  "business_units": array of strings or null (business units in scope),
  "outcome_statement": string or null (the outcome the programme commits to),
  "direction": one of {list(IMPROVEMENT_DIRECTIONS)} or null (does the metric increase/decrease/stay the same),
  "metric_name": string or null (the metric that proves the outcome),
  "unit": string or null (unit the metric is measured in),
  "baseline_value": number or null (only if a current/starting value is explicitly stated),
  "target_value": number or null (only if a target value is explicitly stated),
  "target_date": string or null (YYYY-MM-DD, only if a date is explicitly stated or unambiguously computable)
}}"""
        response = LLMService().generate_from_prompt(prompt)
        raw = json.loads(response)

        def _clean_str(value):
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        def _clean_number(value):
            return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

        def _clean_list(value):
            if isinstance(value, list):
                cleaned = [v.strip() for v in value if isinstance(v, str) and v.strip()]
                return cleaned or None
            return None

        fields = {
            "name": _clean_str(raw.get("name")),
            "objective": _clean_str(raw.get("objective")),
            "workstream_type": raw.get("workstream_type") if raw.get("workstream_type") in WORKSTREAM_TYPES else None,
            "business_units": _clean_list(raw.get("business_units")),
            "outcome_statement": _clean_str(raw.get("outcome_statement")),
            "direction": raw.get("direction") if raw.get("direction") in IMPROVEMENT_DIRECTIONS else None,
            "metric_name": _clean_str(raw.get("metric_name")),
            "unit": _clean_str(raw.get("unit")),
            "baseline_value": _clean_number(raw.get("baseline_value")),
            "target_value": _clean_number(raw.get("target_value")),
            "target_date": _clean_str(raw.get("target_date")),
        }
        if fields["target_date"]:
            from datetime import date
            try:
                date.fromisoformat(fields["target_date"])
            except ValueError:
                fields["target_date"] = None
        return fields

    @staticmethod
    def create_business_first_programme(*, actor, command_key, request):
        """Compatibility boundary for canonical non-Solution programme intake."""
        from app.modules.transformation_room.programme_service import (
            TransformationProgrammeService,
        )

        return TransformationProgrammeService.create_programme(
            actor=actor,
            command_key=command_key,
            request=request,
        )

    # ------------------------------------------------------------------ #
    # Programme entity (PROG-001)                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _create_initiative_for(solution, data, user_id, initiative_type):
        """Create the Transformation Programme entity and link the solution.

        Before PROG-001 a wizard 'programme' collapsed into a single Solution
        with no programme-level object — no grouping, no rollups. Every
        programme now gets a StrategicInitiative; further solutions join via
        Solution.initiative_id (cockpit at /solutions/programmes/<id>).
        """
        from app.models.strategic import StrategicInitiative

        initiative = StrategicInitiative(
            name=solution.name,
            description=data.get("vision") or data.get("description") or "",
            initiative_type=initiative_type,
            target_platform=(data.get("target_platform") or "").strip() or None,
            vendor_key=(data.get("vendor_key") or "").strip().upper() or None,
            status="in_progress",
            priority=data.get("priority") or "high",
            owner_id=user_id,
        )
        db.session.add(initiative)
        db.session.flush()
        solution.initiative_id = initiative.id
        return initiative

    # ------------------------------------------------------------------ #
    # Templates                                                           #
    # ------------------------------------------------------------------ #

    TEMPLATES = [
        {
            "id": "greenfield",
            "name": "Greenfield Transformation",
            "description": "Start from scratch with a clean-slate architecture vision. "
            "Define drivers, goals, and stakeholders before designing the target state.",
            "icon": "layers",
            "starting_phase": "A",
        },
        {
            "id": "brownfield",
            "name": "Brownfield Modernization",
            "description": "Import an existing architecture baseline from an ArchiMate "
            "OEF XML export and plan the modernization journey.",
            "icon": "refresh-cw",
            "starting_phase": "B",
        },
    ]

    def get_templates(self):
        """Return available programme templates."""
        return list(self.TEMPLATES)

    # ------------------------------------------------------------------ #
    # Greenfield                                                          #
    # ------------------------------------------------------------------ #

    def create_greenfield_programme(self, data, user_id):
        """Create a new solution from greenfield wizard.

        Parameters
        ----------
        data : dict
            name, description, vision, business_domain, solution_type,
            drivers: list[{name, type}], goals: list[{name, description}],
            stakeholders: list[{name, role}]
        user_id : int
            Current authenticated user id.

        Returns
        -------
        Solution
        """
        from app.models.solution_architect_models import (
            DriverType,
            SolutionAnalysisSession,
            SolutionDriver,
            SolutionGoal,
            SolutionProblemDefinition,
            SolutionSessionStatus,
        )

        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("Programme name is required.")

        # 1. Create analysis session -----------------------------------------
        session = SolutionAnalysisSession(
            name=name,
            description=data.get("vision") or data.get("description") or "",
            status=SolutionSessionStatus.IN_PROGRESS,
            created_by_id=user_id,
        )
        db.session.add(session)
        db.session.flush()  # need session.id

        # 2. Problem definition -----------------------------------------------
        problem = SolutionProblemDefinition(
            session_id=session.id,
            problem_description=data.get("vision") or data.get("description") or name,
        )
        db.session.add(problem)
        db.session.flush()

        # 3. Drivers ----------------------------------------------------------
        # Map text urgency labels to integer scale (1-5)
        _urgency_map = {"critical": 5, "high": 4, "medium": 3, "low": 2, "minimal": 1}

        for drv_data in data.get("drivers") or []:
            drv_name = (drv_data.get("name") or "").strip()
            if not drv_name:
                continue
            raw_type = (drv_data.get("type") or "internal").lower()
            try:
                drv_type = DriverType(raw_type)
            except ValueError:
                drv_type = DriverType.INTERNAL
            raw_urgency = drv_data.get("urgency") or 3
            if isinstance(raw_urgency, str):
                raw_urgency = _urgency_map.get(raw_urgency.lower(), 3)
            raw_impact = drv_data.get("impact_level") or 3
            if isinstance(raw_impact, str):
                raw_impact = _urgency_map.get(raw_impact.lower(), 3)
            driver = SolutionDriver(
                problem_id=problem.id,
                name=drv_name,
                description=drv_data.get("description") or "",
                driver_type=drv_type,
                impact_level=raw_impact,
                urgency=raw_urgency,
                source="wizard",
            )
            db.session.add(driver)
            sync_archimate_element(driver)

        # 4. Goals ------------------------------------------------------------
        for goal_data in data.get("goals") or []:
            goal_name = (goal_data.get("name") or "").strip()
            if not goal_name:
                continue
            goal = SolutionGoal(
                problem_id=problem.id,
                name=goal_name,
                description=goal_data.get("description") or "",
                priority=goal_data.get("priority") or 3,
            )
            db.session.add(goal)
            sync_archimate_element(goal)

        # 5. Solution ---------------------------------------------------------
        solution = Solution(
            name=name,
            description=data.get("description") or "",
            business_value=data.get("vision") or "",
            solution_type=data.get("solution_type") or "Platform",
            business_domain=data.get("business_domain") or "",
            status="planned",
            adm_phase="A",
            analysis_session_id=session.id,
            created_by_id=user_id,
        )
        db.session.add(solution)
        db.session.flush()  # need solution.id

        # 6. Stakeholders — stored in journey_state to avoid schema-dependency
        # (solution_stakeholders table may not exist in all environments due to
        #  migration freeze; journey_state JSON is always available on Solution)

        # ── Enrich journey_state with new programme fields ─────────────────
        from sqlalchemy.orm.attributes import flag_modified

        existing_js = solution.journey_state or {}

        # Driver enrichment (kpi, owner, benefit go here — urgency is in SolutionDriver)
        drivers_enrichment = []
        for d in data.get("drivers", []):
            enrichment = {}
            if d.get("kpi"):
                enrichment["kpi"] = d["kpi"]
            if d.get("owner"):
                enrichment["owner"] = d["owner"]
            if d.get("benefit"):
                enrichment["benefit"] = d["benefit"]
            if enrichment:
                enrichment["name"] = d.get("name", "")
                drivers_enrichment.append(enrichment)

        # Capability and application IDs (linked post-creation by frontend)
        capability_ids = [c["id"] for c in data.get("capabilities", []) if c.get("id")]
        application_ids = [a["id"] for a in data.get("applications", []) if a.get("id")]

        stakeholders_data = [
            {"name": s.get("name", "").strip(), "role": s.get("role", ""), "influence": s.get("influence", ""), "interest": s.get("interest", "")}
            for s in data.get("stakeholders", [])
            if (s.get("name") or "").strip()
        ]

        solution.journey_state = {
            **existing_js,
            "drivers_enrichment": drivers_enrichment,
            "stakeholders": stakeholders_data,
            "capability_ids": capability_ids,
            "application_ids": application_ids,
            "governance_forums": data.get("governance_forums", []),
            "programme_horizon": {
                "start_year": data.get("programme_start_year"),
                "end_year": data.get("programme_end_year"),
            },
        }
        flag_modified(solution, "journey_state")

        # 7. Capability mappings — convert wizard selections to proper FK rows
        if capability_ids:
            try:
                from app.models.solution_models import SolutionCapabilityMapping
                for cap_id in capability_ids:
                    db.session.add(SolutionCapabilityMapping(
                        solution_id=solution.id,
                        capability_id=cap_id,
                        support_level="planned",
                        created_by_id=user_id,
                    ))
            except Exception as _e:
                logger.warning("capability mapping creation failed: %s", _e)

        # 8. Programme entity (PROG-001) — grouping + governance rollups
        self._create_initiative_for(solution, data, user_id, "greenfield")

        db.session.commit()
        logger.info("Greenfield programme created: solution_id=%s name=%s", solution.id, name)
        return solution

    # ------------------------------------------------------------------ #
    # Brownfield                                                          #
    # ------------------------------------------------------------------ #

    def create_brownfield_programme(self, data, user_id):
        """Create solution from imported architecture.

        Parameters
        ----------
        data : dict
            name, description, xml_content (raw OEF XML string),
            import_strategy (skip_duplicates|update_existing|create_all),
            target_state description.
        user_id : int
            Current authenticated user id.

        Returns
        -------
        Solution
        """
        from app.services.archimate_import_service import ArchiMateImportService

        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("Programme name is required.")

        xml_content = data.get("xml_content") or ""
        strategy = data.get("import_strategy") or "skip_duplicates"

        # 1. Import ArchiMate elements ----------------------------------------
        import_result = None
        if xml_content:
            try:
                import_result = ArchiMateImportService().import_xml(
                    xml_content, strategy=strategy
                )
            except (ValueError, Exception) as exc:
                logger.warning("ArchiMate import failed during brownfield setup: %s", exc)
                import_result = {"created": 0, "error": str(exc)}

        # 2. Solution ---------------------------------------------------------
        solution = Solution(
            name=name,
            description=data.get("description") or "",
            business_value=data.get("target_state") or "",
            solution_type=data.get("solution_type") or "Platform",
            business_domain=data.get("business_domain") or "",
            status="planned",
            adm_phase="B",  # brownfield starts at Phase B
            created_by_id=user_id,
        )
        db.session.add(solution)
        db.session.flush()

        # 3. Link imported elements to solution (if any created) ---------------
        if import_result and import_result.get("created_ids"):
            try:
                from app.models.solution_sad_models import SolutionArchiMateElement

                for elem_id in import_result["created_ids"]:
                    link = SolutionArchiMateElement(
                        solution_id=solution.id,
                        archimate_element_id=elem_id,
                    )
                    db.session.add(link)
            except Exception as exc:
                logger.warning("Could not link imported elements: %s", exc)

        # ── Enrich journey_state with new programme fields ─────────────────
        from sqlalchemy.orm.attributes import flag_modified

        existing_js = solution.journey_state or {}
        drivers_enrichment = []
        for d in data.get("drivers", []):
            enrichment = {}
            if d.get("kpi"):
                enrichment["kpi"] = d["kpi"]
            if d.get("owner"):
                enrichment["owner"] = d["owner"]
            if d.get("benefit"):
                enrichment["benefit"] = d["benefit"]
            if enrichment:
                enrichment["name"] = d.get("name", "")
                drivers_enrichment.append(enrichment)

        capability_ids = [c["id"] for c in data.get("capabilities", []) if c.get("id")]
        application_ids = [a["id"] for a in data.get("applications", []) if a.get("id")]
        stakeholders_data = [
            {"name": s.get("name", "").strip(), "role": s.get("role", ""), "influence": s.get("influence", ""), "interest": s.get("interest", "")}
            for s in data.get("stakeholders", [])
            if (s.get("name") or "").strip()
        ]

        solution.journey_state = {
            **existing_js,
            "drivers_enrichment": drivers_enrichment,
            "stakeholders": stakeholders_data,
            "capability_ids": capability_ids,
            "application_ids": application_ids,
            "governance_forums": data.get("governance_forums", []),
            "programme_horizon": {
                "start_year": data.get("programme_start_year"),
                "end_year": data.get("programme_end_year"),
            },
        }
        flag_modified(solution, "journey_state")

        # Capability mappings — convert wizard selections to proper FK rows
        if capability_ids:
            try:
                from app.models.solution_models import SolutionCapabilityMapping
                for cap_id in capability_ids:
                    db.session.add(SolutionCapabilityMapping(
                        solution_id=solution.id,
                        capability_id=cap_id,
                        support_level="planned",
                        created_by_id=user_id,
                    ))
            except Exception as _e:
                logger.warning("capability mapping creation failed: %s", _e)

        # Programme entity (PROG-001) — grouping + governance rollups
        self._create_initiative_for(solution, data, user_id, "brownfield")

        db.session.commit()
        logger.info(
            "Brownfield programme created: solution_id=%s name=%s import=%s",
            solution.id,
            name,
            import_result,
        )
        return solution
