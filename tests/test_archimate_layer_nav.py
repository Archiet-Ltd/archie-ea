"""The "By Layer" sidebar navigation must actually land on the elements it names.

Three defects are pinned here, all of which made whole slabs of the ArchiMate
model unreachable from the UI while looking, from the sidebar, entirely present:

1. ``app/templates/components/admin_sidebar_northstar_phase2.html`` links ~52
   element types to ``archimate_layers.*``, each of which redirected to
   ``archimate.composer_page`` with ``layer=`` and ``element_type=``.
   ``composer_page`` reads only ``solution_id`` and ``viewpoint``, so both
   filters fell into the query string and were dropped: every business, data,
   technology and physical link landed on the *same* unfiltered diagram editor.
   The nav was a no-op that looked like navigation.

2. ``archimate_crud`` — the generic element browser those links should reach —
   had no ``physical`` layer at all: no ``LAYER_CONFIG`` key and no ``Physical*``
   class in ``MODEL_REGISTRY``, so the four models in
   ``app/models/physical_layer.py`` were unreachable through any read UI.

3. The technology layer was truncated to 7 of the 13 ArchiMate 3.2 types.
   Artifact, Technology Collaboration, Function, Process, Interaction and Event
   all had models and sidebar links, and no way to list them.

The assertions below are deliberately data-driven off ``MODEL_REGISTRY`` /
``LAYER_CONFIG`` so that adding a sidebar link without wiring the type behind it
fails here rather than in production.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

# Every generic "By Layer" nav endpoint, and the (layer, ArchiMate 3.2 type) the
# link promises.  Two endpoints are deliberately absent because they redirect to
# a purpose-built page rather than the generic browser:
#   archimate_layers.strategy_capabilities_tree -> capability_map.index
#   archimate_layers.business_processes         -> apqc.process_list
NAV_TARGETS = {
    # Motivation
    "archimate_layers.motivation_stakeholders": ("motivation", "Stakeholder"),
    "archimate_layers.motivation_drivers": ("motivation", "Driver"),
    "archimate_layers.motivation_assessments": ("motivation", "Assessment"),
    "archimate_layers.motivation_goals": ("motivation", "Goal"),
    "archimate_layers.motivation_outcomes": ("motivation", "Outcome"),
    "archimate_layers.motivation_principles": ("motivation", "Principle"),
    "archimate_layers.motivation_requirements": ("motivation", "Requirement"),
    "archimate_layers.motivation_constraints": ("motivation", "Constraint"),
    "archimate_layers.motivation_meanings": ("motivation", "Meaning"),
    # Strategy
    "archimate_layers.strategy_resources": ("strategy", "Resource"),
    "archimate_layers.strategy_value_streams": ("strategy", "ValueStream"),
    "archimate_layers.strategy_courses_of_action": ("strategy", "CourseOfAction"),
    # Business
    "archimate_layers.business_actors": ("business", "BusinessActor"),
    "archimate_layers.business_roles": ("business", "BusinessRole"),
    "archimate_layers.business_collaborations": ("business", "BusinessCollaboration"),
    "archimate_layers.business_interfaces": ("business", "BusinessInterface"),
    "archimate_layers.business_functions": ("business", "BusinessFunction"),
    "archimate_layers.business_interactions": ("business", "BusinessInteraction"),
    "archimate_layers.business_events": ("business", "BusinessEvent"),
    "archimate_layers.business_services": ("business", "BusinessService"),
    "archimate_layers.business_objects": ("business", "BusinessObject"),
    "archimate_layers.business_contracts": ("business", "Contract"),
    "archimate_layers.business_representations": ("business", "Representation"),
    "archimate_layers.business_products": ("business", "Product"),
    # Application
    "archimate_layers.application_components": ("application", "ApplicationComponent"),
    "archimate_layers.application_collaborations": (
        "application",
        "ApplicationCollaboration",
    ),
    "archimate_layers.application_interfaces": ("application", "ApplicationInterface"),
    "archimate_layers.application_functions": ("application", "ApplicationFunction"),
    "archimate_layers.application_interactions": (
        "application",
        "ApplicationInteraction",
    ),
    "archimate_layers.application_processes": ("application", "ApplicationProcess"),
    "archimate_layers.application_events": ("application", "ApplicationEvent"),
    "archimate_layers.application_services": ("application", "ApplicationService"),
    "archimate_layers.application_data_objects": ("application", "DataObject"),
    # Technology — all 13 ArchiMate 3.2 types
    "archimate_layers.technology_nodes": ("technology", "Node"),
    "archimate_layers.technology_devices": ("technology", "Device"),
    "archimate_layers.technology_system_software": ("technology", "SystemSoftware"),
    "archimate_layers.technology_collaborations": (
        "technology",
        "TechnologyCollaboration",
    ),
    "archimate_layers.technology_interfaces": ("technology", "TechnologyInterface"),
    "archimate_layers.technology_paths": ("technology", "Path"),
    "archimate_layers.technology_networks": ("technology", "CommunicationNetwork"),
    "archimate_layers.technology_functions": ("technology", "TechnologyFunction"),
    "archimate_layers.technology_processes": ("technology", "TechnologyProcess"),
    "archimate_layers.technology_interactions": ("technology", "TechnologyInteraction"),
    "archimate_layers.technology_events": ("technology", "TechnologyEvent"),
    "archimate_layers.technology_services": ("technology", "TechnologyService"),
    "archimate_layers.technology_artifacts": ("technology", "Artifact"),
    # Physical — the whole layer had no read UI
    "archimate_layers.physical_equipment": ("physical", "Equipment"),
    "archimate_layers.physical_facilities": ("physical", "Facility"),
    "archimate_layers.physical_distribution_networks": (
        "physical",
        "DistributionNetwork",
    ),
    "archimate_layers.physical_materials": ("physical", "Material"),
    # Implementation & Migration
    "archimate_layers.implementation_work_packages": ("implementation", "WorkPackage"),
    "archimate_layers.implementation_deliverables": ("implementation", "Deliverable"),
    "archimate_layers.implementation_events": ("implementation", "ImplementationEvent"),
    "archimate_layers.implementation_plateaus": ("implementation", "Plateau"),
}

ARCHIMATE_32_PHYSICAL = ["Equipment", "Facility", "DistributionNetwork", "Material"]

ARCHIMATE_32_TECHNOLOGY = [
    "Node",
    "Device",
    "SystemSoftware",
    "TechnologyCollaboration",
    "TechnologyInterface",
    "Path",
    "CommunicationNetwork",
    "TechnologyFunction",
    "TechnologyProcess",
    "TechnologyInteraction",
    "TechnologyEvent",
    "TechnologyService",
    "Artifact",
]


@pytest.fixture
def client(app):
    """A test client with ``@login_required`` switched off.

    ``LOGIN_DISABLED`` is Flask-Login's documented test switch; without it every
    request below returns the login redirect and the redirect under test never
    runs.  Restored afterwards because ``app`` is session-scoped.
    """
    previous = app.config.get("LOGIN_DISABLED", False)
    app.config["LOGIN_DISABLED"] = True
    try:
        yield app.test_client()
    finally:
        app.config["LOGIN_DISABLED"] = previous


def _browser_path(app):
    from flask import url_for

    with app.test_request_context("/"):
        return url_for("archimate_crud.dashboard")


# ---------------------------------------------------------------------------
# 1. The nav must carry its filter to a screen that reads it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint,layer,element_type",
    [(k, v[0], v[1]) for k, v in NAV_TARGETS.items()],
    ids=list(NAV_TARGETS),
)
def test_layer_nav_redirects_to_the_element_browser_filtered(
    app, client, endpoint, layer, element_type
):
    """Each By-Layer link lands on the element browser, filtered to its type."""
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    with app.test_request_context("/"):
        from flask import url_for

        path = url_for(endpoint)

    resp = client.get(path)
    assert resp.status_code in (301, 302), (
        f"{endpoint} returned {resp.status_code}; the layer nav routes are redirects"
    )

    target = urlparse(resp.headers["Location"])
    args = parse_qs(target.query)

    assert target.path == _browser_path(app), (
        f"{endpoint} redirected to {target.path!r}, which does not list elements. "
        "The composer reads only solution_id and viewpoint, so layer/element_type "
        "are silently dropped and every link lands on the same unfiltered page."
    )
    assert args.get("layer") == [layer], (
        f"{endpoint} redirect carried layer={args.get('layer')!r}, expected {layer!r}"
    )
    assert args.get("element_type") == [element_type], (
        f"{endpoint} redirect carried element_type={args.get('element_type')!r}, "
        f"expected {element_type!r} — the browser filters on the ArchiMate type "
        "name, so a lower-case slug matches nothing"
    )
    assert element_type in MODEL_REGISTRY, (
        f"{element_type} is not in MODEL_REGISTRY, so the browser cannot list it"
    )


def test_every_nav_target_is_configured_in_its_layer():
    """A sidebar link with no LAYER_CONFIG entry is a promise the browser cannot keep."""
    from app.modules.architecture.routes.archimate_crud.routes import (
        LAYER_CONFIG,
        MODEL_REGISTRY,
    )

    missing = []
    for endpoint, (layer, element_type) in NAV_TARGETS.items():
        if layer not in LAYER_CONFIG:
            missing.append(f"{endpoint}: layer {layer!r} not in LAYER_CONFIG")
        elif element_type not in LAYER_CONFIG[layer]["elements"]:
            missing.append(f"{endpoint}: {element_type!r} not in LAYER_CONFIG[{layer!r}]")
        if element_type not in MODEL_REGISTRY:
            missing.append(f"{endpoint}: {element_type!r} not in MODEL_REGISTRY")

    assert not missing, "unreachable element types:\n  " + "\n  ".join(missing)


# ---------------------------------------------------------------------------
# 2. The browser must apply the filter it is handed
# ---------------------------------------------------------------------------


def _dashboard_context(app, client, query):
    """GET the element browser and capture what it hands the template."""
    captured = {}

    from app.modules.architecture.routes.archimate_crud import routes as mod

    def _fake_render(template_name, **context):
        captured["template"] = template_name
        captured.update(context)
        return ""

    with patch.object(mod, "render_template", _fake_render):
        resp = client.get("/architecture/" + query)
    assert resp.status_code == 200
    return captured


def test_browser_pre_selects_the_layer_and_type_it_is_given(app, client):
    context = _dashboard_context(app, client, "?layer=technology&element_type=Artifact")

    assert context["template"] == "archimate_crud/dashboard.html"
    assert context.get("initial_layer") == "technology"
    assert context.get("initial_element_type") == "Artifact", (
        "the browser ignored element_type, so every By-Layer link would still "
        "show the whole layer"
    )


def test_browser_drops_a_type_that_does_not_belong_to_the_layer(app, client):
    """An unknown filter must be dropped, not shown as an active filter matching nothing."""
    context = _dashboard_context(
        app, client, "?layer=technology&element_type=Stakeholder"
    )

    assert context.get("initial_layer") == "technology"
    assert context.get("initial_element_type") is None


def test_browser_drops_an_unknown_layer(app, client):
    context = _dashboard_context(app, client, "?layer=nonsense&element_type=Node")

    assert context.get("initial_layer") is None
    assert context.get("initial_element_type") is None


# ---------------------------------------------------------------------------
# 3. Physical layer and the truncated technology layer
# ---------------------------------------------------------------------------


def test_physical_layer_is_reachable_through_the_generic_browser():
    from app.modules.architecture.routes.archimate_crud.routes import (
        LAYER_CONFIG,
        MODEL_REGISTRY,
    )

    assert "physical" in LAYER_CONFIG, (
        "ArchiMate 3.2 has a Physical layer and app/models/physical_layer.py "
        "maps all four of its elements; without a LAYER_CONFIG key none of them "
        "can be listed, created or opened"
    )
    assert LAYER_CONFIG["physical"]["elements"] == ARCHIMATE_32_PHYSICAL

    for element_type in ARCHIMATE_32_PHYSICAL:
        assert element_type in MODEL_REGISTRY, f"{element_type} has no model registered"


def test_physical_models_are_the_ones_in_physical_layer_py():
    from app.models.physical_layer import (
        PhysicalDistributionNetwork,
        PhysicalEquipment,
        PhysicalFacility,
        PhysicalMaterial,
    )
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    assert MODEL_REGISTRY["Equipment"] is PhysicalEquipment
    assert MODEL_REGISTRY["Facility"] is PhysicalFacility
    assert MODEL_REGISTRY["DistributionNetwork"] is PhysicalDistributionNetwork
    assert MODEL_REGISTRY["Material"] is PhysicalMaterial


def test_technology_layer_covers_all_thirteen_archimate_32_types():
    from app.modules.architecture.routes.archimate_crud.routes import (
        LAYER_CONFIG,
        MODEL_REGISTRY,
    )

    configured = LAYER_CONFIG["technology"]["elements"]
    missing = [t for t in ARCHIMATE_32_TECHNOLOGY if t not in configured]
    assert not missing, f"technology layer is truncated; missing {missing}"

    unregistered = [t for t in ARCHIMATE_32_TECHNOLOGY if t not in MODEL_REGISTRY]
    assert not unregistered, f"no model registered for {unregistered}"


def test_physical_layer_api_answers_for_a_layer_it_now_knows(app, client):
    """The layer endpoints 404'd on ``physical`` because LAYER_CONFIG had no key."""
    resp = client.get("/architecture/api/layer/physical/elements?per_page=1")
    assert resp.status_code == 200, (
        f"expected 200, got {resp.status_code}: the physical layer is not configured"
    )
    payload = resp.get_json()
    assert payload["element_types"] == ARCHIMATE_32_PHYSICAL

    count = client.get("/architecture/api/layer/physical/count")
    assert count.status_code == 200
    assert count.get_json()["layer"] == "physical"


