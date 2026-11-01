"""A new CTO gives their website at sign-up and is offered only the company's own social accounts.

The fixture company's home page links its own LinkedIn, X and GitHub accounts, and also a person's
profile, a post, a share link, a second account on the same network, a partner's account, a
powered-by video channel, a short name on a network the company is not on and a customer's LinkedIn page. In the company step each network appears once,
at the company's own address, and none of the others is offered. Every offered address is a
profile root a person can open.
"""

import os
import threading

import pytest
from http.server import ThreadingHTTPServer
from playwright.sync_api import expect

from tests.smoke.conftest import boot_live_server
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401
    new_page,
    people,
    SHOTS,
    shoot,
    sign_up,
)
from tests.smoke.test_signup_website_profile_journey import _Model, _row
from tests.smoke.website_wait import wait_for_the_model_pass
from tests.test_website_crawl import ABOUT, APP_SHELL, CONTACT, PRODUCTS, ROBOTS, _html, _Site

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OWN = {"LinkedIn": "https://www.linkedin.com/company/acme-robotics", "Twitter/X": "https://x.com/acmerobotics",
       "GitHub": "https://github.com/acme-robotics"}

HOME = """<!doctype html><html><head><title>Acme Robotics | Warehouse robots</title>
<meta name="description" content="Warehouse robots that pick and pack."></head><body>
<header><nav><a href="/about">About</a> <a href="/products">Products</a> <a href="/contact">Contact</a>
<a href="https://x.com/acmerobotics">Follow us</a></nav></header>
<main><h1>Acme Robotics</h1><p>We build warehouse robots that pick and pack orders for logistics operators,
delivered as a monthly subscription to businesses.</p>
<p>Our robots work alongside people in busy distribution centres, learn a new layout in a day and need no
changes to your racking or conveyors.</p>
<p><a href="https://www.linkedin.com/in/priya-raman">Priya on LinkedIn</a>
<a href="https://x.com/priyaraman/status/12345">Our launch post</a>
<a href="https://twitter.com/intent/tweet?text=acme">Share on X</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=acme">Share on Facebook</a>
<a href="https://www.linkedin.com/company/bigbank">Customer: Big Bank</a>
<a href="https://x.com/acme">The other Acme</a>
<a href="https://www.instagram.com/acme">Their photos</a>
<a href="https://x.com/acmerobotics-fans">Fan page</a>
<a href="https://www.youtube.com/watch?v=abc123">Watch the demo</a>
<a href="https://github.com/acme-robotics/pickbot/">PickBot repository</a></p></main>
<footer><a href="https://www.linkedin.com/company/acme-robotics">LinkedIn</a>
<a href="https://github.com/acme-robotics">GitHub</a>
<a href="https://www.youtube.com/@vercel">Powered by Vercel</a></footer></body></html>"""


@pytest.fixture(scope="module")
def company_site():
    pages = {"/": _html(HOME), "/about": _html(ABOUT), "/products": _html(PRODUCTS), "/contact": _html(CONTACT),
             "/app": _html(APP_SHELL), "/robots.txt": (200, "text/plain", ROBOTS)}
    site = _Site(pages)
    yield site
    site.stop()


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
            "NVIDIA_API_KEY": "sk-journey-reader-0001",
            "NVIDIA_BASE_URL": "http://127.0.0.1:%d/v1" % provider.server_port,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        },
    )


def test_the_company_step_offers_one_own_account_per_network_and_nothing_else(browser, server, people, company_site):
    context, page = new_page(browser)
    try:
        sign_up(page, str(server), people)
        page.get_by_label("Website address").fill(company_site.url)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_test_id("signup-facts")).to_be_visible(timeout=120000)
        wait_for_the_model_pass(page)

        accounts = _row(page, "Social profile")
        shown = [accounts.nth(i).locator("[data-fact-value]").input_value() for i in range(accounts.count())]
        # exactly the company's three accounts: one per network, each at its profile address, nothing else
        assert sorted(v.split(": ", 1)[1] for v in shown) == sorted(OWN.values()), shown
        linkedin = _row(page, "LinkedIn")
        assert [linkedin.nth(i).locator("[data-fact-value]").input_value() for i in range(linkedin.count())] \
            == [OWN["LinkedIn"]]
        shoot(page, "company-social-accounts")
        if SHOTS:  # the facts sit in a scroll box: show the account rows themselves at both widths
            for width, height in ((1440, 900), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                accounts.first.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                page.screenshot(path=os.path.join(SHOTS, "social-rows-%d.png" % width))
            page.set_viewport_size({"width": 1440, "height": 900})

        # Each offered address says which page of the company's site it was found on, and that page has content.
        for i in range(accounts.count()):
            source = accounts.nth(i).get_by_test_id("fact-source").get_attribute("href")
            landing = context.new_page()
            landing.goto(source)
            expect(landing.locator("body")).to_contain_text("Acme Robotics")
            landing.close()
    finally:
        context.close()
