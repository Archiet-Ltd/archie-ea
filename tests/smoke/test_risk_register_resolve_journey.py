"""An architect opens the risk register, which lists two open issues and
dependencies. Resolve on one is disabled while it saves, the item reads
Resolved after a reload, and the other stays open. Another organisation's item
cannot be changed from here."""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, titles, kind="issue"):
    from app import create_app, db
    from app.models.raid_item import RaidItem, RaidKind, RaidStatus

    app = create_app("testing")
    ids = []
    with app.app_context():
        for title in titles:
            item = RaidItem(kind=RaidKind(kind), title=title, status=RaidStatus.OPEN, organization_id=org_id)
            db.session.add(item)
            db.session.commit()
            ids.append(item.id)
        db.session.remove()
    return ids


def _status(org_id, item_id):
    from app import create_app, db
    from app.models.raid_item import RaidItem

    app = create_app("testing")
    with app.app_context():
        row = db.session.execute(
            db.select(RaidItem).where(RaidItem.id == item_id, RaidItem.organization_id == org_id)
        ).scalar_one()
        value = row.status.value
        db.session.remove()
    return value


def test_resolve_and_close_from_the_risk_register(live_server, browser):
    noun = "Resolver%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    first_id, second_id = _seed(org["org_id"], ["%s payroll cutover" % noun, "%s data feed" % noun])
    (foreign_id,) = _seed(other["org_id"], ["%s other org item" % noun])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, org["emails"]["enterprise_architect"])

    def open_register():
        page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        return lambda title: page.locator("tr", has_text=title)

    row = open_register()
    first = row("%s payroll cutover" % noun)
    second = row("%s data feed" % noun)
    expect(first).to_contain_text("Open", timeout=PAGE_TIMEOUT)
    expect(second).to_contain_text("Open")
    expect(page.locator("body")).not_to_contain_text("other org item")

    # Hold the save so the disabled state can be seen.
    release = []

    def hold(route):
        if release:
            route.continue_()
        else:
            release.append(route)

    page.route("**/api/raid/%d" % first_id, hold)
    first.get_by_role("button", name="Resolve").click()
    expect(first.get_by_role("button", name="Resolve")).to_be_disabled()
    page.screenshot(path="/home/ubuntu/verify/seats/riskreg01/walk/resolving-1440.png")
    page.wait_for_timeout(500)
    assert release, "Resolve sent no request"
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        release[0].continue_()

    # After a reload the item reads Resolved, the other is still open.
    row = open_register()
    first = row("%s payroll cutover" % noun)
    expect(first).to_contain_text("Resolved", timeout=PAGE_TIMEOUT)
    expect(row("%s data feed" % noun)).to_contain_text("Open")
    assert _status(org["org_id"], first_id) == "resolved"
    assert _status(org["org_id"], second_id) == "open"

    # A resolved item offers Close, and closing it persists.
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        first.get_by_role("button", name="Close").click()
    row = open_register()
    expect(row("%s payroll cutover" % noun)).to_contain_text("Closed", timeout=PAGE_TIMEOUT)
    assert _status(org["org_id"], first_id) == "closed"
    page.screenshot(path="/home/ubuntu/verify/seats/riskreg01/walk/closed-1440.png")

    # Another organisation's item cannot be changed: not found, nothing written.
    code, _body = api(page, "PATCH", "/api/raid/%d" % foreign_id, {"status": "resolved"})
    assert code == 404
    assert _status(other["org_id"], foreign_id) == "open"

    # Narrow screen: the same register, still operable.
    page.set_viewport_size({"width": 390, "height": 900})
    open_register()
    page.wait_for_load_state("load")
    expect(page.locator("tr", has_text="%s data feed" % noun).get_by_role("button", name="Resolve")).to_be_visible()
    page.screenshot(path="/home/ubuntu/verify/seats/riskreg01/walk/register-390.png")

    # Dark theme, both widths: the register is still readable with its actions.
    page.evaluate("localStorage.setItem('theme', 'dark')")
    for width in (390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_load_state("load")
        open_register()
        page.wait_for_load_state("load")
        expect(page.locator("html")).to_have_class(__import__("re").compile("dark"))
        expect(page.locator("tr", has_text="%s data feed" % noun).get_by_role("button", name="Resolve")).to_be_visible()
        page.screenshot(path="/home/ubuntu/verify/seats/riskreg01/walk/register-dark-%d.png" % width)
    context.close()
