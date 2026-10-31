"""Sign up, bring the company in, and land on your own data -- in a real browser.

A new person (a startup CTO, who owns the organisation sign-up makes for them) creates an
account through the sign-up form and goes through the other three steps of the welcome: they give
GitHub, a website they allow to be read, the industry and a framework, and finish on the built-in
AI. The read runs in the background; the sources step shows how far it has got and the dashboard
shows the figures from it; the first question about their own systems is answered and cited. A
second person skips everything, lands on a dashboard that asks for a source, and is offered the
sample company in the chat. A third pays, adds their own AI key first, and it is tested before it
is kept. A fourth gives a website: the facts read from it are shown with the page each came from,
nothing is written until they confirm, and a correction is kept as their own value.

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
from tests.smoke.website_wait import wait_for_the_model_pass
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
READER_KEY = "sk-journey-reader-0001"  # the server's own key: it reads the pages of a website
READER_DELAY = {"seconds": 0.0}  # how long the reader's model takes to answer; a slow free model in one test


class _Provider(BaseHTTPRequestHandler):
    """The AI provider: it accepts GOOD_KEY and refuses every other key; the server's own key reads website pages."""

    def do_POST(self):
        self.server.calls.append((self.path, self.headers.get("Authorization")))
        asked = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        reading = self.headers.get("Authorization") == "Bearer " + READER_KEY
        ok = reading or self.headers.get("Authorization") == "Bearer " + GOOD_KEY
        text = "ok"
        if reading:
            from tests.test_website_crawl import fake_model

            time.sleep(READER_DELAY["seconds"])

            try:
                text = fake_model("\n".join(str(m.get("content")) for m in asked.get("messages", [])))
            except Exception:
                text = "{}"
        body = json.dumps(
            {"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
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
            "OPENAI_API_KEY": READER_KEY,
            "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
            # the website reading goes through the public-input chain, whose first provider with a key is NVIDIA here
            "NVIDIA_API_KEY": READER_KEY,
            "NVIDIA_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
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


def test_a_startup_cto_signs_up_gives_github_and_asks_about_their_own_systems(browser, server, people, app, company_site):
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
        page.get_by_label("Website address").fill(company_site.url)
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_text("You may read this website")).to_be_visible()
        expect(page.get_by_text("Tick the box to let us read this site.")).to_be_visible()
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile("Reading Company website now|Company website is connected|Read "))

        # GitHub starts at once and the person carries on.
        page.get_by_label("GitHub organisation").fill("acme")
        page.get_by_label("Read-only token").fill(TOKEN)
        page.get_by_role("button", name="Connect GitHub").click()
        expect(page.get_by_test_id("signup-status-github")).to_contain_text(re.compile(r"GitHub is connected\. Reading starts in a moment|Reading GitHub now|Read 16 repositories"))

        # Jira is offered on the same step, with the three things it needs; it was left alone here.
        jira = page.get_by_test_id("signup-jira")
        for label in ("Jira site address", "Jira email", "Jira API token"):
            expect(jira.get_by_label(label)).to_be_visible()
        expect(jira.get_by_role("button", name="Connect Jira")).to_be_visible()
        expect(page.get_by_test_id("signup-status-jira")).to_have_text("")

        shoot(page, "step2-sources")
        page.get_by_test_id("signup-continue").click()

        # Step 3: the company. Industry and one framework, nothing preselected.
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_role("heading", name="About your company")).to_be_visible()
        expect(page.locator("[data-framework]:checked")).to_have_count(0)
        assert page.get_by_label("Your industry").input_value() == ""
        page.get_by_label("Your industry").select_option(label="Banking and payments")
        page.get_by_role("button", name="Show all frameworks").click()
        page.get_by_label("ISO 27001").check()
        shoot(page, "step2-filled")
        page.get_by_role("button", name="Continue").click()

        # Step 4: Community, so the built-in AI comes first.
        page.wait_for_url("**/account/welcome?step=4")
        expect(page.get_by_role("heading", name="Your AI provider")).to_be_visible()
        expect(page.get_by_test_id("signup-built-in")).to_be_visible()
        expect(page.get_by_test_id("signup-own-key")).to_be_hidden()
        shoot(page, "step3-community")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # Reading carries on in the background. The person comes back to the sources step and sees how far it
        # has got, then the dashboard shows the figures from what was read instead of asking for a source.
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            page.goto(str(server) + "/account/welcome", wait_until="domcontentloaded")
            if (
                "items added to your model" in page.get_by_test_id("signup-status-github").inner_text()
                and "facts wait for you to confirm" in page.get_by_test_id("signup-status-website").inner_text()
            ):
                break
            page.wait_for_timeout(3000)
        github_line = page.get_by_test_id("signup-status-github").inner_text()
        assert re.search(r"Read 16 repositories; 22 items added to your model\.", github_line), github_line
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile(r"Read 5 pages; \d+ facts wait for you to confirm"))
        shoot(page, "sources-read")

        page.goto(str(server) + "/dashboard/overview", wait_until="domcontentloaded")
        expect(page.get_by_test_id("overview-connect")).to_have_count(0)
        expect(page.get_by_test_id("connect-source-card")).to_have_count(0)
        applications = page.get_by_test_id("welcome-applications")
        expect(applications).to_be_visible()
        total = int(applications.locator("span").first.inner_text())
        assert total >= 3, total
        shoot(page, "dashboard-after-reading")
        # The figure is a link, and it lands on a list that holds the same number of applications by name.
        applications.click()
        listed = page.locator("main")
        expect(listed).to_contain_text("%d shown" % total)
        for name in ("Auth", "Billing Api", "Customer Portal", "Gateway", "Inventory Service", "Mobile App", "Orders Worker", "Payments Service"):
            expect(listed.get_by_text(name, exact=True).first).to_be_visible()
        assert total == 8, total
        # No sample company is offered to someone who has brought their own.
        page.goto(str(server) + "/ai-chat", wait_until="networkidle")
        expect(page.get_by_test_id("chat-first-sample")).to_have_count(0)

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
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")
        # Nothing was given, so the dashboard asks for a source and the offer lands on a page with content.
        expect(page.get_by_test_id("overview-welcome")).to_contain_text("Connect GitHub and your model is built for you")
        expect(page.get_by_test_id("connect-source-card")).to_contain_text("Connect GitHub or Jira")
        expect(page.get_by_test_id("welcome-applications")).to_have_count(0)
        # The kind was answered in the company step, so nothing asks it again.
        expect(page.get_by_test_id("company-kind-selector")).to_have_count(0)
        shoot(page, "dashboard-skipped")
        # Nothing was given, so the chat offers the sample company as well as Connect GitHub.
        page.goto(str(server) + "/ai-chat", wait_until="networkidle")
        steps = page.get_by_test_id("startup-first-steps")
        expect(steps.get_by_role("link", name="Connect GitHub")).to_be_visible(timeout=PAGE_TIMEOUT)
        sample = steps.get_by_role("button", name="See a sample company")
        expect(sample).to_be_visible()
        shoot(page, "chat-offer")
        sample.click()
        expect(page.get_by_test_id("sample-company-indicator")).to_be_visible(timeout=PAGE_TIMEOUT * 2)
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
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
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


