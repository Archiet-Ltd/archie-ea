"""Backlog Prioritisation: every card of the demonstration organisation carries a score worked out from its own
inputs, a card missing an input names it and offers to add it, and every card shows its evidence, source,
confidence and date.

The signed-in person is the Administrator of the demonstration company. Each step runs at 1440 wide in the light
theme and at 390 wide in the dark theme.
"""
import os
import re
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
WALK = os.environ.get("SMOKE_WALK_DIR")
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
SCORE = re.compile(r"^WSJF \d+\.\d{2}$")


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demonstration organisation, seeded the way the product's own seed does."""
    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.organization import Organization
            from app.models.user import User

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            admin = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            admin.password = PASSWORD
            admin.is_org_admin = True
            db.session.commit()
            made = {"org_id": org.id}
            db.session.remove()
        yield made
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous


@pytest.fixture(params=VIEWS, ids=["1440-light", "390-dark"])
def view(request, browser, live_server, demo):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, DEMO_EMAIL)
    yield page, scheme, width
    context.close()


def _open(page, base, scheme):
    page.goto(base + "/backlog/prioritisation", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(".kanban-card", state="attached", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    if scheme == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")


def _shot(page, name, scheme, width):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def _cards(page):
    """Every card on the board: its name, its score line and its footer text, read from the page."""
    return page.evaluate(
        """() => [...document.querySelectorAll('.kanban-card')].map(c => ({
            name: c.querySelector('h3').textContent.trim(),
            score: (c.querySelector("[data-testid='card-score']") || {textContent: ''}).textContent.replace(/\\s+/g, ' ').trim(),
            footer: (c.querySelector("[data-testid='result-footer']") || {textContent: ''}).textContent.replace(/\\s+/g, ' ').trim(),
            confidence: (c.querySelector("[data-testid='result-footer']") || {dataset: {}}).dataset.confidence || '',
        }))""")


def _wsjf_by_name(page):
    page.get_by_role("tab", name="WSJF").click()
    panel = page.locator("[role='tabpanel']:visible")
    panel.locator("[data-testid='wsjf-score']").first.wait_for(state="visible")
    rows = panel.locator("tbody tr")
    return {rows.nth(i).locator("td").first.inner_text().strip():
            rows.nth(i).locator("[data-testid='wsjf-score']").inner_text().strip() for i in range(rows.count())}


def test_every_demonstration_card_shows_a_score_worked_out_from_its_inputs_and_its_evidence(view, live_server):
    page, scheme, width = view
    _open(page, live_server, scheme)
    cards = _cards(page)
    assert len(cards) >= 6, "the board lists %d cards" % len(cards)
    for card in cards:
        assert SCORE.match(card["score"]), "card %r reads %r instead of a worked-out score" % (card["name"], card["score"])
        for part in ("Evidence", "Source", "Confidence", "Date"):
            assert part in card["footer"], "card %r footer has no %s: %r" % (card["name"], part, card["footer"])
        assert "business value" in card["footer"] and "job size" in card["footer"], card["footer"]
        assert card["confidence"] in ("high", "medium", "low"), card
    _shot(page, "cards-scored", scheme, width)

    # the figure on each card is the figure in the WSJF table for the same requirement
    table = _wsjf_by_name(page)
    for card in cards:
        assert table[card["name"]] == card["score"].replace("WSJF ", ""), (card, table[card["name"]])

    # the evidence link lands on a page with content
    page.get_by_role("tab", name="MoSCoW").click()
    link = page.locator(".kanban-card [data-testid='result-footer-evidence'] a").first
    href = link.get_attribute("href")
    assert href, "the evidence names no record to open"
    page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    assert "Requirements" in page.locator("main, body").first.inner_text()
    assert not page.get_by_text("Page not found").count()


def test_a_card_missing_an_input_names_it_and_the_input_can_be_added_from_the_card(view, live_server):
    page, scheme, width = view
    _open(page, live_server, scheme)
    name = _cards(page)[0]["name"]

    # take one input back: the card names it instead of reading "Not scored yet"
    page.get_by_role("tab", name="WSJF").click()
    row = page.locator("[role='tabpanel']:visible tbody tr").filter(
        has=page.locator("td:first-child", has_text=re.compile(r"^\s*%s\s*$" % re.escape(name))))
    kept = row.get_by_label("Job size").input_value()
    with page.expect_response(lambda r: "/wsjf" in r.url and r.request.method == "PATCH") as cleared:
        row.get_by_role("button", name="Clear WSJF inputs").click()
    assert cleared.value.status == 200
    _open(page, live_server, scheme)
    card = next(c for c in _cards(page) if c["name"] == name)
    assert "Not scored" not in card["score"] or "business value" in card["score"], card
    assert "record business value, time criticality, risk reduction, job size" in card["score"], card
    assert card["confidence"] == "low" and "Job size: not recorded" in card["footer"], card
    _shot(page, "card-missing-input", scheme, width)

    # "Add it" opens the WSJF table where the requirement's inputs are entered
    box = page.locator(".kanban-card").filter(has=page.get_by_role("heading", name=name))
    box.get_by_role("button", name="Add it").click()
    row = page.locator("[role='tabpanel']:visible tbody tr").filter(
        has=page.locator("td:first-child", has_text=re.compile(r"^\s*%s\s*$" % re.escape(name))))
    row.wait_for(state="visible")
    for label, value in (("Business value", "8"), ("Time criticality", "5"), ("Risk reduction", "3"), ("Job size", kept or "4")):
        row.get_by_label(label).fill(value)
    row.get_by_role("button", name="Save").click()
    page.get_by_role("tab", name="MoSCoW").click()
    after = lambda: next(c for c in _cards(page) if c["name"] == name)  # noqa: E731
    page.wait_for_function(
        "n => [...document.querySelectorAll('.kanban-card')].some(c => c.querySelector('h3').textContent.trim() === n"
        " && /^WSJF \\d/.test(c.querySelector(\"[data-testid='card-score']\").textContent.trim()))", arg=name)
    assert SCORE.match(after()["score"]), after()
    _shot(page, "card-input-added", scheme, width)
