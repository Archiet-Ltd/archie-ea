"""Typed property writes must coerce, scope and refuse invalid values."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _seed_templates():
    from app.commands.seed_viewpoints import seed_property_templates

    seed_property_templates()


def _make_user(db_session, org_id, email_prefix):
    from app.models.user import Role, User

    Role.insert_roles()
    architect_role = Role.query.filter_by(name="Architect").one()
    user = User(
        email=f"{email_prefix}@example.com",
        first_name="Typed",
        last_name="Writer",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.role = architect_role
    user.password = "TestPass!2026"
    db_session.add(user)
    db_session.flush()
    return user


def _make_solution(db_session, org_id, owner_id, name="Typed properties solution"):
    from app.models.solution_models import Solution

    solution = Solution(
        name=name,
        organization_id=org_id,
        created_by_id=owner_id,
        has_acm_domains=True,
    )
    db_session.add(solution)
    db_session.flush()
    return solution


def _make_proposal(db_session, solution_id, org_id, archimate_type="ApplicationInterface"):
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

    proposal = SolutionBlueprintProposal(
        solution_id=solution_id,
        organization_id=org_id,
        archimate_type=archimate_type,
        name="Payments API",
        status="accepted",
    )
    db_session.add(proposal)
    db_session.flush()
    return proposal


def _make_domain_spec(db_session, solution_id, org_id, domain_code, status="pending"):
    from app.models.solution_domain_spec import SolutionDomainSpec

    spec = SolutionDomainSpec(
        solution_id=solution_id,
        organization_id=org_id,
        domain_code=domain_code,
        status=status,
    )
    db_session.add(spec)
    db_session.flush()
    return spec


def _make_application_with_model(db_session, org_id, name="Typed route app"):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchitectureModel, ArchiMateElement

    app_obj = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(app_obj)
    db_session.flush()

    model = ArchitectureModel(name=f"{name} model", organization_id=org_id)
    db_session.add(model)
    db_session.flush()

    app_element = db_session.get(ArchiMateElement, app_obj.archimate_element_id)
    app_element.architecture_id = model.id
    db_session.flush()
    return app_obj, model


def _make_architecture_element(
    db_session,
    org_id,
    architecture_id,
    *,
    name,
    archimate_type,
    layer,
    acm_properties=None,
):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=name,
        type=archimate_type,
        layer=layer,
        organization_id=org_id,
        architecture_id=architecture_id,
        acm_properties=acm_properties or {},
    )
    db_session.add(element)
    db_session.flush()
    return element


def _make_property_template(
    db_session,
    *,
    org_id,
    archimate_type,
    property_key,
    display_name,
    property_type,
    enum_options=None,
):
    from app.models.acm_property_template import AcmPropertyTemplate

    row = AcmPropertyTemplate(
        organization_id=org_id,
        archimate_type=archimate_type,
        property_key=property_key,
        display_name=display_name,
        property_type=property_type,
        enum_options=enum_options,
        required_for_tier="standard",
        sort_order=10,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_set_element_property_stores_number_unit_and_source(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture_assistant.property_service import PropertyService

    _seed_templates()
    org = make_org("typed-prop-element")
    element = ArchiMateElement(
        name="Customer API",
        type="ApplicationInterface",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()

    entry = PropertyService().set_element_property(element, "rate_limit", "1200 req/min")
    db_session.flush()

    assert entry == {"value": 1200, "unit": "req/min", "source": "user"}
    assert element.acm_properties["rate_limit"] == entry


def test_set_element_property_refuses_unparseable_numeric_value(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture_assistant.property_service import PropertyService, PropertyValidationError

    _seed_templates()
    org = make_org("typed-prop-refusal")
    element = ArchiMateElement(
        name="Customer API",
        type="ApplicationInterface",
        layer="application",
        organization_id=org.id,
        acm_properties={"rate_limit": {"value": 900, "unit": "req/min", "source": "user"}},
    )
    db_session.add(element)
    db_session.flush()

    with pytest.raises(PropertyValidationError, match="Enter a number"):
        PropertyService().set_element_property(element, "rate_limit", "not a number")

    assert element.acm_properties["rate_limit"]["value"] == 900


def test_update_proposal_properties_refuses_invalid_numeric_text(client, db_session, make_org, login_as):
    _seed_templates()
    org = make_org("typed-prop-route")
    owner = _make_user(db_session, org.id, "typed-route-owner")
    solution = _make_solution(db_session, org.id, owner.id)
    proposal = _make_proposal(db_session, solution.id, org.id)
    proposal.acm_properties = {"rate_limit": {"value": 800, "unit": "req/min", "source": "user"}}
    db_session.flush()

    login_as(client, owner)
    response = client.patch(
        f"/architecture-journey/{solution.id}/proposals/{proposal.id}/properties",
        json={"properties": {"rate_limit": "plain text"}},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Enter a number for this property."

    db_session.expire_all()
    reloaded = db_session.get(type(proposal), proposal.id)
    assert reloaded.acm_properties["rate_limit"]["value"] == 800


def test_update_proposal_properties_is_tenant_scoped(client, db_session, make_org, login_as):
    _seed_templates()
    org_a = make_org("typed-prop-a")
    org_b = make_org("typed-prop-b")
    owner_a = _make_user(db_session, org_a.id, "typed-owner-a")
    owner_b = _make_user(db_session, org_b.id, "typed-owner-b")
    solution_b = _make_solution(db_session, org_b.id, owner_b.id, name="Foreign solution")
    proposal_b = _make_proposal(db_session, solution_b.id, org_b.id)
    proposal_b.acm_properties = {"rate_limit": {"value": 700, "unit": "req/min", "source": "user"}}
    db_session.flush()

    login_as(client, owner_a)
    response = client.patch(
        f"/architecture-journey/{solution_b.id}/proposals/{proposal_b.id}/properties",
        json={"properties": {"rate_limit": "1300 req/min"}},
    )

    assert response.status_code == 403

    db_session.expire_all()
    reloaded = db_session.get(type(proposal_b), proposal_b.id)
    assert reloaded.acm_properties["rate_limit"]["value"] == 700


def test_update_element_route_persists_typed_number_and_unit(client, db_session, make_org, login_as):
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement

    _seed_templates()
    org = make_org("typed-prop-element-route")
    owner = _make_user(db_session, org.id, "typed-element-owner")
    solution = _make_solution(db_session, org.id, owner.id, name="Element route solution")
    element = ArchiMateElement(
        name="Payments Service",
        type="ApplicationService",
        layer="application",
        organization_id=org.id,
        acm_properties={"availability_target": {"value": 99.5, "unit": "%", "source": "user"}},
    )
    db_session.add(element)
    db_session.flush()
    db_session.add(SolutionArchiMateElement(solution_id=solution.id, element_id=element.id))
    db_session.flush()

    login_as(client, owner)
    response = client.patch(
        f"/architecture-journey/{solution.id}/element/{element.id}",
        json={"acm_properties": {"availability_target": "99.9%"}},
    )

    assert response.status_code == 200
    db_session.expire_all()
    reloaded = db_session.get(ArchiMateElement, element.id)
    assert reloaded.acm_properties["availability_target"] == {
        "value": 99.9,
        "unit": "%",
        "source": "user",
    }


def test_architecture_api_update_route_persists_typed_number_unit_and_source(
    client, db_session, make_org, login_as
):
    _seed_templates()
    org = make_org("arch-api-route")
    owner = _make_user(db_session, org.id, "arch-api-owner")
    app_obj, model = _make_application_with_model(db_session, org.id, "Architecture API app")
    element = _make_architecture_element(
        db_session,
        org.id,
        model.id,
        name="Orders API",
        archimate_type="ApplicationInterface",
        layer="application",
    )

    login_as(client, owner)
    response = client.put(
        f"/dashboard/api/applications/{app_obj.id}/architecture/elements/{element.id}",
        json={"properties": {"rate_limit": "1200 req/min"}},
    )

    assert response.status_code == 200
    db_session.expire_all()
    reloaded = db_session.get(type(element), element.id)
    assert reloaded.acm_properties["rate_limit"] == {
        "value": 1200,
        "unit": "req/min",
        "source": "user",
    }


def test_architecture_api_update_route_refuses_unparseable_typed_value(
    client, db_session, make_org, login_as
):
    _seed_templates()
    org = make_org("arch-api-invalid")
    owner = _make_user(db_session, org.id, "arch-api-invalid-owner")
    app_obj, model = _make_application_with_model(db_session, org.id, "Architecture API invalid app")
    element = _make_architecture_element(
        db_session,
        org.id,
        model.id,
        name="Orders API",
        archimate_type="ApplicationInterface",
        layer="application",
        acm_properties={"rate_limit": {"value": 900, "unit": "req/min", "source": "user"}},
    )

    login_as(client, owner)
    response = client.put(
        f"/dashboard/api/applications/{app_obj.id}/architecture/elements/{element.id}",
        json={"properties": {"rate_limit": "plain text"}},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Enter a number for this property."
    db_session.expire_all()
    reloaded = db_session.get(type(element), element.id)
    assert reloaded.acm_properties["rate_limit"]["value"] == 900


def test_architecture_api_update_route_is_tenant_scoped(client, db_session, make_org, login_as):
    _seed_templates()
    org_a = make_org("arch-api-a")
    org_b = make_org("arch-api-b")
    owner_a = _make_user(db_session, org_a.id, "arch-api-owner-a")
    _make_user(db_session, org_b.id, "arch-api-owner-b")
    app_b, model_b = _make_application_with_model(db_session, org_b.id, "Foreign architecture app")
    element_b = _make_architecture_element(
        db_session,
        org_b.id,
        model_b.id,
        name="Foreign API",
        archimate_type="ApplicationInterface",
        layer="application",
        acm_properties={"rate_limit": {"value": 700, "unit": "req/min", "source": "user"}},
    )

    login_as(client, owner_a)
    response = client.put(
        f"/dashboard/api/applications/{app_b.id}/architecture/elements/{element_b.id}",
        json={"properties": {"rate_limit": "1300 req/min"}},
    )

    assert response.status_code == 404
    db_session.expire_all()
    reloaded = db_session.get(type(element_b), element_b.id)
    assert reloaded.acm_properties["rate_limit"]["value"] == 700


def test_application_element_update_route_persists_typed_number_unit_and_source(
    client, db_session, make_org, login_as
):
    _seed_templates()
    org = make_org("app-element-route")
    owner = _make_user(db_session, org.id, "app-element-owner")
    app_obj, model = _make_application_with_model(db_session, org.id, "Application element app")
    element = _make_architecture_element(
        db_session,
        org.id,
        model.id,
        name="Billing API",
        archimate_type="ApplicationInterface",
        layer="application",
    )

    login_as(client, owner)
    response = client.put(
        f"/dashboard/api/applications/{app_obj.id}/elements/{element.id}",
        json={"properties": {"rate_limit": "1250 req/min"}},
    )

    assert response.status_code == 200
    db_session.expire_all()
    reloaded = db_session.get(type(element), element.id)
    assert reloaded.acm_properties["rate_limit"] == {
        "value": 1250,
        "unit": "req/min",
        "source": "user",
    }


def test_application_element_update_route_refuses_unparseable_typed_value(
    client, db_session, make_org, login_as
):
    _seed_templates()
    org = make_org("app-element-invalid")
    owner = _make_user(db_session, org.id, "app-element-invalid-owner")
    app_obj, model = _make_application_with_model(db_session, org.id, "Application element invalid app")
    element = _make_architecture_element(
        db_session,
        org.id,
        model.id,
        name="Billing API",
        archimate_type="ApplicationInterface",
        layer="application",
        acm_properties={"rate_limit": {"value": 850, "unit": "req/min", "source": "user"}},
    )

    login_as(client, owner)
    response = client.put(
        f"/dashboard/api/applications/{app_obj.id}/elements/{element.id}",
        json={"properties": {"rate_limit": "plain text"}},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Enter a number for this property."
    db_session.expire_all()
    reloaded = db_session.get(type(element), element.id)
    assert reloaded.acm_properties["rate_limit"]["value"] == 850


def test_application_element_update_route_is_tenant_scoped(
    client, db_session, make_org, login_as
):
    _seed_templates()
    org_a = make_org("app-element-a")
    org_b = make_org("app-element-b")
    owner_a = _make_user(db_session, org_a.id, "app-element-owner-a")
    _make_user(db_session, org_b.id, "app-element-owner-b")
    app_b, model_b = _make_application_with_model(db_session, org_b.id, "Foreign application element app")
    element_b = _make_architecture_element(
        db_session,
        org_b.id,
        model_b.id,
        name="Foreign Billing API",
        archimate_type="ApplicationInterface",
        layer="application",
        acm_properties={"rate_limit": {"value": 650, "unit": "req/min", "source": "user"}},
    )

    login_as(client, owner_a)
    response = client.put(
        f"/dashboard/api/applications/{app_b.id}/elements/{element_b.id}",
        json={"properties": {"rate_limit": "1500 req/min"}},
    )

    assert response.status_code == 404
    db_session.expire_all()
    reloaded = db_session.get(type(element_b), element_b.id)
    assert reloaded.acm_properties["rate_limit"]["value"] == 650


def test_update_lifecycle_route_persists_typed_value_and_source(
    client, db_session, make_org, login_as
):
    org = make_org("lifecycle-route")
    owner = _make_user(db_session, org.id, "lifecycle-owner")
    _make_property_template(
        db_session,
        org_id=org.id,
        archimate_type="Node",
        property_key="lifecycle",
        display_name="Lifecycle",
        property_type="enum",
        enum_options=["active", "retired"],
    )
    element = _make_architecture_element(
        db_session,
        org.id,
        architecture_id=None,
        name="Payments Node",
        archimate_type="Node",
        layer="technology",
    )

    login_as(client, owner)
    response = client.patch(
        f"/api/archimate/elements/{element.id}/lifecycle",
        json={"lifecycle": "active"},
    )

    assert response.status_code == 200
    db_session.expire_all()
    reloaded = db_session.get(type(element), element.id)
    assert reloaded.acm_properties["lifecycle"] == {
        "value": "active",
        "source": "user",
    }


def test_update_lifecycle_route_refuses_invalid_typed_value(
    client, db_session, make_org, login_as
):
    org = make_org("lifecycle-invalid")
    owner = _make_user(db_session, org.id, "lifecycle-invalid-owner")
    _make_property_template(
        db_session,
        org_id=org.id,
        archimate_type="Node",
        property_key="lifecycle",
        display_name="Lifecycle",
        property_type="enum",
        enum_options=["active", "retired"],
    )
    element = _make_architecture_element(
        db_session,
        org.id,
        architecture_id=None,
        name="Payments Node",
        archimate_type="Node",
        layer="technology",
        acm_properties={"lifecycle": {"value": "retired", "source": "user"}},
    )

    login_as(client, owner)
    response = client.patch(
        f"/api/archimate/elements/{element.id}/lifecycle",
        json={"lifecycle": "unknown"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Choose one of the allowed values for this property."
    db_session.expire_all()
    reloaded = db_session.get(type(element), element.id)
    assert reloaded.acm_properties["lifecycle"]["value"] == "retired"


def test_update_lifecycle_route_is_tenant_scoped(client, db_session, make_org, login_as):
    org_a = make_org("lifecycle-a")
    org_b = make_org("lifecycle-b")
    owner_a = _make_user(db_session, org_a.id, "lifecycle-owner-a")
    _make_user(db_session, org_b.id, "lifecycle-owner-b")
    _make_property_template(
        db_session,
        org_id=org_b.id,
        archimate_type="Node",
        property_key="lifecycle",
        display_name="Lifecycle",
        property_type="enum",
        enum_options=["active", "retired"],
    )
    element_b = _make_architecture_element(
        db_session,
        org_b.id,
        architecture_id=None,
        name="Foreign Node",
        archimate_type="Node",
        layer="technology",
        acm_properties={"lifecycle": {"value": "retired", "source": "user"}},
    )

    login_as(client, owner_a)
    response = client.patch(
        f"/api/archimate/elements/{element_b.id}/lifecycle",
        json={"lifecycle": "active"},
    )

    assert response.status_code == 404
    db_session.expire_all()
    reloaded = db_session.get(type(element_b), element_b.id)
    assert reloaded.acm_properties["lifecycle"]["value"] == "retired"


def test_domain_promotion_transfers_typed_properties_with_promotion_source(
    db_session, make_org, tenant_ctx
):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture_assistant.domain_promotion import DomainPromotionService

    _seed_templates()
    org = make_org("domain-promotion")
    owner = _make_user(db_session, org.id, "domain-promotion-owner")
    solution = _make_solution(db_session, org.id, owner.id, name="Promotion solution")
    _make_domain_spec(db_session, solution.id, org.id, "COM", status="confirmed")
    proposal = _make_proposal(
        db_session,
        solution.id,
        org.id,
        archimate_type="ApplicationInterface",
    )
    proposal.acm_domain = "COM"
    proposal.acm_properties = {
        "rate_limit": {"value": "1500", "unit": "req/min", "source": "user"},
        "authentication": {"value": "mTLS", "source": "user"},
    }
    db_session.flush()

    with tenant_ctx(org.id):
        result = DomainPromotionService().promote_domain(solution.id, "COM")

    assert result["promoted"] == 1
    db_session.expire_all()
    reloaded_proposal = db_session.get(type(proposal), proposal.id)
    promoted = db_session.get(ArchiMateElement, reloaded_proposal.promoted_element_id)
    assert reloaded_proposal.status == "promoted"
    assert promoted.acm_properties["rate_limit"] == {
        "value": 1500,
        "unit": "req/min",
        "source": "promotion",
    }
    assert promoted.acm_properties["authentication"] == {
        "value": "mTLS",
        "source": "promotion",
    }
