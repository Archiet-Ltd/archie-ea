"""An administrator chooses a SharePoint list, maps its columns and saves the connection.

SP-1: Ada, an administrator of a company on the ``contoso`` tenant, opens the SharePoint page, sees her site's
lists, chooses ``Application Inventory``, points Annual cost at the ``Budget`` column and saves. The save is a
proposal that is checked and released; once it is released the page shows the saved list and mapping after a
reload, and the Connections page lists "SharePoint (read)" once.

SP-2: Bob, an administrator of another company (tenant ``fabrikam``), sees only his own site's list, is refused
when he posts Ada's list, cannot read the status of Ada's save, and a person who is neither an administrator nor
an architect who brings sources in is refused the page.

Microsoft is never reached: the server runs with the stand-in of ``tests/smoke/graph_standin.py`` answering the
sign-in and Graph hosts from a recorded fixture. Two real organisations are created, each with its own Microsoft
365 settings and its own secret in the vault.

Release. A connector change is released here by a second administrator of the same organisation, through the
approval endpoint every connector journey uses: the automatic review does not yet settle connector changes, so
there is no reviewer to stub. The page text is the same either way.
"""

from __future__ import annotations

import json
import os
import re
import uuid

import pytest
from cryptography.fernet import Fernet
from playwright.sync_api import expect

from tests.smoke import graph_standin
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import add_administrator, api, create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("JOB0_JOURNEY_SHOTS")
LIST_A = "list-apps-contoso"
BEARER_B = "graph-token-fabrikam-8810"
# The server and the seeding process must encrypt and decrypt the stored secret with one key.
MASTER_KEY = Fernet.generate_key().decode()


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    folder = SHOTS or str(tmp_path_factory.mktemp("sharepoint_shots"))
    os.makedirs(folder, exist_ok=True)
    return folder


@pytest.fixture(scope="module")
def standin_server(request, app, tmp_path_factory):
    folder = tmp_path_factory.mktemp("graph_standin_peer")
    (folder / "sitecustomize.py").write_text(graph_standin.SITECUSTOMIZE)
    log = folder / "requests.jsonl"
    log.write_text("")
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    server = boot_live_server(
        request,
        None,
        app,
        extra_env={
            "PYTHONPATH": path,
            "ALLOW_EXTERNAL_API_CALLS": "true",
            "GRAPH_STANDIN_FILE": graph_standin.__file__,
            "GRAPH_STANDIN_LOG": str(log),
            "ORG_ENCRYPTION_MASTER_KEY": MASTER_KEY,
        },
    )
    return {"base": str(server), "log": str(log)}


def _seed_microsoft_365(org_id, tenant, site_name):
    """The organisation's Microsoft 365 settings (as saved on that page) and its secret in the vault."""
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    world = graph_standin.load_world()["tenants"][tenant]
    site = next(s for s in world["sites"] if s["name"] == site_name)
    app = create_app("testing")
    app.config["ORG_ENCRYPTION_MASTER_KEY"] = MASTER_KEY
    with app.app_context():
        db.session.add(
            ConnectorConfig(
                organization_id=org_id,
                connector_type="m365",
                name="Microsoft 365",
                config={"tenant_id": tenant, "client_id": world["client_id"], "site_id": site["id"], "enabled": True},
                status="active",
            )
        )
        db.session.commit()
        OrgCredentialVault().store(org_id=org_id, connector_type="m365", credential_type="client_secret",
                                   value=world["client_secret"])
        db.session.commit()
        db.session.remove()


def _company(tenant, site_name):
    """An organisation with an administrator (who proposes) and a second administrator (who releases)."""
    from app import create_app

    org = create_fresh_org("solution_architect", org_admin=True)
    with create_app("testing").app_context():
        org["approver_id"] = add_administrator(org["org_id"], org["suffix"])
    org["approver_email"] = "launch.admin.%s@example.com" % org["suffix"]
    _seed_microsoft_365(org["org_id"], tenant, site_name)
    return org


@pytest.fixture(scope="module")
def companies(app, standin_server):
    made = []

    def make(tenant, site_name):
        org = _company(tenant, site_name)
        made.append(org["org_id"])
        return org

    make.made = made
    yield make
    remove_organisations(app, made)


