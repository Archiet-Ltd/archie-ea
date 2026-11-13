"""Every capability on the investment pages shows its investment or says why it has none.

Goal: "I read the investment matrix, and for each of the capabilities I see the money behind it and where it
came from, or one sentence saying why there is none and the step that adds it. The total and the average say
what they cover, both pages agree, and on a phone nothing is cut and the evidence sits behind a control."

The demonstration organisation is seeded the way the product seeds it; a second journey uses a new
organisation whose one capability has no work, so the reason and its step are read too.
"""
import os
import pathlib
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_ADMIN = "demo@lantern-quay.example.com"
# The demonstration's own architect is not allowed to see spend; this person, added beside the seed, is.
DEMO_SPENDER = "finance.lead@lantern-quay.example.com"
PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="investment-funding-"))
CAPABILITIES = 24


@pytest.fixture(scope="module")
def demo(live_server):
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with create_app("testing").app_context():
            seed_demo_company()
            _add_demo_spender()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_SPENDER


def _add_demo_spender():
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User

    org_id = db.session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    if User.query.filter_by(email=DEMO_SPENDER, organization_id=org_id).first():
        return
    person = User(email=DEMO_SPENDER, first_name="Finley", last_name="Spender", organization_id=org_id,
                  confirmed=True, enterprise_role="cto", role=Role.query.filter_by(name="Architect").one())
    person.password = PASSWORD
    db.session.add(person)
    db.session.commit()


def _open(browser, live_server, email, path, width, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + path, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("capability-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _thousands(text):
    """The figure in a cell such as '£470.0k' or headline such as '£3.4M', in thousands."""
    match = re.search(r"[^\d\s]?([\d.]+)([kM])", text)
    assert match, text
    return float(match.group(1)) * (1000 if match.group(2) == "M" else 1)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    table = page.get_by_test_id("matrix-scroll")
    table.scroll_into_view_if_needed()
    table.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))))


@pytest.mark.parametrize("path", PAGES)
def test_every_capability_shows_its_investment_and_the_headlines_say_what_they_cover(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 1440)
    try:
        rows = page.get_by_test_id("capability-row")
        assert rows.count() == CAPABILITIES
        summed = 0.0
        for i in range(CAPABILITIES):
            row = rows.nth(i)
            name = row.get_by_test_id("cell-name").locator("a, span").first.inner_text().strip()
            cell = row.get_by_test_id("cell-investment").inner_text()
            assert "from " in cell and re.search(r"work packages?|Initiative", cell), (name, cell)
            summed += _thousands(cell)
            # the evidence beneath the row agrees with the figure
            budget = [line for line in row.get_by_test_id("capability-footer").inner_text().splitlines()
                      if "Budget" in line]
            assert budget and "not estimated" not in budget[0].lower(), (name, budget)
        total = page.get_by_test_id("total-investment").inner_text()
        # the headline total is the sum of the rows it covers, to the rounding of the figures shown
        assert abs(_thousands(total) - summed) <= 0.05 * 1000 + CAPABILITIES * 0.05, (total, summed)
        assert "from %d of %d capabilities" % (CAPABILITIES, CAPABILITIES) in total, total
        assert "every capability has one" in total, total
        average = page.get_by_test_id("average-strategic-score").inner_text()
        assert re.search(r"average of the importance scores of the \d+ rated capabilities, out of 24", average), average
        _shot(page, "matrix" if "strategic" in path else "priorities", 1440, "light")
    finally:
        context.close()


def test_both_pages_state_the_same_total_and_average(browser, live_server, demo):
    seen = []
    for path in PAGES:
        context, page = _open(browser, live_server, demo, path, 1440)
        try:
            seen.append((page.get_by_test_id("total-investment").inner_text(),
                         page.get_by_test_id("average-strategic-score").inner_text()))
        finally:
            context.close()
    assert seen[0] == seen[1]


