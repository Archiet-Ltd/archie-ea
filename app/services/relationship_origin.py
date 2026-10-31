"""Record where a relationship came from, at the moment it is written.

Many places write relationships and most never named an origin, so an answer about what a failure
touches said "origin not recorded" for a link the product itself had just made. Every relationship written
while a source is declared (a web request, or a ``change_source`` block) that names no origin of its own
is given the one that source stands for. A relationship written by a bare script that declares nothing
stays unsourced: no origin is ever invented.
"""
from __future__ import annotations

from sqlalchemy import event

from app.services.change_source import declared_source

_ORIGIN = {
    # source: (kind, label)
    "import": ("import", "an import run"),
    "connector": ("connector", "Written by a connector sync"),
    "system": ("derived", "Worked out by the product"),
}


# A hand-entered source label either names a person ("Ada Lovelace") or describes a kind of source
# ("Entered by the team", "Added from the intake form"). Only a person is "drawn by"; a description reads as given.
_SOURCE_DESCRIPTION_OPENERS = frozenset(
    {"entered", "added", "imported", "written", "worked", "created", "recorded", "synced", "loaded", "from"}
)


def manual_origin_text(label: str) -> str:
    """The words for a hand-entered relationship's source label: "Drawn by <name>" for a person's name,
    the label as given when it describes a kind of source. The one place both the impact rows and the
    explanation chain build this, so the two cannot disagree."""
    label = (label or "").strip()
    first_word = label.split(" ", 1)[0].lower() if label else ""
    if first_word in _SOURCE_DESCRIPTION_OPENERS:
        return label
    return f"Drawn by {label}"


def stamp_origin(mapper, connection, target) -> None:
    if getattr(target, "source_kind", None):
        return
    declared = declared_source()
    if declared is None:
        return
    source, user_id = declared
    if source in ("screen", "ai_proposal"):
        # A person's action: the person is the origin (named later from their id).
        if user_id is None:
            return
        target.source_kind = "manual"
        target.source_ref = str(user_id)
    elif source in _ORIGIN:
        target.source_kind, target.source_label = _ORIGIN[source]
    else:
        return


def register() -> None:
    from app.models.archimate_core import ArchiMateRelationship

    if not event.contains(ArchiMateRelationship, "before_insert", stamp_origin):
        event.listen(ArchiMateRelationship, "before_insert", stamp_origin)
