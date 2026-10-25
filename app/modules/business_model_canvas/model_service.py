"""The Business Model Canvas as a view over the organisation's model.

A canvas holds no entry text. Each box lists elements of the types the box
accepts (``CANVAS_TEMPLATES["business_model_canvas"]``): an element is on a
canvas when its Architecture View (a saved diagram) lists it, and in a box when
its ``profile`` property names that box. Adding an entry looks an existing
element up first and creates one only when nothing matches. Removing an entry
takes it off the view and leaves the element in the model. Cost Structure reads
the cost facts and Revenue Streams the price plans; neither keeps an amount on
an element.
"""

from decimal import Decimal, InvalidOperation


from app import db
from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.models.archimate_core import ArchiMateElement, SavedDiagram, SavedDiagramElement
from app.models.price_plan import BILLING_PERIODS, PRICING_MECHANISMS, PricePlan
from app.services import cost_fact_store
from app.services.archimate_backbone import create_backbone_element

TEMPLATE_KEY = "business_model_canvas"

PRICE_BOX = "revenue_streams"
COST_BOX = "cost_structure"

# The business layer elements a price plan may price: the value propositions.
PRICEABLE_BOX = "value_propositions"

_LAYER_BY_TYPE = {
    "BusinessActor": "business", "BusinessService": "business", "BusinessInterface": "business",
    "BusinessInteraction": "business", "BusinessProcess": "business", "BusinessFunction": "business",
    "ApplicationInterface": "application", "Value": "motivation", "Resource": "strategy",
    "Capability": "strategy",
}


class CanvasError(ValueError):
    """A write the canvas refuses; the message is shown to the person."""


def zones():
    return {z["box_key"]: z for z in CANVAS_TEMPLATES[TEMPLATE_KEY]["zones"]}


def element_boxes():
    return [k for k, z in zones().items() if z["membership"] != "attribute"]


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
            name=canvas.name or "Business Model Canvas",
            viewpoint_type=TEMPLATE_KEY,
            organization_id=canvas.organization_id,
        )
        db.session.add(diagram)
        db.session.flush()
        canvas.saved_diagram_id = diagram.id
    return diagram


def _view_elements(canvas):
    """Elements on the canvas's view, as ArchiMateElement rows."""
    if not canvas.saved_diagram_id:
        return []
    ids = [
        r.element_id
        for r in SavedDiagramElement.query.filter(SavedDiagramElement.diagram_id == canvas.saved_diagram_id).all()
    ]
    if not ids:
        return []
    return ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(ids), ArchiMateElement.organization_id == canvas.organization_id
    ).order_by(ArchiMateElement.id).all()


def _profile(el):
    """The box profile the element carries, however the property writer stored it."""
    value = (el.acm_properties or {}).get("profile")
    return value.get("value") if isinstance(value, dict) else value


def set_profile(el, profile):
    """Place an element in a box by writing its ``profile`` property through the one property writer."""
    from app.modules.architecture_assistant.property_service import PropertyService

    PropertyService().set_element_property(el, "profile", profile, source="user")


def entries(canvas, box_key):
    """Elements in one box of one canvas."""
    zone = zones()[box_key]
    return [
        el for el in _view_elements(canvas)
        if _profile(el) == zone["profile"] and el.type in zone["element_types"]
    ]


def candidates(canvas, box_key):
    """Existing elements of the box's types that this canvas does not show yet.
    An element already placed in another box is not offered: it has one box."""
    zone = zones()[box_key]
    shown = {el.id for el in _view_elements(canvas)}
    rows = ArchiMateElement.query.filter(
        ArchiMateElement.organization_id == canvas.organization_id,
        ArchiMateElement.type.in_(zone["element_types"]),
    ).order_by(ArchiMateElement.name).limit(400).all()
    return [el for el in rows if el.id not in shown and _profile(el) in (None, "", zone["profile"])]


def add_entry(canvas, box_key, element_id=None, name=None):
    """Put an element in a box: the chosen existing one, else one found by
    name among the box's types, else a new one."""
    zone = zones().get(box_key)
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
        ).all():
            if normalise(row.name) == wanted:
                el = row
                break
        if el is None:
            el_type = zone["element_types"][0]
            el = create_backbone_element(
                element_type=el_type, layer=_LAYER_BY_TYPE.get(el_type, "business"), name=name,
                organization_id=org_id, provenance={"created_from": "business_model_canvas"},
            )
    if _profile(el) not in (None, "", zone["profile"]):
        raise CanvasError("That element is already in another box.")
    set_profile(el, zone["profile"])
    diagram = ensure_view(canvas)
    exists = SavedDiagramElement.query.filter_by(diagram_id=diagram.id, element_id=el.id).first()
    if exists is None:
        db.session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=el.id))
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
    el = _entry_or_error(canvas, PRICEABLE_BOX, element_id)
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


def cost_lines(canvas):
    """Recorded annual actual costs of the organisation's applications,
    largest first, from the one cost fact store."""
    from app.models.application_portfolio import ApplicationComponent

    facts = cost_fact_store.list_facts(
        canvas.organization_id, element_type="application", kind="actual", period="annual"
    )
    names = {
        a.id: a.name
        for a in ApplicationComponent.query.filter(
            ApplicationComponent.id.in_({f.element_id for f in facts} or {0}),
            ApplicationComponent.organization_id == canvas.organization_id,
        ).all()
    }
    totals = {}
    for f in facts:
        if f.element_id in names:
            key = (f.element_id, f.currency)
            totals[key] = totals.get(key, Decimal(0)) + f.amount
    lines = [
        {"id": app_id, "name": names[app_id], "amount": amount, "currency": currency}
        for (app_id, currency), amount in totals.items()
    ]
    lines.sort(key=lambda r: r["amount"], reverse=True)
    return lines
