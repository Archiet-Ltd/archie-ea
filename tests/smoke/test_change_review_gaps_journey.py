"""The review cannot be walked round: what a person sees when a change is applied, declined, held, approved by
hand, or asked for by an account that has since been switched off.

One technical founder signs up through the self-serve form (a workspace of one) and uses the assistant.
DeepSeek is the only provider, stubbed on a loopback port: deepseek-flash drafts, deepseek-v4-pro reviews, and
the reviewer fails any change to an application called "Old Ledger".

* applied: the chat says Applied, the record names both models, the application page reads Production.
* declined: the reviewer's reason is in the chat and on the record, and the application is unchanged.
* held: while the workspace has paused the assistant's changes the chat says so, no reviewer is called and
  nothing changes; after writes resume the person presses Approve, the review passes it and it is applied.
* approved by hand: the person presses Approve on a change the reviewer fails; it is declined with the
  reviewer's reason, not applied.
* switched off: a colleague presses Approve on a change asked for by an account that has been switched off;
  it is declined with that reason, no reviewer is called, and nothing changes.

Before this change an Approve button applied the change with no review, and a switched-off account's change
was applied.
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
VIEWS = [(1440, "light"), (390, "dark")]
REVIEWER_REASON = "The portfolio shows this application as decommissioned and the request does not say otherwise."


def _review_reply(text):
    failing = "Old Ledger" in text
    return json.dumps({"criteria": [
        {"id": n, "passed": not (failing and n == 5),
         "reason": REVIEWER_REASON if failing and n == 5 else "Criterion %d holds for this change." % n}
        for n in range(1, 7)
    ]})


def _respond(model, text, has_tools):
    if model == "deepseek-v4-pro" and not has_tools:
        return _review_reply(text)
    if "independent reviewer rejected it" in text:
        draft = re.search(r"=== BEGIN YOUR_DRAFT ===.*?\n\n(\{.*?\})\n=== END YOUR_DRAFT ===", text, re.S)
        arguments = json.loads(draft.group(1))["arguments"] if draft else {}
        return json.dumps({"arguments": arguments, "summary": "The same change."})
    return None


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply="I asked for that change; the result is shown below.", respond=_respond)
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


@pytest.fixture
def shots(tmp_path):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _shot(page, folder, name, theme):
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d-%s.png" % (name, page.viewport_size["width"], theme))),
                    full_page=True)


def _context(browser, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    init = "localStorage.setItem('archie_onboarding_ts', '1');"
    if theme == "dark":
        init += " try { localStorage.setItem('theme', 'dark'); } catch (e) {} document.documentElement.classList.add('dark');"
    ctx.add_init_script(init)
    return ctx


def _sign_up(page, base, email):
    page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Fern")
    page.fill("#last_name", "Founder")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.click("#submit")
    page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)


def _app_context():
    from app import create_app

    return create_app("testing")


def _workspace_of(email, application_names):
    """The signed-up founder's workspace gets these applications (all in development); returns the ids."""
    from app import db

    app = _app_context()
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.user import User

        founder = User.query.filter_by(email=email).one()
        rows = [ApplicationComponent(name=n, organization_id=founder.organization_id, deployment_status="development")
                for n in application_names]
        db.session.add_all(rows)
        db.session.commit()
        out = {"org_id": founder.organization_id, "user_id": founder.id, "apps": {r.name: r.id for r in rows}}
        db.session.remove()
        return out


def _waiting_change(org_id, user_id, application_name):
    """A change in the review loop that has not been settled yet, asked for by ``user_id``."""
    from app import db

    app = _app_context()
    with app.app_context():
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

        approval = create_approval_record(
            organization_id=org_id, operation_type="tool_use", entity_type="update_application_status",
            summary="Move %s to production." % application_name, user_id=user_id,
            original_command="Move %s to production." % application_name,
            operation_payload={"application_name": application_name, "new_status": "production",
                               "rationale": "It is live for customers now."},
            enter_review=True)
        db.session.commit()
        out = approval.id
        db.session.remove()
        return out


