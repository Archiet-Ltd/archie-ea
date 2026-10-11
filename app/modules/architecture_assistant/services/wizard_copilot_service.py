"""Wizard Copilot Service (Layer A).

Real-time AI assistant for wizard steps. Watches fields and proactively
suggests improvements, completions, and corrections.

Two modes:
1. review_field: single field review on 2s idle debounce
2. review_step: batch review all fields ("Enhance All" button)

Returns CopilotSuggestion objects with original vs suggested values,
severity, rationale, and confidence scores.
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CopilotSuggestion:
    suggestion_id: str
    field_name: str
    severity: str  # "missing" | "weak" | "improvement"
    original_value: str
    suggested_value: str
    rationale: str
    impact: str
    confidence: float  # 0.0-1.0


# ---------------------------------------------------------------------------
# Deterministic checks per step (fast, free, always run first)
# ---------------------------------------------------------------------------

DETERMINISTIC_CHECKS = {
    1: [
        {"field": "problem_statement", "min_length": 100, "severity": "weak",
         "msg": "Problem statement too short — will produce generic architecture",
         "impact": "High — vague problems produce generic CRUD"},
        {"field": "budget_min", "required": True, "severity": "missing",
         "msg": "Budget range missing — cost estimates will be unrealistic",
         "impact": "Medium — affects option evaluation accuracy"},
        {"field": "timeline_months", "required": True, "severity": "missing",
         "msg": "Timeline not set — phasing will be arbitrary",
         "impact": "Medium — affects roadmap and migration planning"},
    ],
    2: [
        {"field": "description", "min_length": 30, "array": "capabilities", "severity": "weak",
         "msg": "Capability description too short — codegen can't derive domain logic",
         "impact": "High — empty descriptions produce generic CRUD"},
        {"field": "maturity_current", "required": True, "array": "capabilities", "severity": "missing",
         "msg": "Current maturity not set",
         "impact": "Medium — gap analysis will be imprecise"},
        {"field": "strategic_importance", "required": True, "array": "capabilities", "severity": "missing",
         "msg": "Strategic importance not set",
         "impact": "Low — affects prioritization in roadmap"},
    ],
    3: [
        {"field": "description", "min_length": 20, "array": "elements", "severity": "weak",
         "msg": "Element has no description — will produce placeholder code",
         "impact": "High — 25%+ of elements missing descriptions produces generic output"},
        {"field": "properties.build_buy", "required": True, "array": "elements", "severity": "missing",
         "msg": "Build/buy decision missing",
         "impact": "High — directly determines codegen vs integration stub"},
    ],
    6: [
        {"field": "section_narratives.problem", "min_length": 50, "severity": "weak",
         "msg": "Problem narrative too short for blueprint",
         "impact": "Medium — ARB reviewers need comprehensive context"},
        {"field": "section_narratives.recommended_solution", "min_length": 50, "severity": "weak",
         "msg": "Recommended solution narrative incomplete",
         "impact": "High — this drives the implementation approach in codegen"},
    ],
}


class WizardCopilotService:
    """Real-time field-level AI suggestions for wizard steps."""

    def review_field(
        self,
        solution_id: int,
        step: int,
        field_name: str,
        field_value: str,
        solution_context: dict,
    ) -> Optional[CopilotSuggestion]:
        """Single field review. Called on 2s idle debounce from frontend."""
        # Deterministic check first
        det_suggestion = self._deterministic_field_check(step, field_name, field_value)
        if det_suggestion:
            return det_suggestion

        # If field has content but might be weak, use LLM
        if field_value and len(str(field_value)) >= 10:
            return self._llm_field_review(step, field_name, field_value, solution_context)

        return None

    def review_step(
        self,
        solution_id: int,
        step: int,
        step_data: dict,
        solution_context: dict,
    ) -> List[CopilotSuggestion]:
        """Batch review all fields in current step. Called on 'Enhance All'."""
        suggestions = []

        # Deterministic pass
        suggestions.extend(self._deterministic_step_check(step, step_data))

        # LLM pass for specificity improvements
        llm_suggestions = self._llm_step_review(step, step_data, solution_context)
        if llm_suggestions:
            # Merge: don't duplicate fields already caught by deterministic
            existing_fields = {s.field_name for s in suggestions}
            for s in llm_suggestions:
                if s.field_name not in existing_fields:
                    suggestions.append(s)

        return suggestions

    def accept_suggestion(
        self,
        solution_id: int,
        suggestion_id: str,
        field_name: str,
        new_value: str,
    ) -> dict:
        """Track acceptance for analytics. Actual field update is handled by frontend."""
        from app import db
        from app.models.solution_models import Solution

        solution = Solution.query.get(solution_id)
        if solution is None:
            return {"accepted": False}

        journey_state = solution.journey_state or {}
        stats = journey_state.get("_copilot_stats", {
            "suggestions_shown": 0, "accepted": 0, "rejected": 0,
        })
        stats["accepted"] = stats.get("accepted", 0) + 1
        journey_state["_copilot_stats"] = stats
        solution.journey_state = journey_state
        db.session.commit()

        return {"accepted": True}

    # ------------------------------------------------------------------
    # Deterministic checks
    # ------------------------------------------------------------------

    def _deterministic_field_check(
        self, step: int, field_name: str, field_value: Any,
    ) -> Optional[CopilotSuggestion]:
        """Check a single field against deterministic rules."""
        checks = DETERMINISTIC_CHECKS.get(step, [])
        for check in checks:
            if check.get("array"):
                continue
            if check["field"] != field_name:
                continue
            if check.get("required") and not field_value:
                return CopilotSuggestion(
                    suggestion_id=str(uuid.uuid4()),
                    field_name=field_name,
                    severity="missing",
                    original_value=str(field_value) if field_value else "",
                    suggested_value=self._generate_deterministic_suggestion(field_name, "", step),
                    rationale=check["msg"],
                    impact=check["impact"],
                    confidence=1.0,
                )
            if check.get("min_length") and isinstance(field_value, str) and len(field_value) < check["min_length"]:
                return CopilotSuggestion(
                    suggestion_id=str(uuid.uuid4()),
                    field_name=field_name,
                    severity="weak",
                    original_value=field_value,
                    suggested_value=self._generate_deterministic_suggestion(field_name, field_value, step),
                    rationale=f"{check['msg']} (currently {len(field_value)} chars, need {check['min_length']}+)",
                    impact=check["impact"],
                    confidence=0.7,
                )
        return None

    def _generate_deterministic_suggestion(self, field_name: str, current: str, step: int) -> str:
        """Generate a concrete suggestion without LLM for common fields."""
        if field_name == "problem_statement" and current:
            return (
                f"{current}\n\n"
                "Expand with:\n"
                "- Who is affected (users, departments, customers)?\n"
                "- What is the business impact (cost, time, risk)?\n"
                "- What does success look like (measurable KPIs)?\n"
                "- What constraints exist (compliance, technology, budget)?"
            )
        if field_name == "problem_statement":
            return (
                "Our [department/team] needs to [solve specific problem] because [business impact]. "
                "Currently [describe current state and pain points]. "
                "The solution must [key requirements] within [timeline] and [budget range]. "
                "Success means [measurable outcomes]."
            )
        if field_name == "budget_min":
            return "Set a realistic minimum budget based on solution complexity"
        if field_name == "timeline_months":
            return "Set expected delivery timeline in months"
        return ""

    def _deterministic_step_check(
        self, step: int, step_data: dict,
    ) -> List[CopilotSuggestion]:
        """Run all deterministic checks for a step."""
        suggestions = []
        checks = DETERMINISTIC_CHECKS.get(step, [])

        for check in checks:
            if check.get("array"):
                items = step_data.get(check["array"], []) or []
                for idx, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    item_name = item.get("name", f"#{idx+1}")
                    value = self._get_nested(item, check["field"])
                    field_path = f"{item_name} → {check['field']}"
                    if check.get("required") and not value:
                        suggestions.append(CopilotSuggestion(
                            suggestion_id=str(uuid.uuid4()),
                            field_name=field_path,
                            severity="missing",
                            original_value="",
                            suggested_value=self._generate_deterministic_suggestion(check["field"], "", step),
                            rationale=check["msg"],
                            impact=check["impact"],
                            confidence=1.0,
                        ))
                    elif check.get("min_length") and isinstance(value, str) and len(value) < check["min_length"]:
                        suggestions.append(CopilotSuggestion(
                            suggestion_id=str(uuid.uuid4()),
                            field_name=field_path,
                            severity="weak",
                            original_value=value,
                            suggested_value=self._generate_deterministic_suggestion(check["field"], value, step),
                            rationale=f"{check['msg']} ({len(value)}/{check['min_length']} chars)",
                            impact=check["impact"],
                            confidence=0.7,
                        ))
            else:
                value = self._get_nested(step_data, check["field"])
                if check.get("required") and not value:
                    suggestions.append(CopilotSuggestion(
                        suggestion_id=str(uuid.uuid4()),
                        field_name=check["field"],
                        severity="missing",
                        original_value="",
                        suggested_value=self._generate_deterministic_suggestion(check["field"], "", step),
                        rationale=check["msg"],
                        impact=check["impact"],
                        confidence=1.0,
                    ))
                elif check.get("min_length") and isinstance(value, str) and len(value) < check["min_length"]:
                    suggestions.append(CopilotSuggestion(
                        suggestion_id=str(uuid.uuid4()),
                        field_name=check["field"],
                        severity="weak",
                        original_value=value,
                        suggested_value=self._generate_deterministic_suggestion(check["field"], value, step),
                        rationale=f"{check['msg']} ({len(value)}/{check['min_length']} chars)",
                        impact=check["impact"],
                        confidence=0.7,
                    ))

        return suggestions

    # ------------------------------------------------------------------
    # LLM review
    # ------------------------------------------------------------------

    def _llm_field_review(
        self, step: int, field_name: str, field_value: str, context: dict,
    ) -> Optional[CopilotSuggestion]:
        """LLM review of a single field for quality improvement."""
        try:
            from app.modules.ai_chat.services.llm_service import LLMService

            if not LLMService.is_available():
                return None

            provider, model = LLMService._get_configured_provider()
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=512)

            prompt = f"""You are a solution architecture quality coach. Review this field and suggest an improvement if needed.

