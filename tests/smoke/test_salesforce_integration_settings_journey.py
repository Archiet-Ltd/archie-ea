"""A platform administrator saves Salesforce credentials, reloads and reads them back, and is told in words what happened.

Starts from the goal: "connect our Salesforce org so Entelim can discover its apps". The journey saves the
credentials, reloads and reads every setting back with no secret in the page, is refused in words for a wrong
address with the typed values kept, and runs the connection test and the discovery control, each of which
reports in a sentence. The page must read as the same family as the GitHub connector page.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
URL = "https://journey-org.my.salesforce.com"
KEY = "3MVG9journeyconsumerkey0001"
SECRET = "journey-consumer-secret-0001"
PAGE = "/admin/integrations/salesforce"

# Runs inside the server subprocess, before the application: a made-up Salesforce org resolves to a public
# address and answers from here, never from the network. The right key and secret get a token and two apps
# plus one package; anything else is refused the way Salesforce refuses it.
_SITECUSTOMIZE = '''
def _install():
    import socket
    from urllib.parse import urlparse

    import requests

    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kw):
        if isinstance(host, str) and host.endswith(".salesforce.com"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        return real_getaddrinfo(host, *args, **kw)

    socket.getaddrinfo = getaddrinfo
    original = requests.Session.request

    class _Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.text = str(body)
            self.headers = {}

        def json(self):
            return self._body

    def dispatch(self, method=None, url=None, **kw):
        host = urlparse(url).hostname or "" if isinstance(url, str) else ""
        if not host.endswith(".salesforce.com"):
            return original(self, method, url, **kw)
        if url.endswith("/services/oauth2/token"):
            data = kw.get("data") or {}
            if data.get("client_id") == "%(key)s" and data.get("client_secret") == "%(secret)s":
                return _Resp(200, {"access_token": "journey-token"})
            return _Resp(400, {"error": "invalid_client"})
        if url.endswith("/limits"):
            return _Resp(200, {})
        if "/tooling/query" in url:
            return _Resp(200, {"records": [{"Id": "0A3-1", "SubscriberPackage": {"Name": "Journey Package", "NamespacePrefix": "jp"},
                                           "SubscriberPackageVersion": {"Name": "1.0"}}]})
        if url.endswith("/query"):
            return _Resp(200, {"records": [
                {"DurableId": "06m-1", "Label": "Sales Console", "DeveloperName": "Sales_Console", "NamespacePrefix": None},
                {"DurableId": "06m-2", "Label": "Service Console", "DeveloperName": "Service_Console", "NamespacePrefix": None},
            ]})
        return _Resp(404, {})

    requests.Session.request = dispatch


_install()
''' % {"key": KEY, "secret": SECRET}


@pytest.fixture(scope="module")
def sf_server(request, app, tmp_path_factory):
    """A server whose Salesforce org is a stub: a made-up host resolves and answers from the test."""
    folder = tmp_path_factory.mktemp("salesforce_peer")
    (folder / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    path = os.pathsep.join(p for p in (str(folder), os.environ.get("PYTHONPATH", "")) if p)
    return boot_live_server(request, None, app, extra_env={"PYTHONPATH": path, "ALLOW_EXTERNAL_API_CALLS": "true"})


@pytest.fixture()
def admin_page(browser, sf_server):
    org = create_fresh_org("solution_architect", org_admin=True, platform_role="Administrator")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    page = context.new_page()
    sign_in(page, sf_server, org["emails"]["solution_architect"])
    yield page
    context.close()


def _open(page, base):
    page.goto(base + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", level=1)).to_contain_text("Salesforce")


def test_every_setting_has_a_label_and_the_page_is_one_card(admin_page, sf_server):
    _open(admin_page, sf_server)
    page = admin_page
    for label in ("Instance URL", "Consumer Key", "Consumer Secret"):
        expect(page.get_by_label(label, exact=False).first).to_be_visible()
    expect(page.locator("[data-testid=sf-form] button[type=submit]")).to_have_count(1)
    expect(page.get_by_role("button", name="Test connection")).to_be_visible()
    expect(page.get_by_test_id("sf-state")).to_have_text("Not set")
    # Before anything is saved the discovery card says why it is empty and what to do next.
    expect(page.get_by_test_id("sf-discovery-empty")).to_contain_text("Save the credentials")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_saved_credentials_read_back_and_the_secret_is_never_echoed(admin_page, sf_server):
    _open(admin_page, sf_server)
    page = admin_page
    page.get_by_label("Instance URL").fill(URL)
    page.get_by_label("Consumer Key").fill(KEY)
    page.get_by_label("Consumer Secret").fill(SECRET)
    page.get_by_role("button", name="Save credentials").click()
    expect(page.get_by_test_id("sf-saved")).to_contain_text("Credentials saved")
    expect(page.get_by_test_id("sf-state")).to_have_text("Connected")

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("sf-state")).to_have_text("Connected")
    expect(page.get_by_label("Instance URL")).to_have_value(URL)
    expect(page.get_by_label("Consumer Key")).to_have_value(KEY)
    html = page.content()
    assert SECRET not in html
    expect(page.get_by_label("Consumer Secret")).to_have_value("")
    expect(page.get_by_label("Consumer Secret")).to_have_attribute("placeholder", "Leave blank to keep the existing value")

    # Saving again with the secret left blank keeps the stored secret and changes only what was typed.
    page.get_by_label("Consumer Key").fill(KEY + "-2")
    page.get_by_role("button", name="Save credentials").click()
    expect(page.get_by_test_id("sf-saved")).to_contain_text("Credentials saved")
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_label("Consumer Key")).to_have_value(KEY + "-2")
    expect(page.get_by_test_id("sf-state")).to_have_text("Connected")


def test_a_wrong_address_is_refused_in_words_and_the_typed_values_are_kept(admin_page, sf_server):
    _open(admin_page, sf_server)
    page = admin_page
    page.get_by_label("Instance URL").fill("https://evil.example.com")
    page.get_by_label("Consumer Key").fill(KEY)
    page.get_by_role("button", name="Save credentials").click()
    expect(page.get_by_test_id("sf-error-instance_url")).to_contain_text("Salesforce")
    expect(page.get_by_label("Instance URL")).to_have_value("https://evil.example.com")
    expect(page.get_by_label("Consumer Key")).to_have_value(KEY)
    page.goto(sf_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("sf-state")).to_have_text("Not set")
    expect(page.get_by_label("Instance URL")).to_have_value("")

    # A missing key is named, not a status code.
    page.get_by_label("Instance URL").fill(URL)
    page.get_by_role("button", name="Save credentials").click()
    expect(page.get_by_test_id("sf-error-client_id")).to_contain_text("Consumer Key")


def test_test_and_discover_report_their_result_in_words(admin_page, sf_server):
    _open(admin_page, sf_server)
    page = admin_page
    page.get_by_role("button", name="Discover").click()
    expect(page.get_by_test_id("sf-result-error")).to_contain_text("Save")
    page.get_by_label("Instance URL").fill(URL)
    page.get_by_label("Consumer Key").fill(KEY)
    page.get_by_label("Consumer Secret").fill("a-wrong-secret")
    page.get_by_role("button", name="Test connection").click()
    # Salesforce refuses the wrong secret: the result is a sentence naming what to check.
    expect(page.get_by_test_id("sf-result-error")).to_contain_text("Consumer Key")
    expect(page.get_by_test_id("sf-result-error")).not_to_contain_text("undefined")
    page.get_by_label("Consumer Secret").fill(SECRET)
    page.get_by_role("button", name="Test connection").click()
    expect(page.get_by_test_id("sf-result-ok")).to_contain_text("Connected to")
    page.get_by_role("button", name="Save credentials").click()
    expect(page.get_by_test_id("sf-saved")).to_be_visible()
    # Discovery lists what the org holds, with a count that equals the rows, and import is offered.
    page.get_by_role("button", name="Discover").click()
    expect(page.get_by_test_id("sf-result-ok")).to_contain_text("Discovered 3")
    expect(page.locator("tbody tr")).to_have_count(3)
    expect(page.get_by_text("Sales Console")).to_be_visible()
    expect(page.get_by_text("Journey Package")).to_be_visible()
    expect(page.get_by_test_id("sf-discovery-empty")).to_be_hidden()


def test_a_refused_login_is_never_reported_as_zero_found(admin_page, sf_server):
    _open(admin_page, sf_server)
    page = admin_page
    page.get_by_label("Instance URL").fill(URL)
    page.get_by_label("Consumer Key").fill(KEY)
    page.get_by_label("Consumer Secret").fill("a-wrong-secret")
    page.get_by_role("button", name="Save credentials").click()
    expect(page.get_by_test_id("sf-saved")).to_be_visible()
    page.get_by_role("button", name="Discover").click()
    expect(page.get_by_test_id("sf-result-error")).to_contain_text("Salesforce did not accept")
    expect(page.get_by_test_id("sf-result-ok")).to_be_hidden()


def test_it_reads_as_the_github_connector_page(admin_page, sf_server):
    page = admin_page
    page.goto(sf_server + "/admin/connectors/github", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reference = page.locator("[data-testid=github-connect-form] [data-slot=card]").first.bounding_box()
    _open(page, sf_server)
    card = page.locator("[data-testid=sf-form] [data-slot=card]").first.bounding_box()
    assert abs(card["width"] - reference["width"]) <= 1, (card, reference)
    assert abs(card["x"] - reference["x"]) <= 1, (card, reference)
