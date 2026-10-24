"""A product manager reads the design tracker to see how far each page is from its block.

The tracker is a static page generated from the design map. The journey: open it,
read the totals, open the startup path and see every page beside its reference,
filter the full list to one block and follow a spec link to a spec with content.
"""
import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROOT = Path(__file__).resolve().parents[2]
TRACKER = ROOT / "docs" / "design" / "tracker.html"
DESIGN_MAP = json.loads((ROOT / "docs" / "design-map.json").read_text(encoding="utf-8"))


def _measured():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_design_map_journey", ROOT / "scripts" / "check_design_map.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool.measure()


def _open(browser, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto(TRACKER.as_uri(), wait_until="load")
    return page


@pytest.mark.parametrize("width", [1440, 390])
def test_tracker_totals_agree_with_the_map_and_startup_pages_show_both_images(browser, width):
    pages = DESIGN_MAP["pages"]
    specified = sum(1 for e in pages.values() if e.get("spec"))
    page = _open(browser, width)
    try:
        assert page.inner_text("#total-pages") == str(len(pages))
        assert page.inner_text("#specified-pages") == str(specified) == "12"
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 2, "page scrolls sideways"

        page.click("#tab-startup")
        cards = page.locator("#panel-startup article.card")
        assert cards.count() == len(DESIGN_MAP["startup_path"]) == 12
        for i in range(cards.count()):
            card = cards.nth(i)
            assert card.locator("h3").inner_text().strip()
            imgs = card.locator("img")
            assert imgs.count() == 2, "each startup page shows today beside its reference"
            for j in range(2):
                assert imgs.nth(j).evaluate("i => i.complete && i.naturalWidth > 0"), "image did not load"
    finally:
        page.close()


def test_block_count_opens_exactly_that_blocks_pages_and_spec_link_lands_on_content(browser):
    by_block = {}
    for entry in DESIGN_MAP["pages"].values():
        by_block[entry["block"]] = by_block.get(entry["block"], 0) + 1
    page = _open(browser, 1440)
    try:
        for block in ("public", "chat"):
            page.click("#tab-blocks")
            page.click(".show-block[data-block='%s']" % block)
            assert page.get_attribute("#tab-pages", "aria-selected") == "true"
            assert page.input_value("#block-filter") == block
            shown = page.locator("#pages-table tbody tr:not([hidden])")
            assert shown.count() == by_block[block]
            assert re.match(r"%d of \d+ pages" % by_block[block], page.inner_text("#page-count"))

        page.fill("#page-search", "intelligence/ask.html")
        row = page.locator("#pages-table tbody tr:not([hidden])")
        assert row.count() == 1
        href = row.locator("a", has_text="Spec").get_attribute("href")
        spec = (TRACKER.parent / href).resolve()
        assert spec.is_file() and "## Side by side" in spec.read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)]+\.webp)\)", spec.read_text(encoding="utf-8")):
            assert (spec.parent / link).resolve().stat().st_size > 0

        page.fill("#page-search", "no-such-page-anywhere")
        assert page.inner_text("#page-count").startswith("0 of ")
    finally:
        page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_product_manager_filters_pages_by_persona_and_reads_the_components_to_compose(browser, width):
    """Goal: before briefing a conversion, a product manager finds a persona's pages and reads each page's component list."""
    pages = DESIGN_MAP["pages"]
    persona = "procurement"
    expected = {rel: e for rel, e in pages.items() if e["persona"] == persona}
    assert expected, "the map assigns procurement pages"
    page = _open(browser, width)
    try:
        page.click("#tab-pages")
        page.select_option("#persona-filter", persona)
        shown = page.locator("#pages-table tbody tr:not([hidden])")
        assert shown.count() == len(expected)
        assert page.inner_text("#page-count").startswith("%d of %d" % (len(expected), len(pages)))
        for i in range(shown.count()):
            cells = shown.nth(i).locator("td")
            rel = cells.nth(0).inner_text()
            assert cells.nth(2).inner_text() == DESIGN_MAP["personas"][persona]
            assert cells.nth(3).inner_text() == ", ".join(expected[rel]["components"]) != ""
        assert page.inner_text("#outside-components") == str(_measured()["outside_components_count"])
        page.select_option("#persona-filter", "")
        page.fill("#page-search", "no-such-page-anywhere")
        assert page.inner_text("#page-count").startswith("0 of ")
    finally:
        page.close()