def _context(browser, color_scheme="light", width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=color_scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


def _shoot(page, folder, name, width, scheme):
    page.screenshot(path=os.path.join(folder, "%s-%d-%s.png" % (name, width, scheme)), full_page=True)


def _latest_connector_approval(org_id):
    from app import create_app, db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    with create_app("testing").app_context():
        row = (
            AIChatCRUDApproval.query.filter_by(organization_id=org_id, entity_type="connectors")
            .order_by(AIChatCRUDApproval.id.desc())
            .first()
        )
        found = row.id if row else None
        db.session.remove()
        return found


def _release(browser, base, org, approval_id):
    """The second administrator of the organisation releases the proposal."""
    context, page = _context(browser)
    try:
        sign_in(page, base, org["approver_email"])
        status, body = api(page, "POST", "/ai-chat/approvals/%d/approve" % approval_id)
        assert status == 200 and body.get("success") is True, body
    finally:
        context.close()


def _selected_text(page, field):
    return page.locator("#map-%s" % field).evaluate("el => el.options[el.selectedIndex].text")


def _connections_rows(page, base):
    page.goto(base + "/admin/connectors", wait_until="domcontentloaded")
    return page.locator("tbody tr").filter(has_text="SharePoint (read)")


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"


def test_sp1_an_administrator_chooses_a_list_maps_it_and_saves(browser, standin_server, companies, shots):
    base = standin_server["base"]
    ada = companies("contoso", "Architecture")
    context, page = _context(browser)
    try:
        sign_in(page, base, ada["emails"]["solution_architect"])
        page.goto(base + "/admin/connectors/sharepoint", wait_until="domcontentloaded")

        # 1. The lists on her site, by name; a list of another site is not offered.
        expect(page.get_by_role("link", name="Application Inventory")).to_be_visible()
        expect(page.get_by_role("link", name="Meeting Notes")).to_be_visible()
        expect(page.get_by_text("Finance Apps")).to_have_count(0)
        expect(page.get_by_text("App Data")).to_have_count(0)
        _shoot(page, shots, "sharepoint-before-choosing", 1440, "light")

        # 2. The mapping proposed from the column titles; Annual cost has no column of that name.
        page.get_by_role("link", name="Application Inventory").click()
        expect(page.get_by_test_id("sharepoint-form")).to_be_visible()
        assert _selected_text(page, "name") == "Title"
        assert _selected_text(page, "description") == "Description"
        assert _selected_text(page, "owner") == "Owner"
        assert _selected_text(page, "lifecycle") == "Lifecycle"
        assert _selected_text(page, "annual_cost") == "No column chosen"
        offered = page.locator("#map-annual_cost option").all_inner_texts()
        assert "Budget" in offered and "Content Type" not in offered and "Created" not in offered
        page.locator("#map-annual_cost").select_option(label="Budget")
        _shoot(page, shots, "sharepoint-mapping-before-saving", 1440, "light")

        # 3. Save: the change is checked first.
        page.get_by_test_id("sharepoint-save").click()
        expect(page.get_by_test_id("sharepoint-state")).to_have_text("Checking this change")
        expect(page.get_by_test_id("sharepoint-saved")).to_have_count(0)
        approval_id = _latest_connector_approval(ada["org_id"])
        assert approval_id is not None
        _release(browser, base, ada, approval_id)
        expect(page.get_by_test_id("sharepoint-state")).to_have_text("Connection saved", timeout=PAGE_TIMEOUT)

        # 4. After a reload the saved list and mapping are on the page, with the cost column that was chosen.
        page.reload(wait_until="domcontentloaded")
        page.goto(base + "/admin/connectors/sharepoint", wait_until="domcontentloaded")
        saved = page.get_by_test_id("sharepoint-saved")
        expect(saved).to_be_visible()
        expect(page.get_by_test_id("sharepoint-saved-list")).to_have_text("Application Inventory")
        rows = {
            r.locator("dt").inner_text(): r.locator("dd").inner_text()
            for r in page.get_by_test_id("sharepoint-saved-row").all()
        }
        assert rows == {
            "Name": "Title",
            "Description": "Description",
            "Owner": "Owner",
            "Lifecycle": "Lifecycle",
            "Annual cost": "Budget",
        }, rows
        _shoot(page, shots, "sharepoint-after-saving", 1440, "light")

        # The Connections page lists the connection once, with a name and a state (no dash).
        listed = _connections_rows(page, base)
        assert listed.count() == 1
        text = listed.first.inner_text()
        assert "SharePoint (read)" in text and "inactive" in text.lower()
        assert "\u2014" not in text
        assert "queued" not in text.lower()
    finally:
        context.close()

    # 5. The same on a phone: the saved connection reads in full, nothing scrolls sideways, and a change of
    # mapping is a second proposal that updates the one connection rather than adding another.
    context, page = _context(browser, color_scheme="dark", width=390, height=844)
    try:
        sign_in(page, base, ada["emails"]["solution_architect"])
        page.goto(base + "/admin/connectors/sharepoint", wait_until="domcontentloaded")
        expect(page.get_by_test_id("sharepoint-saved-list")).to_have_text("Application Inventory")
        _no_sideways_scroll(page)
        _shoot(page, shots, "sharepoint-saved", 390, "dark")
        page.get_by_role("link", name="Application Inventory").click()
        assert _selected_text(page, "annual_cost") == "Budget"
        _no_sideways_scroll(page)
        page.locator("#map-description").select_option(label="No column chosen")
        _shoot(page, shots, "sharepoint-mapping-before-saving", 390, "dark")
        page.get_by_test_id("sharepoint-save").click()
        expect(page.get_by_test_id("sharepoint-state")).to_have_text("Checking this change")
        _release(browser, base, ada, _latest_connector_approval(ada["org_id"]))
        expect(page.get_by_test_id("sharepoint-state")).to_have_text("Connection saved", timeout=PAGE_TIMEOUT)
        rows = {
            r.locator("dt").inner_text(): r.locator("dd").inner_text()
            for r in page.get_by_test_id("sharepoint-saved-row").all()
        }
        assert rows["Description"] == "No column chosen" and rows["Annual cost"] == "Budget", rows
        _no_sideways_scroll(page)
        _shoot(page, shots, "sharepoint-after-saving", 390, "dark")
        assert _connections_rows(page, base).count() == 1
    finally:
        context.close()


def _graph_requests(log):
    with open(log) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_sp2_another_organisation_cannot_reach_the_list(browser, standin_server, companies, shots):
    base, log = standin_server["base"], standin_server["log"]
    ada = companies("contoso", "Architecture")
    bob = companies("fabrikam", "Platform")
    viewer = create_fresh_org("portfolio_manager", org_admin=False)
    companies.made.append(viewer["org_id"])

    # Ada makes a proposal of her own, so there is an approval of hers for Bob to try to read.
    context, page = _context(browser)
    try:
        sign_in(page, base, ada["emails"]["solution_architect"])
        page.goto(base + "/admin/connectors/sharepoint?list=" + LIST_A, wait_until="domcontentloaded")
        expect(page.get_by_test_id("sharepoint-form")).to_be_visible()
        page.get_by_test_id("sharepoint-save").click()
        expect(page.get_by_test_id("sharepoint-state")).to_have_text("Checking this change")
        ada_approval = _latest_connector_approval(ada["org_id"])
    finally:
        context.close()

    context, page = _context(browser)
    try:
        sign_in(page, base, bob["emails"]["solution_architect"])

        # 1. Bob sees his own site's list only; nothing of Ada's appears anywhere on the page.
        page.goto(base + "/admin/connectors/sharepoint", wait_until="domcontentloaded")
        expect(page.get_by_role("link", name="Application Inventory")).to_have_count(1)
        body = page.inner_text("body")
        for name in ("Meeting Notes", "Finance Apps", "contoso", LIST_A):
            assert name not in body, name
        _shoot(page, shots, "sharepoint-other-organisation", 1440, "light")

        # 2. Bob posts Ada's list through the page's own form and CSRF token: refused in plain words.
        page.get_by_role("link", name="Application Inventory").click()
        expect(page.get_by_test_id("sharepoint-form")).to_be_visible()
        page.evaluate(
            """(listId) => {
                const form = document.querySelector('[data-testid="sharepoint-form"]');
                form.querySelector('input[name="list_id"]').value = listId;
                form.submit();
            }""",
            LIST_A,
        )
        expect(page.get_by_test_id("sharepoint-messages")).to_contain_text(
            "That list is not on a site this organisation can read"
        )
        assert _connections_rows(page, base).count() == 0
        asked = _graph_requests(log)
        assert not [r for r in asked if r["bearer"] == BEARER_B and "contoso" in r["path"]]
        assert not [r for r in asked if r["bearer"] == BEARER_B and r["path"].endswith("/list-apps-contoso/columns")]

        # 3. Bob cannot read the status of Ada's save.
        status, _body = api(page, "GET", "/admin/connectors/sharepoint/status?approval=%d" % ada_approval)
        assert status == 404
    finally:
        context.close()

    # 4. A person who is neither an administrator nor an architect who brings sources in is refused.
    context, page = _context(browser)
    try:
        sign_in(page, base, viewer["emails"]["portfolio_manager"])
        reply = page.goto(base + "/admin/connectors/sharepoint", wait_until="domcontentloaded")
        assert reply.status == 403
    finally:
        context.close()
