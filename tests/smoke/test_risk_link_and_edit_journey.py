"""An architect records a risk, links it to the application it threatens from
the risk's own page, edits its likelihood, and then finds it on that
application's page and in the impact answer for that application. The register
total and the risk's ranking agree after a create and after a delete. A second
organisation's application is never offered and is refused if its id is sent."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_application(org_id, name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(name=name, organization_id=org_id)
        db.session.add(row)
        db.session.commit()
        out = (row.id, row.archimate_element_id)
        db.session.remove()
    return out


def _risk_id(org_id, title):
    from app import create_app, db
    from app.models.risk import Risk

    app = create_app("testing")
    with app.app_context():
        found = db.session.execute(
            db.select(Risk.id).where(Risk.organization_id == org_id, Risk.title == title)
        ).scalar_one()
        db.session.remove()
    return found


def _open_page(browser, live_server, email, width=1440):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def _register_total(page, base):
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    text = page.locator("div", has_text=re.compile(r"^\s*Total\s*$")).last.locator("xpath=..").inner_text()
    return int(re.search(r"(\d+)", text.replace("Total", "")).group(1))


def _rank_total(page, base, risk_id):
    page.goto("%s/risks/%d" % (base, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reason = page.locator("#risk-rank-reason").inner_text()
    return int(re.search(r"Ranked \d+ of (\d+)", reason).group(1))


def test_risk_link_and_edit_journey(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    app_name = "Calibration Ledger %s" % noun
    title = "Ledger drift %s" % noun
    org = create_fresh_org("enterprise_architect")
    app_id, element_id = _seed_application(org["org_id"], app_name)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"])
    base = live_server

    # Add the risk from the register's own dialog.
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Add Risk").first.click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Title").fill(title)
    dialog.get_by_label("Likelihood (1-5)").select_option("2")
    dialog.get_by_label("Impact (1-5)").select_option("3")
    dialog.get_by_role("button", name="Add Risk").click()
    expect(page.locator("[data-risk-row]", has_text=title)).to_be_visible(timeout=PAGE_TIMEOUT)
    risk_id = _risk_id(org["org_id"], title)

    # Link it from the risk's own page.
    page.goto("%s/risks/%d" % (base, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Link an application").click()
    page.get_by_label("Search applications to link").fill("Calibration Ledger %s" % noun)
    page.get_by_role("button", name=app_name, exact=True).click()
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible(timeout=PAGE_TIMEOUT)

    # Edit its likelihood.
    page.get_by_role("button", name="Edit risk").click()
    page.get_by_label("Likelihood (1-5)").select_option("5")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_test_id("risk-score")).to_contain_text("15", timeout=PAGE_TIMEOUT)

    # Reload: the link and the edit persisted.
    page.goto("%s/risks/%d" % (base, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible()
    expect(page.get_by_test_id("risk-score")).to_contain_text("15")

    # The application's own page lists the risk, and the link lands there.
    page.locator("[data-risk-link]", has_text=app_name).get_by_role("link", name=app_name).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("body")).to_contain_text(title, timeout=PAGE_TIMEOUT)

    # The impact answer for the application names the risk.
    status, body = api(page, "GET", "/api/v1/intelligence/risk/%d" % element_id)
    assert status == 200, body
    data = body.get("data", body)
    assert title in [r["title"] for r in data["risks"]], data

    # Counts agree after the create ...
    total = _register_total(page, base)
    assert total == _rank_total(page, base, risk_id) == 1
    # ... and after a delete of a second risk.
    status, created = api(page, "POST", "/api/risks", {"title": "Second %s" % noun, "likelihood": 1, "impact": 1})
    assert status == 201, created
    assert _register_total(page, base) == _rank_total(page, base, risk_id) == 2
    status, _ = api(page, "DELETE", "/api/risks/%d" % created["id"])
    assert status == 200
    assert _register_total(page, base) == _rank_total(page, base, risk_id) == 1
    context.close()


def test_risk_link_picker_is_fenced_to_the_signed_in_organisation(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    mine, theirs = "Mine App %s" % noun, "Theirs App %s" % noun
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    _seed_application(org["org_id"], mine)
    foreign_id, _ = _seed_application(other["org_id"], theirs)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"], width=390)
    base = live_server
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, created = api(page, "POST", "/api/risks", {"title": "Fence %s" % noun, "likelihood": 2, "impact": 2})
    assert status == 201, created
    page.goto("%s/risks/%d" % (base, created["id"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Link an application").click()
    search = page.get_by_label("Search applications to link")
    search.fill("App %s" % noun)
    expect(page.get_by_role("button", name=mine, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("button", name=theirs, exact=True)).to_have_count(0)
    # Keyboard: the first result is reachable and activated without a pointer.
    search.press("Tab")
    page.keyboard.press("Enter")
    expect(page.locator("[data-risk-link]", has_text=mine)).to_be_visible(timeout=PAGE_TIMEOUT)
    status, body = api(page, "POST", "/api/risks/%d/links" % created["id"],
                       {"entity_type": "application", "entity_id": foreign_id})
    assert status == 400, (status, body)
    context.close()
