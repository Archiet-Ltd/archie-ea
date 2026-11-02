"""One page of the relationships list, with the names of the elements each relationship joins.

The list is ordered by type then id. A citation links to ``?focus=<id>``: the page that holds that row is worked out
from the row's own position in this ordering, so the row is on the page the link opens however many relationships exist.
"""
from sqlalchemy import and_, or_

from app.extensions import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

PAGE_SIZE = 20


def _page_holding(focus_id, per_page):
    """The page number of the relationship ``focus_id`` (1 when it is not one of this organisation's)."""
    focus = db.session.get(ArchiMateRelationship, focus_id) if focus_id else None
    if focus is None:
        return 1
    rel = ArchiMateRelationship
    if focus.type is None:
        before = or_(rel.type.isnot(None), and_(rel.type.is_(None), rel.id < focus.id))
    else:
        before = or_(rel.type < focus.type, and_(rel.type == focus.type, rel.id < focus.id))
    ahead = rel.query.filter(before).count()
    return ahead // per_page + 1


def relationships_page(page=None, focus_id=None, per_page=PAGE_SIZE):
    """(rows, pagination) for the relationships list; ``page`` wins over ``focus_id`` when both are given."""
    rel = ArchiMateRelationship
    if page is None:
        page = _page_holding(focus_id, per_page)
    pagination = rel.query.order_by(rel.type, rel.id).paginate(
        page=max(page, 1), per_page=per_page, error_out=False
    )
    rows = pagination.items
    element_ids = {eid for r in rows for eid in (r.source_id, r.target_id) if eid is not None}
    names_by_id = {}
    if element_ids:
        names_by_id = dict(
            db.session.query(ArchiMateElement.id, ArchiMateElement.name).filter(ArchiMateElement.id.in_(element_ids))
        )
    relationships = [
        {
            "id": r.id,
            "type": r.type,
            "source_id": r.source_id,
            "target_id": r.target_id,
            "source_name": names_by_id.get(r.source_id),
            "target_name": names_by_id.get(r.target_id),
            "row_attrs": {"id": "relationship-%d" % r.id, **({"data-state": "selected"} if r.id == focus_id else {})},
        }
        for r in rows
    ]
    return relationships, pagination
