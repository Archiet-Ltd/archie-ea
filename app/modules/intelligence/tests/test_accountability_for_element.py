"""Tests for ``IntelligenceQueryService.accountability_for_element`` (L4).

The lens answers from the one shared owner chain (``_current_ownerships_batch``)
and serves only what the Ask card shows: ownership type, unit name, start
date. Each test guards one defect from the review that withdrew the read:
expired ownership shown as current, a unit read with no tenant fence, and
personal data serialised without being shown.

Fixtures (app, db_session, make_org) are discovered via
app/modules/conftest.py's import of tests.conftest,
same pattern as test_query_service.py. No import needed here.
"""

from __future__ import annotations

import datetime as _dt


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


def _call(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.accountability_for_element(element_id)


def test_no_tenant_context_is_an_honest_reason_not_an_answer(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.accountability_for_element(1)

    assert result["owners"] == []
    assert result["reasons"] == ["no_tenant_context", "capacity_not_available"]
    assert result["as_of"] is None


def test_an_unknown_element_is_element_not_found(app, db_session, make_org):
    org = make_org("accountability-unknown")
    db_session.commit()

    result = _call(app, org.id, 999999999)

    assert result["owners"] == []
    assert result["reasons"] == ["element_not_found", "capacity_not_available"]


def test_an_element_that_is_not_a_component_says_so(app, db_session, make_org):
    org = make_org("accountability-no-component")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    result = _call(app, org.id, a.id)

    assert result["owners"] == []
    assert result["reasons"] == ["no_application_component", "capacity_not_available"]


def test_a_component_with_no_current_ownership_says_so(app, db_session, make_org):
    org = make_org("accountability-no-ownership")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a)
    db_session.commit()

    result = _call(app, org.id, a.id)

    assert result["owners"] == []
    assert result["reasons"] == ["no_ownership_records", "capacity_not_available"]


def test_current_ownership_is_returned_with_only_the_fields_the_card_shows(app, db_session, make_org):
    org = make_org("accountability-owner")
    a = _element(db_session, org.id, "A")
    component = _component(db_session, org.id, a)
    unit = _unit(db_session, org.id, head_of_unit="Pat Head")
    unit.contact_email = "unit@example.com"
    ownership = _ownership(
        db_session, component, unit, ownership_type="Business Owner",
        primary_contact="Jordan Owner", contact_email="jordan@example.com",
    )
    ownership.start_date = _dt.date(2026, 1, 1)
    db_session.commit()

    result = _call(app, org.id, a.id)

    assert result["owners"] == [
        {
            "owner_id": ownership.id,
            "ownership_type": "Business Owner",
            "start_date": "2026-01-01",
            "organization_unit": {"id": unit.id, "name": "Finance"},
        }
    ]
    assert result["reasons"] == ["capacity_not_available"]
    assert result["as_of"] == _dt.date.today().isoformat()
    blob = repr(result)
    for hidden in ("Jordan Owner", "jordan@example.com", "unit@example.com", "Pat Head"):
        assert hidden not in blob


def test_expired_and_future_dated_ownership_is_not_returned(app, db_session, make_org):
    org = make_org("accountability-dates")
    a = _element(db_session, org.id, "A")
    component = _component(db_session, org.id, a)
    unit = _unit(db_session, org.id)
    today = _dt.date.today()
    old = _ownership(db_session, component, unit, ownership_type="Expired")
    old.end_date = today - _dt.timedelta(days=1)
    later = _ownership(db_session, component, unit, ownership_type="Future")
    later.start_date = today + _dt.timedelta(days=1)
    db_session.commit()

    result = _call(app, org.id, a.id)

    assert result["owners"] == []
    assert result["reasons"] == ["no_ownership_records", "capacity_not_available"]


def test_a_unit_from_another_organisation_is_never_named(app, db_session, make_org):
    """The exact leakable graph the review found: an own-tenant ownership row
    whose unit id names another tenant's unit."""
    org = make_org("accountability-foreign-unit")
    other = make_org("accountability-foreign-unit-other")
    a = _element(db_session, org.id, "A")
    component = _component(db_session, org.id, a)
    foreign = _unit(db_session, other.id, name="Other Tenant Finance")
    _ownership(db_session, component, foreign)
    db_session.commit()

    result = _call(app, org.id, a.id)

    assert result["owners"] == []
    assert "Other Tenant Finance" not in repr(result)


def test_another_organisations_element_is_not_found_and_leaks_nothing(app, db_session, make_org):
    org_a = make_org("accountability-cross-a")
    org_b = make_org("accountability-cross-b")
    a = _element(db_session, org_b.id, "B element")
    component = _component(db_session, org_b.id, a)
    unit = _unit(db_session, org_b.id, name="Org B Finance")
    _ownership(db_session, component, unit)
    db_session.commit()

    result = _call(app, org_a.id, a.id)

    assert result["owners"] == []
    assert result["reasons"] == ["element_not_found", "capacity_not_available"]
    assert "Org B Finance" not in repr(result)


def test_the_lens_and_cross_layer_impact_agree_on_the_first_owner(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import _resolve_owners_batch

    org = make_org("accountability-one-chain")
    a = _element(db_session, org.id, "A")
    component = _component(db_session, org.id, a)
    unit = _unit(db_session, org.id, name="Finance")
    first = _ownership(db_session, component, unit, ownership_type="Business Owner")
    first.start_date = _dt.date(2025, 1, 1)
    second = _ownership(db_session, component, unit, ownership_type="Technical Owner")
    second.start_date = _dt.date(2026, 1, 1)
    db_session.commit()

    lens = _call(app, org.id, a.id)
    with app.test_request_context("/"):
        projected, _reason = _resolve_owners_batch([a.id], org.id)[a.id]

    assert [o["ownership_type"] for o in lens["owners"]] == ["Business Owner", "Technical Owner"]
    assert projected["ownership_type"] == lens["owners"][0]["ownership_type"]
    assert projected["name"] == lens["owners"][0]["organization_unit"]["name"]
