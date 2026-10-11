"""Tests for ``IntelligenceQueryService.accountability_for_element`` (L4):
WITHDRAWN -- the ownership data source is decided, but no shared,
tenant-safe reader for it exists yet, and the original implementation had a
real, unreviewed tenant-isolation gap on ``OrganizationUnit`` -- see the
method's own docstring for the external review that found this.

The withdrawn method deliberately does NOT look up the element, resolve a
component, or query ApplicationOwnership/OrganizationUnit at all -- the
safest way to guarantee nothing from the original tenant-isolation gap can
resurface is for there to be no query to leak from. Real element/tenant
validation for this route still happens at the route layer
(app/modules/intelligence/routes/api.py:accountability_for_element), which
is unchanged and still returns honest 400/404s before ever calling this
method; these tests cover the service method itself, which is now a
constant.

Fixtures (app, db_session, make_org) are discovered via
app/modules/conftest.py's import of tests.conftest,
same pattern as test_query_service.py. No import needed here.
"""

from __future__ import annotations


def _element(db_session, org_id, name, layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _component(db_session, org_id, element, name="A App"):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id)
    db_session.add(component)
    db_session.flush()
    return component


def _unit(db_session, org_id, *, name="Finance", unit_type="Department", head_of_unit=None):
    from app.models.enterprise_intelligence import OrganizationUnit

    unit = OrganizationUnit(organization_id=org_id, name=name, unit_type=unit_type, head_of_unit=head_of_unit)
    db_session.add(unit)
    db_session.flush()
    return unit


def _ownership(db_session, component, unit, *, ownership_type="Business Owner",
               ownership_percentage=100, primary_contact=None, contact_email=None):
    from app.models.enterprise_intelligence import ApplicationOwnership

    ownership = ApplicationOwnership(
        organization_id=component.organization_id,
        application_id=component.id,
        organization_unit_id=unit.id,
        ownership_type=ownership_type,
        ownership_percentage=ownership_percentage,
        primary_contact=primary_contact,
        contact_email=contact_email,
    )
    db_session.add(ownership)
    db_session.flush()
    return ownership


def test_returns_the_withdrawn_reason_for_any_element_id(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("accountability-lens-withdrawn")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.accountability_for_element(999999999)

    assert result["owners"] == []
    assert result["capacity_not_available"] is True
    assert result["reasons"] == ["ownership_reader_not_built", "capacity_not_available"]


def test_returns_the_withdrawn_reason_with_no_tenant_context(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.accountability_for_element(1)

    assert result["owners"] == []
    assert result["reasons"] == ["ownership_reader_not_built", "capacity_not_available"]


def test_seeded_ownership_is_never_returned_the_regression_guard_that_matters(
    app, db_session, make_org
):
    """The one test that actually protects the withdrawal: a real,
    well-formed ApplicationComponent + ApplicationOwnership + OrganizationUnit
    graph exists, exactly the shape the original (unsafe) implementation
    would have served -- and the method must still return nothing, because
    it never queries any of these tables. If this ever starts returning
    owner rows again without a real, tenant-safe reader existing, this test
    is the one that should catch it."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("accountability-lens-withdrawn-guard")
    a = _element(db_session, org.id, "A")
    component = _component(db_session, org.id, a)
    unit = _unit(db_session, org.id, name="Finance", head_of_unit="Pat Head")
    _ownership(
        db_session, component, unit, ownership_type="Business Owner",
        primary_contact="Jordan Owner", contact_email="jordan@example.com",
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.accountability_for_element(a.id)

    assert result["owners"] == []
    assert result["reasons"] == ["ownership_reader_not_built", "capacity_not_available"]
