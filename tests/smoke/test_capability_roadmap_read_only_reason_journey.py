"""A data analyst opening the capability roadmap is told, on each action, why they cannot use it.

The analyst sees Detect Gaps, Add Plateau, Add Gap and Add Work Package disabled, each with the
sentence "Your role may read the roadmap but not change it." The architect of the same
organisation, with the same recorded plateau, sees all four live.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
BASE = "/capability-map/api/roadmap"
REASON = "Your role may read the roadmap but not change it."
LABELS = ("Detect Gaps", "Add Plateau", "Add Gap", "Add Work Package")


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _frame(page, label, width, theme):
    out = os.environ.get("ROADMAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/roadmap-%s-%d-%s.png" % (out, label, width, theme), full_page=True)


def test_an_analyst_is_told_why_and_an_architect_can_act(browser, live_server):
    org = create_fresh_org("data_analyst", platform_role="Viewer", extra_roles=("enterprise_architect",))
    suffix = org["suffix"]
    plateau_name = "Target %s" % suffix

    context, page = _page(browser, 1440, "light")
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, plateau = api(page, "POST", BASE + "/plateaus", {"name": plateau_name, "sequence_order": 1})
        assert status == 201, plateau
    finally:
        context.close()

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_text(plateau_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
            for label in LABELS:
                expect(page.get_by_role("button", name=label, exact=True).first).to_be_enabled()
            assert page.get_by_text(REASON).count() == 0
            _frame(page, "architect", width, theme)
        finally:
            context.close()

        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["data_analyst"])
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_text(plateau_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
            for label in LABELS:
                button = page.get_by_role("button", name=label, exact=True)
                expect(button).to_have_count(1)
                expect(button).to_be_disabled()
            expect(page.get_by_text(REASON)).to_have_count(len(LABELS))
            _frame(page, "viewer", width, theme)
            if width == 1440:
                status, answer = api(page, "POST", BASE + "/gaps/detect", {})
                assert status == 403 and REASON in str(answer)
        finally:
            context.close()