def _footer_in_first_view(page, names):
    """Every action in the step's footer is on screen without scrolling."""
    height = page.viewport_size["height"]
    for name in names:
        box = page.get_by_test_id(name).bounding_box()
        assert box is not None, name
        assert 0 <= box["y"] and box["y"] + box["height"] <= height, "%s is below the first view: %s" % (name, box)


def test_every_step_keeps_its_actions_in_view_and_every_later_step_has_previous(browser, server, people, app):
    for width, height in ((1440, 900), (390, 844)):
        context = browser.new_context(viewport={"width": width, "height": height})
        context.add_init_script(SKIP_WELCOME)
        context.set_default_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        try:
            email, _started = sign_up(page, str(server), people)
            assert_is_a_startup_cto_not_an_administrator(app, email)
            nav = page.get_by_role("navigation", name="Progress steps")
            expect(nav).to_contain_text("Your sources")
            expect(nav).to_contain_text("Your company")
            # Step 2, sources.
            expect(page.get_by_role("heading", name="Bring in your company")).to_be_visible()
            _footer_in_first_view(page, ["signup-continue"])
            expect(page.get_by_test_id("signup-previous")).to_have_count(0)  # nothing before the first step to go back to
            page.get_by_test_id("signup-continue").click()
            # Step 3, the company.
            page.wait_for_url("**/account/welcome?step=3")
            expect(page.get_by_role("heading", name="About your company")).to_be_visible()
            expect(page.get_by_label("What kind of company are you?")).to_have_value("startup")
            _footer_in_first_view(page, ["signup-previous", "signup-continue"])
            # Previous returns to the step before, with its content.
            page.get_by_test_id("signup-previous").click()
            page.wait_for_url("**/account/welcome")
            expect(page.get_by_label("Website address")).to_be_visible()
            page.get_by_test_id("signup-continue").click()
            page.get_by_test_id("signup-continue").click()
            # Step 4, the AI provider.
            page.wait_for_url("**/account/welcome?step=4")
            _footer_in_first_view(page, ["signup-previous", "signup-finish"])
            page.get_by_test_id("signup-previous").click()
            page.wait_for_url("**/account/welcome?step=3")
            expect(page.get_by_role("heading", name="About your company")).to_be_visible()
        finally:
            context.close()


