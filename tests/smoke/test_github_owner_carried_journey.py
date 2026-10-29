"""A CTO approves what GitHub showed and the code owner becomes the application's owner.

Starts from the goal: "the person GitHub names as code owner owns the application I just
added." It drives the review page's own Approve all control, then reads the result where a
person reads it: the application's fact sheet and the applications list.
"""

import json

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _propose(org_id, name, repository, owners):
    from app import create_app, db
    from app.connectors.github import OP_CLASSIFY, PROPOSAL_SOURCE
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    app = create_app("testing")
    with app.app_context():
        create_approval_record(
            organization_id=org_id,
            operation_type=OP_CLASSIFY,
            entity_type="repository",
            summary="GitHub repository %s looks like an application. Approving adds it as an application." % repository,
            operation_payload={
                "repository": repository,
                "name": name,
                "description": "Read from GitHub.",
                "classification": "application",
                "reason": "it has a deployable service",
                "owners": owners,
                "commit": "abcdef1234567890",
                "evidence_file": "CODEOWNERS",
                "group": "Journey system",
            },
            source_table=PROPOSAL_SOURCE,
            expiry_minutes=60 * 24,
        )
        db.session.commit()
        db.session.remove()


def _application_id(org_id, name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent.query.filter_by(name=name, organization_id=org_id).first()
        found = row.id if row else None
        db.session.remove()
        return found


def test_github_code_owner_becomes_application_owner(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True, extra_roles=("cto",))
    owner_email = org["emails"]["cto"]
    _propose(org["org_id"], "Journey Matched App", "journey-org/matched", [owner_email])
    _propose(org["org_id"], "Journey Unmatched App", "journey-org/unmatched", ["nobodyhere"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        page.goto(live_server + "/admin/connectors/github/review", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("cell", name="Journey Matched App", exact=True)).to_be_visible()
        expect(page.get_by_role("cell", name=owner_email)).to_be_visible()

        page.get_by_role("button", name="Approve all (2)").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content").get_by_text("Added 2 items to your model.")).to_be_visible()

        matched_id = _application_id(org["org_id"], "Journey Matched App")
        unmatched_id = _application_id(org["org_id"], "Journey Unmatched App")
        assert matched_id and unmatched_id, "approval did not create both applications"

        # The outcome a person needs: the named person owns the application.
        page.goto("%s/applications/%d/fact-sheet" % (live_server, matched_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Launch cto").first).to_be_visible()

        # The list renders its rows, with the carried owner and the unmatched code owner said plainly.
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Journey Matched App").first).to_be_visible()
        expect(page.get_by_text("Journey Unmatched App").first).to_be_visible()
        expect(page.locator("[data-testid=app-owner-%d]" % matched_id)).to_have_text("Launch cto")
        expect(page.locator("[data-testid=app-owner-%d]" % unmatched_id)).to_contain_text("Code owner on GitHub: @nobodyhere")

        # Assign the unmatched owner on the assign-owner page (no screen links to it yet, so it is opened by address).
        page.goto("%s/applications/%d/assign-owner" % (live_server, unmatched_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("select[name=user_id]", str(org["user_ids"]["cto"]))
        page.locator("button[type=submit]").last.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid=app-owner-%d]" % unmatched_id)).to_have_text("Launch cto")
    finally:
        context.close()
