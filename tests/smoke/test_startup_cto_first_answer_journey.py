"""A startup CTO signs up and gets a cited answer, played in a real browser.

The person: a CTO with no architecture team and a ten-system estate in a
spreadsheet. The goal: from a signed-out browser to "what breaks if Postgres
fails?" answered with the systems that depend on it, each with its source and
date, and a dashboard whose figures agree with the file she imported.

The steps are the ones a timed walk of current main took (register from the
landing page, the welcome steps, the import dialog,
AI chat, the dashboard), not deep links. Every step asserts what the person needs,
not that a page rendered.

Where the product does not yet give the person what they need, the step is an
expected failure that names the snag brief that owns the fix
(launch-cut/walk-15min/NN). The marks are strict: when a fix lands, its step
passes, the run fails, and the mark has to come out. That makes this journey the
gate for those fixes. A step marked "defect" names a gap found by this journey
that has no snag brief yet.

Line 5 (sign-up to first answer, unaided, inside 15 minutes) is asserted by the
last step, and the elapsed time is printed either way.

Set JOURNEY_SHOT_DIR to choose where screenshots and the walk record go.
"""

import os
import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "StartupCto!Journey2026"
BUDGET_SECONDS = 15 * 60
FILE = os.path.join(os.path.dirname(__file__), "..", "fixtures", "startup_cto_estate.csv")

# What the file says. Ten systems; two have no owner (Slack, GitHub); two have
# no cost (GitHub, Analytics Dashboard); every one has a vendor.
SYSTEMS = [
    "Monelytics", "Archiet API", "Postgres", "Redis Cache", "Web App",
    "Stripe Billing", "Slack", "GitHub", "Auth Service", "Analytics Dashboard",
]
WITH_OWNER, WITH_COST, WITH_VENDOR = 8, 8, 10
# Asking what breaks if Postgres fails: Archiet API, Auth Service and Analytics
# Dashboard rely on it directly; Monelytics and Web App rely on Archiet API.
ASKED = "Postgres"
DEPENDANTS = ["Archiet API", "Auth Service", "Analytics Dashboard", "Monelytics", "Web App"]
NOT_DEPENDANTS = ["Slack", "GitHub", "Redis Cache", "Stripe Billing"]

SNAG_04 = "launch-cut walk-15min 04: the only import is the fifty-column spreadsheet, no paste-a-list"
SNAG_05 = "launch-cut walk-15min 05: the import dialog needs two confirmations with unexplained names"
DEFECT_COST = (
    "defect, no snag brief yet: the run cost in the imported file is not shown anywhere on the system's "
    "page, which lists total cost of ownership and owner as missing"
)
DEFECT_FIGURES = (
    "defect, no snag brief yet: the dashboard overview shows how many applications there are but "
    "not how many have an owner or a cost"
)

DATE = re.compile(
    r"\b(20\d\d-\d\d-\d\d|\d{1,2} [A-Z][a-z]{2,8} 20\d\d|[A-Z][a-z]{2,8} \d{1,2}, 20\d\d)\b"
)
SHORT = 8000  # ms: how long a person waits for something that should already be there


class Journey:
    """One person in one browser, carried from step to step."""

    def __init__(self, page, base, tag):
        self.page = page
        self.base = base
        self.tag = tag
        self.started = None
        self.n = 0
        root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
            os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "startup-cto"
        )
        self.dir = os.path.abspath(os.path.join(root, tag))
        os.makedirs(self.dir, exist_ok=True)
        self.record = open(os.path.join(self.dir, "walk-record.md"), "w", encoding="utf-8")
        self.record.write("# Startup CTO walk (%s)\n\n" % tag)

    def shot(self, name):
        self.n += 1
        self.page.screenshot(path=os.path.join(self.dir, "%02d-%s.png" % (self.n, name)), full_page=True)

    def saw(self, step, text=None):
        """Record, in plain words, what the person sees at this step."""
        if text is None:
            try:
                text = self.page.locator("main").first.inner_text(timeout=2000)
            except Exception:  # noqa: BLE001 - the page may be mid-navigation
                text = ""
        text = " | ".join(t.strip() for t in text.splitlines() if t.strip())[:700]
        self.record.write("## %s\n%s\n\n%s\n\n" % (step, self.page.url, text))
        self.record.flush()

    def main_text(self):
        return self.page.locator("main").first.inner_text()


