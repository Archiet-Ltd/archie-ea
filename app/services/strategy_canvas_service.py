"""Projection and writer for the three element-backed strategy canvases.

BCG, Ansoff and Blue Ocean do not have a canvas record of their own: each box
holds the organisation's own ArchiMate elements that carry a ``profile``
property naming that box. This service reads those elements for the active
tenant and groups them by box, and creates a new element in a box by writing
the box's type and profile through the one element writer.

The profile is stored as a plain string under ``acm_properties["profile"]`` —
the same shape ``app/services/composer_export_formats.py`` already reads when
it lists which boxes an export filled — so there is one place the canvas
profile lives, not a second one.

Tenant scope is the ORM's: ``ArchiMateElement`` carries ``TenantMixin``, so a
bare ``.query`` inside a request is already filtered to the caller's active
organisation. Outside a request there is no organisation and the reader
returns nothing rather than another tenant's rows.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.middleware.tenant_context import current_org_id
from app.services.archimate_backbone import create_backbone_element

logger = logging.getLogger(__name__)

PROFILE_PROPERTY = "profile"

# Blue Ocean's value curve reads these two attributes off a competing-factor
# Assessment. Both are plain strings/numbers under acm_properties, never a
# derived score.
OFFERING_LEVEL_PROPERTY = "offering_level"
PLAYER_PROPERTY = "player"

# The ArchiMate layer each element type the strategy canvases use belongs to,
# so the one element writer records it in the right band.
_LAYER_FOR_TYPE = {
    "Product": "Business",
    "BusinessService": "Business",
    "Assessment": "Motivation",
    "Goal": "Motivation",
}

# The strategy canvases this service owns (the three element-backed ones).
STRATEGY_CANVAS_KEYS = ("bcg_matrix", "ansoff_matrix", "blue_ocean_strategy")


def is_strategy_canvas(canvas_key: str) -> bool:
    """True for a canvas this service projects (record == "elements")."""
    tpl = CANVAS_TEMPLATES.get(canvas_key)
    return bool(tpl and tpl.get("record") == "elements")


def _read_profile_value(element) -> Optional[str]:
    props = getattr(element, "acm_properties", None) or {}
    value = props.get(PROFILE_PROPERTY)
    # Tolerate the provenance-wrapped shape the typed-property writer uses, as
    # well as the plain string the export reader expects.
    if isinstance(value, dict):
        return value.get("value")
    return value


def _read_attribute(element, key: str):
    props = getattr(element, "acm_properties", None) or {}
    value = props.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _element_entry(element) -> Dict:
    """One row for a box: the element's id, name and type — never an invented
    field. The name is what a reader clicks; the id is how the box re-finds it
    after a reload."""
    return {
        "id": element.id,
        "name": element.name,
        "type": element.type,
    }


def _curve_point(element) -> Dict:
    """One point on the value curve: the competing factor's name, the player
    whose offering it is, and the 0–10 level. A missing level stays None so the
    curve shows a gap rather than a fabricated zero."""
    level = _read_attribute(element, OFFERING_LEVEL_PROPERTY)
    try:
        level = None if level is None or level == "" else int(level)
    except (TypeError, ValueError):
        level = None
    return {
        "id": element.id,
        "factor": element.name,
        "player": _read_attribute(element, PLAYER_PROPERTY),
        "offering_level": level,
    }


def project(canvas_key: str) -> Dict[str, List[Dict]]:
    """Place the active tenant's profiled elements into the canvas's boxes.

    Returns a dict keyed by box_key. A ``type_profile`` box maps to the list
    of element entries carrying its profile; the Blue Ocean ``value_curve``
    attribute box maps to one point per competing-factor Assessment.
    """
    tpl = CANVAS_TEMPLATES.get(canvas_key)
    if tpl is None or tpl.get("record") != "elements":
        raise ValueError(f"{canvas_key!r} is not an element-backed strategy canvas")

    placed: Dict[str, List[Dict]] = {zone["box_key"]: [] for zone in tpl["zones"]}

    if current_org_id() is None:
        # No active organisation: no rows, never another tenant's.
        return placed

    from app.models.archimate_core import ArchiMateElement

    # Every element type any box of this canvas names, read once and grouped
    # in Python so the whole canvas is one query, not one per box.
    element_types = sorted({t for zone in tpl["zones"] for t in zone["element_types"]})
    if not element_types:
        return placed

    elements = ArchiMateElement.query.filter(
        ArchiMateElement.type.in_(element_types)
    ).all()

    # type_profile boxes: group by (type, profile value).
    by_profile: Dict[str, List] = {}
    for element in elements:
        value = _read_profile_value(element)
        if value:
            by_profile.setdefault(value, []).append(element)

    for zone in tpl["zones"]:
        if zone["membership"] == "type_profile":
            matched = [
                el
                for el in by_profile.get(zone["profile"], [])
                if el.type in zone["element_types"]
            ]
            matched.sort(key=lambda el: (el.name or "").lower())
            placed[zone["box_key"]] = [_element_entry(el) for el in matched]
        elif zone["membership"] == "attribute" and zone["box_key"] == "value_curve":
            # The value curve reads every competing-factor Assessment (the box
            # it anchors to) that carries an offering level.
            anchor = zone.get("anchor_box")
            anchor_zone = next((z for z in tpl["zones"] if z["box_key"] == anchor), None)
            anchor_profile = anchor_zone["profile"] if anchor_zone else None
            curve = [
                _curve_point(el)
                for el in by_profile.get(anchor_profile, [])
                if el.type in zone["element_types"]
            ]
            curve.sort(key=lambda p: (p["factor"] or "").lower())
            placed[zone["box_key"]] = curve

    return placed


def unclassified_count(canvas_key: str) -> int:
    """How many of the active tenant's elements of this canvas's types carry
    no box profile — the honest "N not yet classified" figure, counted, never
    assumed."""
    tpl = CANVAS_TEMPLATES.get(canvas_key)
    if tpl is None or tpl.get("record") != "elements":
        return 0
    if current_org_id() is None:
        return 0

    from app.models.archimate_core import ArchiMateElement

    # Only the types placed by a type_profile box can be "classified"; the
    # value curve is an attribute view, not a box an element is filed into.
    profile_values = {
        zone["profile"] for zone in tpl["zones"] if zone["membership"] == "type_profile"
    }
    element_types = sorted(
        {t for zone in tpl["zones"] if zone["membership"] == "type_profile" for t in zone["element_types"]}
    )
    if not element_types:
        return 0

    count = 0
    for element in ArchiMateElement.query.filter(
        ArchiMateElement.type.in_(element_types)
    ).all():
        if _read_profile_value(element) not in profile_values:
            count += 1
    return count


def add_element(
    canvas_key: str,
    box_key: str,
    name: str,
    *,
    element_type: Optional[str] = None,
    offering_level=None,
    player: Optional[str] = None,
) -> "object":
    """Create an element in a box and file it there.

    The element is written through the one backbone element writer with the
    box's profile set on ``acm_properties``. For the Blue Ocean value curve a
    competing factor also carries its player and 0–10 offering level. Raises
    ValueError on a blank name, an unknown box, or a type the box does not
    hold.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("A name is required.")

    tpl = CANVAS_TEMPLATES.get(canvas_key)
    if tpl is None or tpl.get("record") != "elements":
        raise ValueError(f"{canvas_key!r} is not an element-backed strategy canvas")

    zone = next((z for z in tpl["zones"] if z["box_key"] == box_key), None)
    if zone is None:
        raise ValueError(f"{canvas_key!r} has no box {box_key!r}")
    if zone["membership"] != "type_profile":
        raise ValueError(f"Box {box_key!r} is not one an element can be added to")

    org_id = current_org_id()
    if org_id is None:
        raise ValueError("No active organisation.")

    chosen_type = element_type or zone["element_types"][0]
    if chosen_type not in zone["element_types"]:
        raise ValueError(
            f"Box {box_key!r} holds {zone['element_types']}, not {chosen_type!r}"
        )

    layer = _LAYER_FOR_TYPE.get(chosen_type, "Business")
    element = create_backbone_element(
        element_type=chosen_type,
        layer=layer,
        name=name,
        organization_id=org_id,
        provenance={"source_model": "strategy_canvas"},
    )

    # Every property goes through the one typed-property writer so the box
    # profile and the value-curve attributes are stored the same way the rest
    # of the model's properties are, with provenance.
    from app.modules.architecture_assistant.property_service import PropertyService

    updates = {PROFILE_PROPERTY: zone["profile"]}
    if offering_level is not None and offering_level != "":
        try:
            updates[OFFERING_LEVEL_PROPERTY] = max(0, min(10, int(offering_level)))
        except (TypeError, ValueError):
            pass
    if player:
        updates[PLAYER_PROPERTY] = player.strip()
    PropertyService().merge_element_properties(element, updates, source="user")

    from app import db

    db.session.flush()
    return element
