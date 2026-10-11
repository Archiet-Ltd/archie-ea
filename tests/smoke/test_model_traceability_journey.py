"""Element properties and the traceability check, driven in a real browser.

    enterprise architect: define a required typed property on application
    components, reload, and see the components still missing it.

    solution architect: pick a component whose chain up to a capability is
    broken, add the relationship the check suggests (already held in the
    capability mapping table), reload, and see the chain closed.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    noun = "Tracer %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        component = ApplicationComponent(name="%s Hub" % noun, organization_id=org_id,
                                         lifecycle_status="operational")
        db.session.add(component)
        db.session.commit()
        component_element = component.archimate_element_id

        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        process = element("%s Intake" % noun, "BusinessProcess", "business")
        node = element("%s Cluster" % noun, "Node", "technology")
        capability = element("%s Capability" % noun, "Capability", "strategy")
        db.session.add_all([
            ArchiMateRelationship(type="serving", source_id=component_element, target_id=process.id,
                                  organization_id=org_id),
            ArchiMateRelationship(type="serving", source_id=node.id, target_id=component_element,
                                  organization_id=org_id),
        ])
        db.session.commit()
        unified = UnifiedCapability.query.filter_by(archimate_element_id=capability.id,
                                                    organization_id=org_id).first()
        if unified is None:
            unified = UnifiedCapability(name=capability.name, organization_id=org_id,
                                        archimate_element_id=capability.id, level=1)
            db.session.add(unified)
            db.session.commit()
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=unified.id, application_component_id=component.id))
        db.session.commit()
        return {
            "noun": noun,
            "org": org_id,
            "component_row": component.id,
            "unified_capability": unified.id,
            "elements": [component_element, process.id, node.id, capability.id],
            "component": component_element,
            "component_name": "%s Hub" % noun,
            "capability_name": capability.name,
        }


def _cleanup(seed):
    """Remove everything this module put in the shared seeded organisation.

    The property definition the first journey creates is required, so leaving
    it would refuse later property writes on application components in that
    organisation. Rows are deleted child first, each step in its own
    transaction so a table that refuses a delete does not stop the rest.
    """
    from app import create_app, db
    from app.models.acm_property_template import AcmPropertyTemplate
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    org_id = seed["org"]
    ids = seed["elements"]
    app = create_app("testing")
    with app.app_context():
        steps = [
            lambda: AcmPropertyTemplate.query.filter(
                AcmPropertyTemplate.organization_id == org_id,
                AcmPropertyTemplate.property_key.like("recovery_hours_%"),
            ).delete(synchronize_session=False),
            lambda: ArchiMateRelationship.query.filter(
                ArchiMateRelationship.organization_id == org_id,
                db.or_(ArchiMateRelationship.source_id.in_(ids), ArchiMateRelationship.target_id.in_(ids)),
            ).delete(synchronize_session=False),
            lambda: UnifiedApplicationCapabilityMapping.query.filter(
                UnifiedApplicationCapabilityMapping.application_component_id == seed["component_row"],
            ).delete(synchronize_session=False),
            lambda: UnifiedCapability.query.filter(
                UnifiedCapability.organization_id == org_id,
                UnifiedCapability.archimate_element_id.in_(ids),
            ).delete(synchronize_session=False),
            lambda: ApplicationComponent.query.filter(
                ApplicationComponent.id == seed["component_row"],
            ).delete(synchronize_session=False),
            lambda: ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(ids),
            ).delete(synchronize_session=False),
        ]
        for step in steps:
            try:
                step()
                db.session.commit()
            except Exception:
                db.session.rollback()


@pytest.fixture(scope="module")
def trace_graph(seeded, live_server):
    seed = _seed(seeded["ids"]["org"])
    yield seed
    _cleanup(seed)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _ready(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def test_enterprise_architect_defines_a_required_property_and_sees_what_is_missing(
    page, live_server, seeded, trace_graph
):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/metamodel/properties", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    name = "Recovery hours %s" % uuid.uuid4().hex[:6]
    page.select_option("#mm-type", "ApplicationComponent")
    page.fill("#mm-name", name)
    page.select_option("#mm-value-type", "number")
    page.check("#mm-mandatory")
    page.get_by_role("button", name="Add property").click()
    page.wait_for_url(lambda url: "/metamodel/properties/" in url, timeout=PAGE_TIMEOUT)

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("h1")).to_have_text(name)
    missing = page.locator('[data-testid="metamodel-missing-list"]')
    expect(missing).to_contain_text(trace_graph["component_name"])

    page.goto(live_server + "/metamodel/properties", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator('[data-testid="metamodel-definitions"] tr', has_text=name)
    expect(row).to_contain_text("ApplicationComponent")
    expect(row).to_contain_text("Yes")


def test_solution_architect_closes_a_broken_chain_with_the_suggested_relationship(
    page, live_server, seeded, trace_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/traceability", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "traceabilitySurface")

    type_and_wait(page, "trace", trace_graph["noun"])
    page.locator("#trace-picker-listbox [role=option]", has_text=trace_graph["component_name"]).click()
    page.wait_for_url(lambda url: "element=%d" % trace_graph["component"] in url, timeout=PAGE_TIMEOUT)

    up = page.locator('[data-testid="trace-up"]')
    expect(up).to_have_attribute("data-complete", "false")
    expect(up).to_contain_text("Stops at %s Intake" % trace_graph["noun"])
    expect(page.locator('[data-testid="trace-down"]')).to_have_attribute("data-complete", "true")

    candidate = page.locator('[data-testid="trace-candidates"] li', has_text=trace_graph["capability_name"])
    expect(candidate).to_contain_text("capability mapping")
    _ready(page, "traceabilitySurface")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        candidate.get_by_role("button", name="Add this relationship").click()

    page.reload(wait_until="domcontentloaded")
    up = page.locator('[data-testid="trace-up"]')
    expect(up).to_have_attribute("data-complete", "true")
    expect(up).to_contain_text(trace_graph["capability_name"])
    expect(page.locator('[data-testid="trace-candidates"]')).to_have_count(0)
