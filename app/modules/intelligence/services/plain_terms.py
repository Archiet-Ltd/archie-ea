"""The one generator of the "in plain terms" sentence on a derived row.

Every surface that shows this sentence gets it from the server; it is never
assembled in client code, so they all read the same text. This module is
therefore the ONLY place the sentence is assembled -- a static test
(``test_plain_terms.py``) fails if any other module, template or script in
the tree carries the same wording.

The shape:

    We worked this out because {relationship clause}, {N} hops away.

followed by ONE confidence clause:

    confidence >= 0.90          Very confident ({pct}%).
    0.70 <= confidence < 0.90   Fairly confident ({pct}%).
    confidence < 0.70           Worth a second look -- we are less confident ({pct}%).

The relationship clause depends on the derived fact's stored type, because a
derived fact has a stored SOURCE and TARGET with a direction, and "depends on"
is only true of some of them. The stored direction is the fact's own and never
changes with which end the caller queried from. Wording families:

    dependency        Serving, Realization, Assignment, Triggering, Flow
                      "{target} depends on {source}"   (the stored TARGET first)
    access            Access
                      "{source} accesses {target}"
    whole-part        Composition, Aggregation
                      "{source} is made up of {target}" / "{source} includes {target}"
                      (the stored source is the whole, the target the part)
    general-specific  Specialization
                      "{source} is a specific kind of {target}"
    influence         Influence
                      "{source} influences {target}"
    symmetric         Association
                      "{source} and {target} are linked"   (asserts no direction)

Absent data:

* A missing confidence omits the clause entirely. It is never rendered as
  ``0%``: a 0 that means "not recorded" cannot be told apart from a measured
  zero.
* A missing name (another tenant's element, a deleted element), a missing or
  non-positive depth, or a relationship type this module has no wording for
  makes the WHOLE sentence ``None``. A sentence with a gap in it, or one that
  guesses which way a relationship runs, reads as a claim the data does not
  support; the consuming surface renders its absence state instead.
* The band is chosen from the stored confidence, and the percentage is that
  same value rounded half-up to a whole number, done in ``Decimal`` so the
  result does not depend on binary floating point.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Optional, Tuple

VERY_CONFIDENT_MIN = Decimal("0.90")
FAIRLY_CONFIDENT_MIN = Decimal("0.70")

SOURCE = "source"
TARGET = "target"

# derived_type -> (wording family, which stored end is named first, clause).
# Every relationship type the derivation engine can store is listed
# (``archimate_derivation_service.STRENGTH_ORDER``); a test asserts the two
# stay equal, so a type added to the engine without wording here is caught.
_WORDING: Dict[str, Tuple[str, str, str]] = {
    "Serving": ("dependency", TARGET, "{first} depends on {second}"),
    "Realization": ("dependency", TARGET, "{first} depends on {second}"),
    "Assignment": ("dependency", TARGET, "{first} depends on {second}"),
    "Triggering": ("dependency", TARGET, "{first} depends on {second}"),
    "Flow": ("dependency", TARGET, "{first} depends on {second}"),
    "Access": ("access", SOURCE, "{first} accesses {second}"),
    "Composition": ("whole-part", SOURCE, "{first} is made up of {second}"),
    "Aggregation": ("whole-part", SOURCE, "{first} includes {second}"),
    "Specialization": ("general-specific", SOURCE, "{first} is a specific kind of {second}"),
    "Association": ("symmetric", SOURCE, "{first} and {second} are linked"),
    "Influence": ("influence", SOURCE, "{first} influences {second}"),
}

SUPPORTED_TYPES = frozenset(_WORDING)


def wording_family(relation_type) -> Optional[str]:
    """The wording family for a derived type, or ``None`` if it has none."""
    entry = _WORDING.get(relation_type) if isinstance(relation_type, str) else None
    return entry[0] if entry is not None else None


def _as_decimal(confidence) -> Decimal:
    """``Decimal`` of a stored confidence (``float``/``Decimal``/``int``).

    Goes through ``str`` so ``0.82`` becomes ``Decimal("0.82")`` rather than
    the long binary expansion of the float. Anything else raises: a
    confidence that is not a number is a bug to surface, not a value to
    paper over.
    """
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float, Decimal)):
        raise TypeError(f"confidence must be a number or None, not {type(confidence).__name__}")
    return Decimal(str(confidence))


def _confidence_clause(confidence) -> Optional[str]:
    """The single confidence clause, or ``None`` when confidence is ``None``."""
    if confidence is None:
        return None
    value = _as_decimal(confidence)
    pct = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if value >= VERY_CONFIDENT_MIN:
        return f"Very confident ({pct}%)."
    if value >= FAIRLY_CONFIDENT_MIN:
        return f"Fairly confident ({pct}%)."
    return f"Worth a second look — we are less confident ({pct}%)."


def _usable_name(name) -> Optional[str]:
    if not isinstance(name, str):
        return None
    return name if name.strip() else None


def _hops_away(depth: int) -> str:
    """``1 hop away`` / ``N hops away`` -- never ``1 hops``."""
    return "1 hop away" if depth == 1 else f"{depth} hops away"


def plain_terms_sentence(
    *,
    source_name: Optional[str],
    target_name: Optional[str],
    relation_type: Optional[str],
    depth: Optional[int],
    confidence,
) -> Optional[str]:
    """The plain-terms sentence for one derived row, or ``None``.

    ``source_name`` and ``target_name`` are the derived fact's STORED source
    and target (not "the element the caller started from"); this function
    decides which is named first from ``relation_type`` so the sentence never
    reverses the relationship. Both names come from the identity map
    ``cross_layer_impact`` builds for the same response, so a name is only ever
    one the caller's tenant can see. ``depth`` is ``{N}``, the derived row's own
    ``relation.depth``.
    """
    source = _usable_name(source_name)
    target = _usable_name(target_name)
    if source is None or target is None:
        return None
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        return None
    wording = _WORDING.get(relation_type) if isinstance(relation_type, str) else None
    if wording is None:
        return None

    _family, first_side, clause = wording
    first, second = (source, target) if first_side == SOURCE else (target, source)
    sentence = (
        f"We worked this out because {clause.format(first=first, second=second)}, "
        f"{_hops_away(depth)}."
    )
    confidence_clause = _confidence_clause(confidence)
    if confidence_clause is not None:
        sentence = f"{sentence} {confidence_clause}"
    return sentence


def link_sentence(
    *,
    source_name: Optional[str],
    target_name: Optional[str],
    relation_type: Optional[str],
) -> Optional[str]:
    """One drawn link of a chain in plain words, or ``None``.

    The same wording and the same direction rule as ``plain_terms_sentence``,
    for a single recorded relationship rather than a worked-out one: "Portal
    depends on Gateway." A missing name or a type with no wording gives
    ``None``, never a sentence with a gap in it.
    """
    source = _usable_name(source_name)
    target = _usable_name(target_name)
    if source is None or target is None:
        return None
    wording = _WORDING.get(relation_type) if isinstance(relation_type, str) else None
    if wording is None:
        return None
    _family, first_side, clause = wording
    first, second = (source, target) if first_side == SOURCE else (target, source)
    return clause.format(first=first, second=second) + "."


__all__ = ["SUPPORTED_TYPES", "link_sentence", "plain_terms_sentence", "wording_family"]
