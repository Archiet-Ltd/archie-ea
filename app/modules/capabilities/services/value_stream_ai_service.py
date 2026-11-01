"""AI drafting assist for the value-stream x capability mapping grid.

The BIZBOK grid on a value stream's detail page (app/templates/value_streams/
detail.html, app/modules/capabilities/routes/value_stream_routes.py) is
click-to-set: a user opens a cell and chooses a support level by hand. This
service suggests candidate (stage, capability) pairs from the stream's own
stages and this organization's real capability catalog — advisory only.
Nothing here is written to the database; the UI's "Apply" action reuses the
grid's existing POST /value-streams/api/mapping write path exactly, so the
AI-suggested pairs go through the same validation and persistence as a
manually-set cell.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Set

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_application_capability_mapping import (
    UnifiedApplicationCapabilityMapping,
)
from app.models.unified_capability import (
    CapabilityValueStreamMapping,
    UnifiedCapability,
    ValueStreamStage,
)
from app.modules.ai_chat.services.llm_service_impl import LLMService

logger = logging.getLogger(__name__)

MAX_CAPABILITIES = 50


class ValueStreamAISuggestError(Exception):
    """Raised when the LLM response cannot be parsed into usable suggestions.

    Never caught to fabricate a fallback value — per CLAUDE.md, a screen that
    fabricates a plausible value when the real one is missing is worse than
    one that shows nothing.
    """


class CapabilityCatalogUnavailableError(Exception):
    """Raised when deriving this org's mapped-capability names failed for a
    real reason (a DB error, a bad join) rather than because the org
    genuinely has no mappings yet.

    Previously both queries in _org_scoped_capability_names() swallowed
    their exceptions and logged, so a query failure silently degraded to an
    empty name set — indistinguishable from a genuinely-empty catalog. The
    route then told the user "No capabilities mapped in this organization
    yet — map one manually first", which is a lie when the real cause was a
    failed query: an error masquerading as an empty state. Raising this
    (only when every query failed and nothing could be recovered) lets the
    route tell the two apart and return an honest 502 instead.
    """


def _vs_mapped_capability_names(limit: int) -> List[str]:
    """Capability names this org has mapped onto a value-stream stage."""
    rows = (
        db.session.query(UnifiedCapability.name).filter(UnifiedCapability.retired_into_id.is_(None))
        .join(
            CapabilityValueStreamMapping,
            CapabilityValueStreamMapping.capability_id == UnifiedCapability.id,
        )
        .distinct()
        .limit(limit)
        .all()
    )
    return [name for (name,) in rows if name]


def _app_mapped_capability_names(limit: int) -> List[str]:
    """Capability names mapped to an application component this org owns."""
    rows = (
        db.session.query(UnifiedCapability.name).filter(UnifiedCapability.retired_into_id.is_(None))
        .join(
            UnifiedApplicationCapabilityMapping,
            UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
        )
        .join(
            ApplicationComponent,
            UnifiedApplicationCapabilityMapping.application_component_id
            == ApplicationComponent.id,
        )
        .distinct()
        .limit(limit)
        .all()
    )
    return [name for (name,) in rows if name]


def _org_scoped_capability_names(limit: int = MAX_CAPABILITIES) -> List[str]:
    """This org's real *mapped* capability names — never the global catalog.

    UnifiedCapability has no organization_id column at all (it's a shared,
    cross-org catalog), so querying it directly would egress every other
    org's capability names into this org's LLM prompt. Instead this derives
    names from the union of two things this org has actually mapped:

    - CapabilityValueStreamMapping (TenantMixin) — capabilities this org has
      mapped onto a value-stream stage anywhere. do_orm_execute injects the
      organization_id filter directly onto this entity.
    - UnifiedApplicationCapabilityMapping, joined to ApplicationComponent
      (TenantMixin). The mapping table itself carries no organization_id,
      but the join target does, and do_orm_execute's with_loader_criteria
      applies to any TenantMixin entity present in the query — including a
      join target — so this is scoped to this org's applications.

    A failure in one query still lets the other contribute real names, and
    when it does the partial-but-real result set is used — with a warning
    naming which source failed, so the gap is visible in logs even though
    the caller gets a 200. It is only when a query failed *and* the combined
    name set is empty that a CapabilityCatalogUnavailableError is raised:
    with nothing recovered, an empty list is indistinguishable from "this
    org genuinely has no mappings", and that ambiguity is exactly what the
    route's honest-502 path exists to resolve. Two clean queries that both
    return nothing is the one case that is trusted as genuinely empty.
    """
    names: Set[str] = set()
    failed_sources: List[str] = []

    try:
        names.update(_vs_mapped_capability_names(limit))
    except Exception:
        failed_sources.append("value-stream mappings")
        logger.exception(
            "Failed to derive org-scoped capability names from value-stream mappings"
        )

    try:
        names.update(_app_mapped_capability_names(limit))
    except Exception:
        failed_sources.append("application mappings")
        logger.exception(
            "Failed to derive org-scoped capability names from application mappings"
        )

    if failed_sources and not names:
        raise CapabilityCatalogUnavailableError(
            "Capability-catalog quer" + ("y" if len(failed_sources) == 1 else "ies")
            + f" ({', '.join(failed_sources)}) failed and no real capability "
            "names could be recovered from the other source; the empty "
            "result cannot be trusted as a genuine empty state."
        )

    if failed_sources:
        logger.warning(
            "Proceeding with partial capability-catalog results: %s failed, "
            "but %d real name(s) were recovered from the other source",
            " and ".join(failed_sources),
            len(names),
        )

    return sorted(names)[:limit]


def build_context(value_stream) -> Dict[str, Any]:
    """Assemble only the context this function actually queries: the
    stream's real stages, and this organization's real *mapped* capability
    names (capped so the prompt stays small). Nothing is inferred, invented,
    or pulled from another org's data.
    """
    stages = (
        ValueStreamStage.query.filter(
            ValueStreamStage.value_stream_id == value_stream.id
        )
        .order_by(ValueStreamStage.stage_order)
        .all()
    )
    stage_names = [s.name for s in stages if s.name]

    capability_names = _org_scoped_capability_names()

    return {
        "value_stream_name": value_stream.name,
        "value_stream_description": value_stream.description or None,
        "stages": stage_names,
        "capabilities": capability_names,
    }


def _build_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting a Business Architect mapping capabilities to "
        "value-stream stages (BIZBOK capability x stage grid). Below is the "
        "real, verified context: this stream's actual stages and this "
        "organization's actual capability catalog. Only ever name a stage "
        "or capability that appears verbatim in this context — never invent "
        "one.\n\n"
        f"Context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Suggest which capabilities most strongly support which stages. "
        "Respond ONLY with a single JSON object with exactly these keys:\n"
        '- "suggestions": array of objects, each with "stage" (must exactly '
        'match one of the names in "stages"), "capability" (must exactly '
        'match one of the names in "capabilities"), and "rationale" (a short '
        "string explaining the mapping)\n"
        '- "summary": string, 1-3 sentences summarizing the suggested mapping\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_suggestions(
    raw: str, valid_stages: Set[str], valid_capabilities: Set[str]
) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueStreamAISuggestError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueStreamAISuggestError("LLM response was not a JSON object")

    required_keys = {"suggestions", "summary"}
    missing = required_keys - data.keys()
    if missing:
        raise ValueStreamAISuggestError(
            f"LLM response missing required keys: {sorted(missing)}"
        )

    raw_suggestions = data["suggestions"]
    if not isinstance(raw_suggestions, list):
        raise ValueStreamAISuggestError("LLM response field 'suggestions' was not a list")
    if not isinstance(data["summary"], str):
        raise ValueStreamAISuggestError("LLM response field 'summary' was not a string")

    kept: List[Dict[str, str]] = []
    for item in raw_suggestions:
        if not isinstance(item, dict):
            continue
        stage = item.get("stage")
        capability = item.get("capability")
        rationale = item.get("rationale")
        # Drop any suggestion naming a stage or capability that is not
        # actually part of this stream / this org's catalog — the LLM must
        # not be allowed to invent a mapping target.
        if stage not in valid_stages or capability not in valid_capabilities:
            continue
        kept.append(
            {
                "stage": stage,
                "capability": capability,
                "rationale": str(rationale) if rationale is not None else "",
            }
        )

    if not kept:
        raise ValueStreamAISuggestError(
            "LLM suggested no stage/capability pairs present in the real context"
        )

    return {"suggestions": kept, "summary": data["summary"]}


def generate_stage_mapping_suggestions_from_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AI-suggested (stage, capability) mapping candidates from an
    already-built context (see build_context()). Split out so the route can
    inspect the context — and short-circuit without calling the LLM at all
    when this org has no mapped capabilities yet — before generating.

    Advisory only — the caller must not persist any suggestion directly; the
    UI's "Apply" action goes through the grid's existing write endpoint.
    Raises ValueStreamAISuggestError (or lets an LLMService exception
    propagate) rather than fabricating a fallback suggestion list.
    """
    valid_stages = set(context["stages"])
    valid_capabilities = set(context["capabilities"])
    prompt = _build_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_suggestions(raw, valid_stages, valid_capabilities)


def generate_stage_mapping_suggestions(value_stream) -> Dict[str, Any]:
    """build_context() + generate_stage_mapping_suggestions_from_context()."""
    return generate_stage_mapping_suggestions_from_context(build_context(value_stream))
