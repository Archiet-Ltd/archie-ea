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



def _compose(db_session, org_id, whole, part, type_="composition"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=whole.id, target_id=part.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _ask(app, org_id, element_id):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.portfolio_component_for_element(element_id)


def test_a_part_answers_with_the_application_it_belongs_to(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("portfolio-lens-part")
    whole = _element(db_session, org.id, "MarketingIQ")
    part = _element(db_session, org.id, "MarketingIQ — backend (API and workers)")
    record = ApplicationComponent(name="MarketingIQ", organization_id=org.id, archimate_element_id=whole.id)
    db_session.add(record)
    rel = _compose(db_session, org.id, whole, part)
    db_session.commit()

    result = _ask(app, org.id, part.id)

    assert result["application_component_id"] == record.id
    assert result["reasons"] == []
    assert result["part_of"] == (
        "MarketingIQ — backend (API and workers) is part of MarketingIQ; figures are MarketingIQ's."
    )
    assert result["resolved_through"]["parent_element_id"] == whole.id
    assert result["resolved_through"]["relationship_ids"] == [rel.id]


def test_the_nearest_whole_with_a_record_is_used(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("portfolio-lens-nearest")
    outer = _element(db_session, org.id, "Suite")
    inner = _element(db_session, org.id, "Module")
    part = _element(db_session, org.id, "Worker")
    db_session.add(ApplicationComponent(name="Suite", organization_id=org.id, archimate_element_id=outer.id))
    inner_record = ApplicationComponent(name="Module", organization_id=org.id, archimate_element_id=inner.id)
    db_session.add(inner_record)
    _compose(db_session, org.id, outer, inner)
    _compose(db_session, org.id, inner, part, type_="aggregation")
    db_session.commit()

    result = _ask(app, org.id, part.id)

    assert result["application_component_id"] == inner_record.id


def test_a_part_of_nothing_with_a_record_says_so(app, db_session, make_org):
    org = make_org("portfolio-lens-orphan")
    whole = _element(db_session, org.id, "Whole")
    part = _element(db_session, org.id, "Part")
    _compose(db_session, org.id, whole, part)
    db_session.commit()

    result = _ask(app, org.id, part.id)

    assert result["reasons"] == ["no_application_component"]
    assert result["message"] == "Part is not part of any application in the model."


def test_a_relationship_that_is_not_a_part_does_not_resolve(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("portfolio-lens-serving")
    whole = _element(db_session, org.id, "Server App")
    other = _element(db_session, org.id, "Client")
    db_session.add(ApplicationComponent(name="Server App", organization_id=org.id, archimate_element_id=whole.id))
    _compose(db_session, org.id, whole, other, type_="serving")
    db_session.commit()

    assert _ask(app, org.id, other.id)["reasons"] == ["no_application_component"]


def test_another_organisations_application_never_answers_for_a_part(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org_a = make_org("portfolio-lens-part-a")
    org_b = make_org("portfolio-lens-part-b")
    part = _element(db_session, org_a.id, "A part")
    foreign = _element(db_session, org_b.id, "B application")
    db_session.add(ApplicationComponent(name="B application", organization_id=org_b.id, archimate_element_id=foreign.id))
    # The relationship row is written directly, as a bad import could, in each organisation's name.
    _compose(db_session, org_b.id, foreign, part)
    _compose(db_session, org_a.id, foreign, part)
    db_session.commit()

    result = _ask(app, org_a.id, part.id)

    assert result["application_component_id"] is None
    assert result["reasons"] == ["no_application_component"]
    assert result["resolved_through"] is None
