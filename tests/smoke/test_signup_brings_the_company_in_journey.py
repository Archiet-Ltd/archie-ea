"""Sign up, bring the company in, and land on your own data -- in a real browser.

A new person (a startup CTO, who owns the organisation sign-up makes for them) creates an
account through the sign-up form, then goes through the stepper's other two steps: they give
GitHub and the industry and a framework, and finish on the built-in AI. The read runs in the
background; the dashboard shows how far it has got; the first question about their own systems
is answered and cited. A second person skips everything and lands on the dashboard exactly as
before, with the sample company on offer. A third pays, adds their own AI key first, and it is
tested before it is kept.

GitHub is never called: the recorded organisation of the service tests answers, through the same
sitecustomize the connect-GitHub journey uses. The AI provider is a small local server that
accepts one key. The timings of both first answers are printed (-s to see them).
"""

import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import ask
from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.smoke.test_connect_github_to_cited_answer_journey import (
    _SITECUSTOMIZE,
    SKIP_WELCOME,
    TOKEN,
    remove_organisations,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("SIGNUP_JOURNEY_SHOTS")
GOOD_KEY = "sk-journey-good-key-0001"


class _Provider(BaseHTTPRequestHandler):
    """The AI provider: it accepts GOOD_KEY and refuses every other key."""

    def do_POST(self):
        self.server.calls.append((self.path, self.headers.get("Authorization")))
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        ok = self.headers.get("Authorization") == "Bearer " + GOOD_KEY
        body = json.dumps(
            {"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            if ok else {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error", "code": "invalid_api_key"}}
        ).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    server.calls = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture(scope="module")