def _member_of(db_session, org, label):
    """A confirmed user belonging to ``org``, flushed so a login can resolve it."""
    import uuid

    from app.models.user import User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Layer",
        last_name="Browser",
        organization_id=org.id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_physical_equipment_rows_reach_the_browser_payload(
    app, db_session, client, make_org, tenant_ctx, login_as
):
    """A row in physical_equipment must show up in the layer's element list.

    Created inside a tenant context: PhysicalEquipment carries an after-insert
    listener that mirrors the row into `archimate_elements`, whose
    `organization_id` is NOT NULL. Outside a request there is no
    `g.current_org_id` for the column default to read, so the mirror insert
    fails and takes the equipment insert down with it.
    """
    from app.models.physical_layer import PhysicalEquipment

    org = make_org("physical-nav")
    with tenant_ctx(org.id):
        db_session.add(
            PhysicalEquipment(name="Rack 7 Edge Switch", equipment_type="Switch")
        )
        db_session.commit()

    # The payload is read for one named organisation, so the request is made by a member of it.
    login_as(client, _member_of(db_session, org, "physical-nav"))
    resp = client.get(
        "/architecture/api/layer/physical/elements?element_type=Equipment&per_page=100"
    )
    assert resp.status_code == 200
    names = [e["name"] for e in resp.get_json()["elements"]]
    assert "Rack 7 Edge Switch" in names


