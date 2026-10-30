"""A new CTO gives their website at sign-up and confirms the company profile read from it.

The person signs up, gives the website of a fixture company (served locally) and ticks that it
may be read, and carries on. In the company step the facts read are waiting, each with the page
it came from; they correct one, leave one out, answer the three questions a website cannot, and
continue. What they confirmed is then in Settings with its sources, a corrected fact names the
person as its source, and the model holds the product, the people, the technology, the
compliance mention and the partner. The page keeps its actions in view at 1440 x 900.
"""

import json
import os
import re
import threading
import time

import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import expect

from tests.smoke.website_wait import wait_for_the_model_pass
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401
    assert_is_a_startup_cto_not_an_administrator,
    new_page,
    people,
    shoot,
    sign_up,
)
from tests.test_website_crawl import ABOUT, APP_SHELL, CONTACT, HOME, PRODUCTS, ROBOTS, _html, _Site, fake_model

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


class _Model(BaseHTTPRequestHandler):
    """The AI provider: it reads a page the way the service tests' reader does."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        prompt = "\n".join(str(m.get("content")) for m in body.get("messages", []))
        try:
            text = fake_model(prompt)
        except Exception:
            text = "{}"
        data = json.dumps({
            "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def company_site():
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


@pytest.fixture(scope="module")
def server(request, app, company_site):
    provider = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    request.addfinalizer(provider.shutdown)
    return boot_live_server(
        request, None, app,
        extra_env={
            "ALLOW_EXTERNAL_API_CALLS": "true",
            "ALLOW_INTERNAL_WEBHOOK_TARGETS": "true",
            "OPENAI_API_KEY": "sk-journey-reader-0001",
            "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
            # the website reading goes through the public-input chain, whose first provider with a key is NVIDIA here
            "NVIDIA_API_KEY": "sk-journey-reader-0001",
            "NVIDIA_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        },
    )


def _row(page, label):
    return page.locator("[data-fact]").filter(has=page.get_by_test_id("fact-label").get_by_text(label, exact=True))


def test_a_cto_confirms_and_corrects_the_company_profile_read_from_their_website(browser, server, people, app, company_site):
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        assert_is_a_startup_cto_not_an_administrator(app, email)

        page.get_by_label("Website address").fill(company_site.url)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile("Company website|Reading|Read "))
        # Step 2 has nothing to go back to.
        expect(page.get_by_test_id("signup-previous")).to_have_count(0)
        page.get_by_test_id("signup-continue").click()

        # The company step: the facts appear by themselves once the reading finishes.
        page.wait_for_url("**/account/welcome?step=3")
        facts = page.get_by_test_id("signup-facts")
        expect(facts).to_be_visible(timeout=120000)
        expect(facts).to_contain_text("What we read on " + company_site.url)
        wait_for_the_model_pass(page)
        name = _row(page, "Company name")
        expect(name.locator("[data-fact-value]")).to_have_value("Acme Robotics Ltd")
        # Every fact names the page it came from, and the link opens a page with content.
        for label, path in (("Key person", "/about"), ("Product", "/products"), ("Compliance mention", "/products")):
            link = _row(page, label).first.get_by_test_id("fact-source")
            assert (link.get_attribute("href") or "").endswith(path), (label, link.get_attribute("href"))
        source = name.get_by_test_id("fact-source").get_attribute("href")
        landing = context.new_page()
        landing.goto(source)
        expect(landing.locator("body")).to_contain_text("Acme Robotics")
        landing.close()

        # One screen at 1440 x 900: the actions are in view without scrolling the page.
        box = page.get_by_test_id("signup-continue").bounding_box()
        assert box and box["y"] + box["height"] <= 900, box
        shoot(page, "company-facts")

        # Correct one fact, leave one out, answer the three questions.
        name.locator("[data-fact-value]").fill("Acme Robotics Group")
        priya = _row(page, "Key person").filter(has=page.locator("input[data-fact-value][value*='Priya']"))
        priya.get_by_role("checkbox").uncheck()
        # Facts the reader was unsure of (under 50 per cent) start unticked; the person ticks the ones that are right.
        for label in ("Product", "Compliance mention", "Integration partner"):
            for box in _row(page, label).get_by_role("checkbox").all():
                box.check()
        page.get_by_label("Company size").select_option(label="11 to 50 people")
        page.get_by_label("Your role").fill("CTO")
        page.get_by_label("What do you want first?").select_option(label="Prepare for an audit")
        expect(page.get_by_label("ISO 27001")).to_be_visible()
        expect(page.get_by_label("ITIL 4")).to_be_hidden()
        shoot(page, "company-filled")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # Settings shows what was confirmed: the corrected fact names the person, the others their page.
        page.goto(str(server) + "/account/manage", wait_until="domcontentloaded")
        profile = page.get_by_test_id("company-profile-card")
        profile.scroll_into_view_if_needed()
        shoot(page, "settings-profile")
        expect(profile).to_contain_text("Acme Robotics Group")
        expect(profile).to_contain_text("Corrected by Casey Founder")
        expect(profile).to_contain_text("Dana Whitfield, Chief Executive Officer")
        expect(profile).not_to_contain_text("Priya Raman")
        expect(profile.locator("a[href]").first).to_have_attribute("href", re.compile("^http://127.0.0.1"))

        with app.app_context():
            from app.models.archimate_core import ArchiMateElement
            from app.models.user import User

            org_id = User.query.filter_by(email=email).one().organization_id
            held = {(e.type, e.name) for e in ArchiMateElement.query.filter_by(organization_id=org_id).all()}
            assert {("BusinessService", "PickBot"), ("BusinessActor", "Dana Whitfield"),
                    ("Requirement", "Comply with ISO 27001"), ("ApplicationComponent", "SAP")} <= held
            assert ("BusinessActor", "Priya Raman") not in held
    finally:
        context.close()


def test_a_person_who_gives_no_website_sees_no_profile_step_to_confirm(browser, server, people, app):
    context, page = new_page(browser)
    try:
        sign_up(page, str(server), people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_role("heading", name="About your company")).to_be_visible()
        expect(page.get_by_test_id("signup-facts")).to_have_count(0)
        expect(page.get_by_label("Company size")).to_be_visible()
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")
    finally:
        context.close()