def server(request, app, tmp_path_factory, provider):
    folder = tmp_path_factory.mktemp("signup_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(
        request, None, app,
        extra_env={
            "PYTHONPATH": path,
            "ALLOW_EXTERNAL_API_CALLS": "true",
            "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        },
    )


@pytest.fixture(scope="module")
def people(app):
    """Everyone this module signs up; their organisations are removed at the end."""
    made = []
    yield made
    with app.app_context():
        from app.models.user import User

        org_ids = [User.query.filter_by(email=email).one().organization_id for email in made]
    with app.app_context():
        from sqlalchemy import text

        from app import db

        # Sign-up writes rows no organisation column points at (the audit trail is append-only, and
        # sessions and tokens hang off the person). The switch that stops the triggers firing lasts
        # for this one transaction; every row removed is one of the people this module made.
        ids = {"ids": org_ids}
        db.session.execute(text("SET LOCAL session_replication_role = replica"))
        user_ids = [r[0] for r in db.session.execute(text("SELECT id FROM users WHERE organization_id = ANY(:ids)"), ids)]
        referrers = db.session.execute(text(
            "SELECT kcu.table_name, kcu.column_name FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name "
            "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = rc.unique_constraint_name "
            "WHERE ccu.table_name = 'users' AND ccu.column_name = 'id' AND kcu.table_schema = 'public'"
        )).all()
        for table, column in referrers:
            if "evidence" in table:  # append-only, and sign-up writes none
                continue
            db.session.execute(text('DELETE FROM "%s" WHERE "%s" = ANY(:u)' % (table, column)), {"u": user_ids})
        db.session.execute(text("DELETE FROM soc2_audit_log WHERE organization_id = ANY(:ids)"), ids)
        db.session.execute(text("DELETE FROM users WHERE organization_id = ANY(:ids)"), ids)
        db.session.commit()
        db.session.remove()
    remove_organisations(app, org_ids)


def new_page(browser, width=1440):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


def shoot(page, name):
    """The screen at the two widths people use, for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def sign_up(page, base, people):
    """The sign-up form, filled in and submitted the way a person does. Returns (email, started)."""
    email = "cto.%s@example.com" % uuid.uuid4().hex[:8]
    people.append(email)
    page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("navigation", name="Progress steps")).to_contain_text("Your company")
    page.fill("#first_name", "Casey")
    page.fill("#last_name", "Founder")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    started = time.monotonic()
    page.locator("#submit").click()
    try:
        page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)
    except Exception:
        raise AssertionError("sign-up ended on %s: %s" % (page.url, page.locator("main").inner_text()[:600]))
    return email, started


def assert_is_a_startup_cto_not_an_administrator(app, email):
    with app.app_context():
        from app.models.user import User

        user = User.query.filter_by(email=email).one()
        assert user.persona_lens == "cto", user.persona_lens
        # The owner of the organisation sign-up made for them administers that one organisation only.
        from app.middleware.tenant_decorators import is_platform_admin

        assert not is_platform_admin(user), "the signed-up person must not be a platform administrator"
        assert user.enterprise_role != "platform_admin"


def the_cited_answer(page, base):
    answer = ask(page, base, "What breaks if Payments Service fails")
    cards = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]"))
    expect(cards.first).to_be_visible()
    billing = cards.filter(has_text="Billing Api").first
    expect(billing.locator("[data-citation]").first).to_be_visible()
    assert "acme/billing-api" in billing.inner_text(), billing.inner_text()
    return cards


def test_a_startup_cto_signs_up_gives_github_and_asks_about_their_own_systems(browser, server, people, app):
    context, page = new_page(browser)
    try:
        email, started = sign_up(page, str(server), people)
        assert_is_a_startup_cto_not_an_administrator(app, email)

        # Step 2: the second step of the same stepper, with the account step ticked off.
        expect(page.get_by_role("heading", name="Bring in your company")).to_be_visible()
        expect(page.get_by_role("navigation", name="Progress steps").get_by_role("button", name=re.compile("Step 2"))).to_have_attribute("aria-current", "step")
        expect(page.get_by_text("Not connected", exact=False)).to_have_count(0)
        shoot(page, "step2-company")

        # The site is only recorded, with permission, and the page says so plainly.
        page.get_by_label("Website address").fill("acme.com")
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_text("You may read this website")).to_be_visible()
        expect(page.get_by_text("Tick the box to let us read this site.")).to_be_visible()
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_test_id("signup-status-website")).to_contain_text("https://acme.com will be read soon")

        # GitHub starts at once and the person carries on.
        page.get_by_label("GitHub organisation").fill("acme")
        page.get_by_label("Read-only token").fill(TOKEN)
        page.get_by_role("button", name="Connect GitHub").click()
        expect(page.get_by_test_id("signup-status-github")).to_contain_text(re.compile(r"GitHub is connected\. Reading starts in a moment|Reading GitHub now|Read 16 repositories"))

        # Industry and one framework, nothing preselected.
        expect(page.locator("[data-framework]:checked")).to_have_count(0)
        assert page.get_by_label("Your industry").input_value() == ""
        page.get_by_label("Your industry").select_option(label="Banking and payments")
        page.get_by_label("ISO 27001").check()
        shoot(page, "step2-filled")
        page.get_by_role("button", name="Continue").click()

        # Step 3: Community, so the built-in AI comes first.
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_role("heading", name="Your AI provider")).to_be_visible()
        expect(page.get_by_test_id("signup-built-in")).to_be_visible()
        expect(page.get_by_test_id("signup-own-key")).to_be_hidden()
        shoot(page, "step3-community")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # The dashboard shows each source's progress; no sample company is offered.
        sources = page.get_by_test_id("signup-sources")
        expect(sources).to_contain_text("GitHub.")
        expect(sources).to_contain_text("https://acme.com will be read soon")
        expect(page.get_by_test_id("first-action-sample")).to_have_count(0)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if "items added to your model" in page.get_by_test_id("signup-source-github").inner_text():
                break
            page.wait_for_timeout(3000)
            page.reload(wait_until="domcontentloaded")
        github_line = page.get_by_test_id("signup-source-github").inner_text()
        assert re.search(r"Read 16 repositories; 22 items added to your model\.", github_line), github_line
        shoot(page, "dashboard-sources")

        # The first answer about their own systems, cited to the repository and file.
        cards = the_cited_answer(page, str(server))
        for name in ("Billing Api", "Customer Portal", "Payments Worker"):
            expect(cards.filter(has_text=name).first).to_be_visible()
        seconds = time.monotonic() - started
        print("\nSIGN-UP TO FIRST CITED ANSWER ABOUT OWN SYSTEMS: %.1f seconds" % seconds)
        shoot(page, "first-answer")

        # Settings holds both choices and they can be changed there.
        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        expect(page.get_by_label("Your industry")).to_have_value("banking")
        expect(page.get_by_label("ISO 27001")).to_be_checked()
        expect(page.get_by_label("SOC 2")).not_to_be_checked()
        page.get_by_label("SOC 2").check()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("Saved.")).to_be_visible()
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_label("SOC 2")).to_be_checked()
        expect(page.get_by_label("ISO 27001")).to_be_checked()
        shoot(page, "settings-choices")
    finally:
        context.close()


def test_a_second_cto_skips_everything_and_lands_on_the_dashboard_as_before(browser, server, people, app):
    context, page = new_page(browser)
    try:
        email, started = sign_up(page, str(server), people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        page.get_by_test_id("signup-skip-company").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")
        expect(page.get_by_test_id("signup-sources")).to_have_count(0)
        # Nothing was given, so the sample company is on offer, exactly as before.
        sample = page.get_by_test_id("first-action-sample")
        expect(sample).to_be_visible()
        sample.click()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and not page.get_by_test_id("first-action-sample").count() == 0:
            page.wait_for_timeout(1000)
        answer = ask(page, str(server), "What breaks if Payments service fails")
        expect(answer.locator("[data-answer-row]").first).to_be_visible()
        print("\nSIGN-UP TO FIRST SAMPLE-COMPANY ANSWER: %.1f seconds" % (time.monotonic() - started))
        # Settings shows both choices empty.
        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        assert page.get_by_label("Your industry").input_value() == ""
        expect(page.locator("[data-framework]:checked")).to_have_count(0)
    finally:
        context.close()


def test_a_paying_cto_is_asked_for_their_own_key_first_and_it_is_tested(browser, server, people, app, provider):
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        with app.app_context():
            from app import db
            from app.models.organization import Organization
            from app.models.user import User
            from app.services.billing_plans import ensure_subscription, set_contract_plan

            org = db.session.get(Organization, User.query.filter_by(email=email).one().organization_id)
            org_id = org.id
            ensure_subscription(org)
            set_contract_plan(org, "team", 5)
            db.session.commit()
            db.session.remove()
        page.get_by_test_id("signup-skip-company").click()
        page.wait_for_url("**/account/welcome?step=3")
        own = page.get_by_test_id("signup-own-key")
        expect(own).to_be_visible()
        expect(own).to_contain_text("Your questions go only to the provider you choose, and are never slowed by shared limits")
        expect(page.get_by_test_id("signup-built-in")).to_have_count(0)
        # The built-in AI stays one click away as the secondary button.
        expect(page.get_by_test_id("signup-finish")).to_have_text("Use the built-in AI for now")
        shoot(page, "step3-paying")

        page.get_by_label("AI key").fill("sk-journey-wrong-key-9999")
        page.get_by_role("button", name="Test key").click()
        expect(page.get_by_test_id("signup-key-status")).to_contain_text("Invalid API key")
        with app.app_context():
            from app.models.models import APISettings

            assert APISettings.query.filter_by(provider="openai", organization_id=org_id).count() == 0

        page.get_by_label("AI key").fill(GOOD_KEY)
        page.get_by_role("button", name="Test key").click()
        expect(page.get_by_test_id("signup-key-status")).to_contain_text("your organisation pays OpenAI")
        assert ("/v1/chat/completions", "Bearer " + GOOD_KEY) in provider.calls
        expect(page.get_by_label("AI key")).to_have_value("")
        assert GOOD_KEY not in page.content()
        expect(page.get_by_test_id("signup-finish")).to_have_text("Finish")
        shoot(page, "step3-key-tested")
        with app.app_context():
            from app.models.models import APISettings
            from app.modules.ai_chat.services.llm_service_impl import LLMService
            from flask import g

            row = APISettings.query.filter_by(provider="openai", organization_id=org_id).one()
            assert row.enabled and row.test_status == "success"
            g.current_org_id = org_id
            assert LLMService._get_all_api_keys("openai")[:1] == [GOOD_KEY]
        page.get_by_test_id("signup-finish").click()
        page.wait_for_url("**/dashboard/**")
    finally:
        context.close()
