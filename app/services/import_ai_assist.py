"""Opt-in AI-assist for diagram import.

The deterministic Lucid/Visio transformers are authoritative for everything they
can type from an explicit stencil class — an LLM there would only add variance.
This module handles the *remainder*: shapes the transformer could not type, and
relationships it had to drop because an endpoint was unresolved. It asks a
configured LLM to PROPOSE a type for each skipped shape and to propose the
dropped relationships between elements that WERE imported, and returns those as
suggestions only.

Nothing here writes to the database. The caller surfaces the suggestions in the
import-review screen; only the ones a human accepts are folded into the commit.
That keeps the system-of-record rule intact — the app never fabricates data, it
proposes and waits to be told yes.

If no LLM provider is configured the whole pass is skipped and the deterministic
import still succeeds; the return simply carries ``available: False`` with a
plain reason, mirroring the extract-from-image 503 behaviour.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ArchiMate 3.2 relationship types the composer/model understand. A proposed
# relationship carrying anything else is dropped rather than trusted.
ALLOWED_REL_TYPES = {
    "composition", "aggregation", "assignment", "realization", "serving",
    "access", "influence", "triggering", "flow", "specialization",
    "association",
}

# How many gaps we will hand the model in one pass. A real diagram's skip list
# is dozens at most; this only guards against a pathological upload.
_MAX_GAPS = 80


def _archimate_element_types() -> set:
    """The canonical ArchiMate 3.2 element-type vocabulary.

    Sourced from the transformer's own index so the two never drift — a
    proposed type the importer could not later persist is worse than no
    proposal.
    """
    try:
        from app.services.lucid_archimate_transformer import (
            LucidArchiMateTransformer,
        )
        index = LucidArchiMateTransformer()._canonical_type_index() or {}
        types = {v for v in index.values() if v}
        if types:
            return types
    except Exception:  # noqa: BLE001 - fall through to the static floor
        logger.debug("could not read transformer type index; using static set")
    # Static floor so the feature still validates if the index is unavailable.
    return {
        "BusinessActor", "BusinessRole", "BusinessCollaboration",
        "BusinessInterface", "BusinessProcess", "BusinessFunction",
        "BusinessInteraction", "BusinessEvent", "BusinessService",
        "BusinessObject", "Contract", "Representation", "Product",
        "ApplicationComponent", "ApplicationCollaboration",
        "ApplicationInterface", "ApplicationFunction", "ApplicationInteraction",
        "ApplicationProcess", "ApplicationEvent", "ApplicationService",
        "DataObject", "Node", "Device", "SystemSoftware",
        "TechnologyCollaboration", "TechnologyInterface", "Path",
        "CommunicationNetwork", "TechnologyFunction", "TechnologyProcess",
        "TechnologyInteraction", "TechnologyEvent", "TechnologyService",
        "Artifact", "Equipment", "Facility", "DistributionNetwork",
        "Material", "Stakeholder", "Driver", "Assessment", "Goal", "Outcome",
        "Principle", "Requirement", "Constraint", "Meaning", "Value",
        "Resource", "Capability", "CourseOfAction", "WorkPackage",
        "Deliverable", "ImplementationEvent", "Plateau", "Gap", "Location",
        "Grouping", "Junction",
    }


def _layer_for_type(element_type: str) -> str:
    """Best-effort ArchiMate layer for a proposed element type.

    Mirrors the transformer's own layering so a retyped element lands in the
    right layer band; falls back to 'other', which is what import_payload uses
    when a layer is absent.
    """
    t = element_type or ""
    if t.startswith("Business") or t in {
            "Contract", "Representation", "Product"}:
        return "business"
    if t.startswith("Application") or t == "DataObject":
        return "application"
    if t.startswith("Technology") or t in {
            "Node", "Device", "SystemSoftware", "Path", "CommunicationNetwork",
            "Artifact", "Equipment", "Facility", "DistributionNetwork",
            "Material"}:
        return "technology"
    if t in {"Stakeholder", "Driver", "Assessment", "Goal", "Outcome",
             "Principle", "Requirement", "Constraint", "Meaning", "Value"}:
        return "motivation"
    if t in {"Resource", "Capability", "CourseOfAction", "ValueStream"}:
        return "strategy"
    if t in {"WorkPackage", "Deliverable", "ImplementationEvent", "Plateau",
             "Gap"}:
        return "implementation"
    return "other"


def _strip_json_fence(text: str) -> str:
    """Pull the JSON body out of a ```json … ``` fence if the model added one."""
    if not text:
        return ""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    return (fenced.group(1) if fenced else text).strip()


def _build_prompt(elements: List[Dict[str, Any]],
                  skipped_shapes: List[Dict[str, Any]],
                  skipped_rels: List[Dict[str, Any]],
                  id_to_name: Dict[str, str]) -> str:
    """Compose the instruction the LLM answers with a JSON suggestion object."""
    known = [
        {"id": e.get("id"), "name": e.get("name"), "type": e.get("type")}
        for e in elements if e.get("id")
    ]

    def _rel_hint(r):
        s, t = r.get("source_id"), r.get("target_id")
        return {
            "label": r.get("label") or None,
            "source_id": s, "source_name": id_to_name.get(s),
            "target_id": t, "target_name": id_to_name.get(t),
        }

    payload = {
        "already_imported_elements": known,
        "shapes_needing_a_type": [
            {"id": s.get("id"), "name": s.get("name"),
             "lucid_class": s.get("lucid_class")}
            for s in skipped_shapes
        ],
        "dropped_relationships": [_rel_hint(r) for r in skipped_rels],
    }

    return (
        "You are an enterprise architect completing an ArchiMate 3.2 import.\n"
        "A deterministic importer already typed most shapes; you only handle the\n"
        "gaps it could not. Use ONLY the information given — do not invent\n"
        "elements that are not listed.\n\n"
        "TASK 1 — For each entry in `shapes_needing_a_type`, propose the single\n"
        "most likely ArchiMate 3.2 element type, judging from its name and its\n"
        "Lucid stencil class. If you genuinely cannot tell, omit it.\n\n"
        "TASK 2 — For each entry in `dropped_relationships`, propose the ArchiMate\n"
        "relationship type. Only include it if BOTH endpoints appear in\n"
        "`already_imported_elements` (match by the given ids). Use the label as\n"
        "the strongest signal (e.g. 'Triggers'->triggering, 'Assigned'->assignment,\n"
        "'Serves'/'Uses'->serving, a data payload->flow, 'Accesses'->access).\n"
        "You may also propose an obvious relationship between two imported\n"
        "elements that the list missed, but be conservative.\n\n"
        "Respond with ONLY a JSON object, no prose, exactly this shape:\n"
        '{"retype":[{"id":"<shape id>","proposed_type":"<ArchiMateType>",'
        '"rationale":"<short why>"}],'
        '"relationships":[{"source_id":"<id>","target_id":"<id>",'
        '"rel_type":"<archimate relationship>","rationale":"<short why>"}]}\n\n'
        "INPUT:\n" + json.dumps(payload, ensure_ascii=False)
    )


def _coerce_list(obj: Any, key: str) -> List[Dict[str, Any]]:
    val = obj.get(key) if isinstance(obj, dict) else None
    return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []


def suggest_import_completions(transformed: Dict[str, Any]) -> Dict[str, Any]:
    """Return AI suggestions for the transformer's un-typed shapes and dropped
    relationships. Never raises for an unconfigured provider — returns
    ``{"available": False, "reason": ...}`` instead so the import still ships.
    """
    skipped = transformed.get("skipped") or {}
    skipped_shapes = skipped.get("shapes") or []
    skipped_rels = skipped.get("relationships") or []
    elements = transformed.get("elements") or []

    if not skipped_shapes and not skipped_rels:
        return {"available": True, "retype": [], "relationships": [],
                "note": "The deterministic import left no gaps, so there is "
                        "nothing for AI assist to propose."}

    # Cap the work handed to the model.
    trimmed = False
    if len(skipped_shapes) + len(skipped_rels) > _MAX_GAPS:
        skipped_shapes = skipped_shapes[:_MAX_GAPS]
        skipped_rels = skipped_rels[: max(0, _MAX_GAPS - len(skipped_shapes))]
        trimmed = True

    id_to_name = {e.get("id"): e.get("name") for e in elements if e.get("id")}
    known_ids = set(id_to_name)
    skipped_shape_ids = {s.get("id") for s in skipped_shapes}
    prompt = _build_prompt(elements, skipped_shapes, skipped_rels, id_to_name)

    try:
        from app.services.llm_service import LLMService
        raw = LLMService.generate_from_prompt(prompt, use_cache=True)
    except Exception as exc:  # noqa: BLE001 - provider absent or call failed
        logger.info("import AI-assist unavailable: %s", exc)
        return {
            "available": False,
            "reason": "No LLM provider is configured (or the call failed), so "
                      "AI assist could not run. The import above is complete "
                      "and correct on its own; add an Anthropic, OpenAI or "
                      "Gemini key in Admin -> AI settings to enable AI assist.",
        }

    parsed = None
    try:
        parsed = json.loads(_strip_json_fence(raw))
    except (ValueError, TypeError):
        logger.info("import AI-assist returned unparseable JSON")
    if not isinstance(parsed, dict):
        return {"available": False,
                "reason": "The AI response could not be read as JSON; no "
                          "suggestions were produced. The import is unaffected."}

    allowed_types = _archimate_element_types()

    # ── validate retype suggestions ──
    retype: List[Dict[str, Any]] = []
    seen_ids = set()
    for item in _coerce_list(parsed, "retype"):
        sid = item.get("id")
        ptype = item.get("proposed_type")
        if sid not in skipped_shape_ids or sid in seen_ids:
            continue
        if ptype not in allowed_types:
            continue
        seen_ids.add(sid)
        retype.append({
            "id": sid,
            "name": next((s.get("name") for s in skipped_shapes
                          if s.get("id") == sid), None),
            "proposed_type": ptype,
            "layer": _layer_for_type(ptype),
            "rationale": (item.get("rationale") or "")[:240],
        })

    # ── validate relationship suggestions ──
    existing_pairs = {
        (r.get("source_id"), r.get("target_id"), r.get("type"))
        for r in transformed.get("relationships") or []
    }
    relationships: List[Dict[str, Any]] = []
    seen_pairs = set()
    for item in _coerce_list(parsed, "relationships"):
        s, t = item.get("source_id"), item.get("target_id")
        rtype = (item.get("rel_type") or "").strip().lower()
        if s not in known_ids or t not in known_ids or s == t:
            continue
        if rtype not in ALLOWED_REL_TYPES:
            continue
        key = (s, t, rtype)
        if key in existing_pairs or key in seen_pairs:
            continue
        seen_pairs.add(key)
        relationships.append({
            "source_id": s, "source_name": id_to_name.get(s),
            "target_id": t, "target_name": id_to_name.get(t),
            "rel_type": rtype,
            "rationale": (item.get("rationale") or "")[:240],
        })

    return {
        "available": True,
        "retype": retype,
        "relationships": relationships,
        "trimmed": trimmed,
    }