def test_the_company_kind_chosen_at_sign_up_is_kept_and_not_asked_again(browser, server, people, app):
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_label("What kind of company are you?").select_option(label="Enterprise")
        page.get_by_label("ISO 27001").check()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")
        expect(page.get_by_test_id("company-kind-selector")).to_have_count(0)
        with app.app_context():
            from app.models.user import User
            from app.models.organization import Organization
            from app import db

            org = db.session.get(Organization, User.query.filter_by(email=email).one().organization_id)
            assert org.settings["company_context"]["kind"] == "enterprise"
            db.session.remove()
        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        expect(page.get_by_label("What kind of company are you?")).to_have_value("enterprise")
        expect(page.get_by_label("ISO 27001")).to_be_checked()
        # Nothing was connected, so the dashboard's offer lands on the GitHub connection page, with content.
        page.goto(str(server) + "/dashboard/", wait_until="domcontentloaded")
        connect = page.get_by_test_id("overview-connect")
        expect(connect).to_be_visible()
        connect.click()
        expect(page.get_by_role("heading", name=re.compile("GitHub")).first).to_be_visible()
        assert page.locator("main").inner_text().strip()
    finally:
        context.close()


@pytest.fixture(scope="module")
def company_site():
    """The fixture company's own website, served locally."""
    from tests.test_website_crawl import ABOUT, APP_SHELL, CONTACT, HOME, PRODUCTS, ROBOTS, _html, _Site

    other = _Site({"/elsewhere": _html("<p>another company</p>")})
    pages = {
        "/": _html(HOME % {"linkedin": "https://www.linkedin.com/company/acme-robotics", "other": other.url}),
        "/about": _html(ABOUT), "/products": _html(PRODUCTS), "/contact": _html(CONTACT), "/app": _html(APP_SHELL),
        "/robots.txt": (200, "text/plain", ROBOTS),
    }
    site = _Site(pages)
    yield site
    site.stop()
    other.stop()


def test_a_slow_model_does_not_keep_the_founder_from_seeing_their_company_name(browser, server, people, app, company_site):
    """QA-026: the cal.com sign-up reached step 3 and saw nothing, because each page waited on the model.
    The company name and the facts a page states are on step 3 within seconds, ticked, while the model is
    still reading; the page says more are coming, and the model's facts arrive once it answers."""
    site = company_site
    READER_DELAY["seconds"] = 45.0
    context, page = new_page(browser, 1440)
    try:
        email, _started = sign_up(page, str(server), people)
        page.get_by_label("Website address").fill(site.url)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        asked_at = time.monotonic()

        # Within the walk, the name is there and ticked, and nothing is waiting on the model.
        name = _fact(page, "Company name")
        expect(name.locator("[data-fact-value]")).to_have_value("Acme Robotics Ltd", timeout=30000)
        assert time.monotonic() - asked_at < 30, "the name took %.0f s" % (time.monotonic() - asked_at)
        expect(name.get_by_role("checkbox")).to_be_checked()
        expect(_fact(page, "Contact email").locator("[data-fact-value]")).to_have_value("info@acme-robotics.test")
        # The page says more are coming, whether the first read is still under way (the notice in the facts list) or
        # has finished while the model works (the notice beside it), and offers the way to see them when they arrive.
        coming = page.locator("[data-testid=signup-facts-more], [data-testid=more-facts-coming]")
        expect(coming).to_have_count(1)
        expect(coming).to_contain_text(re.compile(r"More facts (are still being worked out|follow|will be added)"))
        expect(coming.get_by_role("link", name="Show them when they are ready")).to_be_visible()
        assert _fact(page, "Category").count() == 0, "a model-only fact appeared before the model answered"
        shoot(page, "slow-model-first-facts")

        # The model's reading arrives; the person asks for it and gets the richer profile.
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not _fact(page, "Category").count():
            page.wait_for_timeout(5000)
            if page.get_by_test_id("signup-facts-refresh").count():
                page.get_by_test_id("signup-facts-refresh").click()
                page.wait_for_url("**/account/welcome?step=3")
        expect(_fact(page, "Category").locator("[data-fact-value]")).to_have_value("Transportation & Logistics")
        expect(_fact(page, "Product").first).to_be_visible()
        expect(_fact(page, "Company name")).to_have_count(1)
        shoot(page, "slow-model-all-facts")
    finally:
        READER_DELAY["seconds"] = 0.0
        context.close()


