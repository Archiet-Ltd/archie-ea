"""Capability governance dashboard: the figures are the page's own rows, counted.

An enterprise architect opens the governance dashboard to see how well the capability model is owned and
described. Capabilities, those needing attention and the governance score must equal what the table below
them lists (a capability needs attention when it has no owner, criticality, importance or description), the
cards are the library's stat row, and a model with no capabilities says why and offers the next step.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .overview_data import purge_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

URL = "/capability-governance/governance-dashboard"
# (name, owner, criticality, importance, description): the last two are missing something.
CAPS = [("Order intake", "Owen Owner", "mission_critical", "high", "Takes orders"),
        ("Invoicing", "Owen Owner", "important", "medium", "Sends invoices"),
        ("Returns", None, "supporting", "low", "Handles returns"),
        ("Forecasting", "Fay Planner", None, "high", None)]


def _seed(org_id, suffix, rows):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        for name, owner, criticality, importance, description in rows:
            db.session.add(UnifiedCapability(organization_id=org_id, name="%s %s" % (name, suffix), level=1,
                                             business_owner=owner, business_criticality=criticality,
                                             strategic_importance=importance, description=description))
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def populated(live_server):
    info = create_fresh_org("enterprise_architect")
    info["suffix"] = uuid.uuid4().hex[:6]
    _seed(info["org_id"], info["suffix"], CAPS)
    yield info
    purge_org(info["org_id"])


@pytest.fixture(scope="module")
def empty(live_server):
    info = create_fresh_org("enterprise_architect")
    yield info
    purge_org(info["org_id"])


def _open(browser, live_server, info, width=1400):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, info["emails"]["enterprise_architect"])
    response = page.goto(live_server + URL, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    return page


def _figure(page, key):
    return page.get_by_test_id("gov-%s-value" % key).inner_text().strip()


def test_figures_equal_the_rows_they_summarise(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    rows = page.get_by_test_id("gov-row")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    total = rows.count()
    attention = page.get_by_test_id("gov-status").filter(has_text="Needs attention").count()
    governed = page.get_by_test_id("gov-status").filter(has_text="Governed").count()
    assert total == int(_figure(page, "total")) == len(CAPS)
    assert attention == int(_figure(page, "attention")) == 2
    assert governed == total - attention
    assert _figure(page, "score") == "%d%%" % round(100 * governed / total) == "50%"
    # Searching narrows the table, not the model the figures describe.
    page.get_by_test_id("gov-search").fill("Returns")
    expect(page.get_by_test_id("gov-row")).to_have_count(1, timeout=PAGE_TIMEOUT)
    assert int(_figure(page, "total")) == len(CAPS)


def test_the_look_is_the_librarys(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    row = page.get_by_test_id("gov-stat-row")
    cards = row.locator("[data-slot=card]")
    assert 1 <= cards.count() <= 4
    assert row.locator("svg, i[data-lucide]").count() == 0, "stat cards carry no icons"
    assert not re.search(r"border-(2|l-|t-)|bg-(destructive|warning|success)\b", row.inner_html()), "no coloured borders"
    for i in range(cards.count()):
        assert cards.nth(i).locator("p").count() >= 1, "each card says what its figure counts"
    assert page.get_by_test_id("gov-score-band").inner_text().strip() in {"Healthy", "Partly governed", "Weakly governed"}
    assert not re.search(r"\b(bg|border|text)-(orange|amber|emerald|yellow|green|red)-\d", page.locator("main").inner_html())


def test_a_row_opens_its_editor_and_the_change_persists(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    name = "Returns %s" % populated["suffix"]
    page.get_by_test_id("gov-search").fill(name)
    page.get_by_role("button", name="Edit governance for %s" % name).first.click()
    dialog = page.get_by_test_id("gov-edit")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#gov-edit-owner").fill("Rae Owner")
    dialog.get_by_role("button", name="Save Changes").click()
    expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)
    page.reload(wait_until="networkidle")
    page.get_by_test_id("gov-search").fill(name)
    expect(page.get_by_test_id("gov-row")).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("gov-row")).to_contain_text("Rae Owner")


def test_a_model_with_no_capabilities_says_why_and_what_to_do(browser, live_server, empty):
    page = _open(browser, live_server, empty)
    empty_state = page.get_by_test_id("gov-empty")
    expect(empty_state).to_be_visible(timeout=PAGE_TIMEOUT)
    assert "No capabilities are in the model yet" in empty_state.inner_text()
    link = empty_state.get_by_role("link", name="Open the capability map")
    href = link.get_attribute("href")
    page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.locator("main").inner_text().strip()
    page.go_back()
    assert _figure(page, "total") == "0"
    assert _figure(page, "score") == "—"


def test_nothing_overflows_on_a_phone(browser, live_server, populated):
    page = _open(browser, live_server, populated, width=390)
    expect(page.get_by_test_id("gov-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
