"""The strategy drafting engine — one engine for every framework canvas.

A signed-in architect opens a strategy canvas (SWOT, PESTLE, and any later
framework that follows the same contract) for their organisation and presses
"Draft from my model". This service reads the organisation's own ArchiMate
model, asks the product's configured model provider to propose points for
each box, drops any point that is not grounded in a real record (the critique
step), and returns proposals the person accepts, edits or rejects. Every
proposal cites a recorded element that has a detail page to land on; there is
no ungrounded source. Nothing is written until a person accepts.

Nothing here is per framework: the boxes and their profile values come from
the canvas template (CANVAS_TEMPLATES) by key, and the prompt is the file
app/prompts/strategy/<key>.txt found by that same key. Adding a framework is
a template entry plus a prompt file, not a change here.

Reuse, not reinvention:
- the model call is LLMService.generate_from_prompt (the product's configured
  provider; no new provider or key);
- an accepted point becomes an Assessment element written through the one
  backbone element writer (archimate_backbone.create_backbone_element), with
  the box's profile value in acm_properties so the canvas reads it back;
- the caller's active organisation is current_org_id — never trusted from the
  request body.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "strategy")

# How many of the organisation's own records are offered to the model. Bounds
# the prompt size; the records are the only facts the model may use. The
# window is the organisation's most RECENT records, not its lowest ids, so a
# large estate drafts from the applications, risks and costs an architect has
# just recorded rather than from the oldest seed rows.
MAX_RECORDS = 120

# A single model call may not exceed this many seconds. Two calls run per
# draft (propose, then critique); each is bounded so a slow provider surfaces
# as an error the panel shows rather than hanging past the worker timeout.
MODEL_TIMEOUT_SECONDS = 25


# The ArchiMate layer the strategy frameworks' own writer records a new
# element under (create_backbone_element takes a capitalised layer). A cited
# record, by contrast, links to its OWN recorded layer, read off the element.
_LAYER_FOR_TYPE = {
    "Product": "business",
    "BusinessService": "business",
    "Assessment": "motivation",
    "Goal": "motivation",
    "Driver": "motivation",
}

# The layer segments the element detail route accepts; a cited element links
# to its detail page only when its recorded layer is one of these.
_DETAIL_LAYERS = frozenset(
    {"motivation", "strategy", "business", "application", "technology", "physical", "implementation"}
)


def _detail_href(element) -> Optional[str]:
    """A link to the element's own detail page — a page that renders its
    fields and relationships — or None when its recorded layer has no detail
    route, so the footer shows a label without a dead link rather than
    landing nowhere. The layer is read off the element, not re-derived here,
    so there is no second type-to-layer map to drift from the real one.
    """
    from flask import url_for

    layer = str(getattr(element, "layer", "") or "").strip().lower()
    if layer not in _DETAIL_LAYERS:
        return None
    try:
        return url_for(
            "archimate_crud.detail_element",
            layer=layer,
            element_type=element.type,
            element_id=element.id,
        )
    except Exception:  # noqa: BLE001 - a missing endpoint must not 500 the panel
        return None


class StrategyDraftError(Exception):
    """Raised when the model response cannot be parsed into usable proposals.

    Never caught to fabricate a fallback — per CLAUDE.md, a screen that
    invents a plausible value when the real one is missing is worse than one
    that shows nothing.
    """


def _template(template_key: str) -> Dict[str, Any]:
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    tpl = CANVAS_TEMPLATES.get(template_key)
    if tpl is None:
        raise ValueError(f"Unknown strategy framework: {template_key!r}")
    return tpl


def has_prompt(template_key: str) -> bool:
    """True when a drafting prompt file ships for this framework.

    The drafting panel shows for any template with a prompt file; adding a
    framework to the engine is dropping a prompt file beside its template
    entry, not a code change here.
    """
    if not template_key or template_key.startswith("_"):
        return False
    return os.path.isfile(os.path.join(_PROMPT_DIR, f"{template_key}.txt"))


def is_strategy_framework(template_key: str) -> bool:
    """True when this engine can draft and accept for the framework: it has a
    prompt file and every box an accepted point lands in reads Assessment
    elements by a profile value (the engine writes Assessment points)."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    tpl = CANVAS_TEMPLATES.get(template_key)
    if tpl is None or not has_prompt(template_key):
        return False
    zones = tpl["zones"]
    return bool(zones) and all(
        z["membership"] == "type_profile" and z["element_types"] == ["Assessment"]
        for z in zones
    )


