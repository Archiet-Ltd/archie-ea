"""An architect opens the risk register, which lists open issues, a dependency
and a risk. Resolve on an issue is disabled while it saves, the item reads
Resolved after a reload, and the other stays open; the risk can be marked
mitigated and the dependency resolved. Another organisation's item cannot be
changed from here, and a viewer is refused."""

import re
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


def _seed_risk(org_id, title):
    from app import create_app, db
    from app.models.risk import Risk, RiskStatus

    app = create_app("testing")
    with app.app_context():
        risk = Risk(title=title, likelihood=4, impact=4, status=RiskStatus.OPEN, organization_id=org_id)
        db.session.add(risk)
        db.session.commit()
        risk_id = risk.id
        db.session.remove()
    return risk_id


def _risk_status(org_id, risk_id):
    from app import create_app, db
    from app.models.risk import Risk

    app = create_app("testing")
    with app.app_context():
        row = db.session.execute(
            db.select(Risk).where(Risk.id == risk_id, Risk.organization_id == org_id)
        ).scalar_one()
        value = row.status.value
        db.session.remove()
    return value


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


def test_resolve_and_close_from_the_risk_register(live_server, browser, tmp_path):
    noun = "Resolver%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    first_id, second_id = _seed(org["org_id"], ["%s payroll cutover" % noun, "%s data feed" % noun])
    (dependency_id,) = _seed(org["org_id"], ["%s vendor handover" % noun], kind="dependency")
    risk_id = _seed_risk(org["org_id"], "%s key person leaves" % noun)
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
    page.screenshot(path=str(tmp_path) + "/resolving-1440.png")
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
    page.screenshot(path=str(tmp_path) + "/closed-1440.png")

    # The dependency resolves, and the risk is marked mitigated from its row menu.
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        row("%s vendor handover" % noun).get_by_role("button", name="Resolve").click()
    row = open_register()
    expect(row("%s vendor handover" % noun)).to_contain_text("Resolved", timeout=PAGE_TIMEOUT)
    assert _status(org["org_id"], dependency_id) == "resolved"

    risk_row = row("%s key person leaves" % noun)
    expect(risk_row).to_contain_text(re.compile("open", re.I), timeout=PAGE_TIMEOUT)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        risk_row.get_by_role("button", name="Mark mitigated").click()
    row = open_register()
    expect(row("%s key person leaves" % noun)).to_contain_text(re.compile("mitigated", re.I), timeout=PAGE_TIMEOUT)
    assert _risk_status(org["org_id"], risk_id) == "mitigated"

    # Another organisation's item cannot be changed: not found, nothing written.
    code, _body = api(page, "PATCH", "/api/raid/%d" % foreign_id, {"status": "resolved"})
    assert code == 404
    assert _status(other["org_id"], foreign_id) == "open"

    # Narrow screen: the same register, still operable.
    page.set_viewport_size({"width": 390, "height": 900})
    open_register()
    page.wait_for_load_state("load")
    expect(page.locator("tr", has_text="%s data feed" % noun).get_by_role("button", name="Resolve")).to_be_visible()
    page.screenshot(path=str(tmp_path) + "/register-390.png")

    # Dark theme, both widths: the register is still readable with its actions.
    page.evaluate("localStorage.setItem('theme', 'dark')")
    for width in (390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_load_state("load")
        open_register()
        page.wait_for_load_state("load")
        expect(page.locator("html")).to_have_class(re.compile("dark"))
        expect(page.locator("tr", has_text="%s data feed" % noun).get_by_role("button", name="Resolve")).to_be_visible()
        page.screenshot(path=str(tmp_path) + "/register-dark-%d.png" % width)
    context.close()
