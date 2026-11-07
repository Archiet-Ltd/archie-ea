"""The model keeps itself current from GitHub, in a real browser.

The person is the administrator of an organisation that connected GitHub. The scheduled job reads
the connection without anyone pressing Sync: the connectors page says when it read, what changed
and when it reads next; a change at the source (the payments repository moves to another language
and is pushed again) reaches the application's fact sheet with its new value and date after the
next scheduled read; and a second organisation sees none of it.

GitHub is never called: the recorded organisation of the connector suite is served in place of it.
"""

import os
import re
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations
from tests.test_github_connector import github  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("REREAD_JOURNEY_SHOTS")


@pytest.fixture(scope="module")
def server(request, app):
    return boot_live_server(request, None, app)


@pytest.fixture(scope="module")
def world(server, app):
    """Two organisations. The first holds a GitHub connection and the Payments Service
    application the payments repository is read into; the second holds nothing."""
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.application_portfolio import ApplicationComponent
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.archimate_backbone import create_backbone_element

    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    three = create_fresh_org("solution_architect", org_admin=True)
    with app.app_context():
        # The first organisation has delegated syncs; the third has not and holds a connection too.
        from app.models.organization import Organization

        db.session.get(Organization, one["org_id"]).settings = {"connector_action_delegation": {"sync": True}}
        db.session.commit()
        with tenant_scope(three["org_id"]):
            held = ConnectorConfig(
                organization_id=three["org_id"], connector_type="github", name="GitHub (needs approval)",
                config={"organization": "acme"}, status="active",
            )
            db.session.add(held)
            db.session.flush()
            OrgCredentialVault().store(three["org_id"], "github", "api_token", "placeholder-read-only-token")
            db.session.commit()
            held_id = held.id
        db.session.remove()
        with tenant_scope(one["org_id"]):
            element = create_backbone_element(
                element_type="ApplicationComponent", layer="Application", name="Payments Service",
                organization_id=one["org_id"],
            )
            component = ApplicationComponent(
                name="Payments Service", organization_id=one["org_id"], archimate_element_id=element.id
            )
            db.session.add(component)
            cfg = ConnectorConfig(
                organization_id=one["org_id"], connector_type="github", name="GitHub",
                config={"organization": "acme"}, status="active",
            )
            db.session.add(cfg)
            db.session.flush()
            OrgCredentialVault().store(one["org_id"], "github", "api_token", "placeholder-read-only-token")
            db.session.commit()
            connector_id, component_id = cfg.id, component.id
        db.session.remove()
    yield {
        "base": str(server), "one": one, "two": two, "three": three,
        "connector_id": connector_id, "held_id": held_id, "component_id": component_id,
    }
    remove_organisations(app, [one["org_id"], two["org_id"], three["org_id"]])


def shoot(page, name):
    """The connectors table at 1440 wide in light and 390 wide in dark, for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.reload(wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        table = page.locator("table").first
        table.scroll_into_view_if_needed()
        table.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)))
    page.evaluate("document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def run_the_job(app, org_id, now=None):
    """What the scheduler's ten-minute tick does for one organisation."""
    from app.jobs.tenant_safe_job import tenant_scope
    from app.services.connector_schedule import reread_due_connectors

    with app.app_context():
        with tenant_scope(org_id):
            return reread_due_connectors(org_id, now=now)


def signed_in(browser, world, who):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world[who]["emails"]["solution_architect"])
    return context, page


def age_the_connection(app, org_id, connector_id, days):
    """Let time pass: everything the first read recorded is `days` older than it was."""
    from sqlalchemy import text

    from app import db

    with app.app_context():
        ids = {"o": org_id, "c": connector_id, "d": "%d days" % days}
        db.session.execute(
            text("UPDATE external_identity_crosswalk SET last_seen = last_seen - CAST(:d AS interval), "
                 "first_seen = first_seen - CAST(:d AS interval) WHERE organization_id = :o AND source_system = 'github'"),
            ids,
        )
        db.session.execute(
            text("UPDATE sync_logs SET started_at = started_at - CAST(:d AS interval), "
                 "completed_at = completed_at - CAST(:d AS interval) WHERE connector_id = :c"),
            ids,
        )
        db.session.commit()
        db.session.remove()


def seen_dates(page):
    block = page.get_by_test_id("external-identifiers")
    expect(block).to_contain_text("acme/payments-service")
    return block.inner_text()


