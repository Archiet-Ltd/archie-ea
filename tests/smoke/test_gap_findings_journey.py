"""Gap findings: a saving is shown only where recorded costs allow one, and every overlapping application is a link.

Goal: "I am a portfolio manager. I open Gap findings, see which applications overlap behind a capability, and read
a saving only when every one of those applications has an annual cost recorded; otherwise I read why there is no
figure. When I record the costs through the application's own form, the saving appears and equals the cost of
every application except the most expensive."

A second organisation carries an overlap of its own; none of its names shows here.
"""
import os
import pathlib
import tempfile
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import open_page
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="gap-findings-"))
GAPS = "/applications/rationalization/gaps?gaps=rationalization"


def _seed_overlap(org, stem):
    """A capability the organisation models, served by two applications that record no cost."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    tag = uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        record = BusinessCapability(name="%s capability %s" % (stem, tag), organization_id=org["org_id"])
        db.session.add(record)
        db.session.flush()
        db.session.commit()
        # saving the business capability projects its unified record (the one the gap findings read)
        capability = UnifiedCapability.query.filter_by(
            source_table="business_capability", source_id=str(record.id)).one()
        apps = [ApplicationComponent(name="%s %s %s" % (stem, word, tag), organization_id=org["org_id"],
                                     lifecycle_status="operational") for word in ("Ledger", "Books")]
        db.session.add_all(apps)
        db.session.flush()
        for row in apps:
            db.session.add(UnifiedApplicationCapabilityMapping(
                unified_capability_id=capability.id, application_component_id=row.id, is_active=True))
        db.session.commit()
        return {"capability": record.name, "record_id": record.id, "app_ids": [a.id for a in apps],
                "app_names": [a.name for a in apps]}


@pytest.fixture(scope="module")
def organisations(live_server):
    mine = create_fresh_org("portfolio_manager")
    theirs = create_fresh_org("portfolio_manager")
    mine["seed"] = _seed_overlap(mine, "Mine")
    theirs["seed"] = _seed_overlap(theirs, "Theirs")
    return mine, theirs


def _row(page, capability):
    return page.locator('[data-testid="rationalization-opportunity-row"]', has_text=capability)


def _record_cost(page, live_server, app_id, amount):
    page.goto("%s/applications/%d/edit" % (live_server, app_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#total_cost_of_ownership", str(amount))
    page.get_by_role("button", name="Save Changes").click()
    page.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)


def test_a_portfolio_manager_reads_no_invented_saving_and_then_the_worked_out_one(
        browser, live_server, organisations):
    mine, theirs = organisations
    seed = mine["seed"]
    context, page = open_page(browser, live_server, mine["emails"]["portfolio_manager"], 1440, "light")
    try:
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = _row(page, seed["capability"])
        assert row.count() == 1
        assert theirs["seed"]["capability"] not in page.locator("main").inner_text()

        # the capability is a link to its record, which has content
        row.locator('[data-testid="gap-capability-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert seed["capability"] in page.locator("main").inner_text()
        page.go_back()

        # each overlapping application is a link to its own record
        row = _row(page, seed["capability"])
        links = row.locator('[data-testid="gap-application-link"]')
        assert links.count() == 2
        for name, app_id in zip(seed["app_names"], seed["app_ids"]):
            link = row.locator('[data-testid="gap-application-link"]', has_text=name)
            assert link.get_attribute("href").endswith("/applications/%d" % app_id)
            link.click()
            page.wait_for_load_state("domcontentloaded")
            assert page.locator("h1").first.inner_text().strip() == name
            page.go_back()
            row = _row(page, seed["capability"])

        # no cost recorded: no figure, and the reason says so with the real counts
        text = row.inner_text()
        assert "150,000" not in text and "50,000" not in text
        assert "Not worked out: 2 of 2 applications have no annual cost recorded" in text
        page.screenshot(path=str(SHOTS / "gap-findings-no-cost-1440-light.png"), full_page=True)

        # one cost recorded: still no figure
        _record_cost(page, live_server, seed["app_ids"][0], 120000)
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "Not worked out: 1 of 2 applications have no annual cost recorded" in _row(
            page, seed["capability"]).inner_text()

        # both recorded: the saving is the cost of all but the most expensive
        _record_cost(page, live_server, seed["app_ids"][1], 45000)
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = _row(page, seed["capability"])
        assert "45,000" in row.locator('[data-testid="gap-saving"]').inner_text()
        assert "Estimated" in row.inner_text()
        assert "Not worked out" not in row.inner_text()
        page.screenshot(path=str(SHOTS / "gap-findings-saving-1440-light.png"), full_page=True)
    finally:
        context.close()


@pytest.mark.parametrize("width,scheme", [(1440, "dark"), (390, "light")])
def test_the_page_reads_at_the_sizes_a_person_uses(browser, live_server, organisations, width, scheme):
    mine, _ = organisations
    context, page = open_page(browser, live_server, mine["emails"]["portfolio_manager"], width, scheme)
    try:
        page.goto(live_server + GAPS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.screenshot(path=str(SHOTS / ("gap-findings-%d-%s.png" % (width, scheme))), full_page=True)
        page.locator('[data-testid="rationalization-opportunities-table-pager"]').screenshot(
            path=str(SHOTS / ("gap-findings-table-%d-%s.png" % (width, scheme))))
        search = page.locator('[data-testid="rationalization-opportunities-table-pager"] input[type=search]')
        placeholder = search.get_attribute("placeholder")
        box = search.bounding_box()
        # the filter shows its whole placeholder: the text fits the box it is drawn in
        fits = search.evaluate(
            """(el) => { const c = document.createElement('canvas').getContext('2d');
                         const s = getComputedStyle(el); c.font = s.font;
                         return c.measureText(el.placeholder).width <= el.clientWidth
                                - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight); }""")
        assert fits, (placeholder, box)
        if width == 390:
            assert box["width"] >= 0.75 * width, box
            # the saving column sits inside the table frame, which scrolls on its own rather than the page
            frame = page.locator('[data-slot="table-container"]').first
            head = page.locator('[data-testid="rationalization-opportunities-table"] th', has_text="Estimated saving")
            frame_box, head_box = frame.bounding_box(), head.bounding_box()
            reachable = frame.evaluate("(el) => el.scrollWidth > el.clientWidth")
            assert reachable or head_box["x"] + head_box["width"] <= frame_box["x"] + frame_box["width"] + 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            # the sub-navigation keeps the open tab in view
            tab = page.locator('[data-testid="rationalization-gaps-tab"]')
            tab_box = tab.bounding_box()
            assert 0 <= tab_box["x"] and tab_box["x"] + tab_box["width"] <= 390 + 1, tab_box
    finally:
        context.close()
