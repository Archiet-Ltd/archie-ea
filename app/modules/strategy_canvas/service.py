"""Service helpers for the strategy canvas module.

Reads come from the shared projection (app/services/strategy_canvas_projection).
The one write — adding an element from a box — goes through the canonical
ArchiMate element writer (create_backbone_element) so the organisation, layer
and provenance are set in one place, and then records the box's profile so the
projection places the element back in that box on reload.
"""

from __future__ import annotations

from flask_login import current_user

from app import db
from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.services.archimate_backbone import ELEMENT_TYPES, create_backbone_element


def get_template_or_none(template_key):
    """The CANVAS_TEMPLATES entry for a strategy canvas key, or None."""
    tpl = CANVAS_TEMPLATES.get(template_key)
    if tpl is None or tpl.get("record") != "strategy_canvas":
        return None
    return tpl


def _zone_or_none(tpl, box_key):
    for zone in tpl["zones"]:
        if zone["box_key"] == box_key:
            return zone
    return None


def add_element_to_box(template_key, box_key, name):
    """Create an element in the named box of a strategy canvas.

    The element's type is the box's first accepted type and it carries the
    box's profile, so the projection places it in that box on the next read.
    Written through the canonical element writer, scoped to the caller's
    organisation.

    Raises ValueError for an unknown template, box, or blank name.
    """
    tpl = get_template_or_none(template_key)
    if tpl is None:
        raise ValueError(f"Unknown strategy canvas: {template_key}")

    zone = _zone_or_none(tpl, box_key)
    if zone is None:
        raise ValueError(f"Unknown box: {box_key}")

    name = (name or "").strip()
    if not name:
        raise ValueError("A name is required.")

    element_type = zone["element_types"][0]
    # The element type's ArchiMate layer, via the one domain->ArchiMate map.
    _mapped_type, layer = ELEMENT_TYPES.get(element_type, (element_type, "Motivation"))

    org_id = current_user.organization_id
    element = create_backbone_element(
        element_type=element_type,
        layer=layer,
        name=name,
        organization_id=org_id,
        provenance={"source_model": "strategy_canvas", "canvas": template_key, "box": box_key},
    )
    # Record the box profile the projection reads, through the one typed
    # property writer (never a direct acm_properties assignment) so the write
    # carries provenance and is coerced against the seeded profile template.
    from app.modules.architecture_assistant.property_service import PropertyService

    PropertyService().set_element_property(element, "profile", zone["profile"])
    db.session.commit()
    return element
