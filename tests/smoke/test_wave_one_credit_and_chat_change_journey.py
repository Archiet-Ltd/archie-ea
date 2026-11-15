"""Journey: with RELEASE_WAVE=1 a founder still claims the startup credit and an architect still saves a chat change.

Written from two goals on the first release's path:

* a founder opens the billing page, declares the company, presses "Start 90 days free" and, after a
  reload, still sees Startup with its end date;
* an architect asks the assistant to change an application, presses Save on the preview and, after a
  reload, sees the change on the application's own page; the same name in another organisation is untouched.

Both controls send a write; the journey runs the server with the switch on, so a write the release
closes answers 404 and the page shows no change. Run with WAVE_JOURNEY_SWITCH=off for the unswitched server.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .chat_ask import open_chat, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_chat_application_change_journey import ARCHITECT, NEW_DESCRIPTION, _description, _seed, _send
from .test_startup_credit_journey import _claim, _founded_months_ago, _give_website, _sign_up

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SWITCH = "" if os.environ.get("WAVE_JOURNEY_SWITCH") == "off" else "1"


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": SWITCH})


def _frames(page, tmp_path, name):
    folder = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    for width, theme in ((1440, "light"), (390, "dark")):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=str(folder / ("wave-one-%s-%d-%s.png" % (name, width, theme))), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("() => document.documentElement.classList.remove('dark')")


def test_a_founder_claims_the_startup_credit_in_the_first_release(browser, wave_server, app, tmp_path):
    domain = "wave-%s.example" % uuid.uuid4().hex[:8]
    email = "ana@%s" % domain
    context, page = _sign_up(browser, wave_server, email)
    try:
        _give_website(app, email, "https://%s" % domain)
        page.goto(wave_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _frames(page, tmp_path, "credit-form")
        _claim(page, wave_server, "https://%s" % domain, _founded_months_ago(8))
        ends = (datetime.utcnow() + timedelta(days=90)).strftime("%d %b %Y").lstrip("0")
        for turn in range(2):
            expect(page.get_by_test_id("billing-current-plan")).to_have_text("Startup", timeout=PAGE_TIMEOUT)
            expect(page.get_by_text("Free for 90 days", exact=True)).to_be_visible()
            expect(page.get_by_test_id("billing-renewal")).to_have_text(ends)
            expect(page.locator("#startup-credit")).to_have_count(0)
            if turn == 0:
                _frames(page, tmp_path, "credit-applied")
                page.reload(wait_until="domcontentloaded")
    finally:
        context.close()


@pytest.fixture
def orgs():
    name = "Ifiok %s" % uuid.uuid4().hex[:5]
    out = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org.update(_seed(org["org_id"], name))
        org["name"] = name
        out.append(org)
    return out


def test_an_architect_saves_a_chat_change_in_the_first_release(page, wave_server, orgs, tmp_path):
    mine, theirs = orgs
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, wave_server, mine["emails"][ARCHITECT])
    open_chat(page, wave_server)
    preview = _send(page, "set %s's description to %s" % (mine["name"], NEW_DESCRIPTION))
    expect(preview).to_contain_text("Nothing is saved until you press Save")
    _frames(page, tmp_path, "change-preview")
    preview.get_by_test_id("application-change-save").click()
    expect(preview).to_contain_text("Saved: the description of %s is changed" % mine["name"], timeout=PAGE_TIMEOUT)
    assert _description(mine["app_id"]) == NEW_DESCRIPTION
    _frames(page, tmp_path, "change-saved")
    page.goto("%s/applications/%d" % (wave_server, mine["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="networkidle")
    expect(page.locator("main")).to_contain_text(NEW_DESCRIPTION)
    assert _description(theirs["app_id"]) == "Old description"
