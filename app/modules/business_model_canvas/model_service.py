"""A framework canvas (Business Model Canvas, Lean Canvas, SWOT and the rest) as a view over the organisation's model.

A canvas holds no entry text. Each box lists elements of the types the box
accepts (``CANVAS_TEMPLATES[<canvas type>]``): an element is on a canvas when
its Architecture View (a saved diagram) lists it, and in a box when that view
row names the box (``SavedDiagramElement.canvas_box``). The box belongs to the
view, so the same element can sit in a different box of another canvas. Adding
an entry looks an existing element up first and creates one only when nothing
matches. Removing an entry takes it off the view and leaves the element in the
model. Cost Structure reads the cost facts and Revenue Streams the price plans;
neither keeps an amount on an element.
"""

from decimal import Decimal, InvalidOperation


from app import db
from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.models.archimate_core import ArchiMateElement, SavedDiagram, SavedDiagramElement
from app.models.price_plan import BILLING_PERIODS, PRICING_MECHANISMS, PricePlan
from app.services.archimate_backbone import create_backbone_element

TEMPLATE_KEY = "business_model_canvas"

PRICE_BOX = "revenue_streams"
COST_BOX = "cost_structure"

# The box whose elements a price plan may price, for the canvases that have a Revenue Streams box.
PRICEABLE_BOXES = {"business_model_canvas": "value_propositions", "lean_canvas": "solution"}
PRICEABLE_BOX = PRICEABLE_BOXES[TEMPLATE_KEY]

_LAYER_BY_TYPE = {
    "BusinessActor": "business", "BusinessService": "business", "BusinessInterface": "business",
    "BusinessInteraction": "business", "BusinessProcess": "business", "BusinessFunction": "business",
    "BusinessRole": "business", "Product": "business", "Location": "business", "EvidenceRecord": "business",
    "ApplicationInterface": "application", "ApplicationComponent": "application", "ApplicationService": "application",
    "DataObject": "application", "TechnologyService": "technology",
    "Equipment": "physical", "Facility": "physical", "Material": "physical",
    "Value": "motivation", "Assessment": "motivation", "Driver": "motivation", "Goal": "motivation",
    "Outcome": "motivation", "Principle": "motivation", "Constraint": "motivation", "Metric": "motivation",
    "Risk": "motivation", "Stakeholder": "motivation",
    "Resource": "strategy", "Capability": "strategy", "CourseOfAction": "strategy", "Skill": "strategy",
    "WorkPackage": "implementation", "Gap": "implementation",
}


class CanvasError(ValueError):
    """A write the canvas refuses; the message is shown to the person."""


def template_key(canvas):
    """The framework this canvas draws; a canvas with no type is the Business Model Canvas."""
    key = getattr(canvas, "canvas_type", None) or TEMPLATE_KEY
    if key not in CANVAS_TEMPLATES:
        raise CanvasError("This canvas has a type that is not known.")
    return key


def canvas_types():
    """The framework canvases a person can create: key and name, in the order they are offered."""
    return [(k, t["name"]) for k, t in CANVAS_TEMPLATES.items() if t["record"] == TEMPLATE_KEY]


def zones(canvas=None):
    """The boxes of a canvas by box key; the Business Model Canvas's when no canvas is given."""
    key = template_key(canvas) if canvas is not None else TEMPLATE_KEY
    return {z["box_key"]: z for z in CANVAS_TEMPLATES[key]["zones"]}


def element_boxes(canvas=None):
    return [k for k, z in zones(canvas).items() if z["membership"] != "attribute"]


def priceable_box(canvas):
    """The box whose elements this canvas can price, or None when it has no Revenue Streams box."""
    return PRICEABLE_BOXES.get(template_key(canvas))


def element_href(el):
    """The element's own detail page."""
    from flask import url_for

    layer = (el.layer or "").lower()
    if layer not in ("motivation", "strategy", "business", "application", "technology", "physical", "implementation"):
        layer = _LAYER_BY_TYPE.get(el.type, "business")
    return url_for("archimate_crud.detail_element", layer=layer, element_type=el.type, element_id=el.id)


