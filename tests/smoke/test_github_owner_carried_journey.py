"""A CTO approves what GitHub showed and the code owner becomes the application's owner.

Starts from the goal: "the person GitHub names as code owner owns the application I just
added." It drives the review page's own Approve all control, then reads the result where a
person reads it: the application's fact sheet and the applications list.
"""

import json
import os

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def csrf_server(request, ai_protocol_stub, app, tmp_path_factory):
    """A live server with CSRF protection on, the way production runs.

    TestingConfig switches CSRF off, which hides any form that forgot its token. The hook
    below turns it on for this server's process only, before the app is created.
    """
    hook = tmp_path_factory.mktemp("csrf_on")
    (hook / "sitecustomize.py").write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import config\n"
        "config.SmokeTestingConfig.WTF_CSRF_ENABLED = True\n" % REPO_ROOT
    )
    inherited = os.environ.get("PYTHONPATH")
    path = os.pathsep.join([str(hook)] + ([inherited] if inherited else []))
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"PYTHONPATH": path})


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


def _word_whole(element_handle, word):
    """True when ``word`` renders as one unbroken run inside ``element_handle``
    (no mid-word line break -- ``break-all`` would split it, ``break-words``
    only breaks a run too long to fit, which a short word never is)."""
    return element_handle.evaluate(
        """(el, word) => {
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const idx = node.textContent.indexOf(word);
                if (idx !== -1) {
                    const range = document.createRange();
                    range.setStart(node, idx);
                    range.setEnd(node, idx + word.length);
                    return range.getClientRects().length === 1;
                }
            }
            return null;
        }""",
        word,
    )


