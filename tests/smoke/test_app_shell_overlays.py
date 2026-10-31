"""Browser smoke test for app shell overlay z-index and positioning.

Exercises what the app-shell-overlays branch changed:
- Phone sidebar opener z-index is below the mobile sidebar backdrop
- Toast position is top-centre on mobile, bottom-right on desktop
- Help button z-index is below open overlays (backdrop, drawer, modal)
- Search and onboarding modals do not stack at the same z-index level

Every assertion reads computed style (getComputedStyle) from the rendered page,
not a class string from the template source.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


# ── Phone sidebar opener ────────────────────────────────────────────────────


def test_phone_sidebar_drawer_draws_over_the_header_trigger(browser, live_server, seeded):
    """At a phone viewport the sidebar is a drawer. It must draw over the header (and so
    over the trigger that opened it), so nothing of the page is clickable through it."""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        trigger = page.locator('[data-slot="sidebar-trigger"]')
        assert trigger.count() == 1, "phone sidebar trigger not found"
        trigger.click()
        drawer = page.get_by_role("dialog", name="Navigation")
        assert drawer.is_visible(), "the trigger did not open the drawer"
        z_indices = page.evaluate("""() => {
            const header = document.getElementById('admin-header');
            const sheet = document.querySelector('[data-fb-sidebar-mobile] [data-slot="sheet-content"]');
            return {
                headerZ: header ? parseInt(getComputedStyle(header).zIndex, 10) : null,
                sheetZ: sheet ? parseInt(getComputedStyle(sheet.parentElement).zIndex, 10) : null,
            };
        }""")
        assert z_indices["headerZ"] is not None and z_indices["sheetZ"] is not None, z_indices
        assert z_indices["sheetZ"] > z_indices["headerZ"], (
            "drawer z-index (%d) must be above the header's (%d)"
            % (z_indices["sheetZ"], z_indices["headerZ"])
        )
    finally:
        page.close()


# ── Toast position ──────────────────────────────────────────────────────────


def test_toast_position_top_centre_on_mobile(browser, live_server, seeded):
    """At a phone viewport the toast container must render top-centre, not
    bottom-right, so it never covers the onboarding modal's buttons."""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        # Trigger a toast via the Platform.toast API so the container exists
        page.evaluate("""() => {
            if (window.Platform && window.Platform.toast) {
                window.Platform.toast.info('smoke-test-toast');
            }
        }""")
        page.wait_for_timeout(500)

        position = page.evaluate("""() => {
            const container = document.getElementById('platform-toast-container');
            if (!container) return null;
            const cs = getComputedStyle(container);
            const rect = container.getBoundingClientRect();
            return {
                position: cs.position,
                top: cs.top,
                left: cs.left,
                bottom: cs.bottom,
                right: cs.right,
                rectTop: rect.top,
                rectLeft: rect.left,
                viewportHeight: window.innerHeight,
            };
        }""")

        assert position is not None, (
            "platform-toast-container not found after triggering a toast"
        )
        # On mobile the toast must be positioned at the top (top: auto is
        # overridden by the top-16 class), not at the bottom.
        assert position["position"] == "fixed", (
            "toast container must be fixed-positioned"
        )
        # The container should be near the top of the viewport (top-16 ≈ 4rem = 64px)
        assert position["rectTop"] < 200, (
            "toast container rectTop (%d) must be near the top of the viewport "
            "on mobile, not at the bottom" % position["rectTop"]
        )
    finally:
        page.close()


