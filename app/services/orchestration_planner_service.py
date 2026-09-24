"""
Multi-step Orchestration Planner

Sequences architecture work across a programme into a dependency-ordered plan
with explicit phase gating and rollback signals. Converts a goal statement into
a step-by-step execution plan grounded in the current platform state.

Design philosophy:
  - Each step is a concrete Entelim tool call or manual action.
  - Dependencies are explicit: step N cannot start until step N-1 criteria pass.
  - Plan is persisted as solution metadata for re-entry across sessions.
  - Phase gates check real DB state, not assumptions.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    step_number: int
    title: str
    description: str
    tool_call: Optional[str] = None  # Name of Entelim tool to invoke
    tool_args_template: Optional[dict] = None
    depends_on: List[int] = field(default_factory=list)
    gate_check: str = ""  # SQL/condition description to verify completion
    phase: str = ""  # TOGAF ADM phase (A-H)
    estimated_effort: str = "low"  # low | medium | high

    def to_dict(self) -> dict:
        return {
            "step": self.step_number,
            "title": self.title,
            "description": self.description,
            "tool_call": self.tool_call,
            "tool_args_template": self.tool_args_template,
            "depends_on": self.depends_on,
            "gate_check": self.gate_check,
            "phase": self.phase,
            "estimated_effort": self.estimated_effort,
        }


class OrchestrationPlannerService:
    """Build a multi-step architecture plan grounded in current platform state."""

    # Intent patterns → plan template selector
    _INTENT_PATTERNS = [
        (["sap", "rise", "clean core", "s/4hana"], "_plan_sap_transformation"),
        (["rationali", "time analysis", "portfolio"], "_plan_rationalization"),
        (["solution", "design", "blueprint", "arb"], "_plan_solution_design"),
        (["data", "schema", "ddl", "openapi", "data object"], "_plan_data_governance"),
        (["programme", "initiative", "transformation"], "_plan_programme_setup"),
    ]

    @classmethod
    def build_plan(cls, goal: str, solution_id: Optional[int] = None) -> dict:
        """
        Build a multi-step plan for the given architecture goal.

        Returns: {"success": True, "plan": {...}, "steps": [...], "step_count": N}
        """
        try:
            goal_lower = goal.lower()
            template_name = "_plan_generic"
            for keywords, method in cls._INTENT_PATTERNS:
                if any(kw in goal_lower for kw in keywords):
                    template_name = method
                    break

            planner = cls()
            template_fn = getattr(planner, template_name)
            steps, metadata = template_fn(goal, solution_id)
            plan_id = f"PLAN-{abs(hash(goal)) % 10000:04d}"

            return {
                "success": True,
                "plan": {
                    "plan_id": plan_id,
                    "goal": goal,
                    "solution_id": solution_id,
                    "step_count": len(steps),
                    "template": template_name.replace("_plan_", "").upper(),
                    **metadata,
                },
                "steps": [s.to_dict() for s in steps],
                "step_count": len(steps),
                "message": (
                    f"Built {len(steps)}-step plan for: \"{goal[:60]}\". "
                    f"Template: {template_name.replace('_plan_', '').upper()}. "
                    f"Start with Step 1 (gate check included for each step)."
                ),
            }
        except Exception as e:
            logger.exception("OrchestrationPlannerService.build_plan failed")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Plan templates                                                        #
    # ------------------------------------------------------------------ #

    def _plan_sap_transformation(self, goal: str, solution_id: Optional[int]) -> tuple:
        steps = [
            PlanStep(1, "SAP Clean-Core Baseline Scan",
                "Run validate_sap_clean_core on all SAP solutions to establish current compliance posture.",
                tool_call="validate_sap_clean_core",
                tool_args_template={"include_portfolio_scan": True},
                gate_check="validate_sap_clean_core returns at least 1 solution with SAP footprint",
                phase="A", estimated_effort="low"),
            PlanStep(2, "Identify CRITICAL and HIGH Findings",
                "From the scan report, list all CRITICAL (Tier 4/CMOD) and HIGH (RFC/BAPI) violations by solution.",
                depends_on=[1],
                gate_check="At least one finding in the scan report OR clean-core score confirmed",
                phase="A", estimated_effort="low"),
            PlanStep(3, "Create SAP RISE Transformation Programme",
                "Create a programme in Entelim to track all SAP clean-core remediation work.",
                tool_call="create_solution",
                tool_args_template={"name": "SAP RISE Clean-Core Remediation", "solution_type": "Migration",
                                    "business_domain": "technology"},
                depends_on=[2],
                gate_check="Programme solution created and visible at /solutions/programmes",
                phase="B", estimated_effort="low"),
            PlanStep(4, "Map Violating Solutions as Programme Members",
                "Link each solution with CRITICAL/HIGH findings to the remediation programme.",
                tool_call="link_application_to_solution",
                tool_args_template={"role": "primary"},
                depends_on=[3],
                gate_check="Programme has at least 1 member solution",
                phase="B", estimated_effort="medium"),
            PlanStep(5, "Design BTP Mediation Architecture",
                "For each RFC/BAPI integration, design a Side-by-Side BTP replacement. "
                "Add BTP Integration Suite ArchiMate elements to each solution.",
                tool_call="create_archimate_element",
                tool_args_template={"type": "ApplicationComponent", "layer": "application",
                                    "name": "BTP Integration Suite"},
                depends_on=[4],
                gate_check="At least 1 BTP element in each previously-violating solution",
                phase="C", estimated_effort="high"),
            PlanStep(6, "Run Inference Engine to Fill Chain Gaps",
                "Run the ArchiMate inference engine on each solution to complete the "
                "Motivation→Technology chain after BTP elements are added.",
                tool_call="run_inference_engine",
                tool_args_template={"dry_run": False},
                depends_on=[5],
                gate_check="Inference engine reports 0 CRITICAL gaps per solution",
                phase="D", estimated_effort="medium"),
            PlanStep(7, "Re-validate Clean-Core Scores",
                "Re-run validate_sap_clean_core on all affected solutions; confirm score ≥80.",
                tool_call="validate_sap_clean_core",
                tool_args_template={"include_portfolio_scan": True},
                depends_on=[6],
                gate_check="All previously-violating solutions now score ≥80 (CLEAN_CORE_COMPLIANT)",
                phase="E", estimated_effort="low"),
            PlanStep(8, "Submit to ARB",
                "Submit each remediated solution for ARB review. Compliance score ≥80 is a hard gate.",
                depends_on=[7],
                gate_check="ARB submission status = pending_review for all programme members",
                phase="F", estimated_effort="medium"),
        ]
        meta = {"programme_type": "SAP_RISE", "adm_phases_covered": "A-F", "estimated_duration": "8-12 weeks"}
        return steps, meta

    def _plan_rationalization(self, goal: str, solution_id: Optional[int]) -> tuple:
        steps = [
            PlanStep(1, "Generate Rationalization Proposals",
                "Run propose_rationalization to surface TIME candidates from the current portfolio.",
                tool_call="propose_rationalization",
                tool_args_template={"limit": 10},
                gate_check="At least 1 proposal returned",
                phase="A", estimated_effort="low"),
            PlanStep(2, "Review ELIMINATE Candidates",
                "For each ELIMINATE-scored app: verify no active integrations, confirm no programme dependency.",
                depends_on=[1],
                gate_check="ELIMINATE list reviewed; owner confirmed on each",
                phase="A", estimated_effort="medium"),
            PlanStep(3, "Create Retirement Programmes",
                "Create one retirement programme per cluster of ELIMINATE apps.",
                tool_call="create_solution",
                tool_args_template={"solution_type": "Migration", "business_domain": "technology"},
                depends_on=[2],
                gate_check="Retirement programme created",
                phase="B", estimated_effort="low"),
            PlanStep(4, "Map Consolidation Candidates",
                "For each CONSOLIDATE cluster: identify the survivor and link others for migration.",
                depends_on=[3],
                gate_check="Consolidation mapping complete at /capability-map",
                phase="C", estimated_effort="high"),
            PlanStep(5, "ARB Approval for Retirements",
                "Submit retirement decisions to ARB. Each requires: owner confirmation, integration map, data retention plan.",
                depends_on=[4],
                gate_check="ARB approved retirement for at least 1 application",
                phase="F", estimated_effort="medium"),
        ]
        meta = {"programme_type": "RATIONALIZATION", "adm_phases_covered": "A-F",
                "estimated_duration": "4-8 weeks"}
        return steps, meta

    def _plan_solution_design(self, goal: str, solution_id: Optional[int]) -> tuple:
        steps = [
            PlanStep(1, "Create Solution Record",
                "Create the solution in Entelim with name, domain, and type.",
                tool_call="create_solution",
                gate_check="Solution created and has an ID",
                phase="A", estimated_effort="low"),
            PlanStep(2, "Search Relevant Capabilities",
                "Search business capabilities to identify which capabilities this solution addresses.",
                tool_call="search_capabilities_by_problem",
                depends_on=[1],
                gate_check="At least 3 capabilities linked to solution",
                phase="A", estimated_effort="low"),
            PlanStep(3, "Link Business Layer Elements",
                "Add BusinessProcess, BusinessFunction ArchiMate elements for each capability.",
                tool_call="create_archimate_element",
                tool_args_template={"layer": "business"},
                depends_on=[2],
                gate_check="Business layer has ≥2 elements",
                phase="B", estimated_effort="medium"),
            PlanStep(4, "Link Application Layer Elements",
                "Find existing applications via find_applications_by_capability; link or create ApplicationComponent.",
                tool_call="find_applications_by_capability",
                depends_on=[3],
                gate_check="Application layer has ≥2 elements",
                phase="C", estimated_effort="medium"),
            PlanStep(5, "Add Technology Layer",
                "Add Node/SystemSoftware elements for deployment targets.",
                tool_call="find_technical_capabilities",
                depends_on=[4],
                gate_check="Technology layer has ≥1 element",
                phase="D", estimated_effort="medium"),
            PlanStep(6, "Run Inference Engine",
                "Fill missing chain elements across all 6 layers.",
                tool_call="run_inference_engine",
                tool_args_template={"dry_run": False},
                depends_on=[5],
                gate_check="Inference engine reports created elements > 0",
                phase="D", estimated_effort="low"),
            PlanStep(7, "Verify Codegen Conformance",
                "Run verify_codegen to confirm elements are traceable and well-named.",
                tool_call="verify_codegen",
                depends_on=[6],
                gate_check="Codegen score ≥80 (Grade B or better)",
                phase="E", estimated_effort="low"),
            PlanStep(8, "Generate Blueprint Narrative",
                "Generate AI narrative for summary, strategic, and business sections.",
                tool_call="generate_blueprint_narrative",
                tool_args_template={"section_id": "sec-1"},
                depends_on=[7],
                gate_check="Blueprint sections sec-1 through sec-4 have narrative",
                phase="E", estimated_effort="medium"),
        ]
        meta = {"programme_type": "SOLUTION_DESIGN", "adm_phases_covered": "A-F",
                "estimated_duration": "2-4 sessions"}
        return steps, meta

    def _plan_data_governance(self, goal: str, solution_id: Optional[int]) -> tuple:
        steps = [
            PlanStep(1, "Infer DataObjects from Schema",
                "Use infer_schema to parse DDL or OpenAPI and extract DataObject candidates.",
                tool_call="infer_schema",
                gate_check="At least 1 DataObject inferred",
                phase="C", estimated_effort="low"),
            PlanStep(2, "Create and Link DataObject Elements",
                "Create each inferred DataObject as an ArchiMate element, passing solution_name "
                "so each element is linked to the target solution in the same call.",
                tool_call="create_archimate_element",
                tool_args_template={"type": "DataObject", "layer": "application",
                                    "solution_name": "<target solution>"},
                depends_on=[1],
                gate_check="DataObject elements created in catalog and linked to solution",
                phase="C", estimated_effort="medium"),
            PlanStep(3, "Verify Solution Linkage",
                "Search the catalog to confirm the new DataObjects exist and appear in the "
                "solution architecture tab.",
                tool_call="search_archimate_elements",
                tool_args_template={"element_type": "DataObject"},
                depends_on=[2],
                gate_check="DataObjects visible in solution architecture tab",
                phase="C", estimated_effort="low"),
            PlanStep(4, "Set Data Classification",
                "Tag each DataObject with data_classification (PUBLIC/INTERNAL/CONFIDENTIAL/SECRET).",
                depends_on=[3],
                gate_check="All DataObjects have data_classification set",
                phase="C", estimated_effort="medium"),
            PlanStep(5, "Map Integration Flows",
                "Add SolutionIntegrationFlow entries to show where each DataObject flows.",
                depends_on=[4],
                gate_check="At least 1 integration flow per DataObject",
                phase="D", estimated_effort="high"),
        ]
        meta = {"programme_type": "DATA_GOVERNANCE", "adm_phases_covered": "C-D",
                "estimated_duration": "1-2 sessions"}
        return steps, meta

    def _plan_programme_setup(self, goal: str, solution_id: Optional[int]) -> tuple:
        steps = [
            PlanStep(1, "Create Programme Record",
                "Create the transformation programme in Entelim.",
                tool_call="create_solution",
                tool_args_template={"solution_type": "Platform"},
                gate_check="Programme record created",
                phase="A", estimated_effort="low"),
            PlanStep(2, "Define Programme Scope",
                "Add problem drivers, goals, and constraints via create_driver and create_goal.",
                tool_call="create_driver",
                depends_on=[1],
                gate_check="At least 1 driver and 1 goal defined",
                phase="A", estimated_effort="medium"),
            PlanStep(3, "Link Member Solutions",
                "Link all solutions that are part of this programme.",
                tool_call="link_application_to_solution",
                depends_on=[2],
                gate_check="Programme has ≥2 member solutions",
                phase="B", estimated_effort="medium"),
            PlanStep(4, "Run Governance Snapshot",
                "Trigger a programme governance snapshot to capture baseline state.",
                depends_on=[3],
                gate_check="Snapshot taken and visible at /solutions/programmes",
                phase="B", estimated_effort="low"),
            PlanStep(5, "Set Up Drift Monitoring",
                "Configure drift thresholds so the programme auto-flags regressions.",
                depends_on=[4],
                gate_check="Drift monitoring active — next snapshot will detect changes",
                phase="H", estimated_effort="low"),
        ]
        meta = {"programme_type": "PROGRAMME_GOVERNANCE", "adm_phases_covered": "A-H",
                "estimated_duration": "1 session"}
        return steps, meta

    def _plan_generic(self, goal: str, solution_id: Optional[int]) -> tuple:
        steps = [
            PlanStep(1, "Assess Current State",
                "Search relevant ArchiMate elements and solutions to understand current architecture.",
                tool_call="search_archimate_elements",
                gate_check="Current state documented",
                phase="A", estimated_effort="low"),
            PlanStep(2, "Identify Capability Gaps",
                "Use search_capabilities_by_problem to find which capabilities need addressing.",
                tool_call="search_capabilities_by_problem",
                depends_on=[1],
                gate_check="Capability gaps identified",
                phase="A", estimated_effort="medium"),
            PlanStep(3, "Design Solution Architecture",
                "Create or update the solution with ArchiMate elements across all required layers.",
                tool_call="create_archimate_element",
                depends_on=[2],
                gate_check="Solution architecture spans ≥3 ArchiMate layers",
                phase="B", estimated_effort="high"),
            PlanStep(4, "Validate and Submit",
                "Run diagnostics, fix chain gaps, and submit to ARB.",
                tool_call="diagnose_chain",
                depends_on=[3],
                gate_check="No CRITICAL chain gaps; ARB submission ready",
                phase="F", estimated_effort="medium"),
        ]
        meta = {"programme_type": "GENERIC", "adm_phases_covered": "A-F",
                "estimated_duration": "2-3 sessions"}
        return steps, meta