@pytest.fixture(scope="module", params=[(1440, "light"), (390, "dark")], ids=["desktop-light", "phone-dark"])
def journey(request, browser, live_server):
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
    j = Journey(page, live_server, "%d-%s" % (width, theme))
    yield j
    elapsed = time.time() - j.started if j.started else 0
    print("\n[startup-cto %s] sign-up to end of journey: %.0f s" % (j.tag, elapsed))
    j.record.close()
    context.close()


@pytest.fixture(scope="module")
def estate_file(tmp_path_factory):
    """The CTO's spreadsheet: the committed sample, saved as a workbook the way she keeps it."""
    import csv

    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    with open(FILE, newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            sheet.append([int(c) if c.isdigit() else (c or None) for c in row])
    path = tmp_path_factory.mktemp("estate") / "startup_estate.xlsx"
    workbook.save(path)
    return str(path)


def _dismiss_welcome(j):
    """The first-run dialog the way the walk took it: pick the role, continue, skip."""
    page = j.page
    dialog = page.locator("[x-show='showOnboarding']").first
    if not (dialog.count() and dialog.is_visible()):
        return
    page.get_by_text("CTO / CIO", exact=True).first.click()
    for _ in range(5):
        if not dialog.is_visible():
            break
        button = page.locator(
            "[x-show='showOnboarding'] button:visible",
            has_text=re.compile("Continue|Skip|Get started|Done|Finish", re.I),
        )
        if not button.count():
            break
        button.last.click()
        page.wait_for_timeout(500)
    expect(dialog).to_be_hidden(timeout=SHORT)


def _open_ask(j):
    """Go to the AI chat the way the navigation offers it (on a phone, through the sheet)."""
    page = j.page
    if "/ai-chat" in page.url:
        return
    _sidebar(j).locator(
        'a[data-slot="sidebar-menu-button"]', has_text=re.compile(r"^\s*AI chat\s*$")
    ).click()
    page.wait_for_url(re.compile(r".*/ai-chat.*"), timeout=30000)


def _sidebar(j):
    """The navigation: on a phone the drawer, opened with one tap on its trigger."""
    page = j.page
    if j.tag.startswith("390"):
        drawer = page.get_by_role("dialog", name="Navigation")
        if not drawer.is_visible():
            page.get_by_test_id("sidebar-trigger").click()
        return drawer
    return page.get_by_test_id("sidebar")


def _go_home(j):
    _sidebar(j).locator('a[data-slot="sidebar-menu-button"]', has_text=re.compile(r"^\s*Home\s*$")).click()
    j.page.wait_for_url(re.compile(r".*/dashboard/overview.*"), timeout=30000)


# ---------------------------------------------------------------------------
# Sign-up
# ---------------------------------------------------------------------------


def test_01_a_new_person_signs_up_from_the_landing_page(journey):
    j, page = journey, journey.page
    j.started = time.time()
    page.goto(j.base + "/", wait_until="domcontentloaded")
    j.shot("landing")
    page.get_by_role("link", name="Start free").first.click()
    page.wait_for_url(re.compile(r".*/account/register.*"), timeout=30000)
    email = "startup.cto.%s@gmail.com" % uuid.uuid4().hex[:10]
    page.fill("#first_name", "Chidi")
    page.fill("#last_name", "Okeke")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    j.shot("sign-up-form")
    page.locator("#submit").click()
    # Sign-up now carries on through the welcome steps (sources, company, AI provider); she gives
    # nothing, presses Continue twice and Finish, and lands in her workspace.
    page.wait_for_url(re.compile(r".*/account/welcome.*"), timeout=60000)
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3", timeout=60000)
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=4", timeout=60000)
    # The last step offers two plain next steps; each must land on a page with content.
    steps = page.get_by_test_id("signup-next-steps")
    expect(steps.get_by_role("button", name="Import your systems")).to_be_visible(timeout=SHORT)
    expect(steps.get_by_role("button", name="Ask your first question")).to_be_visible(timeout=SHORT)
    j.shot("welcome-finish")
    steps.get_by_role("button", name="Ask your first question").click()
    page.wait_for_url(re.compile(r".*/ai-chat.*"), timeout=60000)
    expect(page.locator("#user-input")).to_be_visible(timeout=SHORT)
    page.goto(j.base + "/account/welcome?step=4", wait_until="domcontentloaded")
    page.get_by_test_id("signup-next-steps").get_by_role("button", name="Import your systems").click()
    page.wait_for_url(re.compile(r".*/applications/.*"), timeout=60000)
    dialog = page.locator("#application-import-modal")
    expect(dialog).to_be_visible(timeout=30000)
    expect(dialog.locator("#excel-file-input")).to_be_attached()
    page.goto(j.base + "/account/welcome?step=4", wait_until="domcontentloaded")
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    page.wait_for_timeout(1500)
    j.saw("Signed up")
    j.shot("signed-up")
    assert "/dashboard" in page.url, "signing up should land the person in their new workspace"


def test_02_sign_up_ends_on_one_next_action_not_a_job_title_question(journey):
    j, page = journey, journey.page
    dialog = page.locator("[x-show='showOnboarding']").first
    asked_a_role_question = bool(dialog.count() and dialog.is_visible())
    j.saw("First screen after sign-up", dialog.inner_text() if asked_a_role_question else None)
    try:
        _dismiss_welcome(j)  # the walk took the dialog: role, continue, skip
    finally:
        j.shot("after-first-run-dialog")
    assert not asked_a_role_question, (
        "a startup CTO should land on one next action, not an eleven-way job-title question and two intro screens"
    )


def test_03_the_dashboard_offers_to_connect_github(journey):
    j, page = journey, journey.page
    j.saw("Dashboard checklist")
    offer = page.locator("main").get_by_role("link", name=re.compile("github", re.I)).or_(
        page.locator("main").get_by_role("button", name=re.compile("github", re.I))
    )
    expect(offer.first).to_be_visible(timeout=SHORT)


def test_04_an_empty_ask_says_how_to_add_a_system(journey):
    j, page = journey, journey.page
    _open_ask(j)
    j.saw("Ask on an empty workspace")
    j.shot("ask-empty")
    way_to_add = page.locator("main").get_by_role(
        "link", name=re.compile(r"\b(add|import|connect)\b", re.I)
    ).or_(page.locator("main").get_by_role("button", name=re.compile(r"\b(add|import|connect)\b", re.I)))
    expect(way_to_add.first).to_be_visible(timeout=SHORT)


# ---------------------------------------------------------------------------
# Bringing the estate in
# ---------------------------------------------------------------------------


def test_05_the_checklist_import_opens_the_import_dialog(journey):
    j, page = journey, journey.page
    _go_home(j)
    # The first-visit Overview offers Connect GitHub only; the person opens Applications from the
    # navigation and presses Import there.
    side = _sidebar(j)
    toggle = side.locator('[data-nav-toggle="systems"]')
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    side.locator('a[data-slot="sidebar-menu-sub-button"]', has_text=re.compile(r"^\s*Applications\s*$")).click()
    page.wait_for_url(re.compile(r".*/applications/?$"), timeout=30000)
    page.get_by_test_id("btn-import").click()
    dialog = page.locator("#application-import-modal")
    expect(dialog).to_be_visible(timeout=30000)
    j.saw("Import dialog", dialog.inner_text())
    j.shot("import-dialog")


@pytest.mark.xfail(strict=True, reason=SNAG_04)
def test_06_the_import_dialog_takes_a_pasted_list_of_systems(journey):
    page = journey.page
    box = page.locator("#application-import-modal textarea:visible")
    expect(box.first).to_be_visible(timeout=SHORT)


def test_07_choosing_the_file_previews_every_system_in_it(journey, estate_file):
    j, page = journey, journey.page
    page.set_input_files("#excel-file-input", estate_file)
    preview = page.locator("#excel-preview")
    expect(preview).to_be_visible(timeout=30000)
    for name in SYSTEMS:
        expect(preview.get_by_text(name, exact=True).first).to_be_visible(timeout=SHORT)
    j.saw("Import preview", preview.inner_text())
    j.shot("import-preview")


@pytest.mark.xfail(strict=True, reason=SNAG_05)
def test_08_one_visible_button_imports_the_file(journey):
    page = journey.page
    one_button = page.locator("#application-import-modal button:visible", has_text=re.compile(r"^\s*Import \d+ systems?", re.I))
    expect(one_button.first).to_be_visible(timeout=SHORT)


def test_09_importing_lists_every_system_with_the_owners_and_vendors_in_the_file(journey):
    j, page = journey, journey.page
    # The walk's route: the analysis choice, then the second confirmation.
    page.get_by_role("button", name="Run direct import").click()
    page.locator("#import-applications-btn").click()
    # The import posts, then the list reloads with the new systems in it.
    expect(page.get_by_text(re.compile(r"Showing 1.{1,3}%d of %d" % (len(SYSTEMS), len(SYSTEMS))))).to_be_visible(
        timeout=90000
    )
    for name in SYSTEMS:
        shown = page.locator("main").get_by_text(name, exact=True).locator("visible=true")
        expect(shown.first).to_be_visible(timeout=SHORT)
    j.shot("import-done")
    close = page.locator("#application-import-modal button[aria-label='Close']")
    if close.count() and close.first.is_visible():
        close.first.click()
    expect(page.locator("#application-import-modal")).to_be_hidden(timeout=SHORT)
    text = j.main_text()
    j.saw("Applications after import", text)
    j.shot("applications-list")
    n = len(SYSTEMS)
    assert re.search(r"\b%d of %d have no owner" % (n - WITH_OWNER, n), text), (
        "the file names an owner for %d of %d systems; the list should say so. Saw: %s" % (WITH_OWNER, n, text[:400])
    )
    assert re.search(r"\bAll %d have a vendor" % WITH_VENDOR, text), (
        "the file names a vendor for every system; the list should say so. Saw: %s" % text[:400]
    )


@pytest.fixture
def system_page(journey):
    """The page a person gets by opening Monelytics from the applications list."""
    j, page = journey, journey.page
    if not hasattr(j, "system_text"):
        if "/applications/" not in page.url or "?import" in page.url:
            page.goto(j.base + "/applications/", wait_until="domcontentloaded")
        name = page.locator("main").get_by_text("Monelytics", exact=True).locator("visible=true").first
        card = name.locator("xpath=ancestor::*[.//a[starts-with(@href, '/applications/')]][1]")
        card.locator("a[href^='/applications/']:visible").first.click()
        page.wait_for_url(re.compile(r".*/applications/\d+$"), timeout=30000)
        page.wait_for_timeout(1500)
        j.system_text = j.main_text()
        j.saw("Monelytics page")
        j.shot("system-page")
    return j


def test_10_a_system_page_opens_with_the_owner_from_the_file(system_page):
    text = system_page.system_text
    assert "Monelytics" in text, "the page opened from the list should be the system that was clicked. Saw: " + text[:300]
    assert "Chidi Okeke" in text, "the owner from the file (Chidi Okeke) should be on the system's page. Saw: " + text[:600]


@pytest.mark.xfail(strict=True, reason=DEFECT_COST)
def test_10b_the_system_page_shows_the_run_cost_from_the_file(system_page):
    text = system_page.system_text
    assert re.search(r"48,?000", text), (
        "the run cost in the file (48,000) should be on the system's page, which instead says "
        "'Missing: ... Total cost of ownership'. Saw: " + text[:600]
    )


# ---------------------------------------------------------------------------
# The first answer
# ---------------------------------------------------------------------------


@pytest.fixture
def asked(journey):
    """The question asked once: what breaks if Postgres fails."""
    j, page = journey, journey.page
    if not hasattr(j, "answer"):
        page.goto(j.base + "/dashboard/overview", wait_until="domcontentloaded")
        _open_ask(j)
        card = ask(page, j.base, "What breaks if %s fails" % ASKED, fresh=False)
        j.answered_at = time.time()
        j.answer = card.inner_text()
        j.citations = [
            h for h in card.locator("a[data-relationship-link]").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            ) if h
        ]
        j.system_hrefs = [
            h for h in dict.fromkeys(
                card.get_by_role("link", name=re.compile("|".join(DEPENDANTS))).evaluate_all(
                    "els => els.map(e => e.getAttribute('href'))"
                )
            ) if h
        ]
        j.saw("Answer to: what breaks if %s fails" % ASKED)
        j.shot("first-answer")
    return j


