"""Every capability on the investment pages shows its investment or says why it has none.

Goal: "I read the investment matrix, and for each of the capabilities I see the money behind it and where it
came from, or one sentence saying why there is none and the step that adds it. The total and the average say
what they cover, both pages agree, and on a phone nothing is cut and the evidence sits behind a control."

The demonstration organisation is seeded the way the product seeds it; a second journey uses a new
organisation whose one capability has no work, so the reason and its step are read too.
"""
import os
import pathlib
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_ADMIN = "demo@lantern-quay.example.com"
PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="investment-funding-"))
CAPABILITIES = 24


@pytest.fixture(scope="module")
def demo(live_server):
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with create_app("testing").app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_ADMIN


def _open(browser, live_server, email, path, width, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + path, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("capability-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _thousands(text):
    """The figure in a cell such as '£470.0k' or headline such as '£3.4M', in thousands."""
    match = re.search(r"[^\d\s]?([\d.]+)([kM])", text)
    assert match, text
    return float(match.group(1)) * (1000 if match.group(2) == "M" else 1)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    table = page.get_by_test_id("matrix-scroll")
    table.scroll_into_view_if_needed()
    table.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))))


@pytest.mark.parametrize("path", PAGES)
def test_every_capability_shows_its_investment_and_the_headlines_say_what_they_cover(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 1440)
    try:
        rows = page.get_by_test_id("capability-row")
        assert rows.count() == CAPABILITIES
        summed = 0.0
        for i in range(CAPABILITIES):
            row = rows.nth(i)
            name = row.get_by_test_id("cell-name").locator("a, span").first.inner_text().strip()
            cell = row.get_by_test_id("cell-investment").inner_text()
            assert "from " in cell and re.search(r"work packages?|Initiative", cell), (name, cell)
            summed += _thousands(cell)
            # the evidence beneath the row agrees with the figure
            budget = [line for line in row.get_by_test_id("capability-footer").inner_text().splitlines()
                      if "Budget" in line]
            assert budget and "not estimated" not in budget[0].lower(), (name, budget)
        total = page.get_by_test_id("total-investment").inner_text()
        # the headline total is the sum of the rows it covers, to the rounding of the figures shown
        assert abs(_thousands(total) - summed) <= 0.05 * 1000 + CAPABILITIES * 0.05, (total, summed)
        assert "from %d of %d capabilities" % (CAPABILITIES, CAPABILITIES) in total, total
        assert "every capability has one" in total, total
        average = page.get_by_test_id("average-strategic-score").inner_text()
        assert re.search(r"average of the importance scores of the \d+ rated capabilities, out of 24", average), average
        _shot(page, "matrix" if "strategic" in path else "priorities", 1440, "light")
    finally:
        context.close()


def test_both_pages_state_the_same_total_and_average(browser, live_server, demo):
    seen = []
    for path in PAGES:
        context, page = _open(browser, live_server, demo, path, 1440)
        try:
            seen.append((page.get_by_test_id("total-investment").inner_text(),
                         page.get_by_test_id("average-strategic-score").inner_text()))
        finally:
            context.close()
    assert seen[0] == seen[1]


def test_a_capability_with_no_work_says_why_and_the_step_lands_on_work_packages(browser, live_server):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    org = create_fresh_org("enterprise_architect")
    name = "Handle returns %s" % uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        db.session.add(UnifiedCapability(name=name, organization_id=org["org_id"], strategic_importance="high", level=1))
        db.session.commit()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("capability-row").filter(has_text=name)
        expect(row).to_have_count(1)
        cell = row.get_by_test_id("cell-investment")
        expect(cell).to_contain_text("Not estimated")
        expect(cell.get_by_test_id("investment-reason")).to_have_text(
            "No work package or funded initiative is linked to this capability yet.")
        total = page.get_by_test_id("total-investment").inner_text()
        assert "No capability has linked work" in total, total
        cell.get_by_test_id("investment-step").click()
        page.wait_for_url(re.compile(r"/implementation/work-packages"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=re.compile("Work Packages", re.I)).first).to_be_visible()
        assert len(page.locator("main").inner_text().strip()) > 100
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_phone_shows_no_cut_column_and_keeps_the_evidence_behind_a_control(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 390, scheme="dark")
    try:
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        scroller = page.get_by_test_id("matrix-scroll")
        frame = scroller.bounding_box()
        # the columns beside the name are whole, not cut at the right edge
        header = scroller.locator("thead th", has_text="Domain").bounding_box()
        assert header["x"] + header["width"] <= frame["x"] + frame["width"] + 1, (header, frame)
        row = page.get_by_test_id("capability-row").first
        domain = row.get_by_test_id("cell-domain").bounding_box()
        assert domain["x"] + domain["width"] <= frame["x"] + frame["width"] + 1, (domain, frame)
        # the evidence is closed; a control opens it, and the same control closes it
        footer = row.get_by_test_id("capability-footer")
        toggle = row.get_by_test_id("evidence-toggle")
        expect(footer).to_be_hidden()
        expect(toggle).to_have_text(re.compile("Show evidence"))
        _shot(page, "closed-" + ("matrix" if "strategic" in path else "priorities"), 390, "dark")
        assert row.get_by_test_id("cell-name").bounding_box()["height"] < 330
        toggle.click()
        expect(footer).to_be_visible()
        _shot(page, "open-" + ("matrix" if "strategic" in path else "priorities"), 390, "dark")
        for label in ("Evidence", "Source", "Confidence", "Date"):
            expect(footer).to_contain_text(label)
        expect(toggle).to_have_text(re.compile("Hide evidence"))
        toggle.click()
        expect(footer).to_be_hidden()
    finally:
        context.close()


def test_desktop_keeps_the_evidence_open_without_a_control(browser, live_server, demo):
    context, page = _open(browser, live_server, demo, PAGES[0], 1440)
    try:
        row = page.get_by_test_id("capability-row").first
        expect(row.get_by_test_id("capability-footer")).to_be_visible()
        expect(row.get_by_test_id("evidence-toggle")).to_be_hidden()
    finally:
        context.close()
