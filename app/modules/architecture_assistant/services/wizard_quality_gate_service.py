"""Wizard Quality Gate Service (Layer C).

Scores every wizard step on 6 quality dimensions. Blocks advancement on
Steps 3 and 6 (highest codegen impact) if score is below threshold.
Soft blocks other steps.

~70% deterministic (fast, free), ~30% LLM (quality judgment, only when
the deterministic score is borderline). LLM portion is skipped entirely
if deterministic score already passes or already fails catastrophically.
"""

import json
import logging
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

from app import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QualityDimension:
    name: str
    score: int  # 0-100
    weight: float  # contribution to overall score
    status: str  # "pass" | "warn" | "fail"
    details: str


@dataclass
class QualityIssue:
    field_path: str
    issue: str
    severity: str  # "blocking" | "warning"
    auto_fixable: bool
    suggested_fix: str


@dataclass
class QualityAssessment:
    step: int
    overall_score: int
    threshold: int
    passed: bool
    hard_block: bool
    dimensions: List[QualityDimension]
    failing_items: List[QualityIssue]
    auto_fixable_count: int
    estimated_fix_time: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class WizardQualityGateService:
    """Step quality assessment with adaptive blocking."""

    THRESHOLDS = {
        1: 65,
        2: 70,
        3: 80,
        4: 65,
        5: 70,
        6: 85,
    }

    HARD_BLOCK_STEPS = {3, 6}

    # Dimension weights (must sum to 1.0)
    DIMENSION_WEIGHTS = {
        "completeness": 0.25,
        "specificity": 0.25,
        "consistency": 0.15,
        "traceability": 0.10,
        "feasibility": 0.10,
        "codegen_readiness": 0.15,
    }

    def assess_step(
        self,
        solution_id: int,
        step: int,
        step_data: dict,
        solution_context: dict,
    ) -> QualityAssessment:
        """Score current step quality. ~70% deterministic, ~30% LLM."""
        threshold = self.THRESHOLDS.get(step, 70)
        hard_block = step in self.HARD_BLOCK_STEPS

        # Run deterministic checks
        dimensions, issues = self._deterministic_assessment(step, step_data, solution_context)

        # Calculate deterministic score
        det_score = self._weighted_score(dimensions)

        # Decide if LLM enhancement is needed
        # Skip LLM if: clearly passing (>= threshold + 10) or clearly failing (< threshold - 20)
        if det_score < threshold - 20 or det_score >= threshold + 10:
            overall_score = det_score
        else:
            # Borderline — run LLM for specificity and feasibility refinement
            llm_dimensions = self._llm_assessment(step, step_data, solution_context)
            if llm_dimensions:
                # Merge LLM-scored dimensions (specificity, feasibility)
                for llm_dim in llm_dimensions:
                    for i, dim in enumerate(dimensions):
                        if dim.name == llm_dim.name:
                            dimensions[i] = llm_dim
                            break
                overall_score = self._weighted_score(dimensions)
            else:
                overall_score = det_score

        auto_fixable = sum(1 for i in issues if i.auto_fixable)
        fix_time = f"~{max(10, auto_fixable * 5)} seconds with auto-fix" if auto_fixable else "Manual fixes required"

        return QualityAssessment(
            step=step,
            overall_score=overall_score,
            threshold=threshold,
            passed=overall_score >= threshold,
            hard_block=hard_block,
            dimensions=dimensions,
            failing_items=issues,
            auto_fixable_count=auto_fixable,
            estimated_fix_time=fix_time,
        )

    def can_advance(
        self,
        solution_id: int,
        step: int,
        step_data: dict,
        solution_context: dict,
    ) -> Tuple[bool, QualityAssessment]:
        """Returns (can_advance, assessment).

        Hard block steps: can_advance = score >= threshold.
        Soft block steps: can_advance = True always.
        """
        assessment = self.assess_step(solution_id, step, step_data, solution_context)
        if assessment.hard_block:
            return assessment.passed, assessment
        return True, assessment

    def record_skip(
        self,
        solution_id: int,
        step: int,
        assessment: QualityAssessment,
    ) -> None:
        """Record that user skipped a soft-block gate."""
        from app.models.solution_models import Solution
        from datetime import datetime

        solution = Solution.query.get(solution_id)
        if solution is None:
            return

        journey_state = solution.journey_state or {}
        quality_scores = journey_state.get("_quality_scores", {})
        quality_scores[str(step)] = {
            "score": assessment.overall_score,
            "passed": assessment.passed,
            "skipped": True,
            "assessed_at": datetime.utcnow().isoformat(),
        }
        journey_state["_quality_scores"] = quality_scores
        solution.journey_state = journey_state
        db.session.commit()

    # ------------------------------------------------------------------
    # Deterministic assessment (~70% of scoring)
    # ------------------------------------------------------------------

    def _deterministic_assessment(
        self, step: int, step_data: dict, context: dict,
    ) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Run all deterministic checks for a step."""
        checker = getattr(self, f"_check_step_{step}", None)
        if checker is None:
            return self._default_dimensions(), []
        return checker(step_data, context)

    def _check_step_1(self, data: dict, ctx: dict) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Step 1 — Problem Definition."""
        issues = []

        # Completeness: problem_statement, budget, timeline, success_metrics
        problem = data.get("problem_statement", "") or ctx.get("problem_statement", "")
        budget_min = data.get("budget_min") or ctx.get("budget_range", "")
        timeline = data.get("timeline_months") or ctx.get("timeline_months")
        success_metrics = data.get("success_metrics") or []
        structured_intake = data.get("structured_intake", {}) or {}

        completeness_items = 0
        completeness_total = 5
        if problem and len(str(problem)) >= 100:
            completeness_items += 1
        else:
            issues.append(QualityIssue("problem_statement", "Problem statement too short (need >= 100 chars)", "blocking", True, "Enrich with business context and specific pain points"))
        if budget_min:
            completeness_items += 1
        else:
            issues.append(QualityIssue("budget_min", "Budget range not set", "warning", True, "Set budget range based on solution scope"))
        if timeline:
            completeness_items += 1
        else:
            issues.append(QualityIssue("timeline_months", "Timeline not set", "warning", True, "Set timeline based on scope and constraints"))
        if success_metrics and len(success_metrics) >= 1:
            completeness_items += 1
        else:
            issues.append(QualityIssue("success_metrics", "No success metrics defined", "warning", True, "Add measurable KPIs"))
        if structured_intake and len(structured_intake) >= 2:
            completeness_items += 1
        else:
            issues.append(QualityIssue("structured_intake", "Structured intake incomplete", "warning", True, "Fill domain, compliance, and technology fields"))

        completeness = int((completeness_items / completeness_total) * 100)

        # Specificity: problem length and keyword density (deterministic proxy)
        specificity = min(100, int(len(str(problem)) / 3)) if problem else 0

        # Consistency: no contradictions detectable deterministically at Step 1
        consistency = 80 if problem else 0

        # Traceability: N/A at Step 1
        traceability = 70

        # Feasibility: budget vs scope rough check (deterministic)
        feasibility = 70  # Default, LLM refines

        # Codegen readiness: problem + domain + constraints present
        codegen_fields = sum([
            bool(problem and len(str(problem)) >= 50),
            bool(ctx.get("business_domain")),
            bool(ctx.get("constraints")),
        ])
        codegen_readiness = int((codegen_fields / 3) * 100)

        dims = self._build_dimensions(completeness, specificity, consistency, traceability, feasibility, codegen_readiness)
        return dims, issues

    def _check_step_2(self, data: dict, ctx: dict) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Step 2 — Capabilities."""
        issues = []
        capabilities = data.get("capabilities", []) or data.get("accepted_capabilities", []) or []

        # Completeness: each cap has description >= 30, maturity set, importance set
        complete_count = 0
        for i, cap in enumerate(capabilities):
            if not isinstance(cap, dict):
                continue
            cap_name = cap.get("name", f"Capability #{i+1}")
            desc = cap.get("description", "") or ""
            if len(desc) < 30:
                issues.append(QualityIssue(f"{cap_name} → description", f"'{cap_name}' description too short ({len(desc)} chars, need >= 30)", "warning", True, "Add domain context and business purpose"))
            maturity_cur = cap.get("maturity_current")
            maturity_tgt = cap.get("maturity_target")
            if maturity_cur is None or maturity_tgt is None:
                issues.append(QualityIssue(f"{cap_name} → maturity", f"'{cap_name}' maturity current/target not set", "warning", True, "Set realistic maturity levels"))
            importance = cap.get("strategic_importance")
            if not importance:
                issues.append(QualityIssue(f"{cap_name} → strategic_importance", f"'{cap_name}' strategic importance not set", "warning", True, "Classify as high/medium/low"))
            if len(desc) >= 30 and maturity_cur is not None and maturity_tgt is not None and importance:
                complete_count += 1

        total = max(len(capabilities), 1)
        completeness = int((complete_count / total) * 100) if capabilities else 0

        # Specificity: average description length as proxy
        avg_desc_len = 0
        if capabilities:
            descs = [len(str(c.get("description", ""))) for c in capabilities if isinstance(c, dict)]
            avg_desc_len = sum(descs) / max(len(descs), 1)
        specificity = min(100, int(avg_desc_len / 1.5))

        # Consistency: maturity_current < maturity_target
        consistency_ok = 0
        for cap in capabilities:
            if not isinstance(cap, dict):
                continue
            cur = cap.get("maturity_current")
            tgt = cap.get("maturity_target")
            if cur is not None and tgt is not None:
                if cur < tgt:
                    consistency_ok += 1
                else:
                    issues.append(QualityIssue("capabilities[].maturity", f"Current maturity ({cur}) >= target ({tgt})", "warning", True, "Target must exceed current"))
        consistency = int((consistency_ok / total) * 100) if capabilities else 50

        # Traceability: capabilities linked back to problem keywords
        traceability = 60 if capabilities else 0

        # Feasibility: default, LLM refines
        feasibility = 70

        # Codegen readiness: enough caps with enough detail
        codegen_readiness = min(100, int(complete_count * 20)) if capabilities else 0

        dims = self._build_dimensions(completeness, specificity, consistency, traceability, feasibility, codegen_readiness)
        return dims, issues

    def _check_step_3(self, data: dict, ctx: dict) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Step 3 — Architecture (HARD BLOCK).

        Checks element properties AND chain completeness (the codegen gate
        requires >= 70% chain completeness — we surface this here so the user
        knows before reaching Step 7).
        """
        issues = []
        elements = data.get("elements", []) or []

        # Completeness: each element has description, build_buy, deployment_model
        complete_count = 0
        for i, el in enumerate(elements):
            if not isinstance(el, dict):
                continue
            el_name = el.get("name", f"Element #{i+1}")
            desc = el.get("description", "") or ""
            build_buy = el.get("build_buy") or (el.get("properties", {}) or {}).get("build_buy")
            deployment = el.get("deployment_model") or (el.get("properties", {}) or {}).get("deployment_model")
            el_complete = True
            if not desc:
                issues.append(QualityIssue(
                    f"{el_name} → description",
                    f"'{el_name}' has no description — codegen will produce generic CRUD",
                    "blocking", True,
                    f"Generate description from '{el_name}' type ({el.get('type', '?')}) and solution context"))
                el_complete = False
            if not build_buy:
                issues.append(QualityIssue(
                    f"{el_name} → build_buy",
                    f"'{el_name}' missing build/buy decision — codegen can't determine integration vs generation",
                    "blocking", True,
                    "Set to build (generate code), buy (integration stub), or reuse (adapter)"))
                el_complete = False
            if not deployment:
                issues.append(QualityIssue(
                    f"{el_name} → deployment_model",
                    f"'{el_name}' missing deployment model",
                    "warning", True,
                    "Set deployment model (cloud/on-prem/hybrid) consistent with org size"))
            if el_complete:
                complete_count += 1

        total = max(len(elements), 1)
        completeness = int((complete_count / total) * 100) if elements else 0

        # Specificity: description quality
        avg_desc_len = 0
        if elements:
            descs = [len(str(e.get("description", ""))) for e in elements if isinstance(e, dict)]
            avg_desc_len = sum(descs) / max(len(descs), 1)
        specificity = min(100, int(avg_desc_len / 1.5))

        # Consistency: deployment model consistent, build_buy makes sense
        consistency = 75 if elements else 0

        # Traceability: relationship density + chain completeness
        relationships = data.get("relationships", []) or []
        connected_elements = set()
        for rel in relationships:
            if isinstance(rel, dict):
                connected_elements.add(rel.get("source_id"))
                connected_elements.add(rel.get("target_id"))
        rel_density = len(connected_elements) / max(total, 1)
        traceability = min(100, int(rel_density * 100))

        # Chain completeness check — codegen requires >= 70%
        chain_pct = self._check_chain_completeness(ctx.get("solution_id"), elements, relationships)
        if chain_pct is not None and chain_pct < 70:
            issues.append(QualityIssue(
                "architecture → chain completeness",
                f"Architecture chain completeness is {chain_pct}% — code generation requires >= 70%. "
                f"Add relationships between elements (serving, composition, flow, realization) to connect the architecture layers.",
                "blocking", True,
                "Auto-complete can add missing relationships between your elements"))
            # Chain completeness directly impacts codegen readiness
            traceability = min(traceability, chain_pct)

        if not elements:
            issues.append(QualityIssue(
                "architecture → elements",
                "No architecture elements defined — generate or add ArchiMate elements first",
                "blocking", False,
                "Use 'Generate Architecture' to create elements from your capabilities"))

        # Minimum element count for meaningful architecture
        if 0 < len(elements) < 5:
            issues.append(QualityIssue(
                "architecture → element count",
                f"Only {len(elements)} elements — a meaningful architecture typically needs 10+ elements across application, technology, and business layers",
                "warning", True,
                "Add elements for missing layers (e.g. database, API gateway, message broker)"))

        # Feasibility: default
        feasibility = 70

        # Codegen readiness: elements with full properties + chain completeness
        base_readiness = int((complete_count / total) * 100) if elements else 0
        if chain_pct is not None:
            codegen_readiness = int(base_readiness * 0.6 + chain_pct * 0.4)
        else:
            codegen_readiness = base_readiness

        dims = self._build_dimensions(completeness, specificity, consistency, traceability, feasibility, codegen_readiness)
        return dims, issues

    def _check_chain_completeness(self, solution_id: Optional[int], elements: list, relationships: list) -> Optional[int]:
        """Check ArchiMate chain completeness from DB if available."""
        if not solution_id:
            # Fall back to relationship density from passed data
            if not elements:
                return 0
            connected = set()
            for rel in relationships:
                if isinstance(rel, dict):
                    connected.add(rel.get("source_id"))
                    connected.add(rel.get("target_id"))
            return int((len(connected) / max(len(elements), 1)) * 100)

        try:
            from app.models.archimate_core import ArchiMateRelationship

            element_ids = set()
            from app.utils.tenant_sql import org_scope
            _org_clause, _org_params = org_scope()
            rows = db.session.execute(db.text(
                "SELECT element_id FROM solution_archimate_elements WHERE solution_id = :sid"
                + _org_clause
            ), {"sid": solution_id, **_org_params}).fetchall()
            element_ids = {r[0] for r in rows if r[0]}

            if not element_ids:
                return 0

            # Count elements with at least one relationship (source or target)
            from sqlalchemy import or_
            (ArchiMateRelationship.query.filter(
                or_(
                    ArchiMateRelationship.source_id.in_(element_ids),
                    ArchiMateRelationship.target_id.in_(element_ids),
                )
            ).count())

            # Chain completeness = connected elements / total elements
            connected = set()
            rels = ArchiMateRelationship.query.filter(
                or_(
                    ArchiMateRelationship.source_id.in_(element_ids),
                    ArchiMateRelationship.target_id.in_(element_ids),
                )
            ).all()
            for r in rels:
                if r.source_id in element_ids:
                    connected.add(r.source_id)
                if r.target_id in element_ids:
                    connected.add(r.target_id)

            return int((len(connected) / max(len(element_ids), 1)) * 100)

        except Exception:
            logger.debug("Chain completeness check failed — skipping", exc_info=True)
            return None

    def _check_step_4(self, data: dict, ctx: dict) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Step 4 — Gap Analysis."""
        issues = []
        gaps = data.get("gaps", []) or data.get("gap_analysis", []) or []

        complete_count = 0
        for i, gap in enumerate(gaps):
            if not isinstance(gap, dict):
                continue
            severity = gap.get("severity")
            rec = gap.get("recommendation", "") or gap.get("recommended_solution", "") or ""
            gap_ok = True
            if not severity:
                issues.append(QualityIssue(f"gaps[{i}].severity", "Gap missing severity", "warning", True, "Classify as critical/high/medium/low"))
                gap_ok = False
            if len(rec) < 50:
                issues.append(QualityIssue(f"gaps[{i}].recommendation", "Recommendation too short (need >= 50 chars)", "warning", True, "Make recommendation specific and actionable"))
                gap_ok = False
            if gap_ok:
                complete_count += 1

        total = max(len(gaps), 1)
        completeness = int((complete_count / total) * 100) if gaps else 0
        specificity = 60 if gaps else 0  # LLM refines
        consistency = 75
        traceability = 60 if gaps else 0
        feasibility = 70
        codegen_readiness = int((complete_count / total) * 80) if gaps else 0

        dims = self._build_dimensions(completeness, specificity, consistency, traceability, feasibility, codegen_readiness)
        return dims, issues

    def _check_step_5(self, data: dict, ctx: dict) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Step 5 — Options."""
        issues = []
        options = data.get("options", []) or data.get("recommendations", []) or []

        complete_count = 0
        has_build = has_buy = False
        for i, opt in enumerate(options):
            if not isinstance(opt, dict):
                continue
            opt_type = str(opt.get("type", "")).lower()
            if "build" in opt_type:
                has_build = True
            if "buy" in opt_type:
                has_buy = True
            cost = opt.get("cost_estimate") or opt.get("estimated_cost")
            timeline = opt.get("timeline_months") or opt.get("estimated_timeline_months")
            opt_ok = True
            if not cost:
                issues.append(QualityIssue(f"options[{i}].cost_estimate", "Cost estimate missing", "warning", True, "Add cost estimate for this option"))
                opt_ok = False
            if not timeline:
                issues.append(QualityIssue(f"options[{i}].timeline", "Timeline missing", "warning", True, "Add timeline estimate"))
                opt_ok = False
            if opt_ok:
                complete_count += 1

        if options and not (has_build or has_buy):
            issues.append(QualityIssue("options", "Options don't cover build or buy approaches", "warning", True, "Add at least build and buy options"))

        total = max(len(options), 1)
        completeness = int((complete_count / total) * 100) if options else 0
        specificity = 60 if options else 0
        consistency = 80 if (has_build and has_buy) else 50
        traceability = 60
        feasibility = 70
        codegen_readiness = int((complete_count / total) * 70) if options else 0

        dims = self._build_dimensions(completeness, specificity, consistency, traceability, feasibility, codegen_readiness)
        return dims, issues

    def _check_step_6(self, data: dict, ctx: dict) -> Tuple[List[QualityDimension], List[QualityIssue]]:
        """Step 6 — Blueprint/ARB (HARD BLOCK)."""
        issues = []

        narratives = data.get("section_narratives", {}) or ctx.get("section_narratives", {}) or {}
        scores = data.get("section_scores", {}) or ctx.get("section_scores", {}) or {}

        expected_sections = [
            "problem", "current_state", "target_state", "gap_analysis",
            "recommended_solution", "migration_approach", "risk_register", "cost_model",
        ]

        # Completeness: all 8 section narratives populated
        filled = 0
        for sec in expected_sections:
            val = narratives.get(sec, "")
            if val and len(str(val)) >= 50:
                filled += 1
            else:
                issues.append(QualityIssue(f"section_narratives.{sec}", f"Section '{sec}' is empty or too short", "blocking", True, f"Generate {sec} narrative from solution context"))
        completeness = int((filled / len(expected_sections)) * 100)

        # Specificity: average narrative length
        all_narrative_lens = [len(str(v)) for v in narratives.values() if v]
        avg_len = sum(all_narrative_lens) / max(len(all_narrative_lens), 1)
        specificity = min(100, int(avg_len / 5))

        # Consistency: section scores all >= 60
        low_scores = 0
        for sec in expected_sections:
            score = scores.get(sec, 0)
            if isinstance(score, (int, float)) and score < 60:
                low_scores += 1
                issues.append(QualityIssue(f"section_scores.{sec}", f"Section '{sec}' score is {score} (need >= 60)", "warning", True, "Improve section content to raise score"))
        consistency = max(0, 100 - (low_scores * 15))

        # Traceability: sections reference capabilities and elements
        traceability = 70 if filled >= 4 else 30

        # Feasibility: default
        feasibility = 70

        # Codegen readiness: all sections + ADR fields
        adr_fields = ["implementation_plan", "rollback_strategy", "monitoring_plan",
                       "risk_register", "compliance_assurance"]
        adr_filled = sum(1 for f in adr_fields if data.get(f) or narratives.get(f))
        codegen_readiness = int(((filled + adr_filled) / (len(expected_sections) + len(adr_fields))) * 100)

        dims = self._build_dimensions(completeness, specificity, consistency, traceability, feasibility, codegen_readiness)
        return dims, issues

    # ------------------------------------------------------------------
    # LLM assessment (~30% — only on borderline scores)
    # ------------------------------------------------------------------

    def _llm_assessment(
        self, step: int, step_data: dict, context: dict,
    ) -> Optional[List[QualityDimension]]:
        """Run LLM-based quality checks for specificity and feasibility."""
        try:
            from app.modules.ai_chat.services.llm_service import LLMService

            if not LLMService.is_available():
                return None

            provider, model = LLMService._get_configured_provider()
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=1024)

            prompt = self._build_llm_prompt(step, step_data, context)
            raw, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=max_tokens,
            )

            if not raw:
                return None

            return self._parse_llm_response(raw)

        except Exception:
            logger.exception("LLM quality assessment failed — falling back to deterministic")
            return None

    def _build_llm_prompt(self, step: int, step_data: dict, context: dict) -> str:
        # Truncate step_data for prompt size
        data_str = json.dumps(step_data, default=str)[:3000]
        return f"""You are a solution architecture quality assessor. Score these two dimensions for wizard step {step}.

SOLUTION CONTEXT:
- Problem: {context.get('problem_statement', '')[:500]}
- Domain: {context.get('business_domain', '')}
- Org size: {context.get('organization_size', '')}
- Budget: {context.get('budget_range', '')}
- Timeline: {context.get('timeline_months', '')} months

STEP {step} DATA:
{data_str}

Score these dimensions (0-100):
1. specificity: Are values specific and measurable, not vague? Is "manages work orders" just repeating the name?
2. feasibility: Are targets realistic for scope/budget/timeline? Is a 1→5 maturity jump in 3 months realistic?

Return ONLY valid JSON:
{{"specificity": {{"score": <int>, "details": "<one line>"}}, "feasibility": {{"score": <int>, "details": "<one line>"}}}}"""

    def _parse_llm_response(self, raw: str) -> Optional[List[QualityDimension]]:
        """Parse LLM JSON response into dimensions."""
        try:
            # Strip markdown code fences if present
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            result = json.loads(text)
            dims = []
            for name in ("specificity", "feasibility"):
                entry = result.get(name, {})
                score = int(entry.get("score", 70))
                score = max(0, min(100, score))
                status = "pass" if score >= 70 else ("warn" if score >= 50 else "fail")
                dims.append(QualityDimension(
                    name=name,
                    score=score,
                    weight=self.DIMENSION_WEIGHTS[name],
                    status=status,
                    details=entry.get("details", ""),
                ))
            return dims
        except (json.JSONDecodeError, ValueError, KeyError):
            logger.warning("Failed to parse LLM quality response")
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_dimensions(
        self,
        completeness: int,
        specificity: int,
        consistency: int,
        traceability: int,
        feasibility: int,
        codegen_readiness: int,
    ) -> List[QualityDimension]:
        """Build dimension list from raw scores."""
        def _status(score: int) -> str:
            if score >= 70:
                return "pass"
            if score >= 50:
                return "warn"
            return "fail"

        return [
            QualityDimension("completeness", completeness, 0.25, _status(completeness), "Are all expected fields filled?"),
            QualityDimension("specificity", specificity, 0.25, _status(specificity), "Are values specific and measurable?"),
            QualityDimension("consistency", consistency, 0.15, _status(consistency), "Do values across fields agree?"),
            QualityDimension("traceability", traceability, 0.10, _status(traceability), "Do elements link back to capabilities?"),
            QualityDimension("feasibility", feasibility, 0.10, _status(feasibility), "Are targets realistic for scope?"),
            QualityDimension("codegen_readiness", codegen_readiness, 0.15, _status(codegen_readiness), "Enough detail for code generation?"),
        ]

    def _weighted_score(self, dimensions: List[QualityDimension]) -> int:
        """Calculate weighted overall score."""
        total = sum(d.score * d.weight for d in dimensions)
        return int(round(total))

    def _default_dimensions(self) -> List[QualityDimension]:
        """Default dimensions when no step-specific checker exists."""
        return self._build_dimensions(50, 50, 50, 50, 50, 50)

    def to_dict(self, assessment: QualityAssessment) -> dict:
        """Serialize assessment for JSON response."""
        return {
            "step": assessment.step,
            "overall_score": assessment.overall_score,
            "threshold": assessment.threshold,
            "passed": assessment.passed,
            "hard_block": assessment.hard_block,
            "dimensions": [asdict(d) for d in assessment.dimensions],
            "failing_items": [asdict(i) for i in assessment.failing_items],
            "auto_fixable_count": assessment.auto_fixable_count,
            "estimated_fix_time": assessment.estimated_fix_time,
        }
