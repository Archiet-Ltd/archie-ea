"""A table header is never smaller than the 12 px the public product images require.

The capture standard refuses any text under 12 px, and the applications list, vendors and the other tables read
their header size from one rule in the stylesheet. That rule is 12 px; only the Overview keeps its approved 11 px.
"""
import re

from app.services.public_pages import STATIC_ROOT

CSS = (STATIC_ROOT / "styles" / "shadcn-components.css").read_text(encoding="utf-8")


def _size(selector_start):
    block = re.search(r"^" + re.escape(selector_start) + r"[^{]*\{([^}]*)\}", CSS, re.M)
    assert block, selector_start
    return re.search(r"font-size:\s*([^;\s]+)", block.group(1)).group(1)


def test_table_headers_are_twelve_pixels():
    assert _size("#main-content th:where(:not([data-slot])") == "0.75rem"


def test_only_the_overview_keeps_eleven_pixel_headers():
    assert _size("#main-content [data-testid=overview-dashboard] th") == "0.6875rem"


def test_completeness_ring_percent_sign_is_not_shrunk_below_twelve_pixels():
    ring = (STATIC_ROOT.parent / "templates" / "components" / "completeness_ring.html").read_text(encoding="utf-8")
    assert 'class="text-xs">%</span>' in ring and "0.6em" not in ring
