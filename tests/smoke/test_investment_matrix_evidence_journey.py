"""The Administrator reads where to invest, and each capability's evidence agrees with its figures.

Goal: "for every capability I see its score, priority and investment, and the evidence beneath says where that
investment figure came from." Runs against the demonstration organisation's own seed, as its Administrator,
on both pages that draw the matrix, at desktop and phone width.
"""
import os
import pathlib
import re
import tempfile

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_ADMIN = "demo@lantern-quay.example.com"
PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="matrix-evidence-"))


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


def _open(browser, live_server, demo, path, width, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, live_server, demo)
    page.goto(live_server + path, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("capability-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.get_by_test_id("capability-row").first.scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))))


@pytest.mark.parametrize("path", PAGES)
def test_every_capability_shows_its_evidence_block_on_both_pages(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 1440)
    try:
        rows = page.get_by_test_id("capability-row")
        footers = page.get_by_test_id("capability-footer")
        assert rows.count() > 0
        assert footers.count() == rows.count()
        for i in range(rows.count()):
            text = footers.nth(i).inner_text()
            assert "undefined" not in text.lower()
            for label in ("Evidence", "Source", "Confidence", "Date"):
                assert label in text, (i, label)
        assert "undefined" not in page.get_by_test_id("matrix-scroll").inner_text().lower()
        _shot(page, "priorities" if "priorities" in path else "matrix", 1440, "light")
    finally:
        context.close()


def test_evidence_budget_line_agrees_with_the_investment_cell(browser, live_server, demo):
    context, page = _open(browser, live_server, demo, PAGES[0], 1440)
    try:
        # The headline carries no loose currency sign of its own.
        expect(page.get_by_test_id("currency-mark")).to_have_count(0)
        rows = page.get_by_test_id("capability-row")
        checked = 0
        for i in range(rows.count()):
            row = rows.nth(i)
            investment = row.get_by_test_id("cell-investment").inner_text()
            budget = [line for line in row.get_by_test_id("capability-footer").inner_text().splitlines()
                      if "Budget" in line]
            assert budget, "row %d shows no budget evidence" % i
            match = re.search(r"from (\d+ work packages?)", investment)
            if match:
                checked += 1
                assert match.group(1) in budget[0], (investment, budget)
                assert "is an estimate" not in budget[0]
            elif "Not estimated" in investment:
                assert "not estimated" in budget[0].lower(), (investment, budget)
        assert checked > 0, "the demonstration organisation has costed work to check"
    finally:
        context.close()


def test_both_pages_give_the_same_figures(browser, live_server, demo):
    seen = []
    for path in PAGES:
        context, page = _open(browser, live_server, demo, path, 1440)
        try:
            headline = page.get_by_test_id("total-investment").inner_text()
            rows = page.get_by_test_id("capability-row")
            figures = {}
            for i in range(rows.count()):
                row = rows.nth(i)
                name = row.get_by_test_id("cell-name").locator("a, span").first.inner_text().strip()
                figures[name] = (row.get_by_test_id("cell-total").inner_text().split("\n")[0].strip(),
                                 row.get_by_test_id("cell-investment").inner_text().strip())
            seen.append((re.search(r"[^\s\d]?[\d.]+[kM]", headline).group(0), figures))
        finally:
            context.close()
    assert seen[0] == seen[1]


@pytest.mark.parametrize("path", PAGES)
def test_no_column_is_cut_off_at_desktop_width(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 1440)
    try:
        scroller = page.get_by_test_id("matrix-scroll")
        assert scroller.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), "the table needs sideways scrolling"
        frame = scroller.bounding_box()
        for header in scroller.locator("thead th").all():
            box = header.bounding_box()
            assert box["x"] + box["width"] <= frame["x"] + frame["width"] + 1
        row = page.get_by_test_id("capability-row").first
        box = row.get_by_test_id("cell-actions").bounding_box()
        assert box["x"] + box["width"] <= frame["x"] + frame["width"] + 1
        recommendation = row.get_by_test_id("capability-recommendation")
        assert recommendation.bounding_box()["width"] > 150
        assert recommendation.inner_text().strip()
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_score_priority_and_investment_are_readable_at_phone_width(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 390, scheme="dark")
    try:
        row = page.get_by_test_id("capability-row").first
        summary = row.get_by_test_id("phone-summary")
        expect(summary).to_be_visible()
        viewport = page.viewport_size["width"]
        for key, column in (("score", "cell-total"), ("investment", "cell-investment")):
            shown = row.get_by_test_id("phone-" + key)
            box = shown.bounding_box()
            assert 0 <= box["x"] and box["x"] + box["width"] <= viewport, key
            assert shown.inner_text().strip()
        assert re.search(r"\d+/100|Not scored", row.get_by_test_id("phone-score").inner_text())
        assert row.get_by_test_id("phone-priority").inner_text().strip() in (
            "CRITICAL", "HIGH", "MEDIUM", "LOW", "NOT SCORED")
        # The phone summary says what the wide columns say.
        assert row.get_by_test_id("phone-score").inner_text().strip() == \
            row.get_by_test_id("cell-total").inner_text().split("\n")[0].strip()
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        _shot(page, "priorities" if "priorities" in path else "matrix", 390, "dark")
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_every_column_can_be_read_clear_of_the_pinned_name_at_phone_width(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 390, scheme="dark")
    try:
        scroller = page.get_by_test_id("matrix-scroll")
        row = page.get_by_test_id("capability-row").first
        cells = row.locator("td").all()
        assert len(cells) == 11
        pinned = [i for i, c in enumerate(cells)
                  if c.evaluate("e => getComputedStyle(e).position") == "sticky"
                  and (c.evaluate("e => getComputedStyle(e).left") != "auto"
                       or c.evaluate("e => getComputedStyle(e).right") != "auto")]
        assert pinned == [1], "only the capability name is pinned, found columns %s" % pinned
        name = cells[1]
        for index, cell in enumerate(cells):
            if index in (0, 1):
                continue
            scroller.evaluate("e => { e.scrollLeft = 0 }")
            frame = scroller.bounding_box()
            pinned_right = frame["x"] + 1 + name.bounding_box()["width"]
            scroller.evaluate("(e, d) => { e.scrollLeft = d }", max(0, cell.bounding_box()["x"] - pinned_right))
            page.wait_for_timeout(100)
            frame = scroller.bounding_box()
            shown, anchor = cell.bounding_box(), name.bounding_box()
            assert shown["x"] >= anchor["x"] + anchor["width"] - 1, ("a pinned cell covers column", index, shown, anchor)
            assert shown["x"] + shown["width"] <= frame["x"] + frame["width"] + 1, ("column cut off", index, shown, frame)
            assert cell.inner_text().strip(), index
    finally:
        context.close()


@pytest.mark.parametrize("width", (1440, 390))
@pytest.mark.parametrize("path", PAGES)
def test_every_column_header_uses_the_same_header_style(browser, live_server, demo, path, width):
    context, page = _open(browser, live_server, demo, path, width)
    try:
        headers = page.get_by_test_id("matrix-scroll").locator("thead th").all()
        assert len(headers) == 11
        styles = [h.evaluate("e => [getComputedStyle(e).fontSize, getComputedStyle(e).textTransform]")
                  for h in headers]
        named = [(h.text_content().strip(), s) for h, s in zip(headers, styles) if h.text_content().strip()]
        assert any(name == "Initiative" for name, _ in named)
        assert len({tuple(s) for _, s in named}) == 1, "header styles differ: %s" % named
        assert named[0][1][1] == "uppercase", named
        _shot(page, ("priorities" if "priorities" in path else "matrix") + "-headers", width, "light")
    finally:
        context.close()
