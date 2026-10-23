"""A startup CTO signs up and gets a cited answer, played in a real browser.

The person: a CTO with no architecture team and a ten-system estate in a
spreadsheet. The goal: from a signed-out browser to "what breaks if Postgres
fails?" answered with the systems that depend on it, each with its source and
date, and a dashboard whose figures agree with the file she imported.

The steps are the ones a timed walk of current main took (register from the
landing page, the first-run dialog, the dashboard checklist, the import dialog,
Ask, the dashboard), not deep links. Every step asserts what the person needs,
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

from .conftest import type_and_wait

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

SNAG_01 = "launch-cut walk-15min 01: sign-up asks an eleven-way job-title question and two intro screens"
SNAG_02 = "launch-cut walk-15min 02: there is nowhere to connect GitHub"
SNAG_03 = "launch-cut walk-15min 03: an empty Ask offers no way to add a system"
SNAG_04 = "launch-cut walk-15min 04: the only import is the fifty-column spreadsheet, no paste-a-list"
SNAG_05 = "launch-cut walk-15min 05: the import dialog needs two confirmations with unexplained names"
SNAG_06_07 = (
    "launch-cut walk-15min 06 and 07: the first answer after an import is '0 connections' because "
    "the import carries no dependencies and they can only be drawn in the composer"
)
SNAG_08 = "launch-cut walk-15min 08: the answer has no sentence a CTO could forward"
SNAG_09 = "launch-cut walk-15min 09: the answer cites no source, author or date"
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
    """Go to Ask the way the dashboard offers it."""
    page = j.page
    if "/intelligence/ask" in page.url:
        return
    page.locator("main").get_by_role("link", name="Ask a question").first.click()
    page.wait_for_url(re.compile(r".*/intelligence/ask.*"), timeout=30000)


def _go_home(j):
    j.page.get_by_role("link", name="Home").first.click()
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
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    page.wait_for_timeout(1500)
    j.saw("Signed up")
    j.shot("signed-up")
    assert "/dashboard" in page.url, "signing up should land the person in their new workspace"


@pytest.mark.xfail(strict=True, reason=SNAG_01)
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


@pytest.mark.xfail(strict=True, reason=SNAG_02)
def test_03_the_dashboard_offers_to_connect_github(journey):
    j, page = journey, journey.page
    j.saw("Dashboard checklist")
    offer = page.locator("main").get_by_role("link", name=re.compile("github", re.I)).or_(
        page.locator("main").get_by_role("button", name=re.compile("github", re.I))
    )
    expect(offer.first).to_be_visible(timeout=SHORT)


@pytest.mark.xfail(strict=True, reason=SNAG_03)
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
    page.get_by_role("link", name="Import", exact=True).first.click()
    page.wait_for_url(re.compile(r".*/applications/\?import=1"), timeout=30000)
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
    assert re.search(r"\b%d of %d applications in the portfolio have a named owner" % (WITH_OWNER, n), text), (
        "the file names an owner for %d of %d systems; the list should say so. Saw: %s" % (WITH_OWNER, n, text[:400])
    )
    assert re.search(r"\b%d of %d applications in the portfolio have a vendor recorded" % (WITH_VENDOR, n), text), (
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
        type_and_wait(page, "ask", ASKED)
        page.locator("#ask-picker-listbox [role=option]").first.click()
        page.locator("#ask-question-impact").click()
        page.wait_for_timeout(4000)
        j.answer = j.main_text()
        j.saw("Answer to: what breaks if %s fails" % ASKED)
        j.shot("first-answer")
    return j


@pytest.mark.xfail(strict=True, reason=SNAG_06_07)
def test_11_the_answer_names_every_system_that_relies_on_it_and_no_others(asked):
    text = asked.answer
    missing = [name for name in DEPENDANTS if name not in text]
    invented = [name for name in NOT_DEPENDANTS if name in text]
    assert not missing, "the answer should name %s; it does not name %s. Saw: %s" % (DEPENDANTS, missing, text[:500])
    assert not invented, "the answer names systems nothing in the file says rely on %s: %s" % (ASKED, invented)


@pytest.mark.xfail(strict=True, reason=SNAG_09)
def test_12_the_answer_shows_where_each_link_came_from_and_when(asked):
    text = asked.answer
    assert re.search(r"\bsource\b", text, re.I), "the answer should say what each link came from. Saw: " + text[:500]
    assert DATE.search(text), "the answer should carry the date each link was recorded. Saw: " + text[:500]


@pytest.mark.xfail(strict=True, reason=SNAG_08)
def test_13_the_answer_gives_a_sentence_a_cto_could_forward(asked):
    text = asked.answer
    assert re.search(r"If %s fails[^.\n]*Archiet API" % ASKED, text), (
        "the answer should open with a sentence such as 'If %s fails, Archiet API ... stop'. Saw: %s" % (ASKED, text[:500])
    )


@pytest.mark.xfail(strict=True, reason=SNAG_06_07)
def test_14_clicking_a_listed_system_lands_on_a_page_with_content_for_it(asked):
    j, page = asked, asked.page
    link = page.locator("main").get_by_role("link", name=re.compile("View on twin map", re.I)).first
    expect(link).to_be_visible(timeout=SHORT)
    ask_url = page.url
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
    assert re.search(r"APPLICATIONS\s*\n\s*%d\b" % len(SYSTEMS), text), (
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


def test_17_sign_up_to_the_end_took_less_than_fifteen_minutes(journey):
    elapsed = time.time() - journey.started
    print("\n[startup-cto %s] elapsed since sign-up: %.0f s (budget %d s)" % (journey.tag, elapsed, BUDGET_SECONDS))
    journey.record.write("## Elapsed\n%.0f seconds from landing page to this step.\n" % elapsed)
    assert elapsed < BUDGET_SECONDS, "sign-up to first answer took %.0f s; the budget is %d s" % (elapsed, BUDGET_SECONDS)
