"""Governed element properties: an organisation defines a typed property with
allowed values and a mandatory flag for one element type; every write through
the element and proposal property writers is checked against it; and the
elements still missing a value are listed.

Every test uses two real organisations and proves organisation B never sees,
counts or is constrained by organisation A's definitions.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, org, name, element_type="ApplicationComponent", props=None):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type=element_type, layer="application",
                               organization_id=org.id, acm_properties=props or {})
    db_session.add(element)
    db_session.flush()
    return element


def _define(org, **overrides):
    from app.modules.architecture_assistant.property_service import GovernedPropertyService

    fields = dict(archimate_type="ApplicationComponent", display_name="Recovery time (hours)",
                  property_type="number", mandatory=True)
    fields.update(overrides)
    return GovernedPropertyService().define(org.id, **fields)


def test_definition_is_visible_to_its_organisation_only(db_session, make_org, tenant_ctx):
    from app.modules.architecture_assistant.property_service import (
        GovernedPropertyService,
        PropertyService,
        template_query,
    )

    org_a = make_org("mm-a")
    org_b = make_org("mm-b")
    with tenant_ctx(org_a.id):
        definition = _define(org_a)
        assert definition.property_key == "recovery_time_hours"
        keys_a = [t["property_key"] for t in PropertyService().get_templates_for_type("ApplicationComponent")]
        assert "recovery_time_hours" in keys_a

    with tenant_ctx(org_b.id):
        keys_b = [t["property_key"] for t in PropertyService().get_templates_for_type("ApplicationComponent")]
        assert "recovery_time_hours" not in keys_b
        assert template_query().filter_by(id=definition.id).first() is None
        assert GovernedPropertyService().definitions(org_b.id) == []
        assert GovernedPropertyService().definition(org_b.id, definition.id) is None


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"archimate_type": "NotAType"}, "Choose an element type."),
        ({"display_name": "  "}, "Give the property a name."),
        ({"property_type": "colour"}, "Choose a value type."),
        ({"property_type": "enum", "allowed_values": ["Gold"]}, "A list needs at least two allowed values."),
        ({"property_type": "number", "allowed_values": ["1", "2"]}, "Allowed values apply only to a list."),
    ],
)
def test_a_definition_that_cannot_be_saved_says_why(db_session, make_org, overrides, reason):
    from app.modules.architecture_assistant.property_service import PropertyDefinitionError

    org = make_org("mm-bad")
    with pytest.raises(PropertyDefinitionError) as exc:
        _define(org, **overrides)
    assert str(exc.value) == reason


def test_the_same_key_twice_is_refused_but_another_organisation_may_use_it(db_session, make_org):
    from app.modules.architecture_assistant.property_service import PropertyDefinitionError

    org_a = make_org("mm-dup-a")
    org_b = make_org("mm-dup-b")
    _define(org_a)
    with pytest.raises(PropertyDefinitionError):
        _define(org_a, display_name="Recovery time hours")
    assert _define(org_b).organization_id == org_b.id


def test_values_are_coerced_or_refused_with_a_reason(db_session, make_org, tenant_ctx):
    from app.modules.architecture_assistant.property_service import GovernedPropertyService

    org_a = make_org("mm-val-a")
    org_b = make_org("mm-val-b")
    service = GovernedPropertyService()
    with tenant_ctx(org_a.id):
        _define(org_a)
        _define(org_a, display_name="Tier", property_type="enum", allowed_values=["Gold", "Silver"], mandatory=False)
        _define(org_a, display_name="Internet facing", property_type="boolean", mandatory=False)
        _define(org_a, display_name="Go live", property_type="date", mandatory=False)

        values, errors = service.validate_updates(
            "application_component",
            {"recovery_time_hours": "4", "tier": "Gold", "internet_facing": "yes", "go_live": "2026-10-01",
             "untemplated_note": "kept as is"},
        )
        assert errors == []
        assert values == {"recovery_time_hours": 4, "tier": "Gold", "internet_facing": True,
                          "go_live": "2026-10-01", "untemplated_note": "kept as is"}

        _, errors = service.validate_updates(
            "ApplicationComponent",
            {"recovery_time_hours": "four", "tier": "Bronze", "internet_facing": "maybe", "go_live": "01/10/2026"},
        )
        assert errors == [
            "Recovery time (hours) must be a number.",
            "Tier must be one of: Gold, Silver.",
            "Internet facing must be yes or no.",
            "Go live must be a date written as YYYY-MM-DD.",
        ]
        _, errors = service.validate_updates("ApplicationComponent", {"recovery_time_hours": ""})
        assert errors == ["Recovery time (hours) needs a value."]

    with tenant_ctx(org_b.id):
        values, errors = service.validate_updates("ApplicationComponent", {"recovery_time_hours": "four"})
        assert errors == []
        assert values == {"recovery_time_hours": "four"}


def test_element_writer_refuses_a_bad_value_and_stores_nothing(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture_assistant.journey_orchestrator import JourneyOrchestrator

    org = make_org("mm-write")
    with tenant_ctx(org.id):
        _define(org)
        element = _element(db_session, org, "Payments", props={"recovery_time_hours": {"value": 8, "source": "user"}})
        db_session.commit()

        refused = JourneyOrchestrator(0).update_element(
            element.id, {"name": "Payments renamed", "acm_properties": {"recovery_time_hours": "soon"}})
        assert refused["property_errors"] == ["Recovery time (hours) must be a number."]
        db_session.expire_all()
        stored = db_session.get(ArchiMateElement, element.id)
        assert stored.name == "Payments"
        assert stored.acm_properties["recovery_time_hours"] == {"value": 8, "source": "user"}

        JourneyOrchestrator(0).update_element(element.id, {"acm_properties": {"recovery_time_hours": "2.5"}})
        db_session.expire_all()
        stored = db_session.get(ArchiMateElement, element.id)
        assert stored.acm_properties["recovery_time_hours"] == {"value": 2.5, "source": "user"}


def test_missing_value_list_names_only_this_organisations_elements(db_session, make_org, tenant_ctx):
    from app.modules.architecture_assistant.property_service import GovernedPropertyService

    org_a = make_org("mm-miss-a")
    org_b = make_org("mm-miss-b")
    with tenant_ctx(org_a.id):
        definition = _define(org_a)
        filled = _element(db_session, org_a, "Filled", props={"recovery_time_hours": {"value": 4, "source": "user"}})
        empty = _element(db_session, org_a, "Empty")
        tbd = _element(db_session, org_a, "Placeholder", props={"recovery_time_hours": {"value": "TBD"}})
        snake = _element(db_session, org_a, "Snake typed", element_type="application_component")
        _element(db_session, org_a, "A process", element_type="BusinessProcess")
        db_session.commit()
    with tenant_ctx(org_b.id):
        _element(db_session, org_b, "B app")
        db_session.commit()

    with tenant_ctx(org_a.id):
        missing = GovernedPropertyService().missing_values(definition, org_a.id)
        assert [m["id"] for m in missing] == [empty.id, tbd.id, snake.id]
        assert filled.id not in [m["id"] for m in missing]
    with tenant_ctx(org_b.id):
        # B's own element of the same type is never counted against A's
        # definition, and B reads nothing of A's.
        assert GovernedPropertyService().missing_values(definition, org_b.id) == []


def _user(db_session, org, role):
    from app.models.user import User

    user = User(email="mm-%s-%s@example.com" % (role, org.id), organization_id=org.id,
                enterprise_role=role, confirmed=True)
    db_session.add(user)
    db_session.commit()
    return user


def test_pages_define_list_and_fence_by_organisation(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.acm_property_template import AcmPropertyTemplate

    org_a = make_org("mm-page-a")
    org_b = make_org("mm-page-b")
    with tenant_ctx(org_a.id):
        _element(db_session, org_a, "Needs a value")
        db_session.commit()
    architect = _user(db_session, org_a, "enterprise_architect")
    solution = _user(db_session, org_a, "solution_architect")
    outsider = _user(db_session, org_b, "enterprise_architect")
    client = app.test_client()

    form = {"archimate_type": "ApplicationComponent", "display_name": "Data owner sign-off",
            "property_type": "enum", "allowed_values": "Signed\nNot signed", "mandatory": "on"}

    login_as(client, solution)
    assert client.post("/metamodel/properties", data=form).status_code == 403

    login_as(client, architect)
    bad = client.post("/metamodel/properties", data=dict(form, allowed_values="Signed"))
    assert bad.status_code == 400
    assert "A list needs at least two allowed values." in bad.get_data(as_text=True)

    login_as(client, architect)
    created = client.post("/metamodel/properties", data=form)
    assert created.status_code == 302
    definition = AcmPropertyTemplate.query.filter_by(organization_id=org_a.id).one()
    assert definition.is_mandatory is True
    assert definition.enum_options == ["Signed", "Not signed"]
    assert created.headers["Location"].endswith("/metamodel/properties/%d" % definition.id)

    login_as(client, architect)
    detail = client.get("/metamodel/properties/%d" % definition.id).get_data(as_text=True)
    assert "Needs a value" in detail
    assert "1 ApplicationComponent element without Data owner sign-off." in detail

    login_as(client, solution)
    index = client.get("/metamodel/properties").get_data(as_text=True)
    assert "Data owner sign-off" in index
    assert 'data-testid="metamodel-define-form"' not in index

    login_as(client, outsider)
    assert client.get("/metamodel/properties/%d" % definition.id).status_code == 404
    login_as(client, outsider)
    assert "Data owner sign-off" not in client.get("/metamodel/properties").get_data(as_text=True)