def test_11_the_answer_names_every_system_that_relies_on_it_and_no_others(asked):
    text = asked.answer
    missing = [name for name in DEPENDANTS if name not in text]
    invented = [name for name in NOT_DEPENDANTS if name in text]
    assert not missing, "the answer should name %s; it does not name %s. Saw: %s" % (DEPENDANTS, missing, text[:500])
    assert not invented, "the answer names systems nothing in the file says rely on %s: %s" % (ASKED, invented)


def test_12_the_answer_shows_where_each_link_came_from_and_when(asked):
    text = asked.answer
    assert re.search(r"\bsource\b", text, re.I), "the answer should say what each link came from. Saw: " + text[:500]
    assert DATE.search(text), "the answer should carry the date each link was recorded. Saw: " + text[:500]


def test_13_the_answer_gives_a_sentence_a_cto_could_forward(asked):
    text = asked.answer
    assert re.search(r"If %s fails[^.\n]*Archiet API" % ASKED, text), (
        "the answer should open with a sentence such as 'If %s fails, Archiet API ... stop'. Saw: %s" % (ASKED, text[:500])
    )


def test_14_clicking_a_listed_system_lands_on_a_page_with_content_for_it(asked):
    j, page = asked, asked.page
    link = page.get_by_test_id("estate-answer").last.get_by_role("link", name=re.compile("|".join(DEPENDANTS))).first
    expect(link).to_be_visible(timeout=SHORT)
    ask_url = page.url
    clicked = link.inner_text().strip()
    link.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)
    text = j.main_text()
    j.saw("The page a cited system opens")
    j.shot("cited-item")
    assert page.url != ask_url, "the link should open the system, not stay on the answer"
    assert any(name in text for name in DEPENDANTS), "the page should show the system that was clicked. Saw: " + text[:400]
    assert not re.search(r"nothing (is )?modelled|no .{0,30} found|not found", text, re.I), (
        "the page a cited system opens is an empty state. Saw: " + text[:400]
    )
    assert clicked in text and "Element #" not in text, (
        "the page should name %r, not an unnamed element. Saw: %s" % (clicked, text[:400])
    )
    assert ASKED in text, (
        "the page for %r should show the relationship the answer cited, the one with %s. Saw: %s"
        % (clicked, ASKED, text[:400])
    )
    assert "No relationships yet" not in text and not re.search(r"\b0 relationships", text), (
        "%r has a recorded relationship, so the page must not say it has none. Saw: %s" % (clicked, text[:400])
    )


