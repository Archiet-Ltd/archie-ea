"""The signed-in shell is the library's sidebar-07: an enterprise architect gets around it.

The person: an enterprise architect in an organisation that already holds applications,
a solution and a contract. Their goals, each asserted as the outcome they need:

- every sidebar entry opens a page with that entry's content and is marked as the
  current one: the top entries directly, the Work and Model items by opening them
  first and choosing a page inside, in the expanded sidebar and in the icon rail;
- they can fold the sidebar to an icon rail with the trigger or Ctrl+B, the rail names
  each icon in a tooltip, and the choice is still there after a reload and on the next
  page;
- on a phone the sidebar is a drawer: the trigger opens it, Tab stays inside it, Escape
  closes it and puts the focus back on the trigger, and an entry opens a page with content;
- an architect who does not administer the organisation is not shown Billing, an owner
  who does is.

Played at 1440 wide in the light theme and at 390 wide in the dark theme. Set
JOURNEY_SHOT_DIR to keep the screenshots.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Entry key -> (name on the sidebar, address it opens, words its page carries or None, the item
# that holds it or None for an entry on the sidebar itself).
ENTRIES = [
    ("home", "Home", r"/dashboard", None, None),
    ("ai_chat", "AI chat", r"/ai-chat", None, None),
    ("applications", "Applications", r"/applications/?$", "Smoke", "systems"),
    ("impact_analysis", "Impact analysis", r"/enterprise/analysis/impact-analysis", None, "impact"),
    ("gaps", "Gaps", r"/enterprise/implementation/gap-analysis", None, "changes"),
    ("solutions", "Solutions", r"/solutions", None, "changes"),
    ("business_cases", "Business cases", r"/business-case/?$", "business case", "changes"),
    ("roadmap", "Roadmap", r"/genome/roadmap", None, "changes"),
    ("risks", "Risks", r"/risks/?$", None, "compliance"),
    ("type_ValueStream", "Value stream", r"/value-streams", None, "layer_strategy"),
    ("frameworks", "Frameworks", r"/frameworks/?$", "Industry process frameworks", None),
]


class Walk:
    def __init__(self, page, base, tag):
        self.page, self.base, self.tag, self.n = page, base, tag, 0
        root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
            os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "sidebar-shell"
        )
        self.dir = os.path.abspath(os.path.join(root, tag))
        os.makedirs(self.dir, exist_ok=True)
        self.phone = tag.startswith("390")

    def shot(self, name):
        self.n += 1
        self.page.screenshot(path=os.path.join(self.dir, "%02d-%s.png" % (self.n, name)), full_page=False)

    def sidebar(self):
        return self.page.locator('[data-slot="sidebar"]')

    def trigger(self):
        return self.page.get_by_test_id("sidebar-trigger")

    def drawer(self):
        return self.page.get_by_role("dialog", name="Navigation")

    def entry(self, key):
        return self.page.locator('[data-slot="sidebar-menu-button"][data-nav-key="%s"]' % key)

    def sub(self, key):
        return self.page.locator('a[data-slot="sidebar-menu-sub-button"][data-nav-key="%s"]' % key)

    def open_item(self, key):
        """Open a Work or Model item if it is shut; its pages are then in the list."""
        item = self.entry(key)
        if item.get_attribute("aria-expanded") != "true":
            item.click()
        expect(item).to_have_attribute("aria-expanded", "true")

    def settle_width(self, width):
        """The fold animates; wait for the sidebar column to reach its width."""
        box = self.page.locator('[data-slot="sidebar-container"]')
        for _ in range(30):
            if round(box.bounding_box()["width"]) == width:
                return
            self.page.wait_for_timeout(100)
        assert round(box.bounding_box()["width"]) == width, box.bounding_box()

    def reach(self, path="/dashboard/overview"):
        self.page.goto(self.base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        self.page.wait_for_function("window.Alpine !== undefined", timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module", params=[(1440, "light"), (390, "dark")], ids=["desktop-light", "phone-dark"])
def walk(request, browser, live_server):
    width, theme = request.param
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    if theme == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    yield Walk(page, live_server, "%d-%s" % (width, theme))
    context.close()


def _architect_email(seeded):
    return seeded["emails"]["enterprise_architect"]


def _arrive_as_architect(walk, seeded):
    """Sign in once per fixture; later tests find the session still there."""
    email = _architect_email(seeded)
    if "/account/login" in walk.page.url or walk.page.url == "about:blank" or not getattr(walk, "signed_in", False):
        from app import create_app
        from app.models.user import User

        app = create_app("testing")
        with app.app_context():
            user = User.query.filter_by(email=email).one()
            assert user.enterprise_role == "enterprise_architect", user.enterprise_role
            assert not user.is_platform_admin
        sign_in(walk.page, walk.base, email)
        walk.signed_in = True
    walk.reach()


def _open_sidebar_on_phone(walk):
    if walk.phone:
        if not walk.drawer().is_visible():
            walk.trigger().click()
        expect(walk.drawer()).to_be_visible()


def test_01_the_shell_is_the_library_sidebar(walk, seeded):
    _arrive_as_architect(walk, seeded)
    page = walk.page
    expect(page.locator('[data-slot="sidebar-wrapper"]')).to_have_count(1)
    expect(page.locator('[data-slot="sidebar-inset"]')).to_have_count(1)
    side = walk.sidebar()
    assert side.get_attribute("data-collapsible-mode") == "icon"
    assert side.get_attribute("data-variant") == "sidebar"
    # The page sits beside the sidebar, not under it.
    if not walk.phone:
        expect(side).to_be_visible()
        assert side.get_attribute("data-state") == "expanded"
        box = page.locator('[data-slot="sidebar-container"]').bounding_box()
        assert box["width"] == 256, box
        main = page.locator("#main-content").bounding_box()
        assert main["x"] >= 256, main
    else:
        expect(side).to_be_hidden()
        expect(walk.trigger()).to_be_visible()
    walk.shot("shell")


def test_02_the_trigger_opens_and_closes_the_drawer_on_a_phone(walk, seeded):
    if not walk.phone:
        pytest.skip("the drawer is the phone's sidebar")
    _arrive_as_architect(walk, seeded)
    page = walk.page
    walk.trigger().click()
    drawer = walk.drawer()
    expect(drawer).to_be_visible()
    expect(drawer.get_by_role("button", name="Systems")).to_be_visible()
    walk.shot("drawer-open")
    # Tab goes round inside the drawer and never reaches the page behind it.
    for _ in range(60):
        page.keyboard.press("Tab")
        assert page.evaluate("!!document.activeElement.closest('[data-fb-sidebar-mobile]')"), "focus left the drawer"
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    assert page.evaluate("document.activeElement.getAttribute('data-slot')") == "sidebar-trigger"
    # The keyboard alone opens it again.
    page.keyboard.press("Enter")
    expect(drawer).to_be_visible()
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()


def test_03_the_trigger_and_ctrl_b_fold_the_sidebar_to_an_icon_rail(walk, seeded):
    if walk.phone:
        pytest.skip("below 768px the sidebar is the drawer, not an icon rail")
    _arrive_as_architect(walk, seeded)
    page = walk.page
    side = walk.sidebar()
    walk.trigger().click()
    assert side.get_attribute("data-state") == "collapsed"
    assert side.get_attribute("data-collapsible") == "icon"
    # The fold animates for 200ms; the rail settles at 3rem.
    walk.settle_width(48)
    # Icons only: the labels are gone, every entry still shows its icon.
    expect(walk.entry("systems")).to_be_visible()
    button = walk.entry("systems").bounding_box()
    label = walk.entry("systems").locator("span").first.bounding_box()
    assert button["width"] == 32, button
    assert label["x"] >= button["x"] + button["width"] - 1, "the label still shows beside the icon"
    walk.shot("icon-rail")
    # Each icon names itself in a tooltip.
    walk.entry("systems").hover()
    expect(page.get_by_role("tooltip")).to_have_text("Systems")
    walk.shot("icon-rail-tooltip")
    # The choice survives a reload and the next page.
    page.reload(wait_until="domcontentloaded")
    assert walk.sidebar().get_attribute("data-state") == "collapsed"
    walk.entry("ai_chat").click()
    page.wait_for_url(re.compile(r"/ai-chat"), timeout=PAGE_TIMEOUT)
    assert walk.sidebar().get_attribute("data-state") == "collapsed"
    expect(page.locator("#user-input")).to_be_visible()
    expect(walk.entry("ai_chat")).to_have_attribute("aria-current", "page")
    # Ctrl+B brings the labels back, and takes them away again.
    page.keyboard.press("Control+b")
    assert walk.sidebar().get_attribute("data-state") == "expanded"
    expect(page.locator('a[data-nav-key="ai_chat"]')).to_contain_text("AI chat")
    page.keyboard.press("Control+b")
    assert walk.sidebar().get_attribute("data-state") == "collapsed"
    page.keyboard.press("Control+b")
    assert walk.sidebar().get_attribute("data-state") == "expanded"
    walk.shot("expanded-again")


@pytest.mark.parametrize("key,name,address,words,parent", ENTRIES, ids=[e[0] for e in ENTRIES])
def test_04_every_entry_lands_on_a_page_with_content(walk, seeded, key, name, address, words, parent):
    _arrive_as_architect(walk, seeded)
    page = walk.page
    _open_sidebar_on_phone(walk)
    if parent:
        walk.open_item(parent)
        link = walk.sub(key)
    else:
        link = walk.entry(key)
    expect(link).to_contain_text(name)
    link.click()
    page.wait_for_url(re.compile(address), timeout=PAGE_TIMEOUT)
    main = page.locator("main").first
    expect(main).not_to_be_empty()
    assert len(main.inner_text().strip()) > 40, "%s opened an empty page" % name
    if words:
        expect(main).to_contain_text(re.compile(words, re.I))
    assert "403" not in page.title() and "Not Found" not in page.title()
    _open_sidebar_on_phone(walk)
    if parent:
        # The page's own entry is marked and its item stays open.
        walk.open_item(parent)
        expect(walk.sub(key)).to_have_attribute("aria-current", "page")
    else:
        expect(walk.entry(key)).to_have_attribute("aria-current", "page")
    walk.shot("entry-%s" % key)


def test_04b_changes_opens_onto_its_pages_in_the_briefed_order(walk, seeded):
    _arrive_as_architect(walk, seeded)
    _open_sidebar_on_phone(walk)
    walk.open_item("changes")
    names = walk.page.locator('a[data-slot="sidebar-menu-sub-button"]:visible').evaluate_all(
        "(links) => links.filter((a) => a.closest('[data-nav-item=\"changes\"]')).map((a) => a.innerText.trim())"
    )
    assert names == ["Gaps", "Solutions", "Options analysis", "Business cases", "Work packages", "Roadmap",
                     "Programmes", "Canvases"], names
    walk.shot("changes-open")


def test_04c_what_a_person_opens_stays_open_on_the_next_page(walk, seeded):
    _arrive_as_architect(walk, seeded)
    _open_sidebar_on_phone(walk)
    walk.open_item("layer_business")
    walk.page.reload(wait_until="domcontentloaded")
    _open_sidebar_on_phone(walk)
    expect(walk.entry("layer_business")).to_have_attribute("aria-expanded", "true")
    expect(walk.sub("type_BusinessProcess")).to_be_visible()
    walk.entry("layer_business").click()
    walk.page.reload(wait_until="domcontentloaded")
    _open_sidebar_on_phone(walk)
    expect(walk.entry("layer_business")).to_have_attribute("aria-expanded", "false")


def test_05_the_architect_has_connections_and_no_billing(walk, seeded):
    _arrive_as_architect(walk, seeded)
    page = walk.page
    _open_sidebar_on_phone(walk)
    expect(page.locator('[data-testid="nav-work"]')).to_contain_text("Work")
    expect(page.locator('[data-testid="nav-model"]')).to_contain_text("Model")
    assert page.locator('[data-testid="nav-architecture"]').count() == 0
    connections = walk.entry("connections")
    expect(connections).to_contain_text("Connections")
    href = connections.get_attribute("href")
    page.locator("[data-testid=user-menu]").click()
    menu = page.get_by_test_id("user-menu-account")
    expect(menu).to_be_visible()
    expect(page.get_by_test_id("user-menu-billing")).to_have_count(0)
    walk.shot("architect-user-menu")
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    if walk.phone:
        # The first Escape closed the menu only; the drawer is still open.
        expect(walk.drawer()).to_be_visible()
    connections.click()
    page.wait_for_url(lambda url: href in url or "/admin/connectors" in url or "/import" in url, timeout=PAGE_TIMEOUT)
    assert len(page.locator("main").first.inner_text().strip()) > 40


def test_06_an_owner_who_administers_sees_billing_and_it_opens(walk):
    from app import create_app, db
    from app.models.user import Role
    from app.modules.account.services.account_service import AccountService

    app = create_app("testing")
    email = "shell.owner.%s@example.com" % uuid.uuid4().hex[:8]
    with app.test_request_context():
        Role.insert_roles()
        AccountService.register_user("Nora", "Owner", email, PASSWORD)
        db.session.remove()
    from app.models.user import User

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.is_org_admin and not user.is_platform_admin
    page = walk.page
    page.context.clear_cookies()
    sign_in(page, walk.base, email)
    walk.signed_in = False
    walk.reach()
    _open_sidebar_on_phone(walk)
    page.locator("[data-testid=user-menu]").click()
    billing = page.get_by_test_id("user-menu-billing")
    expect(billing).to_be_visible()
    walk.shot("owner-user-menu")
    billing.click()
    page.wait_for_url(lambda url: "/admin/billing" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("main").first).to_contain_text("Plan")
