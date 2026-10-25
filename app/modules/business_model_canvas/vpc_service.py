"""The Value Proposition Canvas, read from the elements the Business Model
Canvas holds. It stores nothing of its own: products and services are the
business services in Value Propositions, gain creators are the value
elements there, and the customer segments are the ones in Customer Segments.
"""

from . import model_service


def _note(el):
    return {"text": el.name, "href": model_service.element_href(el)}


def boxes(canvas):
    values = model_service.entries(canvas, "value_propositions")
    return {
        "products-services": [_note(el) for el in values if el.type == "BusinessService"],
        "gain-creators": [_note(el) for el in values if el.type == "Value"],
        "pain-relievers": [],
        "gains": [],
        "pains": [],
        "customer-jobs": [],
    }


def segments(canvas):
    return [_note(el) for el in model_service.entries(canvas, "customer_segments")]