def _connection_rows(page):
    """Open "Show the connections as a list" on a landing and read every row as (from, relationship, to)."""
    toggle = page.locator("#composer-focus-list-toggle")
    expect(toggle).to_be_visible(timeout=SHORT)
    if "Show the connections" in toggle.inner_text():
        toggle.click()
    rows = page.locator("#composer-focus-list tr[data-focus-row]")
    expect(rows.first).to_be_visible(timeout=SHORT)
    out = []
    for i in range(rows.count()):
        cells = [c.strip() for c in rows.nth(i).locator("td").all_inner_texts()]
        out.append((cells[0], cells[1], cells[2]))
    return out


def _cited_system_hrefs(j):
    assert j.system_hrefs, "the answer should link the systems it names. Saw: %s" % j.citations
    return j.system_hrefs


def test_14a_the_connections_list_names_both_ends_of_each_relationship(asked):
    j, page = asked, asked.page
    hrefs = _cited_system_hrefs(j)
    seen = {}
    for href in hrefs:
        page.goto(j.base + href if href.startswith("/") else href, wait_until="domcontentloaded")
        rows = _connection_rows(page)
        j.saw("The connections list of a cited system: %s" % rows)
        same = [r for r in rows if r[0] == r[2]]
        assert not same, "a row names the same system at both ends: %s (all rows: %s)" % (same, rows)
        for r in rows:
            seen[(r[0], r[2])] = r
    assert any(a == ASKED and b in DEPENDANTS for a, b in seen), (
        "the list should show %s serving a system that relies on it. Saw: %s" % (ASKED, sorted(seen))
    )
    assert ("Postgres", "Archiet API") in seen and ("Archiet API", "Monelytics") in seen, (
        "the two-hop row should name Archiet API as the end of the Postgres connection. Saw: %s" % sorted(seen)
    )
    j.shot("connections-list")