def normalise(name):
    return " ".join((name or "").lower().split())



def ensure_view(canvas):
    """The canvas's Architecture View, created on first use."""
    diagram = None
    if canvas.saved_diagram_id:
        diagram = SavedDiagram.query.filter(SavedDiagram.id == canvas.saved_diagram_id).first()
    if diagram is None:
        diagram = SavedDiagram(
            name=canvas.name or CANVAS_TEMPLATES[template_key(canvas)]["name"],
            viewpoint_type=template_key(canvas),
            organization_id=canvas.organization_id,
        )
        db.session.add(diagram)
        db.session.flush()
        canvas.saved_diagram_id = diagram.id
    return diagram


def _view_rows(canvas):
    """``(view row, element)`` for every element on the canvas's view, oldest first."""
    if not canvas.saved_diagram_id:
        return []
    rows = SavedDiagramElement.query.filter(SavedDiagramElement.diagram_id == canvas.saved_diagram_id).all()
    by_id = {
        el.id: el for el in ArchiMateElement.query.filter(
            ArchiMateElement.id.in_({r.element_id for r in rows} or {0}),
            ArchiMateElement.organization_id == canvas.organization_id,
        ).all()
    }
    return sorted(((r, by_id[r.element_id]) for r in rows if r.element_id in by_id), key=lambda pair: pair[1].id)


def _view_elements(canvas):
    """Elements on the canvas's view, as ArchiMateElement rows."""
    return [el for _row, el in _view_rows(canvas)]


def _profile(el):
    """The box profile an older canvas wrote on the element, however the property writer stored it."""
    value = (el.acm_properties or {}).get("profile")
    return value.get("value") if isinstance(value, dict) else value


def _box_of(canvas, row, el, by_profile):
    """The box an element sits in on this canvas: the view row's own box, else the box an older
    canvas recorded as the element's profile."""
    if row.canvas_box:
        return row.canvas_box
    return by_profile.get(_profile(el))


def _boxes_by_profile(canvas):
    return {z["profile"]: k for k, z in zones(canvas).items() if z["profile"]}


def entries(canvas, box_key):
    """Elements in one box of one canvas."""
    zone = zones(canvas)[box_key]
    by_profile = _boxes_by_profile(canvas)
    return [
        el for row, el in _view_rows(canvas)
        if _box_of(canvas, row, el, by_profile) == box_key and el.type in zone["element_types"]
    ]


def candidates(canvas, box_key):
    """Existing elements of the box's types that this canvas does not show yet.
    An element has one box on a canvas."""
    zone = zones(canvas)[box_key]
    shown = {el.id for el in _view_elements(canvas)}
    rows = ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == canvas.organization_id,
        ArchiMateElement.type.in_(zone["element_types"]),
    ).order_by(ArchiMateElement.name).limit(400).all()
    return [el for el in rows if el.id not in shown]


def place(canvas, box_key, el):
    """Put an element in a box of this canvas's view. An element that is already on the view
    stays in the box it has."""
    diagram = ensure_view(canvas)
    row = SavedDiagramElement.query.filter_by(diagram_id=diagram.id, element_id=el.id).first()
    if row is None:
        db.session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=el.id, canvas_box=box_key))
        return True
    if row.canvas_box is None:
        row.canvas_box = _box_of(canvas, row, el, _boxes_by_profile(canvas)) or box_key
    return row.canvas_box == box_key


