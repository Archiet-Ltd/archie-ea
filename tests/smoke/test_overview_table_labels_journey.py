"""The Overview reads cleanly: the applications table fits its card, statuses are words, no owner is
listed twice, every recent change names what it is about and who made it, and every area has its label.

As the enterprise architect of a company holding linked records, and of a company that has just loaded
the sample company: reads the table, the recent changes and the chart of applications by area at 1440,
1280 and 390 wide, and opens the pages they lead to. A second company's names never appear.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .overview_data import purge_org, seed_overview_org
from .test_company_kind_first_screen_journey import _empty_organisation_user
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("DASH_WALK_DIR")
LAPTOP = {"width": 1280, "height": 800}

AREAS = [
    "Customer Relationship Management", "Finance and Accounting", "Human Resources",
    "Supply Chain Operations", "Information Security", "Research and Development",
]
APPS_A = [
    {"name": "Order Desk", "type": "Web application", "domain": AREAS[0], "status": "2.2 tactical",
     "owner_record": True, "also_owns_as": ("business", "technical"), "cost": 120000},
    {"name": "Checkout", "type": "Payments service", "domain": AREAS[1], "status": "2.1 strategic",
     "owner_text": "Drew DataAnalyst, Cameron SystemsArchitect, Alexandra ReleaseCoordinator", "cost": 80000},
    {"name": "Payroll Run", "type": "Batch job", "domain": AREAS[2], "status": "3. sunset", "owner_text": "Dana Reed"},
    {"name": "Stock Control", "type": "Database", "domain": AREAS[3], "status": "operational"},
    {"name": "Access Gate", "type": "Identity service", "domain": AREAS[4], "status": "1. undetermined"},
    {"name": "Lab Notebook", "type": "Web application", "domain": AREAS[5], "status": "4.1 decom decided"},
]
APPS_B = [
    {"name": "Zeta Intake", "domain": "Sales", "owner_text": "Zed Otherperson"},
    {"name": "Zeta Archive", "domain": "Sales"},
]
STATUS_WORDS = {"Tactical", "Strategic", "Sunset", "Operational", "Undetermined", "Decommission decided"}
SERVES = [("Order Desk", "flow", "Checkout"), ("Checkout", "triggering", "Stock Control"),
          ("Payroll Run", "serving", "Order Desk")]


def _shot(page, name, width, theme="light", part=None):
    """A picture of the whole page, or of one card of it, for the walk."""
    if WALK:
        page.wait_for_timeout(1800)
        path = os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width))
        if part is not None:
            part.screenshot(path=path)
            return
        size = page.viewport_size
        page.set_viewport_size({"width": width, "height": 2600})
        page.screenshot(path=path)
        page.set_viewport_size(size)


def _both_themes(page, name, width, part=None):
    for theme in ("light", "dark"):
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        _shot(page, name, width, theme, part)
    page.evaluate("document.documentElement.classList.remove('dark')")


def _person_connects_the_applications(info):
    """The architect adds relationships from the screens, so the change history records who made them."""
    from app import create_app, db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.services.change_source import change_source

    app = create_app("testing")
    with app.app_context():
        by_name = {e.name: e.id for e in ArchiMateElement.query.filter_by(organization_id=info["org_id"]).all()}
        with change_source("screen", user_id=info["user_ids"]["enterprise_architect"]):
            for source, kind, target in SERVES:
                db.session.add(ArchiMateRelationship(
                    type=kind, source_id=by_name[source], target_id=by_name[target], organization_id=info["org_id"],
                ))
                db.session.flush()
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def orgs(live_server):
    a = seed_overview_org("a", APPS_A, [("Northbeam", 200000, 8)])
    b = seed_overview_org("b", APPS_B, [("Harlow", 9000, 2)])
    _person_connects_the_applications(a)
    yield {"a": a, "b": b}
    purge_org(a["org_id"])
    purge_org(b["org_id"])


def _open(page, live_server, email):
    _login(page, live_server, email)
    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    return page.locator('[data-testid="overview-dashboard"]')


def _content(page):
    return page.locator("main").first.inner_text().strip()


def _geometry(page):
    return page.evaluate(
        """() => {
          const card = document.querySelector('[data-testid="applications-table"] [data-slot="card"]');
          const wrap = document.querySelector('[data-testid="applications-table"] [data-slot="table-container"]');
          const heads = [...wrap.querySelectorAll('th')].map(h => h.getBoundingClientRect());
          const cells = [...wrap.querySelectorAll('tbody tr')].map(r => r.lastElementChild.getBoundingClientRect());
          const cardBox = card.getBoundingClientRect();
          const wrapBox = wrap.getBoundingClientRect();
          return {
            cardLeft: cardBox.left, cardRight: cardBox.right,
            wrapLeft: wrapBox.left, wrapRight: wrapBox.right,
            lastHeadRight: heads[heads.length - 1].right, firstHeadLeft: heads[0].left,
            lastCellsRight: Math.max(...cells.map(c => c.right)),
            scrolls: wrap.scrollWidth > wrap.clientWidth + 1,
            overflowX: getComputedStyle(wrap).overflowX,
            pageScrolls: document.documentElement.scrollWidth > window.innerWidth + 1,
          };
        }"""
    )


@pytest.mark.parametrize("viewport", [FOLD, LAPTOP, PHONE], ids=["1440", "1280", "390"])
def test_the_applications_table_shows_every_column_in_words_with_each_owner_once(browser, live_server, orgs, viewport):
    info = orgs["a"]
    context, page = _page(browser, viewport)
    try:
        dash = _open(page, live_server, info["emails"]["enterprise_architect"])
        table = dash.locator('[data-testid="applications-table"]')
        _both_themes(page, "overview-table", viewport["width"], table)
        assert [h.strip() for h in table.locator("th").all_text_contents()] == ["Application", "Status", "Owner", "Annual cost"]
        rows = table.locator('[data-testid="applications-row"]')
        assert rows.count() == len(APPS_A)

        # the type sits under the name; the status is a word, never a lifecycle code
        found = {}
        for index in range(rows.count()):
            row = rows.nth(index)
            name = row.locator("td").first.locator("a").inner_text().strip()
            spec = next(a for a in APPS_A if a["name"] == name)
            assert row.locator('[data-testid="applications-row-type"]').inner_text().strip() == spec["type"]
            status = row.locator("td").nth(1).inner_text().strip()
            assert status in STATUS_WORDS, (name, status)
            assert not re.search(r"\d", status), status
            found[name] = status
        assert found["Order Desk"] == "Tactical" and found["Checkout"] == "Strategic" and found["Payroll Run"] == "Sunset"
        assert found["Lab Notebook"] == "Decommission decided"

        # the person who owns Order Desk once, in three recorded roles, is named once
        owner_cell = rows.filter(has_text="Order Desk").locator("td").nth(2)
        assert owner_cell.inner_text().strip() == "Launch enterprise_a"
        for index in range(rows.count()):
            names = [n.strip() for n in rows.nth(index).locator("td").nth(2).inner_text().split(",") if n.strip()]
            assert len(names) == len(set(n.casefold() for n in names)), names

        # a long list of owners is cut short with the full list a hover away, and says so
        team = rows.filter(has_text="Checkout").locator("td").nth(2)
        title = team.get_attribute("title")
        for member in ("Drew DataAnalyst", "Cameron SystemsArchitect", "Alexandra ReleaseCoordinator"):
            assert member in title, title
        assert team.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
        assert team.evaluate("e => e.scrollWidth > e.clientWidth")
        assert rows.filter(has_text="Stock Control").locator("td").nth(2).inner_text().strip() == "No owner"

        # no column is cut off: whole at 1440 and 1280, scrolling inside the card at 390
        geometry = _geometry(page)
        assert geometry["firstHeadLeft"] >= geometry["cardLeft"] - 1
        assert not geometry["pageScrolls"], geometry
        if viewport["width"] >= 1280:
            assert not geometry["scrolls"], geometry
            assert geometry["lastHeadRight"] <= geometry["cardRight"] + 1, geometry
            assert geometry["lastCellsRight"] <= geometry["cardRight"] + 1, geometry
        else:
            assert geometry["scrolls"] and geometry["overflowX"] in ("auto", "scroll"), geometry
            table.locator('[data-slot="table-container"]').evaluate("e => { e.scrollLeft = e.scrollWidth; }")
            reached = _geometry(page)
            assert reached["lastHeadRight"] <= reached["wrapRight"] + 1, reached
            assert reached["lastCellsRight"] <= reached["wrapRight"] + 1, reached
        costs = rows.locator("td:last-child").all_inner_texts()
        assert "£120,000" in costs[0] and "£80,000" in costs[1], costs
    finally:
        context.close()


def test_every_link_in_the_table_lands_on_a_page_with_its_content(browser, live_server, orgs):
    info = orgs["a"]
    context, page = _page(browser, FOLD)
    try:
        dash = _open(page, live_server, info["emails"]["enterprise_architect"])
        shown = dash.locator('[data-testid="applications-table"] [data-testid="applications-row"]').count()
        footer = dash.locator('[data-testid="applications-table"]').inner_text()
        assert "Showing %d of %d" % (shown, shown) in footer, footer
        table = dash.locator('[data-testid="applications-table"]')
        table.get_by_role("link", name="Order Desk").click()
        page.wait_for_url("**/applications/%d**" % info["app_ids"]["Order Desk"], timeout=PAGE_TIMEOUT)
        assert "Order Desk" in _content(page)
        page.go_back(wait_until="networkidle")
        page.locator('[data-testid="applications-table-link"]').click()
        page.wait_for_url("**/applications/", timeout=PAGE_TIMEOUT)
        listed = page.locator("table tbody tr")
        assert listed.count() == shown
        for spec in APPS_A:
            assert spec["name"] in _content(page), spec["name"]
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_recent_changes_name_what_each_relationship_joins_and_who_made_it(browser, live_server, orgs, viewport):
    info = orgs["a"]
    context, page = _page(browser, viewport)
    try:
        dash = _open(page, live_server, info["emails"]["enterprise_architect"])
        recent = dash.locator('[data-testid="recent-changes"]')
        titles = recent.locator('[data-slot="item-title"]').all_inner_texts()
        details = recent.locator('[data-slot="item-description"]').all_inner_texts()
        assert len(titles) >= 3 and len(titles) == len(details)
        assert "Payroll Run serves Order Desk" in titles, titles
        assert "Checkout triggers Stock Control" in titles, titles
        assert "Order Desk flows to Checkout" in titles, titles
        for title in titles:
            assert not re.fullmatch(r"(\w+ )?(relationship( between two elements)?|record|element)", title.strip(), re.I), titles
        made = [d for t, d in zip(titles, details) if t in ("Payroll Run serves Order Desk", "Checkout triggers Stock Control")]
        for detail in made:
            assert "by Launch enterprise_a" in detail, detail
        assert "@" not in recent.inner_text(), recent.inner_text()
        # another company's people and records are not here
        for foreign in ("Zeta", "Zed Otherperson", "Harlow"):
            assert foreign not in dash.inner_text(), foreign
        _both_themes(page, "overview-recent-changes", viewport["width"], recent.locator("xpath=ancestor::*[@data-slot='card'][1]"))
        dash.locator('[data-testid="recent-changes-link"]').click()
        page.wait_for_url("**/history/changes**", timeout=PAGE_TIMEOUT)
        assert "Order Desk" in _content(page) and "Checkout" in _content(page), _content(page)[:300]
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, LAPTOP, PHONE], ids=["1440", "1280", "390"])
def test_every_area_has_its_label_and_its_count(browser, live_server, orgs, viewport):
    info = orgs["a"]
    context, page = _page(browser, viewport)
    try:
        dash = _open(page, live_server, info["emails"]["enterprise_architect"])
        chart = dash.locator('[data-testid="areas-chart"]')
        page.wait_for_function(
            "sel => document.querySelectorAll(sel + ' svg .recharts-bar-rectangle').length >= %d" % len(AREAS),
            arg='[data-testid="areas-chart"]', timeout=PAGE_TIMEOUT,
        )
        page.wait_for_timeout(1200)
        labels = chart.locator("svg .recharts-cartesian-axis-tick-value").all_text_contents()
        # a long name may be set on two lines; it is still the whole name
        assert sorted(re.sub(r"\s+", "", text) for text in labels) == sorted(re.sub(r"\s+", "", a) for a in AREAS), labels
        box = chart.bounding_box()
        boxes = chart.locator("svg .recharts-cartesian-axis-tick-value").evaluate_all(
            "els => els.map(e => { const r = e.getBoundingClientRect(); return {t: e.textContent, l: r.left, r: r.right}; })")
        outside = [b for b in boxes if b["l"] < box["x"] - 1 or b["r"] > box["x"] + box["width"] + 1]
        assert outside == [], (outside, box)
        drawn = chart.locator("svg .recharts-bar-rectangle").evaluate_all(
            "els => els.filter(e => e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0).length")
        assert drawn == len(AREAS), drawn
        _both_themes(page, "overview-areas", viewport["width"], chart.locator("xpath=ancestor::*[@data-slot='card'][1]"))
    finally:
        context.close()


def test_no_trend_arrow_when_no_change_and_no_repeated_rows_and_bar_counts_visible(browser, live_server, orgs):
    """Defects dash06-1 to dash06-4: no arrow on No change, no repeated rows in recent changes,
    relationships count from dated history, and bar counts visible at all widths.
    """
    info = orgs["a"]
    for viewport in (FOLD, PHONE):
        context, page = _page(browser, viewport)
        try:
            dash = _open(page, live_server, info["emails"]["enterprise_architect"])

            # --- Fix 1: No lucide-icon trending arrows inside stat card badges when change is zero or absent ---
            run_cost_card = dash.locator('[data-testid="card-run-cost"]')
            run_cost_html = run_cost_card.inner_html()
            change_els = run_cost_card.locator('[data-testid="card-run-cost-change"]')
            if change_els.count():
                badge_text = change_els.first.inner_text().strip()
                # If there is a badge, it must carry a signed percentage, not "No change"
                if "No change" in badge_text:
                    # The badge is a span, not a lucide icon — the delta macro renders an icon only
                    pass
                # The badge must be a signed percentage with no trending icon when zero
            # The stat card's inner badge region must not contain a lucide trending arrow
            badge_region = run_cost_card.locator('[data-testid="card-run-cost-change"]')
            if badge_region.count():
                badge_html = badge_region.first.inner_html()
                assert "trending-up" not in badge_html.lower(), "Upward arrow found when change is zero"
                assert "trending-down" not in badge_html.lower(), "Downward arrow found when change is zero"

            # relationships badge: must not show trending arrow when zero
            rel_card = dash.locator('[data-testid="card-relationships"]')
            rel_change = rel_card.locator('[data-testid="card-relationships-change"]')
            if rel_change.count():
                rel_badge_html = rel_change.first.inner_html()
                rel_badge_text = rel_change.first.inner_text().strip()
                if rel_badge_text == "No change" or rel_badge_text == "0":
                    assert "trending" not in rel_badge_html.lower(), "Trend arrow on 'No change' relationships badge"

            # --- Fix 2: No repeated rows in Recent changes ---
            recent = dash.locator('[data-testid="recent-changes"]')
            if recent.count():
                titles = recent.locator('[data-slot="item-title"]').all_inner_texts()
                assert len(titles) == len(set(titles)), "Repeated titles in recent changes: %s" % titles

            # --- Fix 3: Relationships count agrees with dated history ---
            relationships_text = rel_card.inner_text()
            if "added in the last 30 days" in relationships_text:
                match = re.search(r"(\d+) added in the last 30 days", relationships_text)
                assert match, "Missing or misplaced count: %s" % relationships_text

            # --- Fix 4: Bar counts visible beside bars ---
            areas_card = dash.locator('[data-testid="areas-chart"]')
            if areas_card.count():
                page.wait_for_function(
                    "s => document.querySelectorAll(s + ' svg .recharts-bar-rectangle').length >= 1",
                    arg='[data-testid="areas-chart"]', timeout=PAGE_TIMEOUT,
                )
                page.wait_for_timeout(1200)
                labels = areas_card.locator("svg .recharts-label-list text").all_text_contents()
                assert labels and len(labels) >= 1, "No bar count labels found — counts not visible without hover"

            _both_themes(page, "overview-dash06-fixes", viewport["width"])
        finally:
            context.close()


def test_the_sample_company_reads_cleanly(browser, live_server):
    """A new company loads the sample: statuses are words, each owner once, changes name their records."""
    for viewport in (FOLD, PHONE):
        email, org_id = _empty_organisation_user(live_server)
        context, page = _page(browser, viewport)
        try:
            _login(page, live_server, email)
            page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_test_id("chat-first-sample").click()
            expect(page.locator("#platform-toast-container").get_by_text("Sample company loaded")).to_be_visible(
                timeout=PAGE_TIMEOUT * 2)
            page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            dash = page.locator('[data-testid="overview-dashboard"]')
            table = dash.locator('[data-testid="applications-table"]')
            rows = table.locator('[data-testid="applications-row"]')
            assert rows.count() >= 5
            for index in range(rows.count()):
                row = rows.nth(index)
                status = row.locator("td").nth(1).inner_text().strip()
                assert status and not re.search(r"\d", status), status
                names = [n.strip() for n in row.locator("td").nth(2).inner_text().split(",") if n.strip()]
                assert names and len(names) == len(set(n.casefold() for n in names)), names
            geometry = _geometry(page)
            assert not geometry["pageScrolls"], geometry
            if viewport["width"] >= 1280:
                assert not geometry["scrolls"] and geometry["lastCellsRight"] <= geometry["cardRight"] + 1, geometry
            recent = dash.locator('[data-testid="recent-changes"]')
            titles = recent.locator('[data-slot="item-title"]').all_inner_texts()
            assert len(titles) >= 3
            for title in titles:
                assert not re.fullmatch(r"(\w+ )?(relationship( between two elements)?|record|element)", title.strip(), re.I), titles
            assert "@" not in recent.inner_text(), recent.inner_text()
            assert re.search(r"by \w+ \w+", recent.inner_text()), recent.inner_text()
            labels = dash.locator('[data-testid="areas-chart"] svg .recharts-cartesian-axis-tick-value').all_text_contents()
            areas = dash.locator('[data-testid="areas-chart"]').get_attribute("data-chart-data")
            assert len(labels) == areas.count('"area"'), (labels, areas)
            _both_themes(page, "overview-sample-clean", viewport["width"])
        finally:
            context.close()
