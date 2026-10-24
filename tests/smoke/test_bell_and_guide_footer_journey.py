"""
Journey: the notifications bell opens its panel for everyone, and the
guided walkthrough's footer buttons are one component at one size.

Signed in as the solution architect (not an administrator).
"""
import pytest

from .conftest import PAGE_TIMEOUT
from .test_shell_follows_the_pattern_library_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# A page that ships guided-walkthrough steps.
GUIDED_PAGE = "/dashboard/overview"




def test_bell_opens_with_empty_state_for_person_with_no_notifications(page, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    assert "admin" not in email.split("@")[0].lower()
    _login(page, live_server, email)
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1200)

    items = page.evaluate(
        "fetch('/api/notifications', {credentials: 'same-origin'}).then(r => r.json())"
    )
    data = items.get("data", items) if isinstance(items, dict) else items
    assert not (data.get("items") if isinstance(data, dict) else data), "persona must have no notifications"

    bell = page.locator("#notif-btn")
    assert bell.get_attribute("aria-expanded") == "false"
    bell.click()
    panel = page.locator("#notif-dropdown")
    panel.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(1500)  # the panel refreshes itself; it must stay open
    assert panel.is_visible()
    assert bell.get_attribute("aria-expanded") == "true"
    assert "No notifications" in panel.inner_text()
    assert panel.locator("#notif-mark-read-btn").is_hidden()

    page.keyboard.press("Escape")
    panel.wait_for(state="hidden", timeout=5000)
    assert bell.get_attribute("aria-expanded") == "false"


def test_guided_footer_buttons_share_height_and_radius(page, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    _login(page, live_server, email)
    page.goto(live_server + GUIDED_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    trigger = page.locator("#guided-mode-trigger")
    assert trigger.is_visible(), "this page must offer the guided walkthrough"
    trigger.click()
    panel = page.locator("#guided-mode-panel")
    panel.wait_for(state="visible", timeout=5000)

    footer = panel.locator("div.border-t").last
    exit_btn = footer.get_by_role("button", name="Exit")
    next_btn = footer.get_by_role("button", name="Next")
    assert next_btn.is_visible() and exit_btn.is_visible()

    def metrics(loc):
        return loc.evaluate(
            "e => { const s = getComputedStyle(e); return [e.getBoundingClientRect().height, s.borderTopLeftRadius]; }"
        )

    ex, nx = metrics(exit_btn), metrics(next_btn)
    assert ex == nx, f"Exit {ex} and Next {nx} must match"
    assert ex[0] == 36, f"library size is 36px, got {ex[0]}"
    page.screenshot(path="/home/ubuntu/verify/seats/shell01b/walk/guide_footer_tmp.png")

    next_btn.click()
    assert panel.is_visible()


def test_bulk_selection_bar_buttons_are_the_button_component(page, live_server, seeded, app):
    """Procurement selects vendors; the bar's two actions share one size and work."""
    import uuid

    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization

    with app.app_context():
        tag = uuid.uuid4().hex[:6]
        for n in ("Alpha", "Beta"):
            db.session.add(VendorOrganization(name=f"{n} Systems {tag}", vendor_type="software_vendor"))
        db.session.commit()
    email = seeded["emails"]["procurement"]
    _login(page, live_server, email)
    page.goto(live_server + "/vendors/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    boxes = page.get_by_role("checkbox", name="Select row")
    boxes.first.wait_for(state="visible", timeout=15000)
    boxes.first.check()

    bar = page.locator("[role=status]").filter(has_text="selected")
    delete_btn = bar.get_by_role("button", name="Delete selected")
    clear_btn = bar.get_by_role("button", name="Clear selection")
    delete_btn.wait_for(state="visible", timeout=5000)
    assert delete_btn.is_visible() and clear_btn.is_visible()

    def metrics(loc):
        return loc.evaluate(
            "e => [e.getBoundingClientRect().height, getComputedStyle(e).borderTopLeftRadius]"
        )

    assert metrics(delete_btn) == metrics(clear_btn) == [32, "6px"]

    delete_btn.click()
    modal = page.get_by_role("heading", name="Delete Vendors?")
    modal.wait_for(state="visible", timeout=5000)
    page.keyboard.press("Escape")

    clear_btn.click()
    bar.wait_for(state="hidden", timeout=5000)
    assert not boxes.first.is_checked()


def test_rationalization_pipeline_is_a_card_and_its_steps_land_on_content(page, live_server, seeded):
    email = seeded["emails"]["portfolio_manager"]
    _login(page, live_server, email)
    page.goto(live_server + "/applications/rationalization", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    nav = page.get_by_role("navigation", name="Rationalization pipeline")
    nav.wait_for(state="visible", timeout=15000)
    card = nav.locator("[data-slot=card]")
    assert card.count() == 1
    assert "rounded-xl" not in (card.get_attribute("class") or "")
    assert page.locator("nav.rounded-xl.border.bg-card").count() == 0

    hrefs = nav.locator("a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    assert hrefs, "pipeline steps must be links"
    assert len(hrefs) == 5
    for href in hrefs:
        resp = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert resp.status == 200, f"{href} answered {resp.status}"
        assert len(page.locator("main, #main-content").first.inner_text().strip()) > 40, f"{href} is empty"
