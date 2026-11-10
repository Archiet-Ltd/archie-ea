"""The one person in a small workspace asks the assistant to move an application to production.

The server is set up as production is: DeepSeek only (LLM_PROVIDER_ORDER=deepseek, a key, a spend
ceiling), with DeepSeek as a stub on a loopback port. deepseek-chat drafts the change; the router
picks deepseek-reasoner to review it, because no second provider is set up.

Journey one: the person (an application manager, whose assistant may change an application's status)
asks to move Atlas to production. In the same message the assistant shows
"Applied" with no Approve button anywhere. The link under it opens the change's record, which reads
the checks, one review with all six criteria and their reasons, deepseek-chat as the drafter and
deepseek-reasoner as the reviewer, and says both came from DeepSeek. The application's own page then
reads Production.

Journey two: the same person asks to move an application the portfolio shows as decommissioned to
production. The reviewer fails the change on consistency; the drafter's revision is the same draft,
so the review ends. The message shows "Declined" with the reviewer's reason, the record shows it too,
and the application is unchanged.

Before this change the assistant only queued the change ("awaiting approval", "a different reviewer
must approve it") and the one person in the workspace could never get it made.
"""
import json
import os
import pathlib
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OTHERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
CONSISTENCY_REASON = "The portfolio shows this application as decommissioned and the request does not say otherwise."
CHAT_REPLY = "I asked for that change; the result is shown below."


def _review_reply(text):
    """What the stubbed deepseek-reasoner answers: pass everything, except a move of a decommissioned app."""
    failing = "Old Ledger" in text
    return json.dumps({"criteria": [
        {"id": n, "passed": not (failing and n == 5),
         "reason": CONSISTENCY_REASON if failing and n == 5 else "Criterion %d holds for this change." % n}
        for n in range(1, 7)
    ]})


def _respond(model, text, has_tools):
    if model == "deepseek-reasoner" and not has_tools:
        return _review_reply(text)
    if "independent reviewer rejected it" in text:
        # The drafter is asked to revise: it returns its own draft unchanged, which ends the review.
        draft = re.search(r"=== BEGIN YOUR_DRAFT ===.*?\n\n(\{.*?\})\n=== END YOUR_DRAFT ===", text, re.S)
        arguments = json.loads(draft.group(1))["arguments"] if draft else {}
        return json.dumps({"arguments": arguments, "summary": "The same change."})
    return None


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=CHAT_REPLY, respond=_respond)
    yield s
    s.close()


