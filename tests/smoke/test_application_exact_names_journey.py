"""Journey, in a browser: a shared owner name does not block the next save, the chat changes only an
application named exactly, and a coverage nobody entered reads "Not recorded".

An architect picks one of two people called Sam Same by email in the chat and saves, then opens the
Overview, changes only the description and saves: the page shows the new description and the same
owner. She asks the chat to change "Gateway" and is shown the closest names with no Save; she names the
application in full and is shown the change. She asks which capabilities the application supports and
the answer and the application's Capabilities table both read "Not recorded" for the capability nobody
gave a coverage and "0%" for the one recorded as 0. The other organisation's application of the same
name is never read.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from . import fresh_org
from .chat_ask import open_chat, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"

# Opt-in walk screenshots: off unless a directory is named.
WALK = os.environ.get("CHAT_WALK_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id, name):
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(name=name, organization_id=org_id, description="Old description",
                                   deployment_status="production")
        db.session.add(row)
        db.session.flush()
        tag = uuid.uuid4().hex[:4]
        blank = BusinessCapability(name="Blank coverage %s" % tag, organization_id=org_id)
        zero = BusinessCapability(name="Zero coverage %s" % tag, organization_id=org_id)
        db.session.add_all([blank, zero])
        db.session.flush()
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=row.id, business_capability_id=blank.id,
            organization_id=org_id, support_level="primary"))
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=row.id, business_capability_id=zero.id,
            organization_id=org_id, support_level="primary", coverage_percentage=0))
        twins = []
        for label in ("one", "two"):
            person = User(email="sam.%s.%s@example.com" % (label, uuid.uuid4().hex[:6]), first_name="Sam",
                          last_name="Same", organization_id=org_id, confirmed=True, enterprise_role=ARCHITECT)
            person.role = Role.query.filter_by(name="User").one()
            db.session.add(person)
            twins.append(person)
        db.session.commit()
        out = {"app_id": row.id, "blank": blank.name, "zero": zero.name, "twins": [p.email for p in twins]}
        db.session.remove()
    return out


def _send(page, text):
    box = page.locator("#user-input")
    box.fill(text)
    with page.expect_response(lambda r: r.url.endswith("/ai-chat/estate-answer"), timeout=PAGE_TIMEOUT) as seen:
        box.press("Enter")
    return seen.value.json()


def _newest_card(page, before):
    cards = page.get_by_test_id("estate-answer")
    expect(cards).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    return cards.nth(before)


def _phone_dark(page, live_server):
    page.set_viewport_size({"width": 390, "height": 844})
    open_chat(page, live_server)
    page.evaluate("document.documentElement.classList.add('dark')")


@pytest.fixture
def orgs():
    tag = uuid.uuid4().hex[:5]
    name = "Billing%s Gateway" % tag
    out = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org.update(_seed(org["org_id"], name))
        org["name"] = name
        out.append(org)
    return out


def test_the_second_overview_save_the_exact_name_and_the_unrecorded_coverage(page, live_server, orgs):
    mine, theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, live_server, mine["emails"][ARCHITECT])
    open_chat(page, live_server)

    # Partly naming the application gives the closest names and nothing to save.
    before = page.get_by_test_id("estate-answer").count()
    partial = _send(page, "set the description of Gateway to Takes the payments")
    assert partial.get("change_state") != "preview", partial
    card = _newest_card(page, before)
    expect(card).to_contain_text(mine["name"])
    assert card.get_by_test_id("application-change-save").count() == 0
    _shot(page, "chat-closest-names-1440")

    # Picking one of the two people called Sam Same by email, then saving.
    before = page.get_by_test_id("estate-answer").count()
    picked = _send(page, "set %s's business owner to %s" % (mine["name"], mine["twins"][0]))
    assert picked["change_state"] == "preview"
    preview = _newest_card(page, before)
    preview.get_by_test_id("application-change-save").click()
    expect(preview).to_contain_text("Saved: the business owner", timeout=PAGE_TIMEOUT)

    # The Overview, only the description changed: it saves and the owner is still Sam Same.
    url = "%s/applications/%d" % (live_server, mine["app_id"])
    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.locator("#edit-businessOwner").input_value() == "Sam Same"
    page.locator("#edit-description").click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Takes card payments for the shop.")
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name=re.compile("Save")).first.click()
    assert "edit=1" not in page.url, page.url
    body = page.locator("main").inner_text()
    assert "Takes card payments for the shop." in body and "Sam Same" in body, body
    assert "more than one person" not in body
    _shot(page, "overview-second-save-1440")

    # The capabilities: the answer and the table say the same thing.
    open_chat(page, live_server)
    before = page.get_by_test_id("estate-answer").count()
    _send(page, "Which capabilities does %s support?" % mine["name"])
    answer = _newest_card(page, before)
    text = answer.inner_text()
    assert re.search(re.escape(mine["blank"]) + r"[^%]*?Not recorded", text, re.S), text
    assert re.search(re.escape(mine["zero"]) + r".*?0%", text, re.S), text
    for capability in (theirs["blank"], theirs["zero"]):
        assert capability not in text, "the other organisation's capability leaked"
    _shot(page, "chat-coverage-not-recorded-1440")
    page.goto(url + "?tab=capabilities", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    rows = page.locator("tr")
    blank_row = rows.filter(has_text=mine["blank"]).first
    zero_row = rows.filter(has_text=mine["zero"]).first
    expect(blank_row).to_contain_text("Not recorded")
    expect(zero_row).to_contain_text("0%")
    assert "Not recorded" not in zero_row.inner_text()
    _shot(page, "page-coverage-not-recorded-1440")

    # Phone width, dark theme.
    _phone_dark(page, live_server)
    before = page.get_by_test_id("estate-answer").count()
    _send(page, "Which capabilities does %s support?" % mine["name"])
    narrow = _newest_card(page, before)
    expect(narrow).to_contain_text("Not recorded")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    _shot(page, "chat-coverage-not-recorded-dark-390")
    page.goto(url + "?tab=capabilities", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.evaluate("document.documentElement.classList.add('dark')")
    expect(page.locator("tr").filter(has_text=mine["blank"]).first).to_contain_text("Not recorded")
    _shot(page, "page-coverage-not-recorded-dark-390")
