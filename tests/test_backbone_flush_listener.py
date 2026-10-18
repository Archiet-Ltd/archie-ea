"""Repro tests for defects found in the flush-listener fix round.

Defect: add-then-sync without flush leaves TWO elements because the
before_flush hook fires during sync_archimate_element's internal flush and
creates a second element for a row still in session.new.

These tests must fail on the buggy commit 3f7a5d7e and pass after the fix.
"""
import pytest


def test_add_then_sync_without_flush_leaves_one_element(db_session, make_org):
    """Callers that add() then sync_archimate_element() with no intermediate
    flush must produce exactly one element (the one sync creates), not two."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("dupe")
    wp = WorkPackage(
        organization_id=org.id,
        name="Repro WP add-then-sync",
        context="application",
    )
    db_session.add(wp)
    sync_archimate_element(wp, session=db_session)
    db_session.flush()
    db_session.expire_all()

    rows = (
        db_session.query(ArchiMateElement)
        .filter_by(organization_id=org.id)
        .all()
    )
    assert len(rows) == 1
    reloaded = db_session.get(type(wp), wp.id)
    assert reloaded.archimate_element_id == rows[0].id


def test_add_risk_then_sync_without_flush_leaves_one_element(db_session, make_org):
    """Same shape for Risk - must also produce exactly one element."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("dupe-risk")
    risk = Risk(
        organization_id=org.id,
        title="Test risk for add-then-sync",
        description="Repro",
        likelihood=3,
        impact=4,
    )
    db_session.add(risk)
    sync_archimate_element(risk, session=db_session)
    db_session.flush()
    db_session.expire_all()

    rows = (
        db_session.query(ArchiMateElement)
        .filter_by(organization_id=org.id)
        .all()
    )
    assert len(rows) == 1
    reloaded = db_session.get(type(risk), risk.id)
    assert reloaded.archimate_element_id == rows[0].id