def test_14b_nothing_the_answer_named_is_dimmed_on_the_landing(asked):
    j, page = asked, asked.page
    for href in _cited_system_hrefs(j):
        page.goto(j.base + href if href.startswith("/") else href, wait_until="domcontentloaded")
        expect(page.locator("#composer-focus-list-toggle")).to_be_visible(timeout=SHORT)
        page.wait_for_timeout(4000)
        dimmed = page.evaluate("document.querySelectorAll('#composer-canvas .dimmed').length")
        assert dimmed == 0, "%d drawn parts of the neighbourhood opened from %s are dimmed" % (dimmed, href)


TYPE_LABEL_CONTRAST = """() => {
    const parse = (c) => { const m = c.match(/[\\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
    const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
    const over = (top, base) => ({ r: top.r * top.a + base.r * (1 - top.a), g: top.g * top.a + base.g * (1 - top.a), b: top.b * top.a + base.b * (1 - top.a), a: 1 });
    const out = [];
    document.querySelectorAll('#composer-canvas [joint-selector="typeLabel"]').forEach((label) => {
        if (!label.textContent.trim()) return;
        const cell = label.closest('[model-id]');
        const body = cell && cell.querySelector('[joint-selector="body"]');
        if (!body) return;
        const page = parse(getComputedStyle(document.body).backgroundColor);
        const fill = over(parse(getComputedStyle(body).fill), page.a ? page : { r: 255, g: 255, b: 255, a: 1 });
        const ink = over(parse(getComputedStyle(label).fill), fill);
        const hi = Math.max(lum(fill), lum(ink)), lo = Math.min(lum(fill), lum(ink));
        out.push({ text: label.textContent.trim(), ratio: (hi + 0.05) / (lo + 0.05) });
    });
    return out;
}"""


