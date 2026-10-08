"""Solution Options Advisor (AI-3).

The AI Solution Architect as a design partner: given a solution's context
(drivers, goals, constraints, requirements, capabilities), it generates
2-3 architecture OPTIONS with genuine trade-offs and writes an
Architecture Decision Record (ADR, Nygard format) for the recommended
option — the "options paper" a consultancy bills weeks for.

Grounding: context comes from SADDocumentBuilder (live solution data).
The LLM reasons over that context; it does not invent the solution's
facts. Output is persisted as an ArchitectureDecisionRecord with the
options in `alternatives_considered` and the decision in the Nygard
fields. ADR starts as 'proposed' — a human accepts/rejects (the ARB
disposes). Never raises to the caller; returns an error dict on failure.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from app import db

logger = logging.getLogger(__name__)


_PROMPT = """You are an expert Solution Architect. Using ONLY the solution context \
below, produce 2-3 distinct architecture OPTIONS with honest trade-offs, then \
recommend one and write an Architecture Decision Record for it.

Return STRICT JSON (no markdown, no prose outside the JSON) with this exact shape:
{{
  "options": [
    {{
      "name": "short option name",
      "approach": "build | buy | extend | hybrid | reuse",
      "summary": "1-2 sentence description",
      "pros": ["..."],
      "cons": ["..."],
      "cost_band": "low | medium | high",
      "timeline_months": <integer>,
      "risk": "low | medium | high",
      "recommended": <true for exactly ONE option, false for others>
    }}
  ],
  "decision": {{
    "title": "ADR title — the decision in a line",
    "context": "the problem and forces at play (reference the drivers/constraints)",
    "decision": "the chosen option and what it commits us to",
    "rationale": "why this option over the alternatives — be specific about the trade-offs",
    "consequences": "positive and negative implications, what becomes easier/harder",
    "estimated_effort": "Small | Medium | Large | XL",
    "business_value": "Low | Medium | High | Critical"
  }}
}}

=== Solution Context ===
Solution: {name}
{description}

Business drivers:
{drivers}

Goals:
{goals}

Constraints:
{constraints}

Key requirements:
{requirements}

Capabilities in scope:
{capabilities}
=== End Context ===