def add_entry(canvas, box_key, element_id=None, name=None):
    """Put an element in a box: the chosen existing one, else one found by
    name among the box's types, else a new one."""
    zone = zones(canvas).get(box_key)
    if zone is None or zone["membership"] == "attribute":
        raise CanvasError("This box takes its entries from the cost and price records, not from elements.")
    org_id = canvas.organization_id
    el = None
    if element_id:
        el = ArchiMateElement.query.filter(
            ArchiMateElement.id == int(element_id), ArchiMateElement.organization_id == org_id
        ).first()
        if el is None or el.type not in zone["element_types"]:
            raise CanvasError("That element cannot go in this box.")
    else:
        name = " ".join((name or "").split())
        if not name:
            raise CanvasError("Choose an existing element or type a name.")
        wanted = normalise(name)
        for row in ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == org_id,
            ArchiMateElement.type.in_(zone["element_types"]),
        ).order_by(ArchiMateElement.id).all():
            if normalise(row.name) == wanted:
                el = row
                break
        if el is None:
            el_type = zone["element_types"][0]
            el = create_backbone_element(
                element_type=el_type, layer=_LAYER_BY_TYPE.get(el_type, "business"), name=name,
                organization_id=org_id, provenance={"created_from": template_key(canvas)},
            )
    if not place(canvas, box_key, el):
        raise CanvasError("That element is already in another box on this canvas.")
    db.session.commit()
    return el


def rename_entry(canvas, box_key, element_id, name):
    el = _entry_or_error(canvas, box_key, element_id)
    name = " ".join((name or "").split())
    if not name:
        raise CanvasError("An entry needs a name.")
    el.name = name[:500]
    db.session.commit()
    return el


def remove_entry(canvas, box_key, element_id):
    """Take the entry off this canvas. The element stays in the model."""
    el = _entry_or_error(canvas, box_key, element_id)
    SavedDiagramElement.query.filter_by(diagram_id=canvas.saved_diagram_id, element_id=el.id).delete()
    db.session.commit()
    return el


def _entry_or_error(canvas, box_key, element_id):
    for el in entries(canvas, box_key):
        if el.id == int(element_id):
            return el
    raise CanvasError("That entry is not on this canvas.")


def price_plans(canvas):
    ids = {el.id for el in _view_elements(canvas)}
    if not ids:
        return []
    return PricePlan.query.filter(
        PricePlan.organization_id == canvas.organization_id, PricePlan.element_id.in_(ids)
    ).order_by(PricePlan.id).all()


def add_price_plan(canvas, element_id, name, price, currency, billing_period, mechanism=None):
    box = priceable_box(canvas)
    if box is None:
        raise CanvasError("This canvas has no prices.")
    el = _entry_or_error(canvas, box, element_id)
    name = " ".join((name or "").split()) or "Price"
    try:
        amount = Decimal(str(price).strip())
        if amount < 0:
            raise InvalidOperation
    except (InvalidOperation, AttributeError):
        raise CanvasError("Enter the price as a number.")
    currency = (currency or "").strip().upper()
    if len(currency) != 3:
        raise CanvasError("Enter a three-letter currency such as GBP.")
    if billing_period not in BILLING_PERIODS:
        raise CanvasError("Choose how often the price is charged.")
    if mechanism and mechanism not in PRICING_MECHANISMS:
        mechanism = None
    plan = PricePlan(
        organization_id=canvas.organization_id, element_id=el.id, name=name[:255], price=amount,
        currency=currency, billing_period=billing_period, pricing_mechanism=mechanism,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def remove_price_plan(canvas, plan_id):
    ids = {el.id for el in _view_elements(canvas)}
    plan = PricePlan.query.filter(
        PricePlan.id == int(plan_id), PricePlan.organization_id == canvas.organization_id,
        PricePlan.element_id.in_(ids or {0}),
    ).first()
    if plan is None:
        raise CanvasError("That price is not on this canvas.")
    db.session.delete(plan)
    db.session.commit()


def cost_summary(canvas):
    """The organisation's annual cost from the one cost view: the same totals the application pages show."""
    from app.services.cost_view import organisation_cost

    return organisation_cost(canvas.organization_id)


def cost_lines(canvas):
    """Each application's annual total, largest first, in the organisation's reporting currency."""
    return [
        {"id": row["id"], "name": row["name"], "amount": row["total"], "currency": row["currency"]}
        for row in cost_summary(canvas).applications
    ]