def test_14c_the_type_label_under_each_element_name_can_be_read(asked):
    j, page = asked, asked.page
    page.goto(j.base + _cited_system_hrefs(j)[0], wait_until="domcontentloaded")
    expect(page.locator("#composer-focus-list-toggle")).to_be_visible(timeout=SHORT)
    page.wait_for_timeout(4000)
    labels = page.evaluate(TYPE_LABEL_CONTRAST)
    j.shot("landing-type-labels")
    assert labels, "the landing draws no type label under any element name"
    faint = [lab for lab in labels if lab["ratio"] < 4.5]
    assert not faint, "type labels below 4.5:1 against their fill (%s): %s" % (j.tag, faint)


def test_14d_a_landing_opened_from_an_answer_says_what_it_shows(asked):
    j, page = asked, asked.page
    page.goto(j.base + _cited_system_hrefs(j)[0], wait_until="domcontentloaded")
    expect(page.locator("#composer-focus-list-toggle")).to_be_visible(timeout=SHORT)
    focus = page.locator("#composer-focus-banner h3").first.inner_text()
    name = re.match(r"Unsaved view: (.+?), ", focus).group(1)
    subtitle = page.get_by_test_id("diagram-purpose").inner_text()
    j.saw("The landing's subtitle", subtitle)
    assert "free canvas" not in subtitle.lower(), "the landing keeps the blank-canvas subtitle: %r" % subtitle
    assert name in subtitle and "your answer" in subtitle, (
        "the subtitle should name %r and say it comes from the answer. Saw: %r" % (name, subtitle)
    )


