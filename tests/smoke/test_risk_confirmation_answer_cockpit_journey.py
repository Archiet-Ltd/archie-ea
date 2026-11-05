"""An architect on a phone confirms or keeps an Unlink; Escape cancels only the innermost step in the
register's dialog; the permanent answer page names the same risks as the chat card; and the programme
cockpit links and unlinks a risk through the same picker the risk page uses."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_risk_link_and_edit_journey import _open_page, _seed_application

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_programme(org_id, name):
    from app import create_app, db
    from app.models.strategic import StrategicInitiative

    app = create_app("testing")
    with app.app_context():
        row = StrategicInitiative(name=name, organization_id=org_id)
        db.session.add(row)
        db.session.commit()
        out = row.id
        db.session.remove()
    return out


def _linked_risk(page, base, noun, app_id, title=None):
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, created = api(page, "POST", "/api/risks", {"title": title or "Ledger drift %s" % noun, "likelihood": 3, "impact": 3})
    assert status == 201, created
    status, link = api(page, "POST", "/api/risks/%d/links" % created["id"], {"entity_type": "application", "entity_id": app_id})
    assert status in (200, 201), link
    return created["id"]


def _stored_application_links(page, risk_id):
    status, links = api(page, "GET", "/api/risks/%d/links" % risk_id)
    assert status == 200, links
    rows = links if isinstance(links, list) else links.get("links", [])
    return len([row for row in rows if row["entity_type"] == "application"])


@pytest.mark.parametrize("width", [390, 360])
def test_a_phone_user_can_confirm_or_keep_an_unlink(live_server, browser, width):
    noun = uuid.uuid4().hex[:6]
    app_name = "Calibration Ledger With A Long Name %s" % noun
    org = create_fresh_org("enterprise_architect")
    app_id, _ = _seed_application(org["org_id"], app_name)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"], width=width)
    risk_id = _linked_risk(page, live_server, noun, app_id)
    page.goto("%s/risks/%d" % (live_server, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.get_by_role("button", name="Unlink %s" % app_name, exact=True).click()
    group = page.get_by_role("group", name=re.compile("Confirm: unlink"))
    yes = page.get_by_role("button", name="Yes, unlink")
    keep = page.get_by_role("button", name="Keep")
    expect(yes).to_be_focused()
    card = page.locator("[data-risk-link]").first.locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]").bounding_box()
    question = group.locator("span").first.bounding_box()
    for name, box in (("question", question), ("yes", yes.bounding_box()), ("keep", keep.bounding_box())):
        assert box is not None, name
        assert box["x"] >= card["x"] - 1 and box["x"] + box["width"] <= card["x"] + card["width"] + 1, (name, box, card)
        assert box["x"] + box["width"] <= width, (name, box, width)
    # The question is whole (not clipped) and sits above the buttons.
    assert page.get_by_text("from this risk?").is_visible()
    assert question["y"] + question["height"] <= yes.bounding_box()["y"] + 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"

    keep.click()
    expect(page.get_by_role("button", name="Unlink %s" % app_name, exact=True)).to_be_focused()
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible()
    # Keep changed nothing: after a reload the link is still stored.
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-risk-link]", has_text=app_name)).to_be_visible(timeout=PAGE_TIMEOUT)
    assert _stored_application_links(page, risk_id) == 1
    page.get_by_role("button", name="Unlink %s" % app_name, exact=True).click()
    page.get_by_role("button", name="Yes, unlink").click()
    expect(page.locator("[data-risk-link]")).to_have_count(0, timeout=PAGE_TIMEOUT)
    # Yes removed it for good: the API and a reload agree.
    assert _stored_application_links(page, risk_id) == 0
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-risk-link]")).to_have_count(0)
    context.close()


def test_escape_cancels_only_the_innermost_step_in_the_registers_dialog(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    app_name = "Escape Ledger %s" % noun
    org = create_fresh_org("enterprise_architect")
    _seed_application_id, _ = _seed_application(org["org_id"], app_name)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"])
    page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, created = api(page, "POST", "/api/risks", {"title": "Escape drift %s" % noun, "likelihood": 3, "impact": 3})
    assert status == 201, created
    page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    dialog = page.get_by_role("dialog", name="Risk detail")
    page.locator("li[data-risk-row='%d']" % created["id"]).get_by_role("button", name="Link it").click()
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    first = dialog.locator("input[role=combobox]")
    first.fill(app_name)
    expect(dialog.get_by_role("listbox").get_by_role("option")).to_have_count(1, timeout=PAGE_TIMEOUT)
    first.press("Enter")

    # Escape on the Unmap confirmation cancels the confirmation; the dialog stays, focus is on Unmap.
    unmap = dialog.get_by_role("button", name="Unmap %s" % app_name, exact=True)
    unmap.click()
    expect(dialog.get_by_role("button", name="Yes, unmap")).to_be_focused()
    page.keyboard.press("Escape")
    # Wait past the dialog's close transition: a dialog that is going to close has closed by now.
    page.wait_for_timeout(1500)
    expect(dialog).to_be_visible()
    expect(unmap).to_be_focused()
    expect(dialog.get_by_role("button", name="Yes, unmap")).to_have_count(0)

    # Escape in the picker's search clears the search and keeps focus there; the dialog stays.
    combo = dialog.locator("input[role=combobox]")
    if not combo.is_visible():
        dialog.get_by_role("button", name="Map to…").click()
    combo.fill(app_name)
    expect(dialog.get_by_role("listbox").get_by_role("option")).to_have_count(1, timeout=PAGE_TIMEOUT)
    page.keyboard.press("Escape")
    expect(dialog).to_be_visible()
    expect(combo).to_be_focused()
    expect(combo).to_have_value("")

    # A further Escape closes the dialog.
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)
    context.close()


def test_the_permanent_answer_names_the_same_risks_as_the_chat_card(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    app_name = "Answer Ledger %s" % noun
    title = "Answer drift %s" % noun
    org = create_fresh_org("enterprise_architect")
    app_id, _ = _seed_application(org["org_id"], app_name)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"])
    risk_id = _linked_risk(page, live_server, noun, app_id, title)

    page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.locator("#user-input").fill("What breaks if %s fails" % app_name)
    page.locator("#user-input").press("Enter")
    card = page.get_by_test_id("estate-answer").first
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    card_links = card.locator("[data-answer-risk]")
    expect(card_links).to_have_count(1, timeout=PAGE_TIMEOUT)
    card_risks = [(a.inner_text(), a.get_attribute("href")) for a in card_links.all()]
    assert card_risks == [(title, "/risks/%d" % risk_id)], card_risks

    card.get_by_role("link", name=re.compile("Open this answer")).click()
    page.wait_for_load_state("domcontentloaded")
    permanent = page.locator("[data-answer-risk]")
    expect(permanent).to_have_count(1, timeout=PAGE_TIMEOUT)
    assert [(a.inner_text(), a.get_attribute("href")) for a in permanent.all()] == card_risks
    # The stored answer cites the risk it names.
    citations = int(page.locator("[data-citation-count]").inner_text())
    assert citations >= 1
    # The risk link lands on a page with content.
    answer_url = page.url
    permanent.first.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("h1").first).to_contain_text(title)
    page.goto(answer_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    context.close()


def test_the_programme_cockpit_links_and_unlinks_a_risk_through_the_shared_picker(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    app_name = "Cockpit Ledger %s" % noun
    title = "Cockpit drift %s" % noun
    org = create_fresh_org("enterprise_architect")
    _seed_application(org["org_id"], app_name)
    programme_id = _seed_programme(org["org_id"], "Programme %s" % noun)
    context, page = _open_page(browser, live_server, org["emails"]["enterprise_architect"])
    base = live_server
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, created = api(page, "POST", "/api/risks", {"title": title, "likelihood": 3, "impact": 3})
    assert status == 201, created
    status, link = api(page, "POST", "/api/risks/%d/links" % created["id"], {"entity_type": "programme", "entity_id": programme_id})
    assert status in (200, 201), link

    page.goto("%s/solutions/programmes/%d" % (base, programme_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("tr", has_text=title)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    row.get_by_role("button", name=re.compile("Link")).first.click()
    panel = page.get_by_test_id("programme-risk-link-panel")
    expect(panel).to_be_visible()
    combo = panel.locator("input[role=combobox]")
    expect(combo).to_be_focused()
    combo.fill(app_name)
    expect(panel.get_by_role("option", name=app_name, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(panel).to_contain_text("1 result")
    combo.press("Enter")
    expect(row.get_by_text(app_name, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
    # It persisted, on the risk itself.
    status, links = api(page, "GET", "/api/risks/%d/links" % created["id"])
    assert status == 200 and any(l["entity_type"] == "application" for l in (links if isinstance(links, list) else links.get("links", []))), links

    # Unlink asks first; Keep keeps it; Yes removes it.
    row = page.locator("tr", has_text=title)
    row.get_by_role("button", name="Unlink %s" % app_name, exact=True).click()
    expect(row.get_by_text("Unlink %s from this risk?" % app_name)).to_be_visible()   # the whole question
    row.get_by_role("button", name="Keep").click()
    expect(row.get_by_text(app_name, exact=True)).to_be_visible()
    row.get_by_role("button", name="Unlink %s" % app_name, exact=True).click()
    row.get_by_role("button", name="Yes, unlink").click()
    expect(page.locator("tr", has_text=title).get_by_text(app_name, exact=True)).to_have_count(0, timeout=PAGE_TIMEOUT)
    status, links = api(page, "GET", "/api/risks/%d/links" % created["id"])
    assert not any(l["entity_type"] == "application" for l in (links if isinstance(links, list) else links.get("links", []))), links
    context.close()
