"""A startup CTO signs up, asks the assistant to move an application to production, and gets it done.

The person: a technical founder alone in a new workspace, made through the self-serve sign-up (the
form, not a seeded user). The goal: keep the model current by asking the chat. DeepSeek is the only
provider, as in production, stubbed on a loopback port: deepseek-chat drafts, deepseek-reasoner reviews.

Journey one: the founder asks to move an application to production. The same message says "Applied",
the link opens the record (drafter and reviewer named), and the application's own page reads
Production. A second workspace holds an application of the same name; it is untouched.

Journey two: the founder asks to submit a solution to the architecture review board. That needs
other people, so the message says "Declined" with the charter reason in plain words, the record shows
it, and no reviewing model was called for it.

Before this change the founder's assistant refused every change, so journey one declined.
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


def _review_reply(_text):
    return json.dumps({"criteria": [
        {"id": n, "passed": True, "reason": "Criterion %d holds for this change." % n} for n in range(1, 7)
    ]})


def _respond(model, text, has_tools):
    if model == "deepseek-reasoner" and not has_tools:
        return _review_reply(text)
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


def _give_the_workspaces_an_application(email, name):
    """The founder's new workspace gets one application, and a second workspace one of the same name."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.organization import Organization
        from app.models.user import User

        founder = User.query.filter_by(email=email).one()
        assert founder.persona_lens == "cto", "the self-serve sign-up gives the CTO lens"
        other = Organization(name="Other Studio %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        mine = ApplicationComponent(name=name, organization_id=founder.organization_id, deployment_status="development")
        theirs = ApplicationComponent(name=name, organization_id=other.id, deployment_status="development")
        db.session.add_all([mine, theirs])
        db.session.commit()
        out = (mine.id, theirs.id)
        db.session.remove()
        return out


def _status_of(app_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent

        value = db.session.get(ApplicationComponent, app_id).deployment_status
        db.session.remove()
        return value


def _ask(page, base, stub, tool, tool_args, question):
    stub.tool_script = [(tool, tool_args)]
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    box = page.locator("#user-input")
    expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
    box.fill(question)
    box.press("Enter")
    verdicts = page.get_by_test_id("change-verdicts")
    expect(verdicts).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    return verdicts


def _reviews(stub):
    return [r for r in stub.requests if r["model"] == "deepseek-reasoner"]


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_founder_who_signed_up_gets_an_application_moved_to_production(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    name = "Ifiok %s" % uuid.uuid4().hex[:6]
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        mine, theirs = _give_the_workspaces_an_application(email, name)

        verdicts = _ask(page, base, stub, "update_application_status", {
            "application_name": name, "new_status": "production", "rationale": "It is live for customers now.",
        }, "Move %s to production, it is live for customers now." % name)
        text = verdicts.inner_text()
        assert "Applied:" in text, text
        assert name in text and "production" in text, text
        assert "Declined" not in text and "charter" not in text.lower(), text
        _shot(page, shots, "founder-change-applied-in-chat", theme)

        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied")
        facts = page.get_by_test_id("review-record-facts")
        expect(facts.get_by_test_id("property-drafter")).to_have_text("deepseek-chat (DeepSeek)")
        expect(facts.get_by_test_id("property-reviewer")).to_have_text("deepseek-reasoner (DeepSeek)")
        checks = page.get_by_test_id("review-record-checks").inner_text()
        assert "The assistant's charter allows it: passed." in checks, checks
        _shot(page, shots, "founder-change-record", theme)

        page.goto(base + "/applications/%d" % mine, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Production", exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, shots, "founder-application-in-production", theme)
        assert _status_of(mine) == "production"
        assert _status_of(theirs) == "development", "the other workspace's application is untouched"
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_founder_asking_for_a_board_submission_is_declined_plainly_with_no_review_call(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        reviews_before = len(_reviews(stub))
        verdicts = _ask(page, base, stub, "submit_for_arb_review", {"solution_name": "Payments rebuild"},
                        "Submit the Payments rebuild to the architecture review board.")
        text = verdicts.inner_text()
        assert "Declined:" in text, text
        assert "cto founder charter does not allow this kind of change" in text, text
        assert "Applied:" not in text
        _shot(page, shots, "founder-board-submission-declined", theme)

        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Declined")
        expect(page.get_by_test_id("review-record-reasons")).to_contain_text(
            "cto founder charter does not allow this kind of change")
        assert len(_reviews(stub)) == reviews_before, "a charter refusal calls no reviewing model"
    finally:
        context.close()