def test_14e_a_new_organisation_asking_what_breaks_is_offered_the_sample_and_github(browser, live_server, journey):
    from .fresh_org import create_fresh_org, sign_in as sign_in_fresh

    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]
    width = int(journey.tag.split("-")[0])
    context = browser.new_context(viewport={"width": width, "height": 900})
    if journey.tag.endswith("dark"):
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    try:
        sign_in_fresh(page, live_server, email)
        card = ask(page, live_server, "What breaks if %s fails" % ASKED)
        steps = card.get_by_test_id("catalogue-empty-steps")
        expect(steps).to_be_visible(timeout=SHORT)
        expect(steps.get_by_test_id("chat-answer-connect")).to_be_visible()
        sample = steps.get_by_test_id("chat-answer-sample")
        expect(sample).to_be_visible()
        expect(card.get_by_role("link", name=re.compile(r"Add .*to your model"))).to_be_visible()
        page.screenshot(path=os.path.join(journey.dir, "empty-organisation-impact-answer.png"), full_page=True)
        sample.click()
        page.wait_for_load_state("domcontentloaded")
        answered = ask(page, live_server, "What breaks if Payments service fails")
        expect(answered.get_by_test_id("answer-open")).to_be_visible(timeout=SHORT)
        assert "Payments service" in answered.inner_text(), "after loading the sample the question has no answer"
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The figures
# ---------------------------------------------------------------------------


def test_15_the_dashboard_counts_the_systems_in_the_file(journey):
    j, page = journey, journey.page
    page.goto(j.base + "/dashboard/overview", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    text = j.main_text()
    j.saw("Dashboard overview after import", text)
    j.shot("dashboard-overview")
    assert re.search(r"(?m)^%d\s*\n\s*Applications\s*$" % len(SYSTEMS), text), (
        "the dashboard should count the %d systems in the file. Saw: %s" % (len(SYSTEMS), text[:400])
    )


@pytest.mark.xfail(strict=True, reason=DEFECT_FIGURES)
def test_16_the_dashboard_says_how_many_have_an_owner_and_how_many_have_a_cost(journey):
    text = journey.main_text()
    n = len(SYSTEMS)
    has_owner = re.search(r"\b%d\b[^\n]{0,80}owner|owner[^\n]{0,80}\b%d\b" % (WITH_OWNER, WITH_OWNER), text, re.I)
    has_cost = re.search(r"\b%d\b[^\n]{0,80}cost|cost[^\n]{0,80}\b%d\b" % (WITH_COST, WITH_COST), text, re.I)
    assert has_owner, "the dashboard should say %d of %d systems have an owner" % (WITH_OWNER, n)
    assert has_cost, "the dashboard should say %d of %d systems have a cost" % (WITH_COST, n)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def test_17_sign_up_to_the_end_took_less_than_fifteen_minutes(asked):
    j, page = asked, asked.page
    assert j.answer.strip(), "no answer arrived, so there is nothing to time"
    assert j.citations, "the answer cites nothing a person can open. Saw: " + j.answer[:400]
    page.goto(j.base + j.citations[0], wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    cited = j.main_text()
    assert any(name in cited for name in DEPENDANTS + [ASKED]) and not re.search(
        r"not found|nothing (is )?modelled", cited, re.I
    ), (
        "the first citation (%s) opens an empty page. Saw: %s" % (j.citations[0], cited[:300])
    )
    elapsed = j.answered_at - j.started
    print("\n[startup-cto %s] sign-up to first cited answer: %.0f s = %.1f min (budget %d s)" % (
        j.tag, elapsed, elapsed / 60, BUDGET_SECONDS))
    j.record.write("## Elapsed\n%.0f seconds from sign-up to the first cited answer.\n" % elapsed)
    assert elapsed < BUDGET_SECONDS, "sign-up to first answer took %.0f s; the budget is %d s" % (elapsed, BUDGET_SECONDS)
