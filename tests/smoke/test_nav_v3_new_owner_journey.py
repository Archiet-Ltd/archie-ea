"""A new owner finds their way round Entelim from the sidebar alone (navigation v3).

The person: a startup CTO who has just signed up. Their goals, each asserted as the
outcome they need, not as an element on a page:

- they are told what to do first (connect GitHub or Jira) and the button takes them to
  the page where they do it;
- they can ask a question in one action from the sidebar (the AI chat entry) and in one
  action from the search box (type the question, press Enter), and the question is
  waiting in the chat;
- every job is one click away, and each click lands on a page with content for that job
  and marks that entry as the current one;
- a page that has no entry of its own is found by its old name from the same box;
- the old Ask address still opens the chat.

Played at 1440 wide in the light theme and at 390 wide in the dark theme (where the
sidebar is a drawer opened by its trigger first). Set JOURNEY_SHOT_DIR to keep the
screenshots.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "NavThree!Journey2026"
QUESTION = "What breaks if Postgres fails?"

# Entry -> (address it opens, words its page must carry). Same names and order for every role.
JOBS = [
    ("Home", r"/dashboard/overview", None),
    ("AI chat", r"/ai-chat", None),
    ("Systems", r"/applications/?$", None),
    ("Impact", r"/enterprise/analysis/impact-analysis", None),
    ("Changes", r"/enterprise/implementation/gap-analysis", None),
    ("Spend", r"/applications/rationalization", None),
    ("Compliance", r"/risks/?$", None),
    ("Integrations", r"/admin/connectors", "GitHub"),
    ("Reports", r"/solutions/briefings", None),
    ("Help", r"/docs", None),
]


class Walk:
    def __init__(self, page, base, tag):
        self.page, self.base, self.tag, self.n = page, base, tag, 0
        root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
            os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "nav-v3"
        )
        self.dir = os.path.abspath(os.path.join(root, tag))
        os.makedirs(self.dir, exist_ok=True)
        self.phone = tag.startswith("390")

    def shot(self, name):
        self.n += 1
        self.page.screenshot(path=os.path.join(self.dir, "%02d-%s.png" % (self.n, name)), full_page=False)

    def sidebar(self):
        """The sidebar, opened first on a phone (one tap on its trigger)."""
        if self.phone:
            opener = self.page.get_by_role("button", name="Open sidebar")
            if opener.is_visible():
                opener.click()
                self.page.wait_for_timeout(300)
        side = self.page.get_by_test_id("sidebar")
        expect(side).to_be_visible()
        return side


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


def _dismiss_first_run_dialog(page):
    dialog = page.locator("[x-show='showOnboarding']").first
    if not (dialog.count() and dialog.is_visible()):
        return
    for _ in range(6):
        if not dialog.is_visible():
            return
        button = page.locator(
            "[x-show='showOnboarding'] button:visible",
            has_text=re.compile("Skip|Continue|Get started|Done|Finish|Close", re.I),
        )
        if not button.count():
            return
        button.last.click()
        page.wait_for_timeout(400)


def test_01_a_new_owner_signs_up(walk):
    page = walk.page
    page.goto(walk.base + "/account/register", wait_until="domcontentloaded")
    page.fill("#first_name", "Ada")
    page.fill("#last_name", "Obi")
    page.fill("#email", "nav.v3.%s@gmail.com" % uuid.uuid4().hex[:10])
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    page.wait_for_timeout(1000)
    _dismiss_first_run_dialog(page)
    assert "/dashboard" in page.url


def test_02_the_sidebar_says_what_to_do_first_and_the_button_goes_there(walk):
    page = walk.page
    side = walk.sidebar()
    card = side.get_by_test_id("connect-source-card")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Connect GitHub or Jira to build your model")
    walk.shot("connect-card")
    card.get_by_role("link", name="Connect a source").click()
    page.wait_for_url(re.compile(r".*/admin/connectors.*"))
    main = page.locator("main").first
    expect(main).to_contain_text("GitHub")
    expect(main).to_contain_text("Jira")


def test_03_the_ai_chat_is_one_click_from_the_sidebar(walk):
    page = walk.page
    page.goto(walk.base + "/dashboard/overview", wait_until="domcontentloaded")
    _dismiss_first_run_dialog(page)
    walk.sidebar().get_by_role("link", name="AI chat", exact=True).click()
    page.wait_for_url(re.compile(r".*/ai-chat.*"))
    expect(page.locator("#user-input")).to_be_visible()


def test_04_a_question_typed_in_the_search_box_opens_the_chat_with_it(walk):
    page = walk.page
    page.goto(walk.base + "/dashboard/overview", wait_until="domcontentloaded")
    _dismiss_first_run_dialog(page)
    walk.sidebar()
    box = page.locator("#sidebar-search")
    box.click()
    box.fill(QUESTION)
    expect(page.get_by_test_id("sidebar-search-ask")).to_contain_text(QUESTION)
    walk.shot("search-ask")
    box.press("Enter")
    page.wait_for_url(re.compile(r".*/ai-chat.*q=.*"))
    expect(page.locator("#user-input")).to_have_value(QUESTION)


def test_05_ctrl_k_reaches_the_search_box_from_any_page(walk):
    page = walk.page
    if walk.phone:
        pytest.skip("a phone has no keyboard shortcut; the drawer's search box is checked in step 04")
    page.goto(walk.base + "/applications/", wait_until="domcontentloaded")
    page.keyboard.press("Control+k")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.activeElement && document.activeElement.id") == "sidebar-search"


@pytest.mark.parametrize("entry,address,words", JOBS, ids=[j[0] for j in JOBS])
def test_06_each_job_is_one_click_away(walk, entry, address, words):
    page = walk.page
    page.goto(walk.base + "/dashboard/overview", wait_until="domcontentloaded")
    _dismiss_first_run_dialog(page)
    walk.sidebar().locator('a[data-slot="sidebar-item"]', has_text=re.compile(r"^\s*%s\s*$" % re.escape(entry))).click()
    page.wait_for_url(re.compile(r".*%s.*" % address))
    main = page.locator("main").first
    expect(main).not_to_be_empty()
    assert page.locator("h1").first.inner_text().strip(), "%s opened a page with no title" % entry
    if words:
        expect(main).to_contain_text(words)
    assert "Internal Server Error" not in page.content()
    if entry != "Help":  # the documentation page is a public page, outside the signed-in shell
        current = walk.sidebar().locator('a[data-slot="sidebar-item"][aria-current="page"]')
        expect(current).to_have_count(1)
        expect(current).to_contain_text(entry)
    walk.shot("job-%s" % entry.lower().replace(" ", "-"))


def test_07_a_page_with_no_entry_is_found_by_its_old_name(walk):
    page = walk.page
    page.goto(walk.base + "/dashboard/overview", wait_until="domcontentloaded")
    _dismiss_first_run_dialog(page)
    walk.sidebar()
    page.locator("#sidebar-search").fill("roadmap")
    hit = page.get_by_test_id("sidebar-search-page").filter(has_text="Roadmap").first
    expect(hit).to_contain_text("Changes")
    hit.click()
    page.wait_for_url(re.compile(r".*/genome/roadmap.*"))
    expect(page.locator("main").first).not_to_be_empty()


def test_08_the_old_ask_address_opens_the_chat(walk):
    page = walk.page
    page.goto(walk.base + "/intelligence/ask?q=" + "which%20systems%20have%20no%20owner", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r".*/ai-chat.*"))
    expect(page.locator("#user-input")).to_have_value("which systems have no owner")
