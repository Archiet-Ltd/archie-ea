"""Tests for the per-organisation domain-element backfill command.

R1-B18 PR 2 backfill command: creates missing ArchiMate elements for every
domain record type in one organisation. Must be idempotent, respect tenant
boundaries, report duplicates, and never silently remove elements.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.risk import Risk


def _clear_element(db_session, record):
    """Remove the auto-created ArchiMateElement from a record and flush."""
    if record.archimate_element_id:
        el = db_session.get(ArchiMateElement, record.archimate_element_id)
        if el:
            record.archimate_element_id = None
            db_session.flush()
            db_session.delete(el)
            db_session.flush()


def test_backfill_creates_missing_elements(db_session, make_org, tenant_ctx):
    """Records without an element get one created."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-app")
    with tenant_ctx(org.id):
        # Create an app via ORM, then delete the element to simulate missing
        app = ApplicationComponent(
            name="Backfilled App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        stats = backfill_domain_elements(org.id, session=db_session)
        app_stats = stats.get("ApplicationComponent", {})
        assert app_stats.get("created", 0) >= 1

        db_session.expire_all()
        app = db_session.query(ApplicationComponent).filter(
            ApplicationComponent.name == "Backfilled App"
        ).first()
        assert app is not None
        assert app.archimate_element_id is not None


def test_backfill_is_idempotent(db_session, make_org, tenant_ctx):
    """Running backfill again on a fully-linked org creates nothing."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-idem")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Idempotent App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        stats1 = backfill_domain_elements(org.id, session=db_session)
        assert stats1.get("ApplicationComponent", {}).get("created", 0) >= 1

        db_session.expire_all()

        stats2 = backfill_domain_elements(org.id, session=db_session)
        assert stats2.get("ApplicationComponent", {}).get("created", 0) == 0


def test_backfill_respects_tenant_boundary(db_session, make_org, tenant_ctx):
    """Backfill for org A must not create elements in org B."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org_a = make_org("bf-iso-a")
    org_b = make_org("bf-iso-b")

    with tenant_ctx(org_a.id):
        app_a = ApplicationComponent(
            name="App in A", organization_id=org_a.id
        )
        db_session.add(app_a)
        db_session.flush()
        _clear_element(db_session, app_a)

    with tenant_ctx(org_b.id):
        app_b = ApplicationComponent(
            name="App in B", organization_id=org_b.id
        )
        db_session.add(app_b)
        db_session.flush()
        _clear_element(db_session, app_b)

    # Backfill only org A — run inside org A's tenant context so the
    # automatic tenant filter does not scope the query to org B
    with tenant_ctx(org_a.id):
        stats = backfill_domain_elements(org_a.id, session=db_session)

    # Check that only org A's records got elements
    db_session.expire_all()
    a_apps = db_session.query(ApplicationComponent).filter(
        ApplicationComponent.organization_id == org_a.id,
        ApplicationComponent.archimate_element_id.isnot(None),
    ).count()
    b_apps = db_session.query(ApplicationComponent).filter(
        ApplicationComponent.organization_id == org_b.id,
        ApplicationComponent.archimate_element_id.isnot(None),
    ).count()

    assert a_apps >= 1, "org A records should get elements"
    assert b_apps == 0, "org B records must NOT get elements from org A's backfill"


def test_backfill_dry_run_changes_nothing(db_session, make_org, tenant_ctx):
    """--dry-run reports counts without creating any elements."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-dry")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Dry Run App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        stats = backfill_domain_elements(org.id, dry_run=True, session=db_session)
        assert stats.get("ApplicationComponent", {}).get("created", 0) == 0
        assert stats.get("ApplicationComponent", {}).get("scanned", 0) >= 1

        db_session.expire_all()
        app = db_session.query(ApplicationComponent).filter(
            ApplicationComponent.name == "Dry Run App"
        ).first()
        assert app is not None
        assert app.archimate_element_id is None


def test_backfill_handles_multiple_types(db_session, make_org, tenant_ctx):
    """Backfill creates missing elements for multiple domain record types."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-multi")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Multi App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        risk = Risk(
            title="Multi Risk", organization_id=org.id,
            likelihood=3, impact=3
        )
        db_session.add(risk)
        db_session.flush()
        _clear_element(db_session, risk)

        cap = BusinessCapability(
            name="Multi Cap", organization_id=org.id, level=1
        )
        db_session.add(cap)
        db_session.flush()
        _clear_element(db_session, cap)

        stats = backfill_domain_elements(org.id, session=db_session)

        total_created = sum(s.get("created", 0) for s in stats.values())
        assert total_created >= 3


def test_every_record_type_has_a_handler():
    """The backfill command must list every domain record type we know about."""
    from app.commands.backfill_domain_elements import _DOMAIN_TYPES

    expected_types = {
        "ApplicationComponent",
        "BusinessCapability",
        "Risk",
        "VendorContract",
        "ApplicationInterface",
        "DataEntity",
        "WorkPackage",
    }
    actual_types = {path.rsplit(".", 1)[1] for path, _, _, _, _ in _DOMAIN_TYPES}
    assert expected_types == actual_types, (
        f"backfill must handle all domain types. "
        f"Missing: {expected_types - actual_types}. "
        f"Extra: {actual_types - expected_types}."
    )