@pytest.fixture(scope="module")
def deepseek_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _one_person_workspace():
    """A workspace with one person (an application manager who can make changes) and two applications."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Atlas Studio %s" % suffix, slug="atlas-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(email="owner-%s@example.com" % suffix, first_name="Sole", last_name="Owner",
                    organization_id=org.id, enterprise_role="application_manager", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        atlas = ApplicationComponent(name="Atlas %s" % suffix, organization_id=org.id,
                                     deployment_status="development")
        ledger = ApplicationComponent(name="Old Ledger %s" % suffix, organization_id=org.id,
                                      deployment_status="retiring", lifecycle_status="5. decommissioned")
        db.session.add_all([atlas, ledger])
        db.session.commit()
        people = User.query.filter_by(organization_id=org.id).count()
        assert people == 1, "the workspace must have exactly one person"
        out = {"org_id": org.id, "email": user.email, "atlas": (atlas.id, atlas.name),
               "ledger": (ledger.id, ledger.name)}
        db.session.remove()
        return out


def _deployment_status(app_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent

        value = db.session.get(ApplicationComponent, app_id).deployment_status
        db.session.remove()
        return value


def _shot(page, folder, name):
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d.png" % (name, page.viewport_size["width"]))),
                    full_page=True)


@pytest.fixture
def shots(tmp_path):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _ask(page, base, stub, tool_args, question):
    stub.tool_script = [("update_application_status", tool_args)]
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    box = page.locator("#user-input")
    expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
    box.fill(question)
    box.press("Enter")
    verdicts = page.get_by_test_id("change-verdicts")
    expect(verdicts).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    return verdicts


@pytest.mark.parametrize("width", [1440, 390])
def test_the_one_person_in_a_workspace_gets_an_application_moved_to_production_with_its_review_on_record(
    browser, deepseek_server, stub, shots, width
):
    base = deepseek_server
    ws = _one_person_workspace()
    atlas_id, atlas_name = ws["atlas"]
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _sign_in(page, base, ws["email"])
        verdicts = _ask(page, base, stub, {
            "application_name": atlas_name, "new_status": "production",
            "rationale": "It is live for customers now.",
        }, "Move %s to production, it is live for customers now." % atlas_name)

        # The same message carries the outcome: applied, not waiting for anyone.
        text = verdicts.inner_text()
        assert "Applied:" in text, text
        assert atlas_name in text and "production" in text, text
        body = page.locator("#messages-container").inner_text()
        for stale in ("awaiting approval", "different reviewer", "must approve"):
            assert stale not in body, stale
        assert page.get_by_role("button", name="Approve").count() == 0
        _shot(page, shots, "change-applied-in-chat")

        # The reviewer was the other DeepSeek model, and it read Atlas's request.
        reviews = [r for r in stub.requests if r["model"] == "deepseek-reasoner"]
        assert reviews and atlas_name in reviews[-1]["text"]

        # The link opens the record of how it was checked.
        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        record = page.get_by_test_id("review-record")
        expect(record).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied")
        facts = page.get_by_test_id("review-record-facts")
        expect(facts.get_by_test_id("property-drafter")).to_have_text("deepseek-chat (DeepSeek)")
        expect(facts.get_by_test_id("property-reviewer")).to_have_text("deepseek-reasoner (DeepSeek)")
        expect(facts.get_by_test_id("property-rounds")).to_have_text("1")
        expect(facts.get_by_test_id("property-rubric")).to_have_text("1")
        expect(page.get_by_test_id("review-record-same-provider")).to_contain_text("Both models came from DeepSeek")
        checks = page.get_by_test_id("review-record-checks").inner_text()
        assert "The thing it changes exists: passed. Found %s." % atlas_name in checks, checks
        round_one = page.get_by_test_id("review-record-round-1")
        expect(round_one.locator("[data-testid^='review-criterion-']")).to_have_count(6)
        expect(round_one.get_by_test_id("review-criterion-5")).to_contain_text(
            "Consistent: passed. Criterion 5 holds for this change.")
        assert page.get_by_role("button", name="Approve").count() == 0
        _shot(page, shots, "change-record-applied")

        # The application itself now reads Production.
        page.goto(base + "/applications/%d" % atlas_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=atlas_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Production", exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, shots, "application-in-production")
        assert _deployment_status(atlas_id) == "production"
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_a_change_the_portfolio_contradicts_is_declined_with_its_reason_and_nothing_changes(
    browser, deepseek_server, stub, shots, width
):
    base = deepseek_server
    ws = _one_person_workspace()
    ledger_id, ledger_name = ws["ledger"]
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _sign_in(page, base, ws["email"])
        verdicts = _ask(page, base, stub, {
            "application_name": ledger_name, "new_status": "production",
            "rationale": "Bring it back.",
        }, "Move %s to production." % ledger_name)

        text = verdicts.inner_text()
        assert "Declined:" in text, text
        assert CONSISTENCY_REASON in text, text
        assert "Applied:" not in text
        _shot(page, shots, "change-declined-in-chat")

        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Declined")
        expect(page.get_by_test_id("review-record-reasons")).to_contain_text(CONSISTENCY_REASON)
        expect(page.get_by_test_id("review-record-round-1").get_by_test_id("review-criterion-5")).to_contain_text(
            "Consistent: failed. " + CONSISTENCY_REASON)
        expect(page.get_by_test_id("review-record-facts").get_by_test_id("property-rounds")).to_have_text("1")
        _shot(page, shots, "change-record-declined")

        page.goto(base + "/applications/%d" % ledger_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=ledger_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Retiring", exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _deployment_status(ledger_id) == "retiring"
    finally:
        context.close()
