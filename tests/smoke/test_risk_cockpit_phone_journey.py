"""A programme lead on a phone, in dark mode, works the programme cockpit's risks: reads what each risk is
linked to, unlinks it, adds a risk, sets a score, and sees the same figure on every screen."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_risk_confirmation_answer_cockpit_journey import _seed_programme
from .test_risk_link_and_edit_journey import _seed_application

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LONG_NAME = "Calibration Ledger With A Rather Long Name"


def _phone(browser, base, email, width):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900},
                                  color_scheme="dark")
    context.add_init_script("try{localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
                            "localStorage.setItem('theme','dark')}catch(e){}")
    page = context.new_page()
    sign_in(page, base, email)
    return context, page


def _programme_with_linked_risk(browser, base, width, noun):
    app_name = "%s %s" % (LONG_NAME, noun)
    title = "Cockpit phone %s" % noun
    org = create_fresh_org("enterprise_architect")
    app_id, _ = _seed_application(org["org_id"], app_name)
    programme_id = _seed_programme(org["org_id"], "Programme %s" % noun)
    context, page = _phone(browser, base, org["emails"]["enterprise_architect"], width)
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, created = api(page, "POST", "/api/risks", {"title": title, "likelihood": 3, "impact": 3})
    assert status == 201, created
    for kind, entity_id in (("programme", programme_id), ("application", app_id)):
        status, link = api(page, "POST", "/api/risks/%d/links" % created["id"], {"entity_type": kind, "entity_id": entity_id})
        assert status in (200, 201), link
    return context, page, created["id"], title, app_name, programme_id


def _stored_links(page, risk_id, kind):
    status, links = api(page, "GET", "/api/risks/%d/links" % risk_id)
    assert status == 200, links
    rows = links if isinstance(links, list) else links.get("links", [])
    return len([row for row in rows if row["entity_type"] == kind])


def _open_cockpit(page, base, programme_id, title):
    page.goto("%s/solutions/programmes/%d" % (base, programme_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=title)).to_be_visible(timeout=PAGE_TIMEOUT)


def _cells_stay_apart(page, row):
    """Every cell of the row is its own box: none overlaps another."""
    boxes = row.locator("td").evaluate_all(
        "cells => cells.map(c => { const r = c.getBoundingClientRect(); return [r.left, r.right]; })")
    for left, right in zip(boxes, boxes[1:]):
        assert left[1] <= right[0] + 1, ("cells overlap", boxes)


@pytest.mark.parametrize("width", [390, 360])
def test_a_phone_user_reads_and_unlinks_on_the_cockpit(live_server, browser, width):
    noun = uuid.uuid4().hex[:6]
    context, page, risk_id, title, app_name, programme_id = _programme_with_linked_risk(browser, live_server, width, noun)
    _open_cockpit(page, live_server, programme_id, title)
    row = page.locator("tr", has_text=title)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    _cells_stay_apart(page, row)
    # The linked names are read whole, and the page itself does not scroll sideways.
    expect(row.get_by_text(app_name, exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"

    unlink = row.get_by_role("button", name="Unlink %s" % app_name, exact=True)
    unlink.scroll_into_view_if_needed()
    unlink.click(timeout=PAGE_TIMEOUT)          # a real click: another cell on top of it would time out
    row.get_by_role("button", name="Yes, unlink").click(timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=title).get_by_text(app_name, exact=True)).to_have_count(0, timeout=PAGE_TIMEOUT)
    assert _stored_links(page, risk_id, "application") == 0
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=title)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=title).get_by_text(app_name, exact=True)).to_have_count(0)
    context.close()


def test_keyboard_alone_unlinks_and_the_panel_gives_focus_back(live_server, browser):
    noun = uuid.uuid4().hex[:6]
    context, page, risk_id, title, app_name, programme_id = _programme_with_linked_risk(browser, live_server, 390, noun)
    _open_cockpit(page, live_server, programme_id, title)
    row = page.locator("tr", has_text=title)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)

    link = row.get_by_role("button", name=re.compile("^Link"))
    link.focus()
    page.keyboard.press("Enter")
    panel = page.get_by_test_id("programme-risk-link-panel")
    expect(panel).to_be_visible()
    expect(panel.locator("input[role=combobox]")).to_be_focused()
    page.keyboard.press("Escape")               # clears the (empty) search, then closes the panel
    if panel.is_visible():
        page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(link).to_be_focused()                # focus is back on the row's Link button

    unlink = row.get_by_role("button", name="Unlink %s" % app_name, exact=True)
    unlink.focus()
    page.keyboard.press("Enter")
    yes = row.get_by_role("button", name="Yes, unlink")
    expect(yes).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("tr", has_text=title).get_by_text(app_name, exact=True)).to_have_count(0, timeout=PAGE_TIMEOUT)
    assert _stored_links(page, risk_id, "application") == 0
    context.close()


@pytest.mark.parametrize("width", [390, 360])
def test_add_risk_and_set_score_work_and_every_screen_shows_one_figure(live_server, browser, width):
    noun = uuid.uuid4().hex[:6]
    context, page, risk_id, title, app_name, programme_id = _programme_with_linked_risk(browser, live_server, width, noun)
    _open_cockpit(page, live_server, programme_id, title)

    # Add Risk opens a dialog; a refusal keeps what was typed and says why.
    added = "Added on a phone %s" % noun
    page.get_by_role("button", name="Add Risk").first.click()
    dialog = page.get_by_role("dialog", name="Add Programme Risk")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.get_by_label(re.compile("^Title")).fill(added)
    dialog.get_by_label("Owner").fill("o" * 128)
    dialog.get_by_label("Description").fill("Typed text that must survive a refusal")
    dialog.get_by_role("button", name="Add Risk").click()
    expect(page.locator("tr", has_text=added)).to_be_visible(timeout=PAGE_TIMEOUT)     # saved, at once
    status, rows = api(page, "GET", "/api/entities/programme/%d/risks" % programme_id)
    assert status == 200 and any(r["title"] == added for r in rows), rows
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=added)).to_be_visible(timeout=PAGE_TIMEOUT)     # and after a reload

    # A refused save keeps the typed values and shows a plain sentence.
    page.get_by_role("button", name="Add Risk").first.click()
    dialog = page.get_by_role("dialog", name="Add Programme Risk")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.get_by_label(re.compile("^Title")).fill("Refused %s" % noun)
    dialog.locator("#prg-risk-owner").evaluate("el => { el.removeAttribute('maxlength'); }")
    dialog.locator("#prg-risk-owner").fill("o" * 200)
    dialog.get_by_role("button", name="Add Risk").click()
    expect(dialog.get_by_role("alert")).to_contain_text("Owner must be 128 characters or fewer", timeout=PAGE_TIMEOUT)
    expect(dialog.get_by_label(re.compile("^Title"))).to_have_value("Refused %s" % noun)
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)

    # An unscored row says so in words, never "null×null".
    row = page.locator("tr", has_text=added)
    assert "null" not in row.inner_text()
    expect(row.get_by_text("No score recorded").first).to_be_visible()

    # Set score: the row, the risk page and the API read the same effective figure.
    row = page.locator("tr", has_text=title)
    row.get_by_role("button", name="Set score").click()
    dialog = page.get_by_role("dialog", name="Set Risk Score")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.get_by_label(re.compile("^Score kind")).select_option("residual")
    dialog.get_by_label(re.compile("^Likelihood")).select_option("4")
    dialog.get_by_label(re.compile("^Impact")).select_option("5")
    dialog.get_by_role("button", name="Record Score").click()
    expect(page.locator("tr", has_text=title).locator("[data-risk-effective]")).to_have_text("Residual 20", timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=title)).to_contain_text("4×5")
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("tr", has_text=title).locator("[data-risk-effective]")).to_have_text("Residual 20", timeout=PAGE_TIMEOUT)
    status, stored = api(page, "GET", "/api/entities/programme/%d/risks" % programme_id)
    assert [r["score_text"] for r in stored if r["id"] == risk_id] == ["Residual 20"], stored
    page.goto("%s/risks/%d" % (live_server, risk_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).to_contain_text("Residual 20")
    context.close()