def _box_profile(template_key: str) -> Dict[str, str]:
    """Map each box_key of the framework to the profile value naming it."""
    return {z["box_key"]: z["profile"] for z in _template(template_key)["zones"]}


def _proposal_id(template_key: str, box_key: str, point: str) -> str:
    """A stable id for one accepted point, so a double-click or a retry of the
    same accept lands on the same element rather than creating a second one.
    Keyed by the framework, the box and the (edited) point text."""
    import hashlib

    digest = hashlib.sha256(
        f"{template_key}\x1f{box_key}\x1f{point.strip()}".encode("utf-8")
    ).hexdigest()
    return digest[:32]


def _load_prompt(name: str) -> str:
    path = os.path.join(_PROMPT_DIR, f"{name}.txt")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _call_model(prompt: str) -> str:
    """One model call, bounded by MODEL_TIMEOUT_SECONDS so a slow provider
    surfaces as an error the panel shows rather than hanging the worker.
    Caching is off: a draft reads the organisation's current model each time."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    return LLMService.generate_from_prompt(
        prompt, use_cache=False, timeout=MODEL_TIMEOUT_SECONDS
    )


def _organisation(organization_id: int):
    from app.models.organization import Organization

    return Organization.query.filter_by(id=organization_id).first()


def _gather_records(organization_id: int) -> List[Dict[str, Any]]:
    """The organisation's own model as citable records.

    Reads ArchiMateElement (tenant-scoped) for this organisation. Each record
    is {id, kind, name, description}; the id is the element's own id as a
    string, which an accepted point cites. Linked facts — the applications,
    risks and costs an architect has recorded — are already elements on the
    backbone, so they are read here with everything else.
    """
    from app.models.archimate_core import ArchiMateElement

    # Most recent first, so the window is the estate the architect is working
    # in now, not the oldest seed rows. Previously accepted strategy points are
    # themselves Assessment elements carrying a `framework` property; excluding
    # them stops a second draft citing a past SWOT strength as the evidence for
    # a new one (circular grounding that would otherwise pass the filter).
    rows = (
        ArchiMateElement.query.filter_by(organization_id=organization_id)
        .order_by(ArchiMateElement.id.desc())
        .all()
    )
    records = []
    for row in rows:
        if (row.acm_properties or {}).get("framework"):
            continue
        records.append(
            {
                "id": str(row.id),
                "kind": row.type or "element",
                "name": row.name or "",
                "description": (row.description or "")[:400],
            }
        )
        if len(records) >= MAX_RECORDS:
            break
    return records


def _render_records(records: List[Dict[str, Any]]) -> str:
    if not records:
        return "(no records recorded yet)"
    lines = []
    for r in records:
        line = f"- id {r['id']} [{r['kind']}] {r['name']}"
        if r["description"]:
            line += f": {r['description']}"
        lines.append(line)
    return "\n".join(lines)


def _valid_citation_ids(records: List[Dict[str, Any]]) -> set:
    """The ids a point may cite: the organisation's own records, each of which
    has a real detail page to land on. There is no ungrounded source — a point
    that cannot cite a recorded element never reaches the screen."""
    return {r["id"] for r in records}


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_boxes(raw: str, box_keys: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Parse the model's JSON into {box_key: [{point, citations}, ...]}.

    Tolerant of a box the model omitted (treated as empty) but strict about
    the overall shape: a non-object response, or a box whose value is not a
    list, is a parse failure rather than a silent empty — the difference
    between "the model found nothing" and "the model did not answer" must not
    be hidden.
    """
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise StrategyDraftError(f"Model response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StrategyDraftError("Model response was not a JSON object")

    out: Dict[str, List[Dict[str, Any]]] = {}
    for box_key in box_keys:
        value = data.get(box_key, [])
        if value in (None, ""):
            value = []
        if not isinstance(value, list):
            raise StrategyDraftError(f"Model response field {box_key!r} was not a list")
        points = []
        for item in value:
            if not isinstance(item, dict):
                continue
            point = item.get("point")
            citations = item.get("citations") or []
            if not isinstance(point, str) or not point.strip():
                continue
            if not isinstance(citations, list):
                citations = []
            points.append(
                {"point": point.strip(), "citations": [str(c) for c in citations]}
            )
        out[box_key] = points
    return out


def _critique(
    template_key: str,
    boxes: Dict[str, List[Dict[str, Any]]],
    records: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """The critique step: drop uncited or generic points.

    Runs a second model pass over the proposed points with the source records,
    then enforces the citation rule deterministically in code — a point whose
    citations do not all resolve to a real record id in this organisation
    never reaches the screen, whatever the model returns. The model pass
    removes generic boilerplate; the code pass is the hard guarantee that no
    uncited point survives.
    """
    valid_ids = _valid_citation_ids(records)

    critiqued = boxes
    prompt = _load_prompt("_critique").format(
        records=_render_records(records),
        generated=json.dumps(boxes),
    )
    try:
        raw = _call_model(prompt)
        critiqued = _parse_boxes(raw, list(boxes.keys()))
    except StrategyDraftError:
        # The critique pass itself did not return usable JSON. Fall back to the
        # proposals as drafted; the deterministic citation filter below still
        # removes every uncited point, so nothing ungrounded can slip through.
        critiqued = boxes
    except Exception as exc:  # noqa: BLE001 - critique is best-effort over the hard filter below
        logger.warning("Strategy critique pass failed, applying citation filter only: %s", exc)
        critiqued = boxes

    # Deterministic guarantee: every surviving point must carry at least one
    # citation, and every citation must resolve to a record in this
    # organisation.
    filtered: Dict[str, List[Dict[str, Any]]] = {}
    for box_key, points in critiqued.items():
        kept = []
        for item in points:
            citations = [c for c in item.get("citations", []) if c in valid_ids]
            if not citations:
                continue
            kept.append({"point": item["point"], "citations": citations})
        filtered[box_key] = kept
    return filtered


def draft(template_key: str, organization_id: int) -> Dict[str, Any]:
    """Draft proposals for every box of the framework from the organisation's
    own model.

    Returns {"framework": key, "boxes": {box_key: [{point, citations}, ...]}}.
    Advisory only — nothing is written. Raises ValueError for an unknown
    framework or a missing organisation, and StrategyDraftError when the model
    response cannot be parsed.
    """
    if not is_strategy_framework(template_key):
        raise ValueError(f"{template_key!r} is not a strategy framework canvas")
    if not organization_id:
        raise ValueError("An organisation is required to draft from a model")

    organisation = _organisation(organization_id)
    if organisation is None:
        raise ValueError(f"Organisation {organization_id} not found")

    tpl = _template(template_key)
    box_keys = [z["box_key"] for z in tpl["zones"]]
    records = _gather_records(organization_id)

    prompt = _load_prompt(template_key).format(
        organisation_name=organisation.name,
        records=_render_records(records),
    )

    raw = _call_model(prompt)
    boxes = _parse_boxes(raw, box_keys)
    boxes = _critique(template_key, boxes, records)
    return {"framework": template_key, "boxes": boxes}


def saved_boxes(template_key: str, organization_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """The accepted points already on the canvas, grouped by box.

    Reads the organisation's Assessment elements (tenant-scoped) and places
    each into the box whose profile value it carries in acm_properties — the
    same membership check a type_profile zone uses. Returns {box_key: [{id,
    point, citations}, ...]} with every box present (empty when nothing has
    been accepted into it). An element whose profile is not one of this
    framework's boxes is simply not shown here; it belongs to another canvas.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.composer_export_formats import canvas_box_for_element

    tpl = _template(template_key)
    element_types = sorted({t for z in tpl["zones"] for t in z["element_types"]})
    boxes: Dict[str, List[Dict[str, Any]]] = {z["box_key"]: [] for z in tpl["zones"]}

    rows = (
        ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == organization_id,
            ArchiMateElement.type.in_(element_types),
        )
        .order_by(ArchiMateElement.id)
        .all()
    )
    for row in rows:
        # The one membership rule decides the box, under the template's own
        # membership key, so a product filed in BCG and Ansoff shows in both.
        box_key = canvas_box_for_element(row, template_key)
        if box_key is None:
            continue
        props = row.acm_properties or {}
        boxes[box_key].append(
            {
                "id": row.id,
                "point": row.name,
                "citations": props.get("citations") or [],
            }
        )
    return boxes


def project(template_key: str, organization_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Place the organisation's own profiled elements into the template's boxes.

    One projection for every framework, keyed by box_key. A type_profile box
    maps to the element rows (id, name, type, detail_href) filed in it through
    the one membership rule; the Blue Ocean value_curve attribute box maps to
    one curve point per competing-factor Assessment (factor, player, level,
    detail_href), a missing level staying None so the curve shows a gap, never
    a fabricated zero. An empty organisation returns every box present but
    empty. Reused by the canvas view and by the two-organisation tests.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.composer_export_formats import canvas_box_for_element

    tpl = _template(template_key)
    placed: Dict[str, List[Dict[str, Any]]] = {z["box_key"]: [] for z in tpl["zones"]}
    if not organization_id:
        return placed

    element_types = sorted({t for z in tpl["zones"] for t in z["element_types"]})
    if not element_types:
        return placed

    rows = ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == organization_id,
        ArchiMateElement.type.in_(element_types),
    ).all()

    # type_profile boxes, through the one membership rule.
    by_box: Dict[str, List] = {}
    for row in rows:
        box_key = canvas_box_for_element(row, template_key)
        if box_key is not None:
            by_box.setdefault(box_key, []).append(row)

    for zone in tpl["zones"]:
        if zone["membership"] == "type_profile":
            matched = sorted(by_box.get(zone["box_key"], []), key=lambda el: (el.name or "").lower())
            rows_out = []
            for el in matched:
                row = {
                    "id": el.id,
                    "name": el.name,
                    "type": el.type,
                    "detail_href": _detail_href(el),
                }
                # A drafted Assessment point carries the records it rests on;
                # surface them so the footer can resolve each to a link.
                cites = (el.acm_properties or {}).get("citations")
                if cites:
                    row["citations"] = cites
                rows_out.append(row)
            placed[zone["box_key"]] = rows_out
        elif zone["membership"] == "attribute" and zone.get("anchor_box"):
            # The value curve reads every element in its anchor box that
            # carries an offering level; a factor with no level is a gap.
            anchor_rows = by_box.get(zone["anchor_box"], [])
            points = []
            for el in anchor_rows:
                props = el.acm_properties or {}
                level = props.get("offering_level")
                if isinstance(level, dict):
                    level = level.get("value")
                try:
                    level = None if level in (None, "") else int(level)
                except (TypeError, ValueError):
                    level = None
                player = props.get("player")
                if isinstance(player, dict):
                    player = player.get("value")
                points.append(
                    {
                        "id": el.id,
                        "factor": el.name,
                        "player": player,
                        "offering_level": level,
                        "detail_href": _detail_href(el),
                    }
                )
            points.sort(key=lambda p: (p["factor"] or "").lower())
            placed[zone["box_key"]] = points
    return placed


def unclassified_count(template_key: str, organization_id: int) -> int:
    """How many of the organisation's elements of this framework's box types
    carry no box value FOR THIS FRAMEWORK — the honest "not yet classified"
    figure, counted per framework through the one membership rule. A product
    classified in BCG but not Ansoff counts as unclassified in Ansoff only,
    never in both, because each framework reads its own membership key.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.composer_export_formats import canvas_box_for_element

    tpl = _template(template_key)
    if not organization_id:
        return 0
    element_types = sorted(
        {t for z in tpl["zones"] if z["membership"] == "type_profile" for t in z["element_types"]}
    )
    if not element_types:
        return 0

    rows = ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == organization_id,
        ArchiMateElement.type.in_(element_types),
    ).all()
    return sum(1 for row in rows if canvas_box_for_element(row, template_key) is None)


def add_element(
    template_key: str,
    box_key: str,
    name: str,
    organization_id: int,
    *,
    element_type: Optional[str] = None,
    offering_level=None,
    player: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an element in a box and file it there through the one writer.

    For an element-backed framework (a Product in a BCG quadrant, a competing
    factor on the Blue Ocean curve) this is how a person adds directly from
    the canvas. The box value is stored under the template's membership key,
    so a product filed in a BCG quadrant and an Ansoff quadrant carries both.
    Raises ValueError on a blank name, an unknown box, a wrong type, or no
    active organisation — surfaced to the person, never swallowed.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A name is required.")
    if not organization_id:
        raise ValueError("An organisation is required.")

    tpl = _template(template_key)
    zone = next((z for z in tpl["zones"] if z["box_key"] == box_key), None)
    if zone is None:
        raise ValueError(f"{template_key!r} has no box {box_key!r}.")
    if zone["membership"] != "type_profile":
        raise ValueError(f"Box {box_key!r} is not one an element can be added to.")

    chosen_type = element_type or zone["element_types"][0]
    if chosen_type not in zone["element_types"]:
        raise ValueError(
            f"Box {box_key!r} holds {zone['element_types']}, not {chosen_type!r}."
        )

    from app import db
    from app.services.archimate_backbone import create_backbone_element
    from app.services.composer_export_formats import canvas_profile_key

    element = create_backbone_element(
        element_type=chosen_type,
        layer=_LAYER_FOR_TYPE.get(chosen_type, "Business").capitalize(),
        name=name,
        organization_id=organization_id,
        provenance={"source_model": "strategy_canvas", "framework": template_key},
    )
    props = dict(element.acm_properties or {})
    props[canvas_profile_key(template_key)] = zone["profile"]
    props["framework"] = template_key
    # The Blue Ocean value curve reads a competing factor's player and 0–10
    # level off the element; both are plain values, never a derived score.
    if offering_level not in (None, ""):
        try:
            props["offering_level"] = max(0, min(10, int(offering_level)))
        except (TypeError, ValueError):
            raise ValueError("Offering level must be a whole number from 0 to 10.")
    if player:
        props["player"] = player.strip()
    element.acm_properties = props  # untyped-property-write-ok: canvas membership reads the raw profile string (composer_export_formats.canvas_box_for_element), not a typed {"value":...} entry
    db.session.flush()
    return {"element_id": element.id, "box_key": box_key, "name": name}


def resolve_citations(
    citations: List[str], organization_id: int
) -> List[Dict[str, Any]]:
    """Turn a point's citation ids into display rows the footer renders.

    Each row is {label, kind, href}. A record id resolves to the element's
    name and a link to its own detail page — a page with real content, never a
    dead or self-referential link. A citation that no longer resolves to an
    element in this organisation is dropped rather than shown as a dead link,
    so the footer never lands a person on nothing.
    """
    from app.models.archimate_core import ArchiMateElement

    rows: List[Dict[str, Any]] = []
    element_ids = []
    for c in citations or []:
        try:
            element_ids.append(int(c))
        except (TypeError, ValueError):
            continue

    if element_ids:
        found = {
            el.id: el
            for el in ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == organization_id,
                ArchiMateElement.id.in_(element_ids),
            ).all()
        }
        for element_id in element_ids:
            el = found.get(element_id)
            if el is None:
                continue
            rows.append(
                {
                    "label": el.name or f"Element {element_id}",
                    "kind": el.type or "element",
                    "href": _detail_href(el),
                }
            )
    return rows


def accept_point(
    template_key: str,
    box_key: str,
    point: str,
    citations: List[str],
    organization_id: int,
) -> Dict[str, Any]:
    """Save one accepted (or edited) point as an Assessment element in the box.

    The element carries the box's profile value under the template's
    membership key so the canvas reads it straight back, the framework and its
    assessment_type, and the citations the point rests on. Written through the
    one backbone element writer, so the organisation, layer and provenance are
    set in one place.

    Idempotent, keyed by the proposal id (framework, box, point): accepting
    the same proposal twice — a double-click, a retry — returns the existing
    element rather than creating a second one.

    Raises ValueError for an unknown framework/box, a blank point, a missing
    organisation, or a point with no citation that resolves to a record in
    this organisation — the no-point-without-a-citation rule, enforced at the
    write as well as at the draft.
    """
    if not is_strategy_framework(template_key):
        raise ValueError(f"{template_key!r} is not a strategy framework canvas")
    profiles = _box_profile(template_key)
    if box_key not in profiles:
        raise ValueError(f"{box_key!r} is not a box of {template_key}")
    if not isinstance(point, str) or not point.strip():
        raise ValueError("A point cannot be empty")
    if not organization_id:
        raise ValueError("An organisation is required to accept a point")

    organisation = _organisation(organization_id)
    if organisation is None:
        raise ValueError(f"Organisation {organization_id} not found")

    records = _gather_records(organization_id)
    valid_ids = _valid_citation_ids(records)
    resolved = [str(c) for c in (citations or []) if str(c) in valid_ids]
    if not resolved:
        raise ValueError(
            "A point cannot be saved without a citation to a record in this "
            "organisation"
        )

    point = point.strip()
    proposal_id = _proposal_id(template_key, box_key, point)

    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_backbone import create_backbone_element
    from app.services.composer_export_formats import canvas_profile_key

    profile_key = canvas_profile_key(template_key)

    # Idempotency: an accepted point is an Assessment already carrying this
    # proposal id. If one exists in this organisation, return it unchanged.
    existing = (
        ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == organization_id,
            ArchiMateElement.type == "Assessment",
        )
        .order_by(ArchiMateElement.id)
        .all()
    )
    for el in existing:
        if (el.acm_properties or {}).get("proposal_id") == proposal_id:
            return {
                "element_id": el.id,
                "box_key": box_key,
                "profile": profiles[box_key],
                "point": el.name,
                "citations": (el.acm_properties or {}).get("citations") or resolved,
            }

    element = create_backbone_element(
        element_type="Assessment",
        layer="Motivation",
        name=point,
        description=point,
        organization_id=organization_id,
        provenance={
            "source_model": "strategy_draft",
            "framework": template_key,
        },
    )
    # The canvas reads a box's membership from the RAW string stored under the
    # template's membership key (composer_export_formats.canvas_box_for_element
    # compares it to the zone's profile string), so the profile, the
    # framework, its assessment_type, the citations and the proposal id are
    # stored as plain values here rather than through the typed-property
    # writer, whose {"value": ...} wrapper would make the membership
    # comparison never match.
    props = dict(element.acm_properties or {})
    props[profile_key] = profiles[box_key]
    props["framework"] = template_key
    props["assessment_type"] = template_key
    props["citations"] = resolved
    props["proposal_id"] = proposal_id
    element.acm_properties = props  # untyped-property-write-ok: canvas membership reads the raw profile string (composer_export_formats.canvas_box_for_element), not a typed {"value":...} entry
    db.session.flush()

    return {
        "element_id": element.id,
        "box_key": box_key,
        "profile": profiles[box_key],
        "point": point,
        "citations": resolved,
    }
