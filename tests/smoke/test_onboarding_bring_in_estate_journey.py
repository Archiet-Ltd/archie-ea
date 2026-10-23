"""Bring in the estate, in a real browser: the spreadsheet sits beside GitHub as
an equal choice, and the import review shows which rows arrived and which were
refused with the reason and the cell.

A scale-up administrator lands on the setup screen of a brand-new organisation.
Two ways to bring in their estate are offered with equal weight -- add systems
from a spreadsheet, or connect GitHub -- and each one lands on a page with real
content, not a dead link. The administrator uploads a twenty-row spreadsheet
with three bad rows and reads the review: seventeen rows will arrive, three are
refused, and each refused row names the cell it is about and why. They import,
and the count the review promised is the count the applications list then holds
and the dashboard shows -- the three surfaces agree.

A second organisation, with its own administrator, sees none of the first
organisation's systems: the estate one organisation brings in is its own.
"""
import io
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("ONB08_JOURNEY_SHOTS")


def _twenty_row_file(token):
    """Twenty rows: seventeen good systems, and three that must be refused -- one
    with no name at all (its other cell is filled, so it is a real row not a
    blank line), and two that repeat a name already in the file. Both the review
    and the import agree these three never become applications, so the count the
    review promises is the count the list then holds. The second column is a
    label neither path maps, so it is carried without changing any count."""
    lines = ["Name,Reference"]
    for i in range(1, 18):
        lines.append(f"System {i} {token},ref-{i}")
    lines.append(",ref-19")                    # row 19: no name, but a real row
    lines.append(f"System 1 {token},ref-20")   # row 20: repeats row 2 (System 1)
    lines.append(f"System 4 {token},ref-21")   # row 21: repeats row 5 (System 4)
    return "\n".join(lines)


def _shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def _open_import_modal(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="guided-setup"]')).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Add from a spreadsheet").first.click()
    page.wait_for_url("**/applications/?import=1", timeout=PAGE_TIMEOUT)
    expect(page.locator("#application-import-modal")).to_be_visible(timeout=PAGE_TIMEOUT)


