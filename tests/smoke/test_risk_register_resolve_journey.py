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


def _make_viewer(org_id):
    """A read-only person in an existing organisation; returns the email."""
    from app import create_app, db
    from app.models.user import Role, User

    from .fresh_org import PASSWORD

    app = create_app("testing")
    email = "viewer.%s@example.com" % uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        user = User(email=email, first_name="Read", last_name="Only", organization_id=org_id,
                    enterprise_role="procurement", confirmed=True)
        user.role = Role.query.filter_by(name="Viewer").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        db.session.remove()
    return email


def test_a_viewer_sees_the_register_with_no_write_action_and_cannot_write(live_server, browser, tmp_path):
    noun = "Viewable%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    (issue_id,) = _seed(org["org_id"], ["%s payroll cutover" % noun])
    risk_id = _seed_risk(org["org_id"], "%s key person leaves" % noun)
    viewer = _make_viewer(org["org_id"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, viewer)
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        issue_row = page.locator("tr", has_text="%s payroll cutover" % noun)
        expect(issue_row).to_contain_text("Open", timeout=PAGE_TIMEOUT)
        expect(page.locator("tr", has_text="%s key person leaves" % noun)).to_be_visible()
        expect(page.get_by_text("You can view this register. Ask an administrator for edit access.")).to_be_visible()
        for name in ("Resolve", "Close", "Add Risk", "Add item", "Mark mitigated", "Edit", "Delete"):
            expect(page.get_by_role("button", name=name)).to_have_count(0)
        page.screenshot(path=str(tmp_path) + "/viewer-%d.png" % width)

    # The view action still lands on the risk with its content.
    page.goto(live_server + "/risks/?risk=%d" % risk_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("%s key person leaves" % noun).first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("button", name="Delete")).to_have_count(0)

    # Asking the interface directly is refused and nothing is written.
    code, _ = api(page, "PATCH", "/api/raid/%d" % issue_id, {"status": "resolved"})
    assert code == 403
    code, _ = api(page, "PATCH", "/api/risks/%d" % risk_id, {"status": "closed"})
    assert code == 403
    code, _ = api(page, "DELETE", "/api/risks/%d" % risk_id)
    assert code == 403
    assert _status(org["org_id"], issue_id) == "open"
    assert _risk_status(org["org_id"], risk_id) == "open"
    context.close()
