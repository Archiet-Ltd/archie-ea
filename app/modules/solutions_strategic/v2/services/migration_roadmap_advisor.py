"""Migration Roadmap Advisor (AI-8 / PROG-020).

The AI Solution Architect generates a TOGAF Phase F migration roadmap: a sequence
of transition PLATEAUS (stable interim architectures), each with an objective, a
horizon, and the WORK PACKAGES that move the estate from one plateau to the next —
the migration plan a programme office builds by hand.

Grounding mirrors AI-3 (SolutionOptionsAdvisor): context comes from the live
solution (SADDocumentBuilder + linked applications). The LLM sequences the work; it
does not invent the solution's facts. Output is persisted solution-scoped as a
SolutionMigrationRoadmap. Never raises to the caller; returns an error dict on
failure (LLM unavailable, unparseable response, persist error).
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from app import db

logger = logging.getLogger(__name__)


_PROMPT = """You are an expert Solution Architect producing a TOGAF Phase F \
(Migration Planning) roadmap. Using ONLY the solution context below, sequence the \
delivery into 2-4 transition PLATEAUS — each a stable interim architecture — and \
the work packages that move between them.

Return STRICT JSON (no markdown, no prose outside the JSON) with this exact shape:
{{
  "headline": "one-line summary of the migration approach",
  "summary": "2-3 sentence narrative of how the estate moves to the target state",
  "horizon_months": <integer total horizon>,
  "plateaus": [
    {{
      "name": "short plateau name (e.g. 'Plateau 1 — Foundation')",
      "sequence": <integer, 1-based>,
      "horizon_months": <integer months from start to reach this plateau>,
      "objective": "what this plateau achieves and why it is a safe stopping point",
      "target_state": "the architecture once this plateau is reached",
      "work_packages": [
        {{
          "name": "work package name",
          "effort": "S | M | L | XL",
          "depends_on": ["names of work packages this depends on, or empty"],
          "touches": "the applications/capabilities this work package changes"
        }}
      ]
    }}
  ]
}}

=== Solution Context ===
Solution: {name}
{description}

Goals:
{goals}

Constraints:
{constraints}

Key requirements:
{requirements}

Capabilities in scope:
{capabilities}

Applications in scope:
{applications}
=== End Context ===

Remember: plateaus are ordered and cumulative; each must be a viable stopping \
point (not a big-bang); ground every work package in the context above; \
horizons are indicative."""


class MigrationRoadmapAdvisor:
    """Generate a Phase F transition-plateau roadmap for a solution."""

    @classmethod
    def generate(cls, solution_id: int, user_id: int) -> Dict[str, Any]:
        """Returns {"success": bool, "roadmap": {...}} or {"success": False, "error": ...}."""
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
                user_id=user_id, max_tokens=2600,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Roadmap advisor LLM call failed for sol %s: %s", solution_id, exc)
            return {"success": False,
                    "error": "The AI provider is unavailable. Check AI settings and try again."}

        parsed = cls._parse(raw)
        if parsed is None:
            logger.error("Roadmap advisor could not parse LLM JSON for sol %s", solution_id)
            return {"success": False,
                    "error": "The AI returned an unreadable response. Try regenerating."}

        try:
            roadmap = cls._persist(solution, parsed, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Roadmap advisor persist failed for sol %s: %s", solution_id, exc)
            db.session.rollback()
            return {"success": False, "error": "Could not save the generated roadmap."}

        return {"success": True, "roadmap": roadmap.to_dict()}

    @staticmethod
    def latest(solution_id: int):
        from app.models.strategic import SolutionMigrationRoadmap
        return (
            SolutionMigrationRoadmap.query
            .filter_by(solution_id=solution_id)
            .order_by(SolutionMigrationRoadmap.id.desc())
            .first()
        )

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
                out.append(f"  - {label}{(': ' + desc[:140]) if desc else ''}")
            return "\n".join(out)

        try:
            apps = [a.name for a in (solution.applications or [])][:20]
        except Exception:
            apps = []
        app_block = "\n".join(f"  - {n}" for n in apps) if apps else "  (none linked)"

        return _PROMPT.format(
            name=solution.name,
            description=(solution.description or "")[:500],
            goals=_bullets(ctx.get("goals"), ["name"]),
            constraints=_bullets(ctx.get("constraints"), ["name"]),
            requirements=_bullets(ctx.get("requirements"), ["name"]),
            capabilities=_bullets(ctx.get("capabilities"), ["name"]),
            applications=app_block,
        )

    @staticmethod
    def _parse(raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or "plateaus" not in data:
            return None
        if not isinstance(data["plateaus"], list) or not data["plateaus"]:
            return None
        return data

    @staticmethod
    def _persist(solution, parsed: Dict[str, Any], user_id: int):
        from app.models.strategic import SolutionMigrationRoadmap

        plateaus = parsed["plateaus"]
        # normalise sequence + clamp shapes so the template never breaks
        for i, p in enumerate(plateaus, start=1):
            if not isinstance(p.get("sequence"), int):
                p["sequence"] = i
            if not isinstance(p.get("work_packages"), list):
                p["work_packages"] = []
        plateaus.sort(key=lambda p: p.get("sequence", 0))

        horizon = parsed.get("horizon_months")
        if not isinstance(horizon, int):
            horizon = max((p.get("horizon_months") or 0) for p in plateaus) or None

        roadmap = SolutionMigrationRoadmap(
            solution_id=solution.id,
            generated_by_id=user_id,
            headline=(parsed.get("headline") or f"Migration roadmap for {solution.name}")[:300],
            summary=parsed.get("summary") or None,
            horizon_months=horizon,
            plateau_count=len(plateaus),
            plateaus=plateaus,
        )
        db.session.add(roadmap)
        db.session.commit()
        logger.info("AI-8 roadmap %s generated for solution %s (%d plateaus)",
                    roadmap.id, solution.id, len(plateaus))
        return roadmap
