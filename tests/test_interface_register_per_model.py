"""Interface Register is scoped per architecture model, not per roadmap initiative.

A brand-new organisation has no roadmap initiative and nothing in the product
creates one, so a register gated on one could never show a first screen. These
tests use no fixtures beyond the organisation and its user.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org):
    from app.models.user import ROLE_DATA_ARCHITECT, User

    user = User(
        email=f"ifr-model-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="Architect",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=ROLE_DATA_ARCHITECT,
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user


def _make_interface(db_session, org, name, architecture_id=None):
    from app.services.archimate_backbone import create_backbone_element

    element = create_backbone_element(
        element_type="ApplicationInterface",
        layer="Application",
        name=name,
        organization_id=org.id,
    )
    element.architecture_id = architecture_id
    db_session.flush()
    return element


def test_fresh_org_register_lists_interface_without_any_initiative(
    app, db_session, make_org, client, login_as
):
    org = make_org("fresh")
    user = _make_user(db_session, org)
    _make_interface(db_session, org, "Orders Feed")

    login_as(client, user)
    body = client.get("/interface-register/").get_data(as_text=True)

    assert "Orders Feed" in body
    assert "initiative" not in body.lower()


def test_fresh_org_can_register_an_interface_through_the_form(
    app, db_session, make_org, client, login_as
):
    org = make_org("fresh-create")
    user = _make_user(db_session, org)

    login_as(client, user)
    new_page = client.get("/interface-register/new")
    assert new_page.status_code == 200

    login_as(client, user)
    resp = client.post(
        "/interface-register/",
        data={"name": "Billing Sync", "interface_type": "REST", "protocol": "HTTPS"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    login_as(client, user)
    body = client.get("/interface-register/").get_data(as_text=True)
    assert "Billing Sync" in body


def test_register_never_shows_another_organisations_interface(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("iso-a")
    org_b = make_org("iso-b")
    user_a = _make_user(db_session, org_a)
    user_b = _make_user(db_session, org_b)
    a_iface = _make_interface(db_session, org_a, "Org A Secret Interface")
    _make_interface(db_session, org_b, "Org B Interface")

    login_as(client, user_b)
    body = client.get("/interface-register/").get_data(as_text=True)
    assert "Org B Interface" in body
    assert "Org A Secret Interface" not in body

    login_as(client, user_b)
    assert client.get(f"/interface-register/{a_iface.id}/edit").status_code == 404

    login_as(client, user_a)
    assert "Org A Secret Interface" in client.get("/interface-register/").get_data(as_text=True)


def test_register_narrows_to_one_architecture_model(
    app, db_session, make_org, client, login_as
):
    from app.models.archimate_core import ArchitectureModel

    org = make_org("per-model")
    user = _make_user(db_session, org)
    model_one = ArchitectureModel(name="Core Model", organization_id=org.id)
    model_two = ArchitectureModel(name="Other Model", organization_id=org.id)
    db_session.add_all([model_one, model_two])
    db_session.flush()
    _make_interface(db_session, org, "In Core", model_one.id)
    _make_interface(db_session, org, "In Other", model_two.id)

    login_as(client, user)
    everything = client.get("/interface-register/").get_data(as_text=True)
    assert "In Core" in everything and "In Other" in everything

    login_as(client, user)
    narrowed = client.get(
        f"/interface-register/?architecture_id={model_one.id}"
    ).get_data(as_text=True)
    assert "In Core" in narrowed
    assert "In Other" not in narrowed


def _make_initiative(db_session, org, name):
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    model = ArchitectureModel(name=f"{name} model", organization_id=org.id)
    db_session.add(model)
    db_session.flush()
    initiative = TechnologyRoadmapInitiative(
        name=name,
        architecture_id=model.id,
        fiscal_year_start=2026,
        fiscal_year_end=2027,
    )
    db_session.add(initiative)
    db_session.flush()
    return initiative


def test_register_offers_no_comparison_link_without_an_initiative(
    app, db_session, make_org, client, login_as
):
    org = make_org("no-initiative")
    user = _make_user(db_session, org)

    login_as(client, user)
    body = client.get("/interface-register/").get_data(as_text=True)

    assert "interface-comparison-link" not in body


def test_register_links_to_the_comparison_when_an_initiative_exists(
    app, db_session, make_org, client, login_as
):
    org = make_org("one-initiative")
    user = _make_user(db_session, org)
    initiative = _make_initiative(db_session, org, "Platform Uplift")

    login_as(client, user)
    body = client.get("/interface-register/").get_data(as_text=True)

    assert 'data-testid="interface-comparison-link"' in body
    assert f"/interface-register/comparison?initiative_id={initiative.id}" in body


def test_register_lists_one_comparison_link_per_initiative(
    app, db_session, make_org, client, login_as
):
    org = make_org("two-initiatives")
    user = _make_user(db_session, org)
    first = _make_initiative(db_session, org, "Alpha Programme")
    second = _make_initiative(db_session, org, "Beta Programme")

    login_as(client, user)
    body = client.get("/interface-register/").get_data(as_text=True)

    assert f'data-testid="interface-comparison-link-{first.id}"' in body
    assert f'data-testid="interface-comparison-link-{second.id}"' in body
