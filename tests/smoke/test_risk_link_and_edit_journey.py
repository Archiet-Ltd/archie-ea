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


def _ask_chat(page, base, question):
    """Ask the AI chat and return the text of the answer card a person reads."""
    page.goto(base + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)
    before = page.get_by_test_id("estate-answer").count()
    page.locator("#user-input").fill(question)
    page.locator("#user-input").press("Enter")
    expect(page.get_by_test_id("estate-answer")).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    return page.get_by_test_id("estate-answer").nth(before).inner_text()


def _linked_risks_on_application_page(page, base, fact_sheet_url, title):
    """The links to the risk inside the application page's Linked risks panel."""
    page.goto(fact_sheet_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page.locator("li").get_by_role("link", name=title, exact=True)


def test_risk_link_and_edit_journey(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    app_name = "Calibration Ledger %s" % noun
    title = "Ledger drift %s" % noun
    org = create_fresh_org("enterprise_architect")
    app_id, element_id = _seed_application(org["org_id"], app_name)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"])
    base = live_server
    fact_sheet = None

    # Add the risk from the register's own dialog.
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Add Risk").first.click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Title").fill(title)
    dialog.get_by_label("Likelihood (1-5)").select_option("2")
    dialog.get_by_label("Impact (1-5)").select_option("3")
    dialog.get_by_role("button", name="Add Risk").click()
    expect(page.locator("[data-risk-row]", has_text=title).first).to_be_visible(timeout=PAGE_TIMEOUT)
    risk_id = _risk_id(org["org_id"], title)

    # ---- Link it from the register, through the picker (a combobox with a result count). ----
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("li[data-risk-row='%d']" % risk_id).get_by_role("button", name="Link it").click()
    picker = page.get_by_test_id("risk-link-picker")
    combo = picker.locator("input[role=combobox]")
    expect(combo).to_be_visible(timeout=PAGE_TIMEOUT)
    combo.fill(app_name)
    expect(picker.get_by_role("listbox").get_by_role("option", name=app_name, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(picker).to_contain_text("1 result")
    combo.press("Enter")                       # the first result is active: keyboard alone links it
    mapped = page.locator("[data-risk-unlink]").first
    expect(page.get_by_text(app_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
    # Unmap needs a confirmation; "Keep" returns focus to Unmap, "Yes" removes the link.
    page.get_by_role("button", name="Unmap %s" % app_name, exact=True).click()
    expect(page.get_by_role("button", name="Yes, unmap")).to_be_focused()
    page.get_by_role("button", name="Keep").click()
    expect(page.get_by_role("button", name="Unmap %s" % app_name, exact=True)).to_be_focused()
    page.get_by_role("button", name="Unmap %s" % app_name, exact=True).click()
    page.get_by_role("button", name="Yes, unmap").click()
    expect(page.get_by_role("button", name="Unmap %s" % app_name, exact=True)).to_have_count(0, timeout=PAGE_TIMEOUT)
    assert mapped is not None

    # ---- Link it again from the risk's own page, through the same picker. ----
    page.goto("%s/risks/%d" % (base, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    opener = page.locator("#risk-link-open")
    opener.click()
    picker = page.get_by_test_id("risk-link-picker")
    combo = picker.locator("input[role=combobox]")
    assert combo.get_attribute("aria-controls"), "the same combobox the register uses"
    combo.fill(app_name)
    options = picker.get_by_role("listbox").get_by_role("option")
    expect(options).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(picker).to_contain_text("1 result")
    # Result rows read as enabled controls: full-strength text, not muted or disabled.
    option_opacity = options.first.evaluate("e => getComputedStyle(e).opacity")
    assert option_opacity == "1", option_opacity
    combo.press("ArrowDown")
    options.first.click()
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("#risk-link-open")).to_be_focused()          # focus came back to the control that opened it
    expect(page.locator("#risk-live")).to_contain_text("Linked to %s" % app_name)

    # ---- Edit the risk; an empty title is refused beside its field and focus stays there. ----
    edit = page.locator("#risk-edit-open")
    edit.click()
    expect(page.locator("#edit-risk-title")).to_be_focused()
    page.get_by_label("Title", exact=True).fill("")
    page.get_by_role("button", name="Save changes").click()
    expect(page.locator("#edit-risk-title")).to_have_attribute("aria-invalid", "true")
    described = page.locator("#edit-risk-title").get_attribute("aria-describedby")
    assert described and "Title is required" in page.locator("#" + described).inner_text()
    expect(page.locator("#edit-risk-title")).to_be_focused()
    page.get_by_label("Title", exact=True).fill(title)
    page.get_by_label("Likelihood (1-5)").select_option("5")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_test_id("risk-score")).to_contain_text("15", timeout=PAGE_TIMEOUT)
    expect(page.locator("#risk-edit-open")).to_be_focused()
    # Reload: the link and the edit persisted, and the form fields read 5 of 5.
    page.goto("%s/risks/%d" % (base, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible()
    expect(page.get_by_test_id("risk-score")).to_contain_text("15")
    expect(page.get_by_text("5 of 5", exact=True).first).to_be_visible()

    # ---- The application's own page lists the risk as a link, and the link lands on the risk. ----
    page.locator("[data-risk-link]", has_text=app_name).get_by_role("link", name=app_name).click()
    page.wait_for_load_state("domcontentloaded")
    fact_sheet = page.url
    link = _linked_risks_on_application_page(page, base, fact_sheet, title)
    expect(link).to_have_count(1, timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("h1").first).to_contain_text(title)

    # ---- The answers a person reads name the risk. ----
    breaks = _ask_chat(page, base, "What breaks if %s fails" % app_name)
    assert title in breaks, breaks
    hurts = _ask_chat(page, base, "What could hurt %s and what does it touch" % app_name)
    assert title in hurts, hurts

    # ---- Counts agree after the create ... ----
    total = _register_total(page, base)
    assert total == _rank_total(page, base, risk_id) == 1
    # ... and after a delete of a second risk.
    status, created = api(page, "POST", "/api/risks", {"title": "Second %s" % noun, "likelihood": 1, "impact": 1})
    assert status == 201, created
    assert _register_total(page, base) == _rank_total(page, base, risk_id) == 2
    status, _ = api(page, "DELETE", "/api/risks/%d" % created["id"])
    assert status == 200
    assert _register_total(page, base) == _rank_total(page, base, risk_id) == 1

    # ---- Unlink asks first; Keep returns focus to Unlink; Yes removes it everywhere. ----
    page.goto("%s/risks/%d" % (base, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    unlink = page.get_by_role("button", name="Unlink %s" % app_name, exact=True)
    unlink.click()
    expect(page.get_by_role("button", name="Yes, unlink")).to_be_focused()
    page.get_by_role("button", name="Keep").click()
    expect(unlink).to_be_focused()
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible()   # nothing was removed
    unlink.click()
    page.get_by_role("button", name="Yes, unlink").click()
    expect(page.locator("[data-risk-link]")).to_have_count(0, timeout=PAGE_TIMEOUT)
    expect(page.locator("#risk-link-open")).to_be_focused()
    expect(_linked_risks_on_application_page(page, base, fact_sheet, title)).to_have_count(0)
    assert title not in _ask_chat(page, base, "What breaks if %s fails" % app_name)
    assert title not in _ask_chat(page, base, "What could hurt %s and what does it touch" % app_name)
    context.close()


def test_a_closed_linked_risk_is_not_named_by_the_answer(live_server, browser):
    """The same rule in the register and the answer: a closed risk stays in the register's count
    (so totals agree) but is no longer something that could hurt the application."""
    noun = uuid.uuid4().hex[:6]
    app_name = "Closed Ledger %s" % noun
    title = "Closed drift %s" % noun
    org = create_fresh_org("enterprise_architect")
    app_id, _ = _seed_application(org["org_id"], app_name)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"])
    base = live_server
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, created = api(page, "POST", "/api/risks", {"title": title, "likelihood": 3, "impact": 3})
    assert status == 201, created
    status, link = api(page, "POST", "/api/risks/%d/links" % created["id"], {"entity_type": "application", "entity_id": app_id})
    assert status in (200, 201), link
    assert title in _ask_chat(page, base, "What breaks if %s fails" % app_name)
    status, _ = api(page, "PATCH", "/api/risks/%d" % created["id"], {"status": "closed"})
    assert status == 200
    assert title not in _ask_chat(page, base, "What breaks if %s fails" % app_name)
    assert _register_total(page, base) == _rank_total(page, base, created["id"]) == 1
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
    search = page.get_by_test_id("risk-link-picker").locator("input[role=combobox]")
    search.fill("App %s" % noun)
    expect(page.get_by_role("option", name=mine, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("option", name=theirs, exact=True)).to_have_count(0)
    # Keyboard: arrow keys move through the results and Enter links the active one.
    search.press("ArrowDown")
    search.press("Enter")
    expect(page.locator("[data-risk-link]", has_text=mine)).to_be_visible(timeout=PAGE_TIMEOUT)
    status, body = api(page, "POST", "/api/risks/%d/links" % created["id"],
                       {"entity_type": "application", "entity_id": foreign_id})
    assert status == 400, (status, body)
    context.close()
