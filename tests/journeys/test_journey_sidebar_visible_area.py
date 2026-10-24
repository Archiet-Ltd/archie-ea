"""Journey: the sidebar's scrolling list has more room, the search box stays visible while it scrolls,
a no-match search says so, and long labels wrap instead of being cut.

Reported measurements: at 1440x900 #sidebar-nav's client height was 693px by the footer's position (652px by
the audit's own script), against a target of at least 730px. The duplicated "Collapse sidebar (Ctrl+B)" row
inside the sidebar (the header already carries the same toggle, with the same Ctrl+B hint in its title) and
the search box living inside the scrolling <nav> both ate into that height for no reason.

Navigation v3: the search box sits above the scrolling list, outside it, and a search with no page match
offers to ask the AI chat instead.
"""
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
from playwright.sync_api import expect

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
MIN_NAV_HEIGHT = 700


def _persona(app, enterprise_role):
    from datetime import datetime

    from app import db
    from app.models.user import Permission, Role, User

    with app.app_context():
        org_id = make_org(db, "SidebarVisibleArea")
        user_id = make_user(db, org_id, "u", enterprise_role, role_name="Architect")
        # A fresh user with an empty workspace (no seeded applications/elements/capabilities/vendors)
        # trips admin_base.html's first-login onboarding modal, which renders on top of the real
        # sidebar this journey drives and can intercept clicks meant for it (e.g. the header's
        # collapse toggle, or the search box). This journey is about sidebar layout, not first-login
        # onboarding, so the persona represents a returning user -- see PR 74's fix to the sibling
        # sidebar-search journey file for the same issue.
        User.query.filter_by(id=user_id).update({"onboarding_completed_at": datetime.utcnow()})
        db.session.commit()
        if enterprise_role == "platform_admin":
            # get_sidebar_zones() gates the Admin ZONE on user.is_admin() (a real Role permission check,
            # Permission.ADMINISTER), and each admin-requires link on is_platform_admin/is_org_admin --
            # neither is enterprise_role alone (see its own docstring). Without a Role carrying ADMINISTER,
            # this fixture would never see its own Admin zone, same as a real platform_admin who was never
            # granted the Administrator role.
            user = db.session.get(User, user_id)
            user.is_platform_admin = True
            user.is_org_admin = True
            user.role = Role.query.filter(
                Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
            ).first()
            db.session.commit()
        return user_id


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _open(browser, client, document, path, width=1440, height=900):
    pg = browser.new_page(viewport={"width": width, "height": height})

    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == "/api/sidebar/search":
            r = client.get(url.path, query_string=dict(parse_qsl(url.query)))
            return route.fulfill(status=r.status_code, content_type="application/json", body=r.get_data())
        if url.path == path:
            return route.fulfill(status=200, content_type="text/html", body=document)
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)
    pg.goto("http://app.test" + path)
    pg.wait_for_selector("#sidebar-nav")
    return pg


def test_the_scrolling_list_holds_every_entry_at_1440(app, client, browser):
    """Navigation v3: the search box sits above the list (outside it), so the list is a
    little shorter than before; it must still hold every entry an architect has with no
    scrolling at 1440x900."""
    login(client, _persona(app, "solution_architect"))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path)
    try:
        height = pg.eval_on_selector("#sidebar-nav", "el => el.clientHeight")
        assert height >= MIN_NAV_HEIGHT, "sidebar-nav is %dpx tall, expected at least %dpx" % (height, MIN_NAV_HEIGHT)
        overflow = pg.eval_on_selector("#sidebar-nav", "el => el.scrollHeight - el.clientHeight")
        assert overflow <= 1, "the architect's entries do not fit without scrolling (%dpx over)" % overflow
    finally:
        pg.close()


def test_the_search_box_stays_on_screen_when_the_list_is_scrolled(app, client, browser):
    # platform_admin has the longest zone list, most likely to overflow the nav.
    login(client, _persona(app, "platform_admin"))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path)
    try:
        pg.eval_on_selector("#sidebar-nav", "el => { el.scrollTop = el.scrollHeight; }")
        pg.wait_for_timeout(150)
        visible = pg.eval_on_selector(
            '#sidebar-search',
            "el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.width > 0; }",
        )
        assert visible, "the search box scrolled out of view"
    finally:
        pg.close()


def test_a_no_match_search_still_says_so_after_the_layout_change(app, client, browser):
    login(client, _persona(app, "solution_architect"))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path)
    try:
        pg.fill('#sidebar-search', "zzzznomatch")
        results = pg.locator('[data-testid="sidebar-search-results"]')
        # Navigation v3: nothing matches, so the way out is asking the AI chat.
        expect(results).to_contain_text("No page matches. Press Enter to ask the AI chat.")
        expect(results.locator('[data-testid="sidebar-search-ask"]')).to_contain_text("zzzznomatch")

        pg.press('#sidebar-search', "Escape")
        pg.wait_for_timeout(150)
        assert not results.is_visible()
    finally:
        pg.close()


def test_a_long_label_wraps_instead_of_being_cut(app, client, browser):
    login(client, _persona(app, "enterprise_architect"))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path)
    try:
        link = pg.locator('#sidebar-nav a[title="Composer"] span.flex-1')
        link.wait_for(state="visible", timeout=10000)
        info = link.evaluate("""el => ({
            wraps: getComputedStyle(el).whiteSpace !== 'nowrap',
            fullText: el.textContent.trim(),
            clipped: el.scrollWidth > el.clientWidth && getComputedStyle(el).textOverflow === 'ellipsis',
        })""")
        assert info["wraps"], "the label still forces a single line (white-space: nowrap)"
        assert info["fullText"] == "Composer"
        assert not info["clipped"], "the label still ellipsis-clips instead of wrapping"
    finally:
        pg.close()