Field: {field_name}
Value: {field_value[:500]}
Solution domain: {context.get('business_domain', '')}
Problem: {context.get('problem_statement', '')[:300]}

If the value is already good, return {{"needs_improvement": false}}.
If it needs improvement, return:
{{"needs_improvement": true, "suggested_value": "...", "rationale": "...", "impact": "High|Medium|Low — ...", "confidence": 0.0-1.0}}

Return ONLY valid JSON."""

            raw, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=max_tokens,
            )

            if not raw:
                return None

            return self._parse_field_review(raw, field_name, field_value)

        except Exception:
            logger.exception("Copilot field review LLM failed")
            return None

    def _llm_step_review(
        self, step: int, step_data: dict, context: dict,
    ) -> Optional[List[CopilotSuggestion]]:
        """LLM batch review of all fields in a step."""
        try:
            from app.modules.ai_chat.services.llm_service import LLMService

            if not LLMService.is_available():
                return None

            provider, model = LLMService._get_configured_provider()
            max_tokens = LLMService.get_max_tokens_limit(provider, model, requested_max=2048)

            data_str = json.dumps(step_data, default=str)[:3000]

            prompt = f"""You are a solution architecture quality coach reviewing wizard step {step} on Entelim.

CONTEXT:
- Problem: {context.get('problem_statement', '')[:400]}
- Domain: {context.get('business_domain', '')}
- Org size: {context.get('organization_size', '')}