def test_another_organisations_equipment_is_absent_from_the_browser_payload(
    app, db_session, client, make_org, tenant_ctx, login_as
):
    """Each organisation's physical layer lists its own equipment and never the other's."""
    from app.models.physical_layer import PhysicalEquipment

    org_a = make_org("physical-a")
    org_b = make_org("physical-b")
    with tenant_ctx(org_a.id):
        db_session.add(PhysicalEquipment(name="Rack 7 Edge Switch", equipment_type="Switch"))
        db_session.commit()
    with tenant_ctx(org_b.id):
        db_session.add(PhysicalEquipment(name="Plant 3 Chiller", equipment_type="Chiller"))
        db_session.commit()
    user_a = _member_of(db_session, org_a, "physical-a")
    user_b = _member_of(db_session, org_b, "physical-b")

    url = "/architecture/api/layer/physical/elements?element_type=Equipment&per_page=100"
    login_as(client, user_a)
    names_a = [e["name"] for e in client.get(url).get_json()["elements"]]
    login_as(client, user_b)
    names_b = [e["name"] for e in client.get(url).get_json()["elements"]]

    assert "Rack 7 Edge Switch" in names_a and "Plant 3 Chiller" not in names_a
    assert "Plant 3 Chiller" in names_b and "Rack 7 Edge Switch" not in names_b