def test_the_footer_name_and_email_carry_a_title_with_the_full_text(app, client, browser):
    import uuid

    from app import db
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    long_email = "a-name-long-enough-to-need-a-title.%s@example.com" % suffix
    with app.app_context():
        org_id = make_org(db, "SidebarVisibleArea")
        user_id = make_user(db, org_id, "long", "solution_architect", role_name="Architect")
        user = db.session.get(User, user_id)
        user.first_name, user.last_name = "Aniekannneemeka", "Longernamewouldneverfit"
        user.email = long_email
        db.session.commit()
    login(client, user_id)
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path)
    try:
        name_title = pg.eval_on_selector('[data-slot="sidebar-footer"] .sidebar-footer-text span.font-medium', "el => el.title")
        email_title = pg.eval_on_selector('[data-slot="sidebar-footer"] .sidebar-footer-text span.text-muted-foreground', "el => el.title")
        assert name_title == "Aniekannneemeka Longernamewouldneverfit"
        assert email_title == long_email
    finally:
        pg.close()


def test_collapse_still_works_from_the_header_toggle_and_ctrl_b(app, client, browser):
    login(client, _persona(app, "solution_architect"))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path)
    try:
        assert "sidebar-collapsed" not in pg.eval_on_selector('[data-testid="sidebar"]', "el => el.className")
        pg.click('button[title*="Collapse sidebar (Ctrl+B)"]')
        pg.wait_for_timeout(200)
        assert "sidebar-collapsed" in pg.eval_on_selector('[data-testid="sidebar"]', "el => el.className")

        pg.keyboard.press("Control+b")
        pg.wait_for_timeout(200)
        assert "sidebar-collapsed" not in pg.eval_on_selector('[data-testid="sidebar"]', "el => el.className")
    finally:
        pg.close()


def test_the_duplicated_collapse_row_is_gone(app, client, browser):
    """The sidebar's own "Collapse sidebar (Ctrl+B)" row duplicated the header toggle; removed to reclaim
    height. Exactly one occurrence of the hint text remains, in the header's own `:title` binding
    (`'Collapse sidebar (Ctrl+B)'`, present as raw source even though the value is applied by Alpine)."""
    login(client, _persona(app, "solution_architect"))
    document = client.get("/dashboard/overview").get_data(as_text=True)
    assert document.count("Collapse sidebar (Ctrl+B)") == 1
    assert "sidebar-show-all-btn" not in document


def test_a_long_label_wraps_in_the_mobile_drawer_too(app, client, browser):
    """The same wrapping holds at 390x844 (the mobile drawer width), not only at 1440x900."""
    login(client, _persona(app, "enterprise_architect"))
    path = "/dashboard/overview"
    document = client.get(path).get_data(as_text=True)
    pg = _open(browser, client, document, path, width=390, height=844)
    try:
        link = pg.locator('#sidebar-nav a[title="Composer"] span.flex-1')
        link.wait_for(state="attached", timeout=10000)
        info = link.evaluate("""el => ({
            wraps: getComputedStyle(el).whiteSpace !== 'nowrap',
            fullText: el.textContent.trim(),
        })""")
        assert info["wraps"], "the label still forces a single line at 390x844"
        assert info["fullText"] == "Composer"
    finally:
        pg.close()


@pytest.mark.parametrize("role", [
    "solution_architect", "enterprise_architect", "business_architect", "cto", "security_architect",
    "data_architect", "procurement", "application_manager", "portfolio_manager", "arb_member",
    "platform_admin",
])
def test_no_zone_link_or_label_changed_for_any_persona(app, client, role):
    """Navigation v3: the rendered sidebar entries are exactly what the navigation
    registry gives this persona, in its order, for each of the eleven personas."""
    import html as html_module
    import re

    from app.navigation import registry

    class _StubUser:
        is_authenticated = True

        def __init__(self, role):
            self.id = 0
            self.organization_id = None
            self.enterprise_role = role
            self.is_platform_admin = role == "platform_admin"
            self.is_org_admin = role == "platform_admin"

        def is_admin(self):
            return self.enterprise_role == "platform_admin"

        def can(self, permission):
            from app.models.user import Permission

            if permission == Permission.ADMINISTER:
                return self.is_admin()
            return permission == Permission.GENERAL

    login(client, _persona(app, role))
    document = client.get("/dashboard/overview").get_data(as_text=True)
    nav = document[document.index('id="sidebar-nav"'):]
    nav = nav[:nav.index("</nav>")]
    rendered_labels = [html_module.unescape(label) for _, label in re.findall(r'href="([^"]+)"\s+title="([^"]+)"', nav)]
    rendered_labels = [label for label in rendered_labels if label != "Connect a source"]

    with app.test_request_context("/dashboard/overview"):
        expected = registry.sidebar_for(_StubUser(role), "dashboard.overview")
    expected_labels = []
    for group in ("main", "architecture", "secondary"):
        for item in expected[group]:
            expected_labels.append(item.label)
            expected_labels.extend(child.label for child in item.children)

    assert rendered_labels == expected_labels, (role, rendered_labels, expected_labels)