def test_the_connection_is_read_again_by_itself_and_the_page_says_so(browser, app, world, github):
    today = datetime.utcnow()
    context, page = signed_in(browser, world, "one")
    try:
        page.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
        expect(page.get_by_test_id("connector-what-changed")).to_have_count(0)  # never read yet
        expect(page.get_by_text("Never", exact=True)).to_be_visible()
        expect(page.get_by_test_id("connector-next-read")).to_have_text("Next read within ten minutes")

        # The job runs. Nobody presses Sync.
        first = run_the_job(app, world["one"]["org_id"])
        assert first["read"] == [world["connector_id"]], first

        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("connector-what-changed")).to_contain_text("Read 12 repositories")
        expect(page.get_by_test_id("connector-schedule")).to_have_text("Read again every hour")
        expect(page.get_by_test_id("connector-next-read")).to_contain_text(
            re.compile(r"Next read within ten minutes after \d\d:\d\d UTC on \d{4}-\d\d-\d\d")
        )
        shoot(page, "connectors-after-first-read")

        # The application's page cites the repository and the day it was read.
        element_page = world["base"] + "/architecture/application/ApplicationComponent/%d" % world["component_id"]
        page.goto(element_page, wait_until="domcontentloaded")
        assert today.strftime("%d %b %Y") in seen_dates(page)
        expect(page.get_by_test_id("source-facts")).to_contain_text("Main language at the source")
        assert "Rust" not in page.get_by_test_id("source-facts").inner_text()

        # Three days pass; the payments repository moves to another language and is pushed again.
        age_the_connection(app, world["one"]["org_id"], world["connector_id"], 3)
        page.reload(wait_until="domcontentloaded")
        assert today.strftime("%d %b %Y") not in seen_dates(page)
        for repo in github.bundle["orgs/acme/repos"]:
            if repo["name"] == "payments-service":
                repo["language"], repo["pushed_at"] = "Rust", "2026-11-05T09:00:00Z"
        second = run_the_job(app, world["one"]["org_id"])
        assert second["read"] == [world["connector_id"]], second

        page.reload(wait_until="domcontentloaded")
        assert today.strftime("%d %b %Y") in seen_dates(page)  # the new read date
        # and the person reading the application sees the changed fact itself, not only its date
        expect(page.get_by_test_id("source-facts")).to_contain_text("Rust")
        expect(page.get_by_test_id("source-facts")).to_contain_text("2026-11-05T09:00:00Z")
        assert _fact(app, world, "github_main_language") == "Rust"
        assert _fact(app, world, "github_last_activity") == "2026-11-05T09:00:00Z"

        page.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
        expect(page.get_by_test_id("connector-what-changed")).to_have_text("Read 12 repositories; 0 new, 1 changed")
        expect(page.get_by_test_id("connector-next-read")).to_contain_text("Next read")
        shoot(page, "connectors-after-second-read")

        # A third read with nothing changed at the source says so and writes nothing.
        age_the_connection(app, world["one"]["org_id"], world["connector_id"], 1)
        third = run_the_job(app, world["one"]["org_id"])
        assert third["read"] == [world["connector_id"]], third
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("connector-what-changed")).to_have_text("Read 12 repositories; nothing changed")
    finally:
        context.close()


def _fact(app, world, key):
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    with app.app_context():
        with tenant_scope(world["one"]["org_id"]):
            component = db.session.get(ApplicationComponent, world["component_id"])
            element = db.session.get(ArchiMateElement, component.archimate_element_id)
            value = (element.acm_properties or {}).get(key, {}).get("value")
        db.session.remove()
    return value


def test_another_organisation_has_no_connection_and_nothing_is_read_for_it(browser, app, world):
    outcome = run_the_job(app, world["two"]["org_id"])
    assert outcome == {"read": [], "waiting": [], "running": [], "failed": [], "proposed": []}
    context, page = signed_in(browser, world, "two")
    try:
        page.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
        expect(page.get_by_test_id("connectors-empty")).to_contain_text("No connectors configured for this organisation yet.")
        expect(page.get_by_test_id("connector-what-changed")).to_have_count(0)
        # and its page for the other organisation's application is not there
        reply = page.goto(
            world["base"] + "/architecture/application/ApplicationComponent/%d" % world["component_id"],
            wait_until="domcontentloaded",
        )
        assert reply.status == 404
    finally:
        context.close()


def test_an_organisation_that_has_not_delegated_is_asked_not_read_and_the_page_says_so(browser, app, world, github):
    outcome = run_the_job(app, world["three"]["org_id"])
    assert outcome["proposed"] == [world["held_id"]] and outcome["read"] == [], outcome
    assert run_the_job(app, world["three"]["org_id"])["proposed"] == []  # one request, not a pile

    context, page = signed_in(browser, world, "three")
    try:
        page.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
        expect(page.get_by_test_id("connector-what-changed")).to_have_count(0)  # nothing was read
        expect(page.get_by_text("Never", exact=True)).to_be_visible()
        expect(page.get_by_test_id("connector-schedule")).to_have_text("Reads of this connection wait for approval")
        expect(page.get_by_test_id("connector-schedule")).not_to_contain_text("every hour")
        shoot(page, "connectors-needs-approval")

        # On a phone the schedule is read in the row, without scrolling the table sideways.
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="domcontentloaded")
        box = page.get_by_test_id("connector-schedule").bounding_box()
        assert box is not None and 0 <= box["x"] and box["x"] + box["width"] <= 390, box
        sideways = page.evaluate("() => { const t = document.querySelector('table').parentElement; return t.scrollWidth - t.clientWidth; }")
        assert sideways <= 0, sideways
        page.set_viewport_size({"width": 1440, "height": 900})

        # The link lands on the queue, and the one request to read this connection is in it.
        page.get_by_role("link", name="Open the approval queue").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("Run sync for connector 'GitHub (needs approval)'")).to_have_count(1)

        # A connection that is switched off is not read on any schedule, and the page does not say it is.
        from app import db
        from app.jobs.tenant_safe_job import tenant_scope
        from app.models.connector_config import ConnectorConfig

        with app.app_context():
            with tenant_scope(world["three"]["org_id"]):
                db.session.get(ConnectorConfig, world["held_id"]).status = "inactive"
                db.session.commit()
            db.session.remove()
        page.goto(world["base"] + "/admin/connectors", wait_until="domcontentloaded")
        expect(page.get_by_test_id("connector-schedule")).to_have_text("Not read again while the connection is inactive")
        expect(page.get_by_test_id("connector-next-read")).to_have_count(0)
        shoot(page, "connectors-inactive")
    finally:
        context.close()
