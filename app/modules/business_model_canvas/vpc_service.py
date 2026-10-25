"""The Value Proposition Canvas, read from the elements the Business Model
Canvas holds. Products and services are the business services in Value
Propositions, gain creators are the value elements there, and the customer
segments are the ones in Customer Segments.

The customer profile (jobs, pains, gains) and the pain relievers are elements
too, placed in boxes of the canvas's own view. A segment owns its profile by an
association from the segment to the element; a pain reliever or gain creator
fits a pain or gain by an influence relationship, which is what the fit
arrows list.
"""

from app import db
from app.models.archimate_core import ArchiMateRelationship

from . import model_service

OWNS = "Association"
FITS = "Influence"

# Box key -> what the box takes. Same shape as a zone of the canvas templates.
ZONES = {
    "customer_jobs": {"box_key": "customer_jobs", "label": "Customer Jobs", "element_types": ["BusinessProcess"], "membership": "type_profile", "layer": "business"},
    "pains": {"box_key": "pains", "label": "Pains", "element_types": ["Assessment"], "membership": "type_profile", "layer": "motivation"},
    "gains": {"box_key": "gains", "label": "Gains", "element_types": ["Outcome"], "membership": "type_profile", "layer": "motivation"},
    "pain_relievers": {"box_key": "pain_relievers", "label": "Pain Relievers", "element_types": ["Requirement"], "membership": "type_profile", "layer": "motivation"},
}
# The boxes a person fills here; products and gain creators are filled on the business model canvas.
EDITABLE = tuple(ZONES)


def _note(el):
    return {"text": el.name, "href": model_service.element_href(el)}


def _links(canvas, source_ids, rel_type):
    """``(source id, target id)`` pairs of this type that start at one of the sources."""
    if not source_ids:
        return []
    rows = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.organization_id == canvas.organization_id,
        ArchiMateRelationship.type == rel_type,
        ArchiMateRelationship.source_id.in_(source_ids),
    ).all()
    return [(r.source_id, r.target_id) for r in rows]


def segments(canvas):
    return model_service.entries(canvas, "customer_segments")


def chosen_segment(canvas, segment_id=None):
    """The segment whose profile is shown: the one asked for, else the first."""
    found = segments(canvas)
    for seg in found:
        if segment_id and seg.id == segment_id:
            return seg
    return found[0] if found else None


def profile(canvas, segment=None, note=None):
    """Every box of the canvas for one segment, and the fits between its boxes.

    Products and gain creators are the organisation's offer, shown for every segment. Jobs, pains, gains
    and pain relievers are the ones the segment owns."""
    note = note or (lambda box_key, el: _note(el))
    values = model_service.entries(canvas, "value_propositions")
    owned = {k: model_service.entries(canvas, k) for k in ZONES}
    if segment is not None:
        mine = {t for s, t in _links(canvas, [segment.id], OWNS)}
        owned = {k: [el for el in els if el.id in mine] for k, els in owned.items()}
    else:
        owned = {k: [] for k in ZONES}
    boxes = {
        "products-services": [_note(el) for el in values if el.type == "BusinessService"],
        "gain-creators": [_note(el) for el in values if el.type == "Value"],
        "pain-relievers": [note("pain_relievers", el) for el in owned["pain_relievers"]],
        "gains": [note("gains", el) for el in owned["gains"]],
        "pains": [note("pains", el) for el in owned["pains"]],
        "customer-jobs": [note("customer_jobs", el) for el in owned["customer_jobs"]],
    }
    return boxes, fits(canvas, owned, values)


def fits(canvas, owned, values):
    """The pain relievers and gain creators that meet a pain or a gain, as pairs of notes."""
    pairs = []
    for source_box, target_box, pool in (
        ("pain_relievers", "pains", owned["pain_relievers"]),
        ("gain_creators", "gains", [el for el in values if el.type == "Value"]),
    ):
        targets = {el.id: el for el in owned[target_box]} if target_box in owned else {}
        by_id = {el.id: el for el in pool}
        for src, tgt in _links(canvas, list(by_id), FITS):
            if tgt in targets:
                pairs.append({
                    "kind": "Pain relievers meet pains" if source_box == "pain_relievers" else "Gain creators meet gains",
                    "from": _note(by_id[src]), "to": _note(targets[tgt]),
                })
    return pairs


def add_links(canvas, el, box_key, segment_id, fits_id=None):
    """Tie a new entry to the segment that owns it and, for a reliever or a gain, to the pain or gain it meets."""
    seg = next((s for s in segments(canvas) if s.id == segment_id), None)
    if seg is None:
        raise model_service.CanvasError("Choose the customer segment this belongs to.")
    _relate(canvas, seg, el, OWNS)
    if fits_id and box_key == "pain_relievers":
        pain = next((p for p in model_service.entries(canvas, "pains") if p.id == fits_id), None)
        if pain is None:
            raise model_service.CanvasError("That pain is not on this canvas.")
        _relate(canvas, el, pain, FITS)


def _relate(canvas, source, target, rel_type):
    if not ArchiMateRelationship.query.filter_by(
        organization_id=canvas.organization_id, source_id=source.id, target_id=target.id, type=rel_type
    ).first():
        db.session.add(ArchiMateRelationship(
            organization_id=canvas.organization_id, source_id=source.id, target_id=target.id, type=rel_type,
        ))


def options(canvas, segment):
    """The pains of a segment, for the pain reliever dialog to choose from."""
    if segment is None:
        return []
    mine = {t for s, t in _links(canvas, [segment.id], OWNS)}
    return [{"id": el.id, "name": el.name} for el in model_service.entries(canvas, "pains") if el.id in mine]


def owner_segment(canvas, element_id):
    """The segment that owns an entry of its profile, if any."""
    ids = {s.id for s in segments(canvas)}
    row = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.organization_id == canvas.organization_id,
        ArchiMateRelationship.type == OWNS,
        ArchiMateRelationship.target_id == element_id,
        ArchiMateRelationship.source_id.in_(ids or {0}),
    ).first()
    return row.source_id if row else None