STEP {step} DATA:
{data_str}

Review each field and return ONLY fields that need improvement. For each, provide:
- field_name: the field path
- severity: "missing" | "weak" | "improvement"
- suggested_value: the improved value
- rationale: why this change matters for code generation
- impact: "High|Medium|Low — explanation"
- confidence: 0.0-1.0

Return ONLY a JSON array of suggestions. Empty array if everything is good."""

            raw, _ = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider, max_tokens=max_tokens,
            )

            if not raw:
                return None

            return self._parse_step_review(raw)

        except Exception:
            logger.exception("Copilot step review LLM failed")
            return None

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_field_review(
        self, raw: str, field_name: str, original: str,
    ) -> Optional[CopilotSuggestion]:
        try:
            text = self._strip_fences(raw)
            result = json.loads(text)
            if not result.get("needs_improvement"):
                return None
            return CopilotSuggestion(
                suggestion_id=str(uuid.uuid4()),
                field_name=field_name,
                severity="improvement",
                original_value=original,
                suggested_value=result.get("suggested_value", ""),
                rationale=result.get("rationale", ""),
                impact=result.get("impact", ""),
                confidence=min(1.0, max(0.0, float(result.get("confidence", 0.7)))),
            )
        except (json.JSONDecodeError, ValueError):
            return None

    def _parse_step_review(self, raw: str) -> List[CopilotSuggestion]:
        try:
            text = self._strip_fences(raw)
            items = json.loads(text)
            if not isinstance(items, list):
                return []

            suggestions = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                suggestions.append(CopilotSuggestion(
                    suggestion_id=str(uuid.uuid4()),
                    field_name=item.get("field_name", ""),
                    severity=item.get("severity", "improvement"),
                    original_value=str(item.get("original_value", "")),
                    suggested_value=str(item.get("suggested_value", "")),
                    rationale=item.get("rationale", ""),
                    impact=item.get("impact", ""),
                    confidence=min(1.0, max(0.0, float(item.get("confidence", 0.7)))),
                ))
            return suggestions

        except (json.JSONDecodeError, ValueError):
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_nested(self, data: dict, path: str) -> Any:
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _strip_fences(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        return text.strip()

    def to_dict(self, suggestion: CopilotSuggestion) -> dict:
        return asdict(suggestion)

    def to_dict_list(self, suggestions: List[CopilotSuggestion]) -> List[dict]:
        return [asdict(s) for s in suggestions]
