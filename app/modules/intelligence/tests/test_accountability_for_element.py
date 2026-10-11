"""Tests for ``IntelligenceQueryService.accountability_for_element`` (L4):

The method now answers from the canonical ``cross_layer_impact`` walk:
collects every unique owner from the affected elements and returns them.
The withdrawn empty answer (``ownership_reader_not_built``) is replaced
by this real answer.

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


def _make_user(db_session, org, first_name="Alice", last_name="Owner"):
    from app.models.user import User

    user = User(
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name.lower()}.{last_name.lower()}@example.com",
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _application_owner(db_session, org, application_id, user, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    ao = ApplicationOwner(
        application_id=application_id,
        user_id=user.id,
        organization_id=org.id,
        ownership_type=ownership_type,
    )
    db_session.add(ao)
    db_session.flush()
    return ao


def test_returns_empty_owners_for_nonexistent_element(app, db_session, make_org):
    """A non-existent element id returns empty owners with no_ownership_records."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("accountability-lens-nonexistent")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.accountability_for_element(999999999)

    assert result["owners"] == []
    assert result["capacity_not_available"] is True
    assert "no_ownership_records" in result["reasons"]


def test_returns_empty_owners_with_no_tenant_context(app, db_session, make_org):
    """No tenant context returns empty owners with no_ownership_records."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.accountability_for_element(1)

    assert result["owners"] == []
    assert result["reasons"] == ["no_ownership_records"]


def test_returns_owners_from_application_owner_records(app, db_session, make_org):
    """When ApplicationOwner records exist on affected elements, they are
    returned in the accountability answer."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("accountability-lens-owners")
    a = _element(db_session, org.id, "Root")
    b = _element(db_session, org.id, "Child")
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=a.id, target_id=b.id, type="Serving", organization_id=org.id
    )
    db_session.add(rel)

    # Create ApplicationComponent and ApplicationOwner for element B.
    comp_b = _component(db_session, org.id, b, name="Child App")
    user = _make_user(db_session, org, "Diana", "Owner")
    _application_owner(db_session, org, comp_b.id, user, "primary")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.accountability_for_element(a.id)

    # The accountability answer must return owners from the walk.
    assert "owners" in result, "accountability_for_element missing owners key"
    assert len(result["owners"]) > 0, (
        "accountability_for_element returned empty owners"
    )
    owner_names = [o.get("name") for o in result["owners"]]
    assert "Diana Owner" in owner_names, (
        f"Expected Diana Owner in accountability owners, got {owner_names}"
    )