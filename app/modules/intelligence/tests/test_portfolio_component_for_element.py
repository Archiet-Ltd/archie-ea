"""Tests for ``IntelligenceQueryService.portfolio_component_for_element``
(L3): resolving an ArchiMate element to the ApplicationComponent row the
one real Portfolio deep link (rationalization planning) is keyed on.

Fixtures (app, db_session, make_org) are discovered via
app/modules/conftest.py's import of tests.conftest. No import needed here.
"""

from __future__ import annotations


def _element(db_session, org_id, name, layer="application", type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def test_element_with_no_component_returns_honest_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("portfolio-lens-none")
    # A BusinessActor, not an ApplicationComponent -- the real, common case
    # this reason code exists for.
    a = _element(db_session, org.id, "A", layer="business", type_="BusinessActor")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.portfolio_component_for_element(a.id)

    assert result["application_component_id"] is None
    assert result["reasons"] == ["no_application_component"]


def test_unknown_element_returns_element_not_found_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("portfolio-lens-unknown")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.portfolio_component_for_element(999999999)

    assert result["application_component_id"] is None
    assert result["reasons"] == ["element_not_found"]


def test_no_tenant_context_returns_honest_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("portfolio-lens-no-ctx")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.portfolio_component_for_element(a.id)

    assert result["application_component_id"] is None
    assert result["reasons"] == ["no_tenant_context"]


def test_component_resolved_via_reverse_lookup(app, db_session, make_org):
    """The element carries no forward application_component_id, so this
    proves the fallback reverse lookup (ApplicationComponent.
    archimate_element_id) -- the more common real-world shape, per the
    same dual-lookup strategic_routes.py already establishes."""
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("portfolio-lens-reverse")
    a = _element(db_session, org.id, "A")
    component = ApplicationComponent(name="A App", organization_id=org.id, archimate_element_id=a.id)
    db_session.add(component)
    db_session.commit()
    component_id = component.id

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.portfolio_component_for_element(a.id)

    assert result["reasons"] == []
    assert result["application_component_id"] == component_id


def test_forward_fk_on_the_element_is_honoured_first(app, db_session, make_org):
    """At normal runtime ``app.models.ArchiMateElement`` is the class in
    ``app/models/models.py`` (``archimate_core`` re-exports it unless
    APP_FAST_INIT is set), and that class carries ``application_component_id``.
    So the forward link is a live first branch: when the element names a
    component directly, that component is the answer, even when a different
    component points back at the element."""
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    assert hasattr(ArchiMateElement, "application_component_id")

    org = make_org("portfolio-lens-forward")
    a = _element(db_session, org.id, "A")
    forward = ApplicationComponent(name="Forward App", organization_id=org.id)
    reverse = ApplicationComponent(name="Reverse App", organization_id=org.id, archimate_element_id=a.id)
    db_session.add_all([forward, reverse])
    db_session.flush()
    a.application_component_id = forward.id
    db_session.commit()
    forward_id = forward.id

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.portfolio_component_for_element(a.id)

    assert result["reasons"] == []
    assert result["application_component_id"] == forward_id

