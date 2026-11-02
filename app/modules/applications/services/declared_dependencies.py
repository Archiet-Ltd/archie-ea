"""Dependencies a person wrote in the import file, kept as model relationships.

A "Depends On" cell lists, separated by semicolons, the systems this one relies on.
Each becomes a Serving relationship from the system relied on to this one, so asking
what breaks when the first fails finds this one. The file is recorded as the source.
"""

from datetime import datetime

from app import db
from app.models.application_layer import ApplicationComponent
from app.models.archimate_relationship_sync import _ensure_relationship

HEADER = "dependson"


def depends_on_cell(row):
    """The 'Depends On' cell of one file row (a dict keyed by header), or None."""
    for key, value in row.items():
        if key and str(key).strip().lower().replace(" ", "").replace("_", "") == HEADER:
            return value
    return None


def record(pairs, source_label):
    """Store each (system, text listing what it depends on) pair as relationships.

    Names are matched to the organisation's own systems, ignoring case; a name that
    matches none is left out, since nothing is then known about it. Returns how many
    relationships were added.
    """
    wanted = [
        (name, [part.strip() for part in str(text).split(";") if part.strip()])
        for name, text in pairs
        if text and str(text).strip()
    ]
    if not wanted:
        return 0
    elements = {
        a.name.lower(): a.archimate_element_id
        for a in ApplicationComponent.query.limit(10000).all()
        if a.name and a.archimate_element_id
    }
    now = datetime.utcnow()
    added = 0
    for name, relied_on in wanted:
        dependant = elements.get(name.lower())
        for other in relied_on:
            provider = elements.get(other.lower())
            if not dependant or not provider or provider == dependant:
                continue
            rel = _ensure_relationship(db.session, "Serving", provider, dependant)
            if rel is not None and rel.source_kind is None:
                rel.source_kind = "import"
                rel.source_label = source_label
                rel.source_recorded_at = now
                added += 1
    db.session.commit()
    return added