def test_toast_position_bottom_right_on_desktop(browser, live_server, seeded):
    """At a desktop viewport the toast container must render bottom-right."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        page.evaluate("""() => {
            if (window.Platform && window.Platform.toast) {
                window.Platform.toast.info('smoke-test-toast-desktop');
            }
        }""")
        page.wait_for_timeout(500)

        position = page.evaluate("""() => {
            const container = document.getElementById('platform-toast-container');
            if (!container) return null;
            const rect = container.getBoundingClientRect();
            return {
                rectTop: rect.top,
                rectBottom: rect.bottom,
                rectRight: rect.right,
                viewportHeight: window.innerHeight,
                viewportWidth: window.innerWidth,
            };
        }""")

        assert position is not None, (
            "platform-toast-container not found after triggering a toast"
        )
        # On desktop the toast must be near the bottom-right of the viewport
        assert position["rectBottom"] > position["viewportHeight"] - 200, (
            "toast container must be near the bottom of the viewport on desktop"
        )
        assert position["rectRight"] > position["viewportWidth"] - 200, (
            "toast container must be near the right edge of the viewport on desktop"
        )
    finally:
        page.close()


# ── Help button below open overlays ─────────────────────────────────────────


def test_help_button_z_index_below_overlays(browser, live_server, seeded):
    """The guided-mode help button must render at z-30, below every overlay
    (backdrop z-40, drawer z-50, modal z-[100]/z-[200])."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        # The dashboard overview page defines guided_mode_steps, so the help
        # button renders there.
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        z_indices = page.evaluate("""() => {
            const trigger = document.getElementById('guided-mode-trigger');
            const panel = document.getElementById('guided-mode-panel');
            // Find the mobile backdrop for reference
            const backdrop = document.querySelector(
                'div.fixed.inset-0.z-40, div[class*="fixed inset-0"][class*="z-40"]'
            );
            return {
                triggerZ: trigger ? parseInt(getComputedStyle(trigger).zIndex, 10) : null,
                panelZ: panel ? parseInt(getComputedStyle(panel).zIndex, 10) : null,
                backdropZ: backdrop ? parseInt(getComputedStyle(backdrop).zIndex, 10) : null,
            };
        }""")

        assert z_indices["triggerZ"] is not None, (
            "guided-mode-trigger not found on a page that defines guided_mode_steps"
        )
        assert z_indices["panelZ"] is not None, (
            "guided-mode-panel not found"
        )
        # The help button and panel must be at z-30, below the backdrop's z-40
        assert z_indices["triggerZ"] == 30, (
            "help button z-index (%d) must be 30, below every overlay"
            % z_indices["triggerZ"]
        )
        assert z_indices["panelZ"] == 30, (
            "help panel z-index (%d) must be 30, below every overlay"
            % z_indices["panelZ"]
        )
        if z_indices["backdropZ"] is not None:
            assert z_indices["triggerZ"] < z_indices["backdropZ"], (
                "help button z-index (%d) must be below backdrop z-index (%d)"
                % (z_indices["triggerZ"], z_indices["backdropZ"])
            )
    finally:
        page.close()


def test_help_button_reserves_space_on_main(browser, live_server, seeded):
    """When a page defines guided_mode_steps (the review board's page does) the
    main-content element must carry pb-20 so the help button never covers a control."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/arb/",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        padding = page.evaluate("""() => {
            const main = document.getElementById('main-content');
            if (!main) return null;
            const cs = getComputedStyle(main);
            return {
                paddingBottom: cs.paddingBottom,
                paddingBottomPx: parseFloat(cs.paddingBottom),
            };
        }""")

        assert padding is not None, "main-content element not found"
        # pb-20 = 5rem = 80px at default font size
        assert padding["paddingBottomPx"] >= 64, (
            "main-content padding-bottom (%s) must reserve space for the "
            "help button (pb-20 ≈ 80px)" % padding["paddingBottom"]
        )
    finally:
        page.close()


# ── Search results and the first screen: nothing stands over what a person needs ─


def test_sidebar_search_results_stand_above_the_page_and_can_be_clicked(browser, live_server, seeded):
    """The search box lives in the sidebar. Its results open over the page content and the
    first result is what a click at its centre lands on (nothing sits on top of it)."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)
        assert page.locator("#search-modal").count() == 0, "a second search modal is on the page"

        page.fill('[data-testid="sidebar-search"]', "applications")
        page.wait_for_selector('[data-testid="sidebar-search-page"]', timeout=PAGE_TIMEOUT)
        hit = page.evaluate("""() => {
            const first = document.querySelector('[data-testid="sidebar-search-page"]');
            const box = first.getBoundingClientRect();
            const atCentre = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
            return {
                text: first.textContent.trim(),
                landsOnResult: !!atCentre && first.contains(atCentre),
                panelZ: parseInt(getComputedStyle(document.getElementById('sidebar-search-results')).zIndex, 10),
            };
        }""")
        assert "Applications" in hit["text"], hit
        assert hit["landsOnResult"], "something covers the first search result: %s" % hit
        assert hit["panelZ"] >= 30, hit
    finally:
        page.close()


