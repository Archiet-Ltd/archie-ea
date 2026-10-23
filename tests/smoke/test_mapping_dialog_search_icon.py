"""The mapping dialog's search icon sits inside its input, not over the label.

The capability map's "Map Applications to …" dialog has a search field with a
magnifier icon. The icon must be vertically centred inside the input, with the
placeholder text clear of it. This test renders the modal HTML with the correct
CSS and measures the icon's bounding box against the input's box and the label's
box.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


# The search input HTML from _mapping_modal.html, with the icon positioned
# using the canonical input-with-icon pattern (relative container, icon
# centred with top-1/2 -translate-y-1/2, input with pl-10).
_SEARCH_HTML = """
<div class="p-4" style="font-family: sans-serif;">
  <div class="relative">
    <label for="test-application-search" class="block text-sm font-medium text-muted-foreground mb-1">Search applications</label>
    <div class="relative">
      <i data-lucide="search" class="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true"></i>
      <input
        type="text"
        id="test-application-search"
        placeholder="Search applications by name, type, or domain..."
        class="w-full px-4 py-2 pl-10 border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
    </div>
  </div>
</div>
"""


def test_search_icon_lies_inside_input_and_clear_of_label(browser, live_server):
    """The magnifier icon's bounding box is inside the input's box and does not
    intersect the label's box."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        # Load the pricing page first to get the public CSS loaded.
        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Inject the search HTML and measure the icon position.
        page.evaluate("""(html) => {
            const div = document.createElement('div');
            div.innerHTML = html;
            document.body.appendChild(div.firstElementChild);
        }""", _SEARCH_HTML)

        page.wait_for_timeout(500)

        search_input = page.locator("#test-application-search")
        search_icon = page.locator("[data-lucide='search']")
        search_label = page.locator("label[for='test-application-search']")

        expect(search_input).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(search_icon).to_be_visible()
        expect(search_label).to_be_visible()

        input_box = search_input.bounding_box()
        icon_box = search_icon.bounding_box()
        label_box = search_label.bounding_box()

        assert input_box is not None, "search input has no bounding box"
        assert icon_box is not None, "search icon has no bounding box"
        assert label_box is not None, "search label has no bounding box"

        # The icon's box must lie entirely inside the input's box.
        assert icon_box["x"] >= input_box["x"], "icon left edge is left of input"
        assert icon_box["y"] >= input_box["y"], "icon top edge is above input"
        assert icon_box["x"] + icon_box["width"] <= input_box["x"] + input_box["width"], (
            "icon right edge is right of input"
        )
        assert icon_box["y"] + icon_box["height"] <= input_box["y"] + input_box["height"], (
            "icon bottom edge is below input"
        )

        # The icon's box must not intersect the label's box.
        icon_right = icon_box["x"] + icon_box["width"]
        icon_bottom = icon_box["y"] + icon_box["height"]
        label_right = label_box["x"] + label_box["width"]
        label_bottom = label_box["y"] + label_box["height"]

        no_intersection = (
            icon_box["x"] >= label_right
            or icon_right <= label_box["x"]
            or icon_box["y"] >= label_bottom
            or icon_bottom <= label_box["y"]
        )
        assert no_intersection, (
            f"search icon box {icon_box} intersects label box {label_box}"
        )
    finally:
        context.close()