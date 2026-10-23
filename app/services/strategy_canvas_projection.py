"""Projection of a strategy canvas (VRIO, McKinsey 7S, Porter's five forces)
over the tenant's own ArchiMate elements.

A strategy canvas has no per-record store: each box is a projection over the
organisation's existing elements. An element belongs to a box when its type is
one the box accepts and it carries the box's ``profile`` value in
``acm_properties`` — the same plain ``type_profile`` membership rule the canvas
exporter uses (``app/services/composer_export_formats.canvas_export_reasons``),
read here directly over the live elements rather than over a saved diagram.

The read is tenant-scoped automatically: ``ArchiMateElement`` is a
``TenantMixin`` model, so the query carries ``WHERE organization_id =
g.current_org_id`` inside a request. One organisation never sees another's
elements.
"""

from __future__ import annotations

from typing import Dict, List


def _element_profile(element) -> str | None:
    """The box profile recorded on an element, or ``None``.

    ``acm_properties['profile']`` is the plain box-naming value the canvas
    membership rule reads (the same key the exporter reads); it is distinct
    from the provenance-wrapped typed properties the element profile service
    writes.
    """
    props = element.acm_properties or {}
    value = props.get("profile")
    if isinstance(value, dict):
        # Tolerate a provenance-wrapped value as well as a plain string, so a
        # profile written either way still places the element.
        value = value.get("value")
    return value or None


def project_canvas(template_key: str) -> List[Dict]:
    """Return the template's zones, each with the current tenant's elements
    placed in it by the ``type_profile`` rule.

    Each returned zone is the ``CANVAS_TEMPLATES`` zone dict plus an
    ``entries`` list of ``{id, name, type, element_type}`` for every element
    that lands in it. A zone with no matching element has an empty ``entries``
    list and keeps its ``empty_reason`` for the page to explain.

    Returns ``[]`` for an unknown key. Outside a request context (no tenant)
    the element read returns nothing, so every box is empty — it never leaks
    across organisations.
    """
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES
    from app.models.archimate_core import ArchiMateElement

    tpl = CANVAS_TEMPLATES.get(template_key)
    if not tpl:
        return []

    # One tenant-scoped read of every element whose type any box accepts, then
    # placed by profile in Python — one query, not one per box.
    accepted_types = {t for zone in tpl["zones"] for t in zone["element_types"]}
    elements = []
    if accepted_types:
        elements = (
            ArchiMateElement.query.filter(ArchiMateElement.type.in_(accepted_types)).all()
        )

    # Index the tenant's elements by (type, profile) once.
    by_type_profile: Dict[tuple, list] = {}
    for el in elements:
        profile = _element_profile(el)
        if not profile:
            continue
        by_type_profile.setdefault((el.type, profile), []).append(el)

    projected = []
    for zone in tpl["zones"]:
        entries = []
        for element_type in zone["element_types"]:
            for el in by_type_profile.get((element_type, zone["profile"]), []):
                entries.append({
                    "id": el.id,
                    "name": el.name or "",
                    "type": el.type,
                    "element_type": el.type,
                    # The native archimate_elements row links through the CRUD
                    # element view; its layer column drives the layer-scoped URL.
                    "layer": (el.layer or "motivation").lower(),
                })
        projected_zone = dict(zone)
        projected_zone["entries"] = entries
        projected.append(projected_zone)

    return projected


def unclassified_count(template_key: str) -> int:
    """How many of the tenant's elements of a type the canvas accepts carry a
    profile that no box of this template names — the "not yet classified"
    count the page shows. A genuine read, never a fabricated figure.
    """
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES
    from app.models.archimate_core import ArchiMateElement

    tpl = CANVAS_TEMPLATES.get(template_key)
    if not tpl:
        return 0

    accepted_types = {t for zone in tpl["zones"] for t in zone["element_types"]}
    if not accepted_types:
        return 0

    box_profiles = {zone["profile"] for zone in tpl["zones"]}
    elements = (
        ArchiMateElement.query.filter(ArchiMateElement.type.in_(accepted_types)).all()
    )
    count = 0
    for el in elements:
        profile = _element_profile(el)
        if profile and profile not in box_profiles:
            count += 1
    return count
