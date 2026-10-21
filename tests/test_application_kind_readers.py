"""Every reader of the element type handles the Application specialisation (ADR-0014 Decision 8).

An organisation holds one Application and one component (an ApplicationComponent that is not an inventory
mirror). Each reader that means "an application" or looks the type up in a table keyed by standard type must
treat the Application as it treats a component.
"""

import pytest

pytestmark = pytest.mark.journey

APP = "Application"
COMP = "ApplicationComponent"


@pytest.fixture
def world(app, db_session, make_org):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    org = make_org("appkind-readers")
    root = ArchiMateElement(name="Root", type="BusinessProcess", layer="business", organization_id=org.id)
    application = ArchiMateElement(name="Billing", type=APP, layer="application", organization_id=org.id)
    component = ArchiMateElement(name="Billing API", type=COMP, layer="application", organization_id=org.id)
    db_session.add_all([root, application, component])
    db_session.flush()
    for target in (application, component):
        db_session.add(
            ArchiMateRelationship(
                source_id=root.id, target_id=target.id, type="Serving", organization_id=org.id
            )
        )
    row = ApplicationComponent(name="Billing", organization_id=org.id, archimate_element_id=application.id)
    db_session.add(row)
    user = User(first_name="Ada", last_name="Owner", email="ada.owner@example.com", organization_id=org.id)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        ApplicationOwner(
            application_id=row.id, user_id=user.id, organization_id=org.id, ownership_type="primary"
        )
    )
    db_session.commit()
    return dict(
        org_id=org.id, root_id=root.id, application_id=application.id, component_id=component.id, row_id=row.id
    )


def _ctx(app, org_id):
    ctx = app.test_request_context("/")
    ctx.push()
    from flask import g

    g.current_org_id = org_id
    return ctx


def test_impact_answer_reaches_both_kinds(app, world):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    ctx = _ctx(app, world["org_id"])
    try:
        result = IntelligenceQueryService.cross_layer_impact(world["root_id"], include_derived=False)
    finally:
        ctx.pop()
    reached = {row["element_id"] for row in result["rows"]}
    assert {world["application_id"], world["component_id"]} <= reached
    types = {el["type"] for el in result["elements"].values()}
    assert {APP, COMP} <= types


def test_owners_of_an_application_are_found_in_the_impact_answer(app, world):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    ctx = _ctx(app, world["org_id"])
    try:
        result = IntelligenceQueryService.accountability_for_element(world["root_id"])
    finally:
        ctx.pop()
    assert "Ada Owner" in [o.get("name") for o in result["owners"]]


def test_element_to_inventory_resolution_finds_the_applications_row(app, world):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    ctx = _ctx(app, world["org_id"])
    try:
        found = IntelligenceQueryService.portfolio_component_for_element(world["application_id"])
        comp = IntelligenceQueryService.portfolio_component_for_element(world["component_id"])
    finally:
        ctx.pop()
    assert found["application_component_id"] == world["row_id"]
    assert found["reasons"] == []
    # a component that is not an inventory mirror has no row
    assert comp["application_component_id"] is None


def test_every_viewpoint_that_allows_components_includes_the_application():
    from app.config.archimate_viewpoints import (
        VIEWPOINTS,
        get_viewpoints_allowing_element,
        is_element_allowed_in_viewpoint,
    )

    allowing_component = {c for c, vp in VIEWPOINTS.items() if COMP in vp.allowed_elements or "*" in vp.allowed_elements}
    assert allowing_component, "expected at least one viewpoint to allow components"
    for code in allowing_component:
        assert is_element_allowed_in_viewpoint(APP, code), code
    assert {vp.code for vp in get_viewpoints_allowing_element(APP)} == {
        vp.code for vp in get_viewpoints_allowing_element(COMP)
    }


def test_traceability_layer_of_an_application_is_application():
    from app.modules.intelligence.services.traceability_check_service import _layer_of

    assert _layer_of({"type": APP}) == "application"
    assert _layer_of({"type": COMP}) == "application"


def test_validity_tables_resolve_the_specialisation():
    from app.services.archimate_validity_service import _aspect, _layer

    assert _layer(APP) == "application"
    assert _aspect(APP) == "active"


def test_the_layer_service_accepts_an_application_as_it_accepts_a_component(app, db_session, make_org):
    """The architecture layer services refused any element whose type was not the literal ApplicationComponent."""
    from app.models import ArchiMateElement
    from app.modules.architecture.services.application_layer_service import ApplicationLayerService

    org = make_org("appkind-layer")
    el = ArchiMateElement(name="Billing", type=APP, layer="application", organization_id=org.id)
    db_session.add(el)
    db_session.commit()
    element_id = el.id

    class _Llm:
        def generate_from_prompt(self, prompt):
            return "{}"

    service = ApplicationLayerService.__new__(ApplicationLayerService)
    service.llm_service = _Llm()
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        try:
            service.analyze_application_dependencies(element_id)
        except ValueError as exc:  # pragma: no cover - the failure being pinned
            pytest.fail(f"an Application was refused: {exc}")
