"""The My Applications tab strip never clips a tab on a phone.

An application owner on a 390 wide screen opens each page of the family (Dashboard, Applications,
Health, Roadmap). Every tab of the strip is either fully on screen or reachable by scrolling the strip
alone, the tab of the page they are on is in view when it loads, the page itself does not scroll
sideways, and the arrow keys move between the tabs.
"""
import pytest

from .conftest import PAGE_TIMEOUT
from .overview_data import purge_org
from .test_my_applications_dashboard_journey import OWNED, NOT_OWNED, _seed
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = [("/my-applications/", "Dashboard"), ("/my-applications/list", "Applications"),
         ("/my-applications/health", "Health"), ("/my-applications/roadmap", "Roadmap")]
LABELS = [label for _, label in PAGES]
VIEWPORT = 390


@pytest.fixture(scope="module")
def org(live_server):
    info = _seed(OWNED, NOT_OWNED)
    yield info
    purge_org(info["org_id"])


def _open(page, live_server, email, path):
    page.set_viewport_size({"width": VIEWPORT, "height": 844})
    _login(page, live_server, email)
    page.evaluate("() => document.documentElement.classList.add('dark')")
    response = page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(300)


def _strip(page):
    return page.locator('[aria-label="My Applications"]:has(a:text-is("Roadmap"))')


def _box(page, label):
    return _strip(page).locator("a:text-is(\"%s\")" % label).bounding_box()


def _inside(box):
    return box is not None and box["x"] >= -0.5 and box["x"] + box["width"] <= VIEWPORT + 0.5


@pytest.mark.parametrize("path,active", PAGES)
def test_no_tab_is_cut_on_a_phone(live_server, org, page, path, active):
    _open(page, live_server, org["emails"]["application_manager"], path)
    strip = _strip(page)
    assert strip.count() == 1, "the page carries one tab strip"

    overflow = strip.evaluate(
        "el => [el.scrollWidth - el.clientWidth, getComputedStyle(el).overflowX, getComputedStyle(el).flexWrap]")
    assert overflow[0] <= 0 or overflow[1] in ("auto", "scroll") or overflow[2] == "wrap", (
        "the strip is wider than its box and a finger cannot scroll it: %s" % overflow)
    assert _inside(_box(page, active)), "the tab of this page is in view on load: %s" % _box(page, active)
    assert page.evaluate("document.documentElement.scrollWidth") == VIEWPORT, "the page scrolls sideways"

    for label in LABELS:
        _strip(page).locator("a:text-is(\"%s\")" % label).scroll_into_view_if_needed()
        assert _inside(_box(page, label)), "tab %s is cut at %s: %s" % (label, VIEWPORT, _box(page, label))
    assert page.evaluate("document.documentElement.scrollWidth") == VIEWPORT


@pytest.mark.parametrize("path,active", PAGES)
def test_arrow_keys_move_between_the_tabs(live_server, org, page, path, active):
    _open(page, live_server, org["emails"]["application_manager"], path)
    _strip(page).locator("a:text-is(\"%s\")" % active).focus()
    index = LABELS.index(active)
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.textContent.trim()") == LABELS[(index + 1) % len(LABELS)]
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("document.activeElement.textContent.trim()") == active
    assert page.evaluate("document.documentElement.scrollWidth") == VIEWPORT