def _second_person(org_id, switched_off):
    from app import db

    app = _app_context()
    with app.app_context():
        from datetime import datetime

        from app.models.user import Role, User

        user = User(email="colleague-%s@example.com" % uuid.uuid4().hex[:8], first_name="Casey", last_name="Colleague",
                    organization_id=org_id, confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        if switched_off:
            user.disabled_at = datetime.utcnow()
        db.session.add(user)
        db.session.commit()
        out = user.id
        db.session.remove()
        return out


def _writes(org_id, paused):
    from app import db

    app = _app_context()
    with app.app_context():
        from flask import g

        with app.test_request_context("/"):
            g.current_org_id = org_id
            from app.models.agent_oversight_state import AgentOversightState
            from app.models.user import User

            state = AgentOversightState.get_for_org(org_id)
            if paused:
                state.pause(User.query.filter_by(organization_id=org_id).first().id, "Audit week")
            else:
                state.resume()
            db.session.commit()
        db.session.remove()


def _status_of(app_id):
    app = _app_context()
    with app.app_context():
        from app import db
        from app.models.application_portfolio import ApplicationComponent

        value = db.session.get(ApplicationComponent, app_id).deployment_status
        db.session.remove()
        return value


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


def _reviews(stub):
    return [r for r in stub.requests if r["model"] == "deepseek-v4-pro"]


def _status_args(name):
    return {"application_name": name, "new_status": "production", "rationale": "It is live for customers now."}




def _press_approve(page, base, approval_id):
    page.goto(base + "/ai-chat/approvals/inbox?approval=%d" % approval_id, wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    card = page.get_by_test_id("approval-card-%d" % approval_id)
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    card.get_by_role("button", name="Approve").click()
    page.wait_for_load_state("domcontentloaded")


def _open_record_from_decided_list(page, approval_id):
    link = page.get_by_test_id("approval-record-link-%d" % approval_id)
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=%d" % approval_id), timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("review-record")).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_change_that_passes_review_is_applied_and_its_record_shows_both_models(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])
        verdicts = _ask(page, base, stub, _status_args(name), "Move %s to production, it is live now." % name)
        text = verdicts.inner_text()
        assert "Applied:" in text and name in text and "production" in text, text
        _shot(page, shots, "change-applied-in-chat", theme)
        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied")
        facts = page.get_by_test_id("review-record-facts")
        expect(facts.get_by_test_id("property-drafter")).to_have_text("deepseek-flash (DeepSeek)")
        expect(facts.get_by_test_id("property-reviewer")).to_have_text("deepseek-v4-pro (DeepSeek)")
        checks = page.get_by_test_id("review-record-checks").inner_text()
        assert "The thing it changes exists: passed. Found %s." % name in checks, checks
        _shot(page, shots, "change-applied-record", theme)
        page.goto(base + "/applications/%d" % ws["apps"][name], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Production", exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _status_of(ws["apps"][name]) == "production"
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_change_the_reviewer_fails_is_declined_in_the_chat_and_on_the_record(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Old Ledger %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])
        verdicts = _ask(page, base, stub, _status_args(name), "Move %s to production." % name)
        text = verdicts.inner_text()
        assert "Declined:" in text and REVIEWER_REASON in text and "Applied:" not in text, text
        _shot(page, shots, "change-declined-in-chat", theme)
        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Declined")
        expect(page.get_by_test_id("review-record-reasons")).to_contain_text(REVIEWER_REASON)
        _shot(page, shots, "change-declined-record", theme)
        assert _status_of(ws["apps"][name]) == "development"
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_change_is_held_while_writes_are_paused_and_applied_after_they_resume_and_it_passes_review(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])
        _writes(ws["org_id"], paused=True)
        reviews_before = len(_reviews(stub))
        verdicts = _ask(page, base, stub, _status_args(name), "Move %s to production." % name)
        text = verdicts.inner_text()
        assert "In review:" in text and "paused for this workspace" in text, text
        assert "Declined" not in text and "Applied:" not in text, text
        assert len(_reviews(stub)) == reviews_before, "a held change costs no review"
        _shot(page, shots, "change-held-in-chat", theme)

        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        approval_id = int(re.search(r"approval=(\d+)", page.url).group(1))
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("In review")
        expect(page.get_by_test_id("review-record-waiting")).to_contain_text("paused for this workspace")
        _shot(page, shots, "change-held-record", theme)
        assert _status_of(ws["apps"][name]) == "development"

        # Writes resume; the person presses Approve; the review passes it and it is applied.
        _writes(ws["org_id"], paused=False)
        _press_approve(page, base, approval_id)
        assert "The change has been made" in page.locator("body").inner_text()
        assert len(_reviews(stub)) == reviews_before + 1, "the Approve went through the review"
        _open_record_from_decided_list(page, approval_id)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied")
        assert _status_of(ws["apps"][name]) == "production"
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_pressing_approve_on_a_change_the_reviewer_fails_declines_it_with_the_reason(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Old Ledger %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])
        approval_id = _waiting_change(ws["org_id"], ws["user_id"], name)
        reviews_before = len(_reviews(stub))
        _press_approve(page, base, approval_id)
        body = page.locator("body").inner_text()
        assert "Declined" in body and REVIEWER_REASON in body, body
        assert "The change has been made" not in body, body
        assert len(_reviews(stub)) == reviews_before + 1, "the Approve went through the review"
        _shot(page, shots, "approve-declined-by-review", theme)
        _open_record_from_decided_list(page, approval_id)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Declined")
        expect(page.get_by_test_id("review-record-reasons")).to_contain_text(REVIEWER_REASON)
        assert _status_of(ws["apps"][name]) == "development"
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_change_asked_for_by_a_switched_off_account_is_declined_and_not_reviewed(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])
        gone = _second_person(ws["org_id"], switched_off=True)
        approval_id = _waiting_change(ws["org_id"], gone, name)
        reviews_before = len(_reviews(stub))
        _press_approve(page, base, approval_id)
        body = page.locator("body").inner_text()
        assert "has been switched off" in body and "The change has been made" not in body, body
        assert len(_reviews(stub)) == reviews_before, "a switched-off account's change is not reviewed"
        _shot(page, shots, "switched-off-declined", theme)
        _open_record_from_decided_list(page, approval_id)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Declined")
        expect(page.get_by_test_id("review-record-reasons")).to_contain_text("has been switched off")
        _shot(page, shots, "switched-off-record", theme)
        assert _status_of(ws["apps"][name]) == "development"
    finally:
        context.close()