def _run_review(page, token):
    """Upload the file, map the two columns on the real mapping controls, run
    the analysis and wait for the review to render."""
    page.set_input_files("#excel-file-input", files=[{
        "name": "systems.csv",
        "mimeType": "text/csv",
        "buffer": _twenty_row_file(token).encode("utf-8"),
    }])
    expect(page.locator("#excel-preview")).to_be_visible(timeout=PAGE_TIMEOUT)
    # The preview auto-maps "Name" from the header; that is the one column this
    # review needs, the way a person would leave it.
    name_select = page.locator('select.mapping-select[data-original-header="Name"]')
    expect(name_select).to_have_value("name", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Run standard analysis").click()
    expect(page.locator('[data-testid="import-review"]')).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def two_orgs():
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    return {"one": one, "two": two}


def test_bring_in_estate_end_to_end(browser, live_server, two_orgs):
    one = two_orgs["one"]
    token = uuid.uuid4().hex[:8]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    try:
        page = context.new_page()
        sign_in(page, live_server, one["emails"]["solution_architect"])

        # 1. The setup screen offers both routes with equal weight.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        setup = page.locator('[data-testid="guided-setup"]')
        expect(setup).to_be_visible(timeout=PAGE_TIMEOUT)
        spreadsheet = setup.get_by_role("link", name="Add from a spreadsheet")
        github = setup.get_by_role("link", name="Connect GitHub")
        expect(spreadsheet).to_be_visible()
        expect(github).to_be_visible()
        _shoot(page, "1-setup")

        # Each choice lands on a page with real content, not a dead link.
        github.click()
        page.wait_for_url("**/admin/connectors/github", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Connect GitHub")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_label("Read-only token")).to_be_visible()
        _shoot(page, "2-github")

        # 2. The spreadsheet route: upload, read the review.
        _open_import_modal(page, live_server)
        _run_review(page, token)
        _shoot(page, "3-review")

        review = page.locator('[data-testid="import-review"]')
        expect(review.locator('[data-testid="review-accepted"]')).to_have_text("17")
        expect(review.locator('[data-testid="review-refused"]')).to_have_text("3")
        expect(review.locator('[data-testid="review-total"]')).to_have_text("20")

        # Every refused row names a cell and gives a reason; none is empty.
        refused_rows = review.locator("[data-review-refused-row]")
        expect(refused_rows).to_have_count(3)
        all_reasons = []
        for i in range(refused_rows.count()):
            cells = refused_rows.nth(i).locator("td")
            cell = cells.nth(0).inner_text().strip()
            reason = cells.nth(1).inner_text().strip()
            assert cell, "a refused row shows no cell: %s" % refused_rows.nth(i).inner_text()
            assert reason, "a refused row shows no reason: %s" % refused_rows.nth(i).inner_text()
            all_reasons.append(reason)
        joined = " ".join(all_reasons)
        assert "No application name" in joined, joined
        # Two rows repeat a name already in the file, each pointing at the
        # earlier row it duplicates.
        assert joined.count("already on row") >= 2, joined

        # On a phone the review still shows its cells and reasons without a
        # sideways scroll.
        page.set_viewport_size({"width": 390, "height": 844})
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, "the review scrolls sideways by %spx at 390 wide" % overflow
        _shoot(page, "3-review-phone")
        page.set_viewport_size({"width": 1440, "height": 900})

        # 3. Import the accepted rows and confirm the three surfaces agree.
        page.on("dialog", lambda d: d.accept())
        page.locator("#import-applications-btn").click()
        # The import posts and the page reloads once it has landed.
        page.wait_for_timeout(3000)
        page.wait_for_load_state("domcontentloaded")

        # The applications list holds the seventeen that arrived, and the list's
        # own figure agrees with the count the review promised.
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid='applications-table']", timeout=PAGE_TIMEOUT)
        list_count = _portfolio_count(live_server, page)
        assert list_count == 17, "the list holds %s applications, the review promised 17" % list_count
        for name in ("System 1 %s" % token, "System 17 %s" % token):
            expect(page.get_by_text(name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shoot(page, "4-list")

        # The dashboard application figure agrees with the list.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        kpi = page.locator('[data-testid="health-kpi-applications"]').first.inner_text().strip()
        assert kpi == "17", "the dashboard shows %r applications, the list holds 17" % kpi
        _shoot(page, "5-dashboard")
    finally:
        context.close()


def _portfolio_count(base, page):
    """How many applications the list holds, read from the list's own API."""
    status, body = _api(page, "GET", "/applications/api/list?page=1&per_page=1")
    assert status == 200, (status, body)
    assert isinstance(body, dict) and isinstance(body.get("total"), int), body
    return body["total"]


def _api(page, method, path):
    return page.evaluate(
        """async ([method, path]) => {
            const meta = document.querySelector('meta[name=\"csrf-token\"]');
            const headers = {'Accept': 'application/json'};
            if (meta) headers['X-CSRFToken'] = meta.content;
            const r = await fetch(path, {method, headers, credentials: 'same-origin'});
            const text = await r.text();
            let body = text;
            try { body = JSON.parse(text); } catch (e) {}
            return [r.status, body];
        }""",
        [method, path],
    )


def test_a_second_organisation_sees_none_of_the_first_estate(browser, live_server, two_orgs):
    """Isolation: the systems one organisation brought in are not the other's."""
    two = two_orgs["two"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    try:
        page = context.new_page()
        sign_in(page, live_server, two["emails"]["solution_architect"])
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # None of the first organisation's systems appear here.
        expect(page.get_by_text(re.compile(r"System \d+ "))).to_have_count(0)
        # The second organisation's own setup screen still offers both equal choices.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        setup = page.locator('[data-testid="guided-setup"]')
        expect(setup.get_by_role("link", name="Add from a spreadsheet")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(setup.get_by_role("link", name="Connect GitHub")).to_be_visible()
    finally:
        context.close()