def _fact(page, label):
    return page.locator("[data-fact]").filter(has=page.get_by_test_id("fact-label").get_by_text(label, exact=True))


def _elements_of(app, email):
    with app.app_context():
        from app import db
        from app.models.archimate_core import ArchiMateElement
        from app.models.user import User

        org_id = User.query.filter_by(email=email).one().organization_id
        held = {(e.type, e.name) for e in ArchiMateElement.query.filter_by(organization_id=org_id).all()}
        db.session.remove()
    return held


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_website_is_read_shown_with_its_pages_and_written_only_when_confirmed(browser, server, people, app, company_site, width):
    site = company_site
    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        email, _started = sign_up(page, str(server), people)
        assert_is_a_startup_cto_not_an_administrator(app, email)

        # The site is given with permission to read it.
        page.get_by_label("Website address").fill(site.url)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile("Company website|Reading|Read "))
        page.get_by_test_id("signup-continue").click()

        # Its facts are waiting in the company step, each with the page it came from, and nothing is in the model yet.
        page.wait_for_url("**/account/welcome?step=3")
        facts = page.get_by_test_id("signup-facts")
        expect(facts).to_be_visible(timeout=120000)
        expect(facts).to_contain_text("What we read on " + site.url)
        wait_for_the_model_pass(page)
        assert not _elements_of(app, email), "the model must hold nothing until the person confirms"
        name = _fact(page, "Company name")
        expect(name.locator("[data-fact-value]")).to_have_value("Acme Robotics Ltd")
        for label, path in (("Key person", "/about"), ("Product", "/products"), ("Compliance mention", "/products")):
            link = _fact(page, label).first.get_by_test_id("fact-source")
            assert (link.get_attribute("href") or "").endswith(path), (label, link.get_attribute("href"))
        # The page a fact names opens, and it is the company's own page with content.
        landing = context.new_page()
        landing.goto(name.get_by_test_id("fact-source").get_attribute("href"))
        expect(landing.locator("body")).to_contain_text("Acme Robotics")
        landing.close()
        shoot(page, "website-facts-%d" % width)

        # The person corrects the name, leaves one person out, and ticks the facts the reader was unsure of.
        name.locator("[data-fact-value]").fill("Acme Robotics Group")
        _fact(page, "Key person").filter(has=page.locator("input[data-fact-value][value*='Priya']")).get_by_role("checkbox").uncheck()
        for label in ("Product", "Compliance mention", "Integration partner"):
            for box in _fact(page, label).get_by_role("checkbox").all():
                box.check()
        page.get_by_label("Company size").select_option(label="11 to 50 people")
        page.get_by_label("Your role").fill("CTO")
        page.get_by_label("What do you want first?").select_option(label="Prepare for an audit")
        assert not _elements_of(app, email), "choosing is not confirming"
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")

        # Confirming writes what was ticked, and only that.
        held = _elements_of(app, email)
        assert {("BusinessService", "PickBot"), ("BusinessActor", "Dana Whitfield"),
                ("Requirement", "Comply with ISO 27001"), ("ApplicationComponent", "SAP")} <= held, held
        assert ("BusinessActor", "Priya Raman") not in held
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # The correction is the person's own value, named as theirs, beside the pages the others came from.
        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        profile = page.get_by_test_id("company-profile-card")
        profile.scroll_into_view_if_needed()
        expect(profile).to_contain_text("Acme Robotics Group")
        expect(profile).to_contain_text("Corrected by Casey Founder")
        expect(profile).not_to_contain_text("Acme Robotics Ltd")
        expect(profile).to_contain_text("Dana Whitfield, Chief Executive Officer")
        expect(profile).not_to_contain_text("Priya Raman")
        expect(profile.locator("a[href]").first).to_have_attribute("href", re.compile("^http://127.0.0.1"))
        shoot(page, "website-settings-%d" % width)
    finally:
        context.close()


