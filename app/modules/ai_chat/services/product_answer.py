"""A question about what one of the organisation's own products can do, answered from its model.

The organisation's products and their capabilities are elements of its model, loaded from a
product file (see ``app/services/customer_zero_model.py``), each carrying its shipped status,
what it does and its known limits. The chat asks this before it asks the model: a question that
names such a product is answered from the capability the question is about, citing that
element, or says plainly that the model records nothing about it. Nothing here guesses.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.models.archimate_core import ArchiMateElement
from app.services.customer_zero_model import (
    CAPABILITY_TYPE, KEY_PREFIX, PRODUCT_TYPE, STATUS_WORDS, WITHDRAWN,
)

_ASKS = re.compile(r"\b(can|could|does|do|did|is|are|will|what|which|how|support|supports|able|have|has)\b", re.I)
# Words that carry no subject: a question is matched on what remains.
_FILLER = frozenset(
    "a an the can could does do did is are will what which how it its of to for in on at and or with my your you "
    "we i be been able yet ever also any all about from by this that these those there support supports "
    "have has tell me us not so if when who whom whose s".split()
)
_ENOUGH = 0.6  # the share of the question's own words a capability must account for


def _words(text: str) -> List[str]:
    return [re.sub(r"(ing|ed|es|s)$", "", word) if len(word) > 4 else word.rstrip("s") if len(word) > 3 else word
            for word in re.findall(r"[a-z0-9]+", (text or "").lower())]


def _stem_set(text: str, drop=()) -> set:
    return {w for w in _words(text) if w and w not in _FILLER and w not in drop}


def products_named(message: str, organization_id: int) -> List[ArchiMateElement]:
    """The organisation's product elements whose name appears in *message*, longest first."""
    rows = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.organization_id == organization_id,
            ArchiMateElement.type == PRODUCT_TYPE,
            ArchiMateElement.source_ref.like(KEY_PREFIX + "%"),
        )
    ).scalars().all()
    lowered = (message or "").lower()
    named = [row for row in rows if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(row.name.lower()), lowered)]
    return sorted(named, key=lambda row: (-len(row.name), row.id))


def _capabilities_of(product: ArchiMateElement) -> List[ArchiMateElement]:
    prefix = "%s:" % product.source_ref
    return list(db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.organization_id == product.organization_id,
            ArchiMateElement.type == CAPABILITY_TYPE,
            ArchiMateElement.source_ref.like(prefix + "%"),
        ).order_by(ArchiMateElement.id)
    ).scalars().all())


def _best(question_words: set, capabilities: List[ArchiMateElement]) -> Optional[ArchiMateElement]:
    best, best_key = None, None
    for element in capabilities:
        props = element.custom_properties or {}
        name_words = _stem_set(element.name)
        all_words = name_words | _stem_set(props.get("what_it_does") or "")
        in_name = len(question_words & name_words)
        share = len(question_words & all_words) / len(question_words)
        if share < _ENOUGH or not in_name:
            continue
        key = (share, in_name, props.get("shipped_status") != WITHDRAWN, -element.id)
        if best_key is None or key > best_key:
            best, best_key = element, key
    return best


def _facts(element: ArchiMateElement) -> Dict[str, Any]:
    props = element.custom_properties or {}
    status = props.get("shipped_status")
    return {
        "element": element,
        "status": status,
        "status_word": STATUS_WORDS.get(status, "Not recorded"),
        "status_detail": props.get("status_detail"),
        "does": props.get("what_it_does") or None,
        "limits": props.get("limits") or [],
        "evidence": props.get("evidence"),
        "commit": props.get("commit"),
    }


def answer_product_question(message: str, organization_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """What the model records about the capability *message* asks of a product of this
    organisation, or ``None`` when the question names no such product or is not a question."""
    from app.modules.intelligence.services import answer_envelope as envelope

    if not organization_id or not _ASKS.search(message or ""):
        return None
    if envelope.classify_estate_parts(message) or envelope.classify_estate_question(message):
        return None
    products = products_named(message, organization_id)
    if not products:
        return None
    product = products[0]
    capabilities = _capabilities_of(product)
    words = _stem_set(message, drop=_stem_set(product.name))
    if not words:
        shipped = [c for c in capabilities if (c.custom_properties or {}).get("shipped_status") in ("shipped", "narrow")]
        return {"kind": "overview", "product": product, "listed": [_facts(c) for c in shipped],
                "recorded": len(capabilities), "shipped": len(shipped)}
    chosen = _best(words, capabilities)
    if chosen is None:
        return {"kind": "not_recorded", "product": product, "recorded": len(capabilities)}
    return {"kind": "capability", "product": product, **_facts(chosen)}


def answer_line(answer: Dict[str, Any]) -> str:
    """The sentence kept in the conversation for this answer."""
    product = answer["product"].name
    if answer["kind"] == "not_recorded":
        return "Not recorded in your model: nothing about that is recorded for %s." % product
    if answer["kind"] == "overview":
        return "%s: %d of %d recorded capabilities are shipped." % (product, answer["shipped"], answer["recorded"])
    return "%s: %s. %s" % (answer["element"].name, answer["status_word"], answer["does"] or "")
