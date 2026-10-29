"""An architect exports an element's diagram and sees what it actually connects to.

The element page's Export diagram button used to ask the diagram service for one
element id and the application viewpoint, whatever the element's own layer -- so
the downloaded picture showed a single box with no sign of what the element is
assigned to, serves, or is served by. This journey proves the download now
carries the element's direct relationships and their other ends, drawn in
ArchiMate notation, and that an element with nothing linked still exports (one
box, with the page saying so beside the button) and never picks up another
organisation's element.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def diagram_estate(seeded, live_server):
    """A business role assigned to another role, an application component
    serving another, an element with nothing linked, and a relationship
    recorded under the first organisation whose other end belongs to a
    second organisation -- the shape a cross-tenant data error would take,
    so the export is proved never to draw or name it."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        # Names stay at or under the diagram service's 22-character label cutoff
        # (app/services/archimate_diagram_service.py) so an assertion on the full
        # name never collides with its own truncation; the tag keeps each row
        # distinct from other runs sharing this database.
        role = ArchiMateElement(
            name="Calibrator %s" % tag, type="BusinessRole",
            layer="business", organization_id=org_id,
        )
        approver = ArchiMateElement(
            name="Approver %s" % tag, type="BusinessRole",
            layer="business", organization_id=org_id,
        )
        db.session.add_all([role, approver])
        db.session.commit()
        db.session.add(ArchiMateRelationship(
            type="Assignment", source_id=role.id, target_id=approver.id,
            organization_id=org_id,
        ))

        component = ArchiMateElement(
            name="Billing Svc %s" % tag, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        served = ArchiMateElement(
            name="Payments GW %s" % tag, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        db.session.add_all([component, served])
        db.session.commit()
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=component.id, target_id=served.id,
            organization_id=org_id,
        ))

        lonely = ArchiMateElement(
            name="Unlinked Proc %s" % tag, type="BusinessProcess",
            layer="business", organization_id=org_id,
        )
        db.session.add(lonely)
        db.session.commit()

        other_org = Organization(name="Other Co %s" % tag, slug="other-co-%s" % tag)
        db.session.add(other_org)
        db.session.flush()
        leaked = ArchiMateElement(
            name="Leaked Node %s" % tag, type="BusinessRole",
            layer="business", organization_id=other_org.id,
        )
        db.session.add(leaked)
        db.session.commit()
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=role.id, target_id=leaked.id,
            organization_id=org_id,
        ))
        db.session.commit()

        return {
            "role": role.id, "role_name": role.name, "approver_name": approver.name,
            "component": component.id, "component_name": component.name,
            "served_name": served.name,
            "lonely": lonely.id, "lonely_name": lonely.name,
            "leaked_name": leaked.name,
        }


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _open_element(page, live_server, layer, element_id):
    page.goto(
        live_server + "/architecture/%s/ArchiMateElement/%d" % (layer, element_id),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )


def _export(page):
    with page.expect_download() as download_info:
        page.click('button:has-text("Export diagram")')
    return download_info.value.path().read_text()


def test_element_diagram_export_draws_its_relationships(page, live_server, seeded, diagram_estate):
    """A solution architect exports a business role with one assignment and an
    application component with a serving relationship; each downloaded SVG
    shows both ends and the connecting line, and never a second
    organisation's element."""
    _login(page, live_server, seeded["emails"]["solution_architect"])

    _open_element(page, live_server, "business", diagram_estate["role"])
    svg = _export(page)
    assert diagram_estate["role_name"] in svg, "the exported diagram does not show the business role itself"
    assert diagram_estate["approver_name"] in svg, "the exported diagram does not show the role it is assigned to"
    assert 'marker-end="url(#arrow)"' in svg, "the exported diagram does not draw the assignment relationship"
    assert diagram_estate["leaked_name"] not in svg, (
        "the exported diagram drew a second organisation's element"
    )

    _open_element(page, live_server, "application", diagram_estate["component"])
    svg = _export(page)
    assert diagram_estate["component_name"] in svg, "the exported diagram does not show the application component itself"
    assert diagram_estate["served_name"] in svg, "the exported diagram does not show what it serves"
    assert 'marker-end="url(#arrow)"' in svg, "the exported diagram does not draw the serving relationship"


def test_element_with_no_relationships_still_exports_and_says_so(page, live_server, seeded, diagram_estate):
    """An element nothing is linked to still exports a one-box diagram, and the
    page says so beside the Export diagram button, with a link to add one."""
    _login(page, live_server, seeded["emails"]["solution_architect"])

    _open_element(page, live_server, "business", diagram_estate["lonely"])
    note = page.get_by_test_id("export-diagram-no-relationships")
    expect(note).to_contain_text("This element has no relationships yet.")
    expect(note.locator("a")).to_have_text("Add one")

    svg = _export(page)
    assert diagram_estate["lonely_name"] in svg, "the exported diagram does not show the element itself"
    assert 'marker-end="url(#arrow)"' not in svg, "an element with no relationships drew one anyway"