# ----------------------------------------------------------------------------- the public signals follow the company the person confirmed

def _no_hits():
    return {
        "https://newsapi.org/": {"status": "ok", "articles": []}, "https://api.search.brave.com/": {"web": {"results": []}},
        "https://efts.sec.gov/": {"hits": {"hits": []}}, "https://developer.uspto.gov/": {"results": []},
        "https://api.openalex.org/": {"results": []}, "https://www.reddit.com/": {"data": {"children": []}},
        "https://hn.algolia.com/": {"hits": []}, "https://public.api.bsky.app/": {"posts": []},
        "https://mastodon.social/": {"statuses": []}, "https://query1.finance.yahoo.com/": {"quotes": []},
    }


def _keep_signals_in_process(app, org_id, answers):
    """Read the public sources for one organisation from recorded answers; returns the addresses asked."""
    from tests.test_public_signals import FakeHttp, load

    from app.modules.organization import public_signals

    http = FakeHttp(answers)
    with app.app_context():
        try:
            public_signals.refresh_organization(org_id, http=http, llm=lambda prompt: json.dumps(load("sentiment")), force=True)
        finally:
            from app import db

            db.session.remove()
    return http.calls


def _signal_titles(app, org_id):
    with app.app_context():
        from app import db
        from app.models.organization_signal import OrganizationSignal

        held = sorted((r.subject, r.title) for r in OrganizationSignal.query.filter_by(organization_id=org_id).all())
        db.session.remove()
    return held


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_the_public_signals_are_about_the_company_the_person_confirmed_and_sit_under_its_name(
        browser, server, people, app, company_site, width, monkeypatch):
    from app.models.organization import Organization
    from app.models.organization_signal import OrganizationSignal
    from tests.smoke.fresh_org import create_fresh_org, sign_in

    from app import db

    for name in ("NEWSAPI_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.setenv(name, "journey-key")
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    site = company_site
    about, products = site.url + "/about", site.url + "/products"

    acme = _no_hits()
    acme["https://newsapi.org/"] = {"status": "ok", "articles": [
        {"source": {"name": "Robotics Weekly"}, "title": "Acme Robotics Group opens a pick-and-pack line", "description": "Warehouse robots.",
         "url": about, "publishedAt": "2026-10-20T09:30:00Z"},
        {"source": {"name": "Daily Wire"}, "title": "Warehouse automation trends for 2026", "description": "An industry overview.",
         "url": "https://dailywire.example/automation-2026", "publishedAt": "2026-10-19T09:30:00Z"}]}
    acme["https://api.search.brave.com/"] = {"web": {"results": [
        {"title": "Acme Robotics Group - company profile", "url": products, "description": "Makers of PickBot."}]}}
    acme["https://hn.algolia.com/"] = {"hits": [
        {"title": "Show HN: robots that pick and pack", "url": products, "objectID": "11", "created_at": "2026-10-12T00:00:00Z"},
        {"title": "Entrepreneurial passion and the 2008 start-up", "objectID": "12", "created_at": "2008-03-01T00:00:00Z"},
        # other companies' launches that only carry the company's booking link are not about it
        {"title": "Show HN: Praxos", "story_text": "Book a call at %s/demo" % site.url, "url": "https://praxos.example/", "objectID": "15", "created_at": "2026-10-21T00:00:00Z"},
        {"title": "Launch HN: Vendo", "story_text": "Questions? %s/ask" % site.url, "url": "https://vendo.example/", "objectID": "16", "created_at": "2026-10-21T01:00:00Z"},
        # the same story from the same source on the same day, found at two addresses, is one signal
        {"title": "Acme Robotics Group is going closed source", "objectID": "13", "created_at": "2026-10-18T08:00:00Z"},
        {"title": "Acme Robotics Group is going closed source", "objectID": "14", "created_at": "2026-10-18T09:30:00Z"}]}
    # some sources fail with a status code, and one has no key: the page says neither
    from app.modules.organization.public_signals import sources as _sources

    acme["https://efts.sec.gov/"] = _sources.SourceFailed("answered 403")
    acme["https://api.openalex.org/"] = {"results": [
        {"id": "https://openalex.org/W9", "display_name": "Neuroprotective Effect of Lithium Chloride in Rat Model of Cardiac Arrest",
         "publication_date": "2020-01-01", "abstract_inverted_index": {"Rats": [0], "recover": [1]}}]}
    birch = _no_hits()
    birch["https://hn.algolia.com/"] = {"hits": [{"title": "Birch Robotics ships a gripper", "objectID": "21", "created_at": "2026-10-14T00:00:00Z"}]}

    # A second company, already confirmed under its own name, reads its own public sources.
    other = create_fresh_org("enterprise_architect")
    with app.app_context():
        row = db.session.get(Organization, other["org_id"])
        row.name = "Birch Robotics"
        row.settings = {"onboarding": {"website": {"url": "https://birch.example", "consent_at": "2026-10-01T00:00:00"}},
                        "company_profile": {"company_name": {"value": "Birch Robotics"}}}
        db.session.commit()
        db.session.remove()

    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        email, _started = sign_up(page, str(server), people)
        # The welcome notice is a line on the page: it covers neither the progress steps nor the step heading.
        expect(page.get_by_test_id("signup-notice")).to_contain_text("Account created")
        assert page.get_by_test_id("signup-notice").evaluate("e => e.scrollWidth <= e.clientWidth + 1 && !e.querySelector('.truncate')"), "the welcome notice is cut off"
        assert page.locator("#platform-toast-container [role=alert]").count() == 0
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.screenshot(path=os.path.join(SHOTS, "step2-as-it-opens-%d.png" % width))
        heading = page.get_by_role("heading", name="Bring in your company")
        assert page.evaluate(
            "([h]) => { const e = document.elementFromPoint(h.x + 4, h.y + h.height / 2); return e && e.textContent.includes('Bring in your company'); }",
            [heading.bounding_box()]), "something sits over the step heading"
        labels = [b for b in (page.get_by_role("navigation", name="Progress steps").locator("div.text-xs").nth(i).bounding_box() for i in range(4))]
        for left, right in zip(labels, labels[1:]):
            assert left["x"] + left["width"] <= right["x"] + 1, ("progress labels run together", left, right)
        with app.app_context():
            from app.models.user import User

            org_id = User.query.filter_by(email=email).one().organization_id
            assert db.session.get(Organization, org_id).name == "Casey Founder's Workspace"  # the stand-in, until the company is confirmed
            db.session.remove()

        page.get_by_label("Website address").fill(site.url)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile("Company website|Reading|Read "))
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_test_id("signup-facts")).to_be_visible(timeout=120000)
        shoot(page, "step3-facts")
        page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
        for fact in page.locator("[data-fact]").all():  # every fact says which page, how sure, and when it was read
            expect(fact.get_by_test_id("fact-source")).to_have_attribute("href", re.compile("^http"))
            line = fact.get_by_test_id("fact-confidence")
            assert re.fullmatch(r"· \d+% sure · read \d{2} \w{3} \d{4}", line.inner_text().strip()), fact.inner_text()
            assert line.evaluate("e => e.getBoundingClientRect().right <= e.parentElement.getBoundingClientRect().right + 1"), "the date is cut off"

        # Before the company is confirmed nothing is searched: the daily read finds no name to search for.
        with app.app_context():
            db.session.add(OrganizationSignal(organization_id=org_id, checksum="old-1", subject="Casey Founder's Workspace", source="hackernews",
                                              kind="mention", title="Entrepreneurial passion and the 2008 start-up", summary="", fetched_at=_now()))
            db.session.commit()
            db.session.remove()
        from app.modules.organization.public_signals import SignalsRefused

        with pytest.raises(SignalsRefused, match="not been confirmed"):
            _keep_signals_in_process(app, org_id, acme)
        assert _signal_titles(app, org_id) == [], "nothing is kept under the stand-in name"

        # The person confirms the company under its own name.
        name = _fact(page, "Company name")
        name.locator("[data-fact-value]").fill("Acme Robotics Group")
        page.get_by_label("Company size").select_option(label="11 to 50 people")
        page.get_by_label("Your role").fill("CTO")
        page.get_by_label("What do you want first?").select_option(label="Prepare for an audit")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        with app.app_context():
            assert db.session.get(Organization, org_id).name == "Acme Robotics Group"
            db.session.remove()

        asked = _keep_signals_in_process(app, org_id, acme)
        assert not any("Casey" in str(kw.get("params")) for _url, kw in asked), "the stand-in name is never searched"
        _keep_signals_in_process(app, other["org_id"], birch)
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # Settings: the card is under the confirmed name and every item names the company or its address.
        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        card = page.get_by_test_id("company-signals-card")
        card.scroll_into_view_if_needed()
        expect(card).to_contain_text("What the public sources say about Acme Robotics Group")
        assert "Casey" not in card.inner_text()
        table = card.get_by_test_id("company-signals-table")
        rows = table.locator("tbody tr")
        shown = [r.locator("a, span.font-medium").first.inner_text() for r in rows.all()]
        assert sorted(t for t in shown if not t.startswith("Public sentiment")) == sorted([
            "Acme Robotics Group opens a pick-and-pack line", "Acme Robotics Group - company profile", "Show HN: robots that pick and pack",
            "Acme Robotics Group is going closed source"]), shown
        for off in ("Praxos", "Vendo"):
            assert off not in table.inner_text(), off
        left_out = card.get_by_test_id("company-signals-left-out")
        expect(left_out).to_have_text("Some public sources could not be read today; we try again tomorrow.")
        assert not re.search(r"_KEY|\b(403|429|503)\b|answered", page.locator("body").inner_text()), "the page shows a variable name or a status code"
        host = site.url.split("//")[1].split(":")[0]
        for row in rows.all():
            cite = row.get_by_test_id("signal-cite")
            assert re.search(r" · (\d{2} \w{3} \d{4}|read \d{2} \w{3} \d{4})$", cite.inner_text()), cite.inner_text()
        assert "\u2014" not in table.inner_text(), "a bare dash stands where the card should say why"
        for plain in table.get_by_test_id("signal-informs-none").all():
            expect(plain).to_have_text("Not matched to PESTLE or SWOT")
        from app.modules.organization.public_signals import sources as sources_module

        with app.app_context():
            kept = [r for r in OrganizationSignal.query.filter_by(organization_id=org_id).all() if r.kind != "sentiment"]
            assert kept and all(sources_module.is_about(r.title, "Acme Robotics Group", host, sources_module.own_link(r.url, r.details)) for r in kept), [r.title for r in kept]
            assert {r.subject for r in OrganizationSignal.query.filter_by(organization_id=org_id).all()} == {"Acme Robotics Group"}
            assert OrganizationSignal.query.filter_by(organization_id=org_id, title="Acme Robotics Group is going closed source").count() == 1
            db.session.remove()
        for gone in ("Entrepreneurial", "Lithium", "Warehouse automation trends", "Casey"):
            assert gone not in card.inner_text(), gone
        expect(page.get_by_test_id("company-profile-card")).to_contain_text("Acme Robotics Group")
        # each link lands on a page with content
        for title, expected in (("Acme Robotics Group opens a pick-and-pack line", "Dana Whitfield"), ("Acme Robotics Group - company profile", "PickBot")):
            with context.expect_page() as opened:
                table.get_by_role("link", name=title).click()
            landing = opened.value
            landing.wait_for_load_state("domcontentloaded")
            expect(landing.locator("body")).to_contain_text(expected)
            landing.close()
        if SHOTS:
            card.screenshot(path=os.path.join(SHOTS, "signals-card-only-%d.png" % width))

        # The other company's card holds its own items and none of ours.
        other_context, other_page = new_page(browser, width)
        try:
            sign_in(other_page, str(server), other["emails"]["enterprise_architect"])
            other_page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
            theirs = other_page.get_by_test_id("company-signals-card")
            expect(theirs).to_contain_text("What the public sources say about Birch Robotics")
            expect(theirs).to_contain_text("Birch Robotics ships a gripper")
            assert "Acme" not in theirs.inner_text() and "Show HN" not in theirs.inner_text()
        finally:
            other_context.close()
    finally:
        context.close()


