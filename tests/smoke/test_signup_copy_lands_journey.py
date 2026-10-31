"""Every promise in the four sign-up steps lands where the person can go.

The person: a startup CTO signing up alone. A mistyped email keeps what they typed and says why Register is off;
while their website is being read, step 3 says what to do now and refreshes itself; the reading line never
shows under the AI chat step; "Account settings" opens the page the creator can use, never the administrator's.
Set SIGNUP_COPY_SHOTS to save frames.
"""
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (these are fixtures)
    new_page,
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("SIGNUP_COPY_SHOTS")
HOME = "<html><body><p>Welcome.</p><a href='/about'>About</a></body></html>"


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=True)


@pytest.fixture
def held_site():
    """A website whose first page answers at once and whose other pages wait until the test lets them go."""
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/robots.txt"):
                release.wait(120)
            body = (HOME if self.path == "/" else "<p>missing</p>").encode()
            self.send_response(200 if self.path == "/" else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    release.set()
    httpd.shutdown()


def test_a_mistyped_email_keeps_the_passwords_and_says_why_register_is_off(browser, server, people):
    context, page = new_page(browser)
    try:
        page.goto(str(server) + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        email = "casey.%s@nowhere" % uuid.uuid4().hex[:6]
        page.fill("#first_name", "Casey")
        page.fill("#last_name", "Founder")
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.locator("#submit").click()
        # The refusal is in words, and both password fields are still filled.
        expect(page.locator("main")).to_contain_text("We cannot send to this address")
        expect(page.locator("#password")).to_have_value(PASSWORD)
        expect(page.locator("#password2")).to_have_value(PASSWORD)
        _shot(page, "register-refused-email-1440-light")
        page.emulate_media(color_scheme="dark")
        page.evaluate("document.documentElement.classList.add('dark')")
        page.set_viewport_size({"width": 390, "height": 844})
        _shot(page, "register-refused-email-390-dark")
        page.evaluate("document.documentElement.classList.remove('dark')")
        page.set_viewport_size({"width": 1440, "height": 900})
        # With the password cleared the button is off and the page says why, under the button.
        page.fill("#password", "")
        expect(page.locator("#submit")).to_be_disabled()
        expect(page.get_by_test_id("register-disabled-reason")).to_have_text("Enter a password to continue")
        # A reserved but well-formed address is accepted by the check (the mail transport decides).
        page.fill("#email", "casey.%s@example.com" % uuid.uuid4().hex[:6])
        page.fill("#password", PASSWORD)
        expect(page.get_by_test_id("register-disabled-reason")).to_be_hidden()
    finally:
        context.close()



def _the_bar_draws_and_the_count_is_said_once(page):
    """The reading bar is a real bar (a track at least 200 px wide whose visible fill is the read share of it) and the
    page count is said by exactly one element."""
    bar = page.locator("[data-reading-bar] [role='progressbar']")
    expect(bar).to_be_visible()
    box = bar.bounding_box()
    assert box and box["width"] >= 200, "the reading bar is %s px wide" % (box and box["width"])
    now = float(bar.get_attribute("aria-valuenow"))
    assert now > 0
    indicator = bar.locator("[data-slot='progress-indicator']")
    assert indicator.count() == 1, "the bar has no library fill"
    fill = indicator.bounding_box()
    visible = max(0.0, min(fill["x"] + fill["width"], box["x"] + box["width"]) - max(fill["x"], box["x"]))
    assert abs(visible - box["width"] * now / 100.0) <= 2, "fill %s px of a %s px bar at %s%%" % (visible, box["width"], now)
    assert visible >= 4
    said = page.get_by_text(re.compile(r"page \d+ of up to \d+"))
    assert said.count() == 1, "the page count is said %d times on step 3" % said.count()
    assert page.get_by_text("Reading now:").count() == 0


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_the_four_steps_promise_only_what_the_person_can_reach(browser, server, people, held_site, width, dark):
    context, page = new_page(browser, width)
    try:
        if dark:
            page.emulate_media(color_scheme="dark")
            page.add_init_script("document.documentElement.classList.add('dark')")
        email, _started = sign_up(page, str(server), people)
        page.get_by_label("Website address").fill(held_site)
        page.get_by_label("You may read this website").check()
        page.get_by_role("button", name="Save website").click()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")

        # Step 3 while the read is held at page 1: what to do now, the bar, and a refresh every ten seconds.
        expect(page.get_by_role("heading", name="About your company")).to_be_visible()
        line = page.get_by_test_id("signup-reading-line")
        expect(line).to_contain_text(
            re.compile(r"We are reading .+: page 1 of up to 30\. Answer the questions below now; "
                       r"the facts we find will be waiting on your Overview to confirm"), timeout=60000)
        expect(page.locator("[data-reading-bar]")).to_be_visible()
        _the_bar_draws_and_the_count_is_said_once(page)
        assert page.locator("[data-signup-steps]").get_attribute("data-reload-when-read") is not None
        assert page.locator("[data-signup-steps]").get_attribute("data-refresh-seconds") == "10"
        marker = page.evaluate("window.__stayed = true")
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(time.monotonic()) if frame == page.main_frame else None)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not navigations:
            page.wait_for_timeout(1000)
        assert navigations, "step 3 did not refresh itself while the website was being read"
        _shot(page, "step3-reading-%d-%s" % (width, "dark" if dark else "light"))
        page.wait_for_load_state("domcontentloaded")
        _no_accessibility_violations(page)

        # "Account settings" on step 3 opens the creator's page with content, never /settings.
        link = page.get_by_role("link", name="Account settings")
        expect(link.first).to_have_attribute("href", re.compile(r"^/account/manage"))
        links = _crawl_the_links(page, str(server))
        assert links, "step 3 has no links"

        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        # Step 4: nothing about the website read, no website error.
        expect(page.get_by_role("heading", name="Your AI chat")).to_be_visible()
        assert page.get_by_test_id("website-reading").count() == 0
        assert page.locator("[data-source='website']").count() == 0
        assert "We are reading" not in page.locator("main").inner_text()
        _shot(page, "step4-%d-%s" % (width, "dark" if dark else "light"))
        _no_accessibility_violations(page)
        links += _crawl_the_links(page, str(server))
        link = page.get_by_role("link", name="Account settings")
        expect(link.first).to_have_attribute("href", re.compile(r"^/account/manage"))
        assert not any("/settings" == href.split("?")[0] for href in links), links
        link.first.click()
        page.wait_for_url(re.compile(r".*/account/manage"))
        assert page.locator("main").inner_text().strip()
    finally:
        context.close()


def _crawl_the_links(page, base):
    """Every same-site link on the open step, followed signed in; none may answer 403 or 404."""
    hrefs = sorted({h for h in page.eval_on_selector_all("main a[href]", "els => els.map(e => e.getAttribute('href'))")
                    if h.startswith("/") and not h.startswith("//") and "/static/" not in h})
    for href in hrefs:
        if href.startswith("/oauth") or "logout" in href:
            continue
        response = page.request.get(base + href)
        assert response.status not in (403, 404), "%s answered %s" % (href, response.status)
    return hrefs


def _no_accessibility_violations(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, options={"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]}})
    data = report.response if hasattr(report, "response") else report
    found = [(v["id"], [n["target"] for n in v["nodes"]][:3]) for v in data.get("violations", [])]
    assert not found, found