def test_first_screen_of_a_new_user_has_no_overlay_over_the_page(browser, live_server):
    """A person with no role in an empty workspace meets the page itself: no modal, wizard or
    other full-screen layer stands over it, and the middle of the page is the page."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import User
    import uuid

    suffix = uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        org = Organization(name="Onboard smoke %s" % suffix,
                           slug="onboard-smoke-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="onboard-smoke-%s@example.com" % suffix,
            first_name="Onboard", last_name="Smoke",
            organization_id=org.id,
            enterprise_role=None,
            confirmed=True,
        )
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        org_id = org.id

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _login(page, live_server,
               "onboard-smoke-%s@example.com" % suffix)
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)

        state = page.evaluate("""() => {
            const covering = [...document.querySelectorAll('body *')].filter((el) => {
                const cs = getComputedStyle(el);
                if (cs.position !== 'fixed' || cs.display === 'none' || cs.visibility === 'hidden') return false;
                const r = el.getBoundingClientRect();
                return r.width >= window.innerWidth * 0.9 && r.height >= window.innerHeight * 0.9
                    && parseFloat(cs.opacity) > 0;
            }).map((el) => el.id || el.className.toString().slice(0, 60));
            const main = document.getElementById('main-content').getBoundingClientRect();
            const atMiddle = document.elementFromPoint(main.left + main.width / 2, window.innerHeight / 2);
            return {
                covering,
                middleIsPage: !!atMiddle && document.getElementById('main-content').contains(atMiddle),
                role: document.body.textContent.includes("What's your role?"),
            };
        }""")
        assert state["covering"] == [], "a full-screen layer stands over the first screen: %s" % state["covering"]
        assert state["middleIsPage"], "the middle of the first screen is not the page"
        assert not state["role"], "the role wizard is back over the first screen"
    finally:
        page.close()
        with app.app_context():
            from app.services.org_deletion_service import OrganizationDeletionService

            db.session.remove()
            OrganizationDeletionService.delete_organization(org_id)
            db.session.execute(
                db.text("DELETE FROM user_sessions WHERE user_id=:uid"), {"uid": user_id})
            db.session.execute(
                db.text("DELETE FROM soc2_audit_log WHERE user_id=:uid"), {"uid": user_id})
            db.session.execute(
                db.text("DELETE FROM users WHERE id=:uid"), {"uid": user_id})
            db.session.commit()
            db.session.remove()


# ── Phone sidebar trigger clickable ──────────────────────────────────────────


def test_phone_sidebar_trigger_clickable_and_opens_the_drawer(browser, live_server, seeded):
    """At a phone viewport the sidebar trigger must be clickable (no force), open the
    drawer, and Escape must close it."""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/dashboard/overview",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        trigger = page.locator('[data-slot="sidebar-trigger"]')
        assert trigger.count() > 0, "phone sidebar trigger not rendered at 390px"
        trigger.first.click()
        drawer = page.get_by_role("dialog", name="Navigation")
        assert drawer.is_visible(), "the click on the trigger did not open the drawer"
        page.keyboard.press("Escape")
        assert not drawer.is_visible(), "Escape did not close the drawer"
    finally:
        page.close()