def _application_id(org_id, name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent.query.filter_by(name=name, organization_id=org_id).first()
        found = row.id if row else None
        db.session.remove()
        return found


def test_github_code_owner_becomes_application_owner(browser, csrf_server):
    live_server = csrf_server
    org = create_fresh_org("solution_architect", org_admin=True, extra_roles=("cto",))
    owner_email = org["emails"]["cto"]
    _propose(org["org_id"], "Journey Matched App", "journey-org/matched", [owner_email])
    _propose(org["org_id"], "Journey Unmatched App", "journey-org/unmatched", ["@octocat"])

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
        expect(page.locator("[data-testid=app-owner-%d]" % unmatched_id)).to_have_text(
            "Code owner on GitHub: @octocat, not yet matched to a person"
        )
        # CO-25: no mid-word break at 1440, where nothing overflows in the first place.
        unmatched_cell = page.locator("[data-testid=app-owner-%d]" % unmatched_id).element_handle()
        assert _word_whole(unmatched_cell, "octocat") is True, "octocat split mid-word at 1440"
        assert _word_whole(unmatched_cell, "person") is True, "person split mid-word at 1440"

        # At 390 wide in dark mode, the owner cell (carrying the long unmatched
        # sentence, before it is replaced by a real owner below) does not
        # overflow the card, and no word inside it is split mid-word.
        narrow_context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        narrow_page = narrow_context.new_page()
        try:
            sign_in(narrow_page, live_server, org["emails"]["solution_architect"])
            narrow_page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            cell = narrow_page.locator("[data-testid=app-owner-mobile-%d]" % unmatched_id)
            expect(cell).to_be_visible()
            card = cell.locator("xpath=ancestor::*[contains(@class,'rounded') or contains(@class,'border')][1]")
            overflow = narrow_page.evaluate(
                "el => el.scrollWidth <= el.clientWidth + 1", card.first.element_handle()
            )
            assert overflow, "the owner cell overflows its card at 390 wide"
            mobile_cell = cell.element_handle()
            assert _word_whole(mobile_cell, "GitHub") is True, "GitHub split mid-word at 390 dark"
            assert _word_whole(mobile_cell, "person") is True, "person split mid-word at 390 dark"
        finally:
            narrow_context.close()

        # The detail page offers the same next step: the header action (the
        # owner-section's own "Assign owner" link was removed -- the header
        # button and the owner step's picker are the two controls left).
        page.goto("%s/applications/%d" % (live_server, unmatched_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-header-assign-owner")).to_have_text("Assign owner")

        # From the list, the editor clicks the link and lands on a page that names the owner and offers people.
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("app-assign-owner-%d" % unmatched_id).click()
        page.wait_for_url("**/applications/%d/assign-owner" % unmatched_id, timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content")).to_contain_text("Code owner on GitHub: @octocat, not yet matched to a person")
        expect(page.locator("#main-content")).not_to_contain_text("<strong>")
        expect(page.locator("#main-content")).not_to_contain_text("@@")

        # The empty choice is stopped in the browser: the select reports it is
        # missing and the form sends no request, not merely a URL that still
        # says "/assign-owner" (the server's own empty-choice redirect lands
        # on that same URL, so the URL alone cannot tell the two apart).
        select = page.locator("select[name=user_id]")
        assert select.input_value() == ""
        requests_sent = []
        page.on("request", lambda request: requests_sent.append(request) if request.method == "POST" else None)
        page.locator("button[type=submit]", has_text="Assign owner").click()
        page.wait_for_timeout(300)
        assert not requests_sent, "the empty choice must not be submitted"
        assert select.evaluate("el => el.validity.valueMissing") is True

        # Before the owner is assigned, the detail header offers "Assign owner".
        page.goto("%s/applications/%d" % (live_server, unmatched_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-header-assign-owner")).to_have_text("Assign owner")

        page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("select[name=user_id]", str(org["user_ids"]["cto"]))
        page.locator("button[type=submit]", has_text="Assign owner").click()
        page.wait_for_url("**/applications/%d" % unmatched_id, timeout=PAGE_TIMEOUT)

        # The owner is on the fact sheet and the list.
        page.goto("%s/applications/%d/fact-sheet" % (live_server, unmatched_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Launch cto").first).to_be_visible()
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-testid=app-owner-%d]" % unmatched_id)).to_have_text("Launch cto")

        # An owned row shows its owner text alone -- the ghost Assign action
        # belongs only to an empty Owner cell; the detail header's Change
        # owner is the way to change an already-owned row.
        expect(page.get_by_test_id("app-assign-owner-%d" % unmatched_id)).to_have_count(0)

        # After the assignment, the detail header offers "Change owner" instead.
        page.goto("%s/applications/%d" % (live_server, unmatched_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-header-assign-owner")).to_have_text("Change owner")

        # An application with no code owner at all still gets the list's ghost
        # Assign action, with the ghost variant's own class, and the assign
        # page opens for it rather than redirecting it away.
        _propose(org["org_id"], "Journey No Owner App", "journey-org/no-owner", [])
        page.goto(live_server + "/admin/connectors/github/review", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Approve all (1)").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        no_owner_id = _application_id(org["org_id"], "Journey No Owner App")
        assert no_owner_id, "approval did not create the no-owner application"

        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        ghost_button = page.get_by_test_id("app-assign-owner-%d" % no_owner_id)
        expect(ghost_button).to_be_visible()
        assert "cn-button-variant-ghost" in (ghost_button.get_attribute("class") or "")
        list_h1_x = page.locator("h1").first.bounding_box()["x"]
        ghost_button.click()
        page.wait_for_url("**/applications/%d/assign-owner" % no_owner_id, timeout=PAGE_TIMEOUT)
        expect(page.locator("select[name=user_id]")).to_be_visible()

        # CO-22: an application with no GitHub code owner at all reads one
        # plain, capitalised sentence -- not a leading ". " left over from a
        # missing first sentence.
        subtitle_text = page.locator("p.mt-1.text-sm.text-muted-foreground").first.inner_text()
        assert subtitle_text == "Choose the person in your organization who owns it.", subtitle_text
        assert subtitle_text[0].isupper(), subtitle_text

        # CO-24: the assign page is on the same Page column as the list, so
        # its heading sits at the same x position, not 24px further right.
        assign_h1_x = page.locator("h1").first.bounding_box()["x"]
        assert abs(assign_h1_x - list_h1_x) < 1, "h1 x: %s vs list h1 x: %s" % (assign_h1_x, list_h1_x)
    finally:
        context.close()


def test_viewer_role_cannot_assign_owner(browser, csrf_server):
    """A persona with no owner-assign governance ability, whose organisation
    role is Viewer, sees no action for it and is refused by the route. Note:
    GOVERNANCE_ACTIONS["owner"]["assign"] in app/core/auth/permissions.py
    names "data_architect" as one of the personas allowed to assign an
    owner regardless of their organisation (matrix) role, so a data-architect
    persona is the wrong choice for this negative case post CO-13 -- "cto" is
    not in that allowlist and is used here instead."""
    live_server = csrf_server
    # A separate administrator approves the proposal (approving one's own
    # proposal is refused, and the Viewer persona under test may not have
    # general write permission to approve anything at all); the Viewer
    # persona is only ever used to exercise the permission boundary below.
    org = create_fresh_org("solution_architect", org_admin=True, extra_roles=("cto",))
    _propose(org["org_id"], "Journey Viewer App", "journey-org/viewer-case", [])

    from app import create_app, db
    from app.models.org_role import OrgRole

    app = create_app("testing")
    with app.app_context():
        # The "cto" person's organisation (matrix) role becomes Viewer,
        # independent of their Architect platform role: a direct role
        # grant, the same mechanism the permissions matrix reads first.
        OrgRole.set_role(
            org["org_id"], org["user_ids"]["cto"], "viewer",
            granted_by_id=org["user_ids"]["solution_architect"],
        )
        db.session.commit()
        db.session.remove()

    # Approve through the live server, signed in as the administrator, the
    # same way the review page's own button does -- running the approval
    # outside a request/tenant context leaves the new row's organization_id
    # unset and the insert fails its not-null constraint.
    admin_context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    admin_page = admin_context.new_page()
    try:
        sign_in(admin_page, live_server, org["emails"]["solution_architect"])
        admin_page.goto(live_server + "/admin/connectors/github/review", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        admin_page.get_by_role("button", name="Approve all (1)").click()
        admin_page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    finally:
        admin_context.close()

    app_id = _application_id(org["org_id"], "Journey Viewer App")
    assert app_id, "the viewer-case application was not created"

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto("%s/applications/%d" % (live_server, app_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("application-header-assign-owner")).to_have_count(0)
        expect(page.locator("[data-owner-picker]")).to_have_count(0)

        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("app-assign-owner-%d" % app_id)).to_have_count(0)

        refused = page.goto(
            "%s/applications/%d/assign-owner" % (live_server, app_id),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )
        assert refused.status == 403, "a Viewer with no owner-assign ability reached assign-owner: %s" % refused.status

        status, _ = api(page, "POST", "/applications/%d/owners" % app_id, {"user_id": org["user_ids"]["solution_architect"]})
        assert status == 403, "a Viewer with no owner-assign ability reached the owners API: %s" % status
    finally:
        context.close()


def test_owner_assign_rule_matches_display_and_route_for_edit_and_governance(browser, csrf_server):
    """CO-23's probe table as the test: the one rule
    (``can_assign_owner`` -- "assign owner", or "edit application") is what
    every owner-assign control shows and every owner-assign route allows,
    for an architect persona outside the owner-assign governance list who
    holds the ordinary ability to edit an application, and separately for a
    persona inside that governance list whose organisation role is Viewer
    (so they hold no edit right at all). Each row is a positive case:
    reverting the rule to "assign owner" alone would refuse the first, and
    reverting it to "edit application" alone would refuse the second (CO-28).
    """
    live_server = csrf_server
    org = create_fresh_org(
        "solution_architect", org_admin=True,
        extra_roles=("architect", "data_architect"),
    )

    from app import create_app, db
    from app.models.org_role import OrgRole

    app = create_app("testing")
    with app.app_context():
        # "architect" keeps its matrix role from its Architect platform
        # role (edit rights, no governance "assign owner" persona). The
        # data-architect persona's organisation (matrix) role becomes Viewer
        # (no edit right), independent of its governance ability.
        OrgRole.set_role(
            org["org_id"], org["user_ids"]["data_architect"], "viewer",
            granted_by_id=org["user_ids"]["solution_architect"],
        )
        db.session.commit()
        db.session.remove()

    _propose(org["org_id"], "Journey Architect Edit App", "journey-org/architect-edit", [])
    _propose(org["org_id"], "Journey Data Architect Viewer App", "journey-org/data-architect-viewer", [])

    admin_context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    admin_page = admin_context.new_page()
    try:
        sign_in(admin_page, live_server, org["emails"]["solution_architect"])
        admin_page.goto(live_server + "/admin/connectors/github/review", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        admin_page.get_by_role("button", name="Approve all (2)").click()
        admin_page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    finally:
        admin_context.close()

    architect_app_id = _application_id(org["org_id"], "Journey Architect Edit App")
    data_architect_app_id = _application_id(org["org_id"], "Journey Data Architect Viewer App")
    assert architect_app_id and data_architect_app_id, "approval did not create both applications"

    for persona, app_id in (
        ("architect", architect_app_id),
        ("data_architect", data_architect_app_id),
    ):
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            sign_in(page, live_server, org["emails"][persona])

            page.goto("%s/applications/%d" % (live_server, app_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("application-header-assign-owner")).to_have_text("Assign owner")
            expect(page.locator("[data-owner-picker]")).to_have_count(1)

            page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("app-assign-owner-%d" % app_id)).to_be_visible()

            allowed = page.goto(
                "%s/applications/%d/assign-owner" % (live_server, app_id),
                wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
            )
            assert allowed.status == 200, "%s was refused assign-owner: %s" % (persona, allowed.status)

            status, _ = api(
                page, "POST", "/applications/%d/owners" % app_id,
                {"user_id": org["user_ids"]["solution_architect"], "ownership_type": "backup"},
            )
            assert status in (201, 409), "%s was refused the owners API: %s" % (persona, status)
        finally:
            context.close()