Remember: exactly one option has "recommended": true; costs/timelines are \
indicative; ground every point in the context above."""


class SolutionOptionsAdvisor:
    """Generate architecture options + an ADR for a solution."""

    @classmethod
    def generate(cls, solution_id: int, user_id: int) -> Dict[str, Any]:
        """Returns {"success": bool, "adr": {...}} or {"success": False, "error": ...}."""
        from app.models.solution_models import Solution

        solution = db.session.get(Solution, solution_id)
        if solution is None:
            return {"success": False, "error": "Solution not found."}

        prompt = cls._build_prompt(solution)

        try:
            from app.modules.ai_chat.services.llm_service_impl import LLMService
            provider, model = LLMService._get_configured_provider()
            raw, _interaction = LLMService._call_llm(
                prompt=prompt, model=model, provider=provider,
                user_id=user_id, max_tokens=2200,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Options advisor LLM call failed for sol %s: %s", solution_id, exc)
            return {"success": False, "error": "The AI provider is unavailable. Check API settings and try again."}

        parsed = cls._parse(raw)
        if parsed is None:
            logger.error("Options advisor could not parse LLM JSON for sol %s", solution_id)
            return {"success": False, "error": "The AI returned an unreadable response. Try regenerating."}

        try:
            adr = cls._persist(solution, parsed, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Options advisor persist failed for sol %s: %s", solution_id, exc)
            db.session.rollback()
            return {"success": False, "error": "Could not save the generated decision."}

        return {"success": True, "adr": cls.to_dict(adr)}

    @staticmethod
    def latest(solution_id: int):
        from app.models.architecture_decision import ArchitectureDecision
        return (
            ArchitectureDecision.query
            .filter_by(solution_id=solution_id)
            .order_by(ArchitectureDecision.id.desc())
            .first()
        )

    @classmethod
    def set_status(cls, adr_id: int, status: str, user_id: int) -> Dict[str, Any]:
        """Change status on the one canonical writer.

        architecture_decisions is the only writer (lead ruling):
        _persist() above creates every new decision there directly, with no
        paired legacy row at all, so adr_id here is always a canonical
        ArchitectureDecision id -- not .query.get(), which can return a
        cached cross-tenant row from the identity map on a hit.
        """
        from app.models.architecture_decision import ArchitectureDecision

        valid = {"proposed", "accepted", "rejected", "deprecated", "superseded"}
        if status not in valid:
            return {"success": False, "error": f"Invalid status. Allowed: {sorted(valid)}"}
        adr = ArchitectureDecision.query.filter_by(id=adr_id).first()
        if adr is None:
            return {"success": False, "error": "Decision not found."}
        adr.status = status
        if status == "accepted":
            adr.decided_at = datetime.utcnow()
        db.session.commit()
        result = cls.to_dict(adr)
        result["status"] = status
        return {"success": True, "adr": result}

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_prompt(solution) -> str:
        from app.services.sad_document_builder import SADDocumentBuilder
        ctx = SADDocumentBuilder.build(solution)

        def _bullets(items, fields):
            if not items:
                return "  (none captured)"
            out = []
            for it in items[:12]:
                label = next((it.get(f) for f in fields if it.get(f)), "")
                desc = it.get("description") or it.get("summary") or ""
                out.append(f"  - {label}{(': ' + desc[:160]) if desc else ''}")
            return "\n".join(out)

        return _PROMPT.format(
            name=solution.name,
            description=(solution.description or "")[:500],
            drivers=_bullets(ctx.get("drivers"), ["name"]),
            goals=_bullets(ctx.get("goals"), ["name"]),
            constraints=_bullets(ctx.get("constraints"), ["name"]),
            requirements=_bullets(ctx.get("requirements"), ["name"]),
            capabilities=_bullets(ctx.get("capabilities"), ["name"]),
        )

    @staticmethod
    def _parse(raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        # strip markdown fences if present
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        # extract the outermost JSON object
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or "options" not in data or "decision" not in data:
            return None
        if not isinstance(data["options"], list) or not data["options"]:
            return None
        return data

    @staticmethod
    def _persist(solution, parsed: Dict[str, Any], user_id: int):
        from app.models.architecture_decision import ArchitectureDecision

        options = parsed["options"]
        decision = parsed["decision"]

        adr = ArchitectureDecision(
            decision_id=ArchitectureDecision.next_decision_id(),
            title=(decision.get("title") or f"Architecture decision for {solution.name}")[:200],
            solution_id=solution.id,
            status="proposed",
            context=decision.get("context") or "—",
            decision=decision.get("decision") or "—",
            rationale=decision.get("rationale") or "—",
            consequences=decision.get("consequences") or "—",
            alternatives=json.dumps(options),
            estimated_effort=(decision.get("estimated_effort") or "")[:50] or None,
            business_value=(decision.get("business_value") or "")[:50] or None,
            decided_by_label="AI Solution Architect (proposed)",
            organization_id=solution.organization_id,
        )
        db.session.add(adr)
        db.session.flush()
        db.session.commit()
        logger.info("AI-3 ADR %s generated for solution %s (%d options)",
                    adr.id, solution.id, len(options))
        return adr

    @staticmethod
    def to_dict(adr) -> Dict[str, Any]:
        try:
            options = json.loads(adr.alternatives) if adr.alternatives else []
        except (ValueError, TypeError):
            options = []
        return {
            "id": adr.id,
            "decision_id": adr.decision_id,
            "title": adr.title,
            "status": adr.status,
            "context": adr.context,
            "decision": adr.decision,
            "rationale": adr.rationale,
            "consequences": adr.consequences,
            "estimated_effort": adr.estimated_effort,
            "business_value": adr.business_value,
            "options": options,
            "generated_at": adr.created_at.isoformat() if getattr(adr, "created_at", None) else None,
        }
