"""One traceability page that opens on the broken chains.

    enterprise architect: open the old traceability address, land on the
    matrix, see the chains that stop short with a count equal to the rows,
    open a row's record, trace it, and trace an application and a capability
    from their own record pages.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    noun = "Chain %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        component = ApplicationComponent(name="%s Billing" % noun, organization_id=org_id,
                                         lifecycle_status="operational")
        db.session.add(component)
        db.session.commit()
        component_element = component.archimate_element_id

        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        process = element("%s Invoicing" % noun, "BusinessProcess", "business")
        capability = element("%s Collect payment" % noun, "Capability", "strategy")
        db.session.add(ArchiMateRelationship(type="serving", source_id=component_element, target_id=process.id,
                                             organization_id=org_id))
        cap_row = BusinessCapability(name=capability.name, organization_id=org_id,
                                     archimate_element_id=capability.id, level=1)
        db.session.add(cap_row)
        db.session.commit()
        return {
            "org": org_id,
            "noun": noun,
            "component_row": component.id,
            "component": component_element,
            "component_name": component.name,
            "capability": capability.id,
            "capability_row": cap_row.id,
            "capability_name": capability.name,
            "elements": [component_element, process.id, capability.id],
        }


def _cleanup(seed):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.business_capabilities import BusinessCapability

    ids = seed["elements"]
    app = create_app("testing")
    with app.app_context():
        steps = [
            lambda: ArchiMateRelationship.query.filter(
                ArchiMateRelationship.organization_id == seed["org"],
                db.or_(ArchiMateRelationship.source_id.in_(ids), ArchiMateRelationship.target_id.in_(ids)),
            ).delete(synchronize_session=False),
            lambda: BusinessCapability.query.filter_by(id=seed["capability_row"]).delete(synchronize_session=False),
            lambda: ApplicationComponent.query.filter_by(id=seed["component_row"]).delete(synchronize_session=False),
            lambda: ArchiMateElement.query.filter(
                ArchiMateElement.organization_id == seed["org"], ArchiMateElement.id.in_(ids),
            ).delete(synchronize_session=False),
        ]
        for step in steps:
            try:
                step()
                db.session.commit()
            except Exception:
                db.session.rollback()


@pytest.fixture(scope="module")
def chains(seeded, live_server):
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


def test_enterprise_architect_starts_from_the_broken_chains(page, live_server, seeded, chains):
    from app import create_app
    from app.models.user import User

    email = seeded["emails"]["enterprise_architect"]
    with create_app("testing").app_context():
        assert User.query.filter_by(email=email).one().enterprise_role == "enterprise_architect"
    _login(page, live_server, email)

    page.goto(live_server + "/intelligence/traceability", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.url.endswith("/architecture/traceability"), page.url
    expect(page.locator("h1")).to_contain_text("Traceability")

    rows = page.locator('[data-testid="broken-chains"] [data-testid="broken-chain-row"]')
    count = int(page.locator('[data-testid="broken-count"]').inner_text())
    assert count == rows.count() and count >= 2
    component_row = rows.filter(has_text=chains["component_name"])
    expect(component_row).to_contain_text("No capability above it")
    expect(component_row).to_contain_text("stops at %s Invoicing" % chains["noun"])
    capability_row = rows.filter(has_text=chains["capability_name"])
    expect(capability_row).to_contain_text("No technology below it")

    # The name opens the element's own record, with content.
    component_row.get_by_role("link", name=chains["component_name"]).click()
    page.wait_for_url(lambda url: "/traceability/%d" % chains["component"] in url or
                      "element_id=%d" % chains["component"] in url or str(chains["component"]) in url,
                      timeout=PAGE_TIMEOUT)
    expect(page.locator("main, body").first).to_contain_text(chains["component_name"])

    # Trace narrows the page to that element and explains where the chain stops.
    page.goto(live_server + "/architecture/traceability", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Trace sits in the row's menu.
    page.locator('[data-testid="broken-chains"] [data-testid="broken-chain-row"]', has_text=chains["component_name"]) \
        .get_by_role("button", name="Actions for").click()
    page.get_by_test_id("broken-trace-%d" % chains["component"]).click()
    page.wait_for_url(lambda url: "element=%d" % chains["component"] in url, timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="trace-up"]')).to_have_attribute("data-complete", "false")
    expect(page.locator('[data-testid="trace-up"]')).to_contain_text("Stops at %s Invoicing" % chains["noun"])
    expect(page.locator('[data-testid="broken-chains"]')).to_have_count(0)

    # Clearing the filter returns to the list, never a blank page.
    page.get_by_role("link", name="Show broken chains").click()
    page.wait_for_url(lambda url: "element=" not in url, timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="broken-chains"] [data-testid="broken-chain-row"]').first).to_be_visible()


def test_trace_from_the_application_and_capability_records(page, live_server, seeded, chains):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])

    page.goto("%s/applications/%d/fact-sheet" % (live_server, chains["component_row"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Trace").first.click()
    page.wait_for_url(lambda url: "element=%d" % chains["component"] in url, timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="trace-element"]')).to_have_attribute(
        "data-element-id", str(chains["component"]))
    expect(page.get_by_role("heading", name=chains["component_name"])).to_be_visible()

    page.goto("%s/enterprise/capabilities/%d/fact-sheet" % (live_server, chains["capability_row"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Trace").first.click()
    page.wait_for_url(lambda url: "element=%d" % chains["capability"] in url, timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="trace-down"]')).to_have_attribute("data-complete", "false")
    expect(page.get_by_role("heading", name=chains["capability_name"])).to_be_visible()