def test_a_capability_with_no_work_says_why_and_the_step_lands_on_work_packages(browser, live_server):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    org = create_fresh_org("portfolio_manager")
    name = "Handle returns %s" % uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        db.session.add(UnifiedCapability(name=name, organization_id=org["org_id"], strategic_importance="high", level=1))
        db.session.commit()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["portfolio_manager"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("capability-row").filter(has_text=name)
        expect(row).to_have_count(1)
        cell = row.get_by_test_id("cell-investment")
        expect(cell).to_contain_text("Not estimated")
        expect(cell.get_by_test_id("investment-reason")).to_have_text(
            "No work package or funded initiative is linked to this capability yet.")
        total = page.get_by_test_id("total-investment").inner_text()
        assert "No capability has linked work" in total, total
        cell.get_by_test_id("investment-step").click()
        page.wait_for_url(re.compile(r"/implementation/work-packages"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=re.compile("Work Packages", re.I)).first).to_be_visible()
        assert len(page.locator("main").inner_text().strip()) > 100
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_phone_shows_no_cut_column_and_keeps_the_evidence_behind_a_control(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 390, scheme="dark")
    try:
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        scroller = page.get_by_test_id("matrix-scroll")
        frame = scroller.bounding_box()
        # the columns beside the name are whole, not cut at the right edge
        header = scroller.locator("thead th", has_text="Domain").bounding_box()
        assert header["x"] + header["width"] <= frame["x"] + frame["width"] + 1, (header, frame)
        row = page.get_by_test_id("capability-row").first
        domain = row.get_by_test_id("cell-domain").bounding_box()
        assert domain["x"] + domain["width"] <= frame["x"] + frame["width"] + 1, (domain, frame)
        # the evidence is closed; a control opens it, and the same control closes it
        footer = row.get_by_test_id("capability-footer")
        toggle = row.get_by_test_id("evidence-toggle")
        expect(footer).to_be_hidden()
        expect(toggle).to_have_text(re.compile("Show evidence"))
        _shot(page, "closed-" + ("matrix" if "strategic" in path else "priorities"), 390, "dark")
        assert row.get_by_test_id("cell-name").bounding_box()["height"] < 330
        toggle.click()
        expect(footer).to_be_visible()
        _shot(page, "open-" + ("matrix" if "strategic" in path else "priorities"), 390, "dark")
        for label in ("Evidence", "Source", "Confidence", "Date"):
            expect(footer).to_contain_text(label)
        expect(toggle).to_have_text(re.compile("Hide evidence"))
        toggle.click()
        expect(footer).to_be_hidden()
    finally:
        context.close()


def test_desktop_keeps_the_evidence_open_without_a_control(browser, live_server, demo):
    context, page = _open(browser, live_server, demo, PAGES[0], 1440)
    try:
        row = page.get_by_test_id("capability-row").first
        expect(row.get_by_test_id("capability-footer")).to_be_visible()
        expect(row.get_by_test_id("evidence-toggle")).to_be_hidden()
    finally:
        context.close()


READER = "drew.holloway@lantern-quay.example.com"
HELD_BACK = "Cost figures are withheld for your role."


def _owner_of_money_fixture(estimates, first_role="portfolio_manager", others=("enterprise_architect",)):
    """A new organisation of one spend person and one other person; the spend person's capabilities carry the estimates."""
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage

    org = create_fresh_org(first_role, extra_roles=others)
    names = []
    with create_app("testing").app_context():
        caps = []
        for i, _ in enumerate(estimates):
            name = "Settle claims %s %d" % (uuid.uuid4().hex[:5], i)
            cap = UnifiedCapability(name=name, organization_id=org["org_id"], strategic_importance="high", level=1)
            db.session.add(cap)
            db.session.flush()
            caps.append(cap)
            names.append(name)
        for cap, cost in zip(caps, estimates):
            db.session.add(UnifiedWorkPackage(name="Work for %s" % cap.name, organization_id=org["org_id"],
                                              capability_id=cap.id, capability_ids=[cap.id], estimated_cost=cost))
        # one package shared by the first two capabilities is split between them, never counted twice
        db.session.add(UnifiedWorkPackage(name="Shared %s" % uuid.uuid4().hex[:5], organization_id=org["org_id"],
                                          capability_id=caps[0].id, capability_ids=[caps[0].id, caps[1].id],
                                          estimated_cost=1750.0))
        db.session.commit()
    org["capabilities"] = names
    return org


@pytest.mark.parametrize("path", PAGES)
def test_a_person_without_spend_authority_reads_every_capability_and_no_money(browser, live_server, demo, path):
    context, page = _open(browser, live_server, READER, path, 1440)
    try:
        rows = page.get_by_test_id("capability-row")
        assert rows.count() == CAPABILITIES
        for i in range(CAPABILITIES):
            cell = rows.nth(i).get_by_test_id("cell-investment")
            expect(cell).to_have_text(HELD_BACK)
            footer = rows.nth(i).get_by_test_id("capability-footer").inner_text()
            assert "£" not in footer, footer
        total = page.get_by_test_id("total-investment").inner_text()
        assert "Withheld" in total and HELD_BACK in total, total
        assert "Not estimated" not in total and "£" not in total, total
        # nothing on the page, drawn or hidden, carries a pound figure
        assert "£" not in page.content()
        _shot(page, "restricted-" + ("matrix" if "strategic" in path else "priorities"), 1440, "light")
        # the capability's own page still opens from the matrix, with content
        link = rows.first.get_by_test_id("cell-name").locator("a").first
        if link.count():
            link.click()
            page.wait_for_load_state("domcontentloaded")
            assert len(page.locator("main").inner_text().strip()) > 100
    finally:
        context.close()


def test_a_restricted_phone_in_the_dark_shows_the_held_back_note_beside_each_capability(browser, live_server, demo):
    context, page = _open(browser, live_server, READER, PAGES[0], 390, scheme="dark")
    try:
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        expect(page.get_by_test_id("phone-investment").first).to_have_text(HELD_BACK)
        _shot(page, "restricted-matrix", 390, "dark")
    finally:
        context.close()


def test_the_total_is_the_sum_of_the_estimates_on_both_pages_and_nothing_of_another_organisation(browser, live_server):
    mine = _owner_of_money_fixture([12345.0, 6789.0])
    theirs = _owner_of_money_fixture([777777.0, 555555.0])
    expected = 12345.0 + 6789.0 + 1750.0
    seen = []
    for path in PAGES:
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            sign_in(page, live_server, mine["emails"]["portfolio_manager"])
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            rows = page.get_by_test_id("capability-row")
            expect(rows).to_have_count(2, timeout=PAGE_TIMEOUT)
            cells = [rows.nth(i).get_by_test_id("cell-investment").inner_text() for i in range(2)]
            # each row shows its own estimate (the shared package split evenly), the total shows their sum
            assert abs(sum(_thousands(c) for c in cells) - expected / 1000) <= 0.11, cells
            total = page.get_by_test_id("total-investment").inner_text()
            assert abs(_thousands(total) - expected / 1000) <= 0.06, (total, expected)
            assert "from 2 of 2 capabilities" in total, total
            seen.append(total)
            body = page.content()
            for figure in ("777777", "777,777", "777.8k", "555.6k", theirs["capabilities"][0]):
                assert figure not in body, (path, figure)
            if "strategic" in path:
                _shot(page, "spend-matrix", 1440, "light")
        finally:
            context.close()
    assert seen[0] == seen[1]
    # the same sum is what the roadmap timeline carries for these packages
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        from .fresh_org import api

        sign_in(page, live_server, mine["emails"]["portfolio_manager"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, body = api(page, "GET", "/strategic/api/investment-analysis")
        assert status == 200
        assert body["portfolio_metrics"]["total_estimated_investment"] == pytest.approx(expected)
    finally:
        context.close()


def test_a_person_without_spend_authority_is_given_no_figure_by_the_feed(browser, live_server):
    org = _owner_of_money_fixture([12345.0, 6789.0])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        from .fresh_org import api

        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, body = api(page, "GET", "/strategic/api/investment-analysis")
        assert status == 200
        text = str(body)
        for figure in ("12345", "6789", "1750", "20884"):
            assert figure not in text, figure
        assert body["costs_withheld"] is True
        expect(page.get_by_test_id("capability-row")).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("cell-investment").first).to_have_text(HELD_BACK)
    finally:
        context.close()


def test_a_finance_person_reads_the_figures_and_a_solution_architect_reads_the_sentence(browser, live_server):
    """Cost rule: whoever may open Spend reads the matrix's money; the solution architect of the same
    organisation reads the one sentence in its place, on the desktop in light and the phone in dark."""
    org = _owner_of_money_fixture([12345.0, 6789.0], first_role="finance", others=("solution_architect",))
    expected = 12345.0 + 6789.0 + 1750.0
    context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["finance"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("capability-row")
        expect(rows).to_have_count(2, timeout=PAGE_TIMEOUT)
        cells = [rows.nth(i).get_by_test_id("cell-investment").inner_text() for i in range(2)]
        assert abs(sum(_thousands(c) for c in cells) - expected / 1000) <= 0.11, cells
        total = page.get_by_test_id("total-investment").inner_text()
        assert abs(_thousands(total) - expected / 1000) <= 0.06, (total, expected)
        assert HELD_BACK not in total and "Withheld" not in total, total
        assert HELD_BACK not in " ".join(cells), cells
        _shot(page, "finance-matrix", 1440, "light")
    finally:
        context.close()
    context = browser.new_context(viewport={"width": 390, "height": 900}, color_scheme="dark")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("capability-row")).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("phone-investment").first).to_have_text(HELD_BACK)
        total = page.get_by_test_id("total-investment").inner_text()
        assert HELD_BACK in total and "£" not in total, total
        assert "£" not in page.content()
        for figure in ("12345", "6789", "1750", "20884"):
            assert figure not in page.content(), figure
        _shot(page, "architect-matrix", 390, "dark")
    finally:
        context.close()


@pytest.mark.parametrize("path", PAGES)
def test_show_evidence_opens_and_closes_from_the_keyboard(browser, live_server, demo, path):
    context, page = _open(browser, live_server, demo, path, 390, scheme="dark")
    try:
        row = page.get_by_test_id("capability-row").first
        toggle = row.get_by_test_id("evidence-toggle")
        footer = row.get_by_test_id("capability-footer")
        expect(footer).to_be_hidden()
        toggle.focus()
        expect(toggle).to_be_focused()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        page.keyboard.press("Enter")
        expect(footer).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(toggle).to_have_text(re.compile("Hide evidence"))
        for label in ("Evidence", "Source", "Confidence", "Date"):
            expect(footer).to_contain_text(label)
        page.keyboard.press("Space")
        expect(footer).to_be_hidden()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(toggle).to_have_text(re.compile("Show evidence"))
    finally:
        context.close()