def _now():
    from datetime import datetime

    return datetime.utcnow()


# ----------------------------------------------------------------------------- a branded title gives the name, and the signals are about the company

@pytest.fixture(scope="module")
def brand_site():
    """A company whose home page has no declared name: only a title, "Brand | tagline"."""
    from tests.test_website_crawl import _Site

    text = "<p>We help small teams plan their work together and keep every decision in one place. </p>" * 6
    site = _Site({
        "/": (200, "text/html", "<!doctype html><html><head><title>Brand | Plans that run themselves</title></head>"
                                "<body><main><h1>Plans that run themselves</h1>%s</main></body></html>" % text),
        "/robots.txt": (200, "text/plain", ""),
    })
    yield site
    site.stop()


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_founder_who_presses_continue_gets_the_brand_as_their_company_and_a_signals_card_that_holds_only_news_about_it(
        browser, server, people, app, brand_site, width, monkeypatch):
    from app.models.organization import Organization
    from app import db

    for name in ("NEWSAPI_KEY", "BRAVE_SEARCH_API_KEY"):
        monkeypatch.setenv(name, "journey-key")
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    brand = _no_hits()
    brand["https://hn.algolia.com/"] = {"hits": [
        {"title": "Launch HN: Brand (YC W20)", "story_text": "Open source planning. Privacy, GDPR, lawsuits, funding.", "objectID": "1",
         "created_at": "2026-10-01T00:00:00Z"},
        {"title": "Brand's New Website", "objectID": "2", "created_at": "2026-10-11T10:00:00Z"},
        {"title": "New Brand Website", "objectID": "3", "created_at": "2026-10-10T10:00:00Z"},
        {"title": "Show HN: Laminar – Open-Source DataDog + Brand for LLM Apps", "objectID": "4", "created_at": "2026-10-05T00:00:00Z"},
        {"title": "Show HN: Phospho – Text analytics for LLM apps (Brand for Prompts)", "objectID": "5", "created_at": "2026-10-06T00:00:00Z"},
    ]}

    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        email, _started = sign_up(page, str(server), people)
        page.get_by_label("Website address").fill(brand_site.url)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_test_id("signup-facts")).to_be_visible(timeout=120000)

        # The name proposed is the brand, not the whole title, and it is ticked: the founder only presses Continue.
        name = _fact(page, "Company name")
        expect(name.locator("[data-fact-value]")).to_have_value("Brand")
        expect(name.get_by_role("checkbox")).to_be_checked()
        assert "Plans that run themselves" not in name.inner_text(), name.inner_text()
        shoot(page, "brand-name-%d" % width)
        page.get_by_label("Company size").select_option(label="11 to 50 people")
        page.get_by_label("Your role").fill("CTO")
        page.get_by_label("What do you want first?").select_option(label="Prepare for an audit")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        with app.app_context():
            from app.models.user import User

            org_id = User.query.filter_by(email=email).one().organization_id
            assert db.session.get(Organization, org_id).name == "Brand"  # the workspace is no longer the stand-in
            db.session.remove()

        _keep_signals_in_process(app, org_id, brand)
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        card = page.get_by_test_id("company-signals-card")
        card.scroll_into_view_if_needed()
        expect(card).to_contain_text("What the public sources say about Brand")
        assert "Confirm your company name first" not in page.locator("body").inner_text()
        table = card.get_by_test_id("company-signals-table")
        shown = [r.locator("a, span.font-medium").first.inner_text() for r in table.locator("tbody tr").all()]
        assert sorted(t for t in shown if not t.startswith("Public sentiment")) == sorted([
            "Launch HN: Brand (YC W20)", "Brand's New Website"]), shown  # the other products and the repeat are not here
        launch = table.locator("tbody tr").filter(has_text="Launch HN: Brand (YC W20)")
        expect(launch.get_by_test_id("signal-informs-none")).to_have_text("Not matched to PESTLE or SWOT")
        assert "Legal" not in launch.inner_text()
        if SHOTS:
            card.screenshot(path=os.path.join(SHOTS, "signals-brand-%d.png" % width))
    finally:
        context.close()
