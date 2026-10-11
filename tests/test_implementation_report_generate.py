"""Tests for GET /implementation/reports/generate.

Proves the models the report route queries have the to_dict() methods
the route calls.  The route itself is gated behind a feature flag and
the USE_NEW_APPLICATIONS env var; these unit-level tests verify the
fix directly without the full middleware stack.
"""

import uuid

import pytest


def _make_gap(db_session, org, label="test-gap"):
    from app.models.implementation_migration import Gap

    suffix = uuid.uuid4().hex[:8]
    gap = Gap(
        organization_id=org.id,
        name=f"{label}-{suffix}",
        description="A test gap for report generation",
        gap_type="coverage",
        priority="high",
        severity="medium",
        resolution_status="identified",
    )
    db_session.add(gap)
    db_session.flush()
    return gap


def test_gap_to_dict_returns_expected_fields(app, db_session, make_org):
    """Gap.to_dict() returns the fields the report route expects."""
    org = make_org("gap-dict")
    gap = _make_gap(db_session, org, "dict-gap")
    db_session.commit()

    d = gap.to_dict()
    assert d["id"] == gap.id
    assert d["name"] == gap.name
    assert d["priority"] == "high"
    assert d["severity"] == "medium"
    assert d["resolution_status"] == "identified"
    assert "description" in d
    assert "gap_type" in d


def test_deliverable_to_dict_returns_expected_fields(app, db_session, make_org):
    """Deliverable.to_dict() returns the fields the report route expects."""
    from app.models.implementation_migration import Deliverable, WorkPackage

    org = make_org("del-dict")
    wp = WorkPackage(
        organization_id=org.id,
        name=f"wp-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(wp)
    db_session.flush()

    suffix = uuid.uuid4().hex[:8]
    d = Deliverable(
        name=f"del-{suffix}",
        description="A test deliverable",
        work_package_id=wp.id,
        delivery_status="planned",
        deliverable_type="document",
    )
    db_session.add(d)
    db_session.commit()

    dd = d.to_dict()
    assert dd["id"] == d.id
    assert dd["name"] == d.name
    assert dd["delivery_status"] == "planned"
    assert "description" in dd


def test_gap_to_dict_with_no_gaps_empty_org(app, db_session, make_org):
    """Gap.to_dict() works correctly even when no gaps exist (empty query is
    handled by the caller, but the method itself must exist and be callable)."""
    from app.models.implementation_migration import Gap

    org = make_org("empty-org")
    # No gaps created -- prove the model method exists and is importable
    gaps = Gap.query.filter_by(organization_id=org.id).all()
    assert gaps == []
    # Prove to_dict exists on the class
    assert hasattr(Gap, "to_dict")
    assert callable(Gap.to_dict)