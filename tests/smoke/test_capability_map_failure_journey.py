"""A CTO opens the capability map while the database cannot count; the page says so, plainly.

Goal: "which applications run each capability?" When the count of capabilities, or of the
applications mapped to them, cannot be read, the CTO must see the plain error page or an
error on the map, and never a capability that reads "0 apps" as if that were measured. The
same organisation, read while the counts work, shows the real application counts.

The failure is made inside the live server's own process by a start-up hook that lives in
this test (a sitecustomize module on that process's path); the application carries no switch
for it. It runs at 1440 wide in the light theme and at 390 wide in the dark theme.
"""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_capability_map_applications_journey import _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("CAPABILITY_FAILURE_SHOTS", "") or "/tmp/capability-failure-shots")

HOOK = '''
import os
import sys

mode = os.environ.get("JOURNEY_BREAK")
if mode:
    sys.path.insert(0, os.getcwd())
if mode == "count":
    from app.modules.capabilities.services import capability_count_service

    _real = capability_count_service.count_capabilities

    def _broken():
        # Only the capability map's own request fails, so the error page can still draw the shell.
        from flask import has_request_context, request

        if has_request_context() and request.path.startswith("/capability-map"):
            raise RuntimeError("database did not answer")
        return _real()

    capability_count_service.count_capabilities = _broken
elif mode == "mapping":
    from app.models import unified_application_capability_mapping as module

    class _Unreadable(type):
        def __getattr__(cls, name):
            raise RuntimeError("database did not answer")

    class Broken(metaclass=_Unreadable):
        pass

    module.UnifiedApplicationCapabilityMapping = Broken
'''


def _broken_server(request, ai_protocol_stub, app, tmp_path_factory, mode):
    hook_dir = tmp_path_factory.mktemp("failure_hook_" + mode)
    (hook_dir / "sitecustomize.py").write_text(HOOK)
    path = os.pathsep.join(filter(None, [str(hook_dir), os.environ.get("PYTHONPATH", "")]))
    return boot_live_server(request, ai_protocol_stub, app,
                            extra_env={
                                "PYTHONPATH": path,
                                "JOURNEY_BREAK": mode,
                                # As deployed: the testing configs let an exception through unhandled.
                                "FLASK_CONFIG": "production",
                                "TRANSFORMATION_COMMAND_CAPABILITY_SECRET": "journey-only-secret-" + "x" * 16,
                                "SESSION_COOKIE_SECURE": "false",
                                "REMEMBER_COOKIE_SECURE": "false",
                            })


@pytest.fixture(scope="module")
def count_down_server(request, ai_protocol_stub, app, tmp_path_factory):
    return _broken_server(request, ai_protocol_stub, app, tmp_path_factory, "count")


@pytest.fixture(scope="module")
def mapping_down_server(request, ai_protocol_stub, app, tmp_path_factory):
    return _broken_server(request, ai_protocol_stub, app, tmp_path_factory, "mapping")


@pytest.fixture
def estate():
    # A new organisation each time: the hierarchy answer is cached per organisation, and a cached
    # good answer would hide the failure from the next server.
    org = create_fresh_org("cto", org_admin=True)
    _seed(org["org_id"], "PAY-1", "Payments Processing", ["Billing Engine", "Ledger Service"])
    return org


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


VIEWS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


@pytest.mark.parametrize("viewport,scheme", VIEWS)
def test_cto_sees_real_application_counts_while_the_counts_work(browser, live_server, estate, viewport, scheme):
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, estate["emails"]["cto"])
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        tile = page.locator('[role="tabpanel"][aria-label="Map view"] [role="button"]',
                            has_text="Payments Processing")
        expect(tile).to_have_count(1, timeout=PAGE_TIMEOUT)
        tile.click()
        expect(page.locator('[role="dialog"][aria-labelledby="drawer-title-capability-detail"]')).to_contain_text("Supporting applications (2)")
        expect(page.get_by_test_id("capability-map-error")).to_have_count(0)
    finally:
        page.close()


@pytest.mark.parametrize("viewport,scheme", VIEWS)
def test_cto_sees_the_plain_error_page_when_capabilities_cannot_be_counted(
        browser, count_down_server, estate, viewport, scheme):
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, count_down_server, estate["emails"]["cto"])
        reply = page.goto(count_down_server + "/capability-map/", wait_until="domcontentloaded",
                          timeout=PAGE_TIMEOUT)
        assert reply.status == 500
        body = page.locator("body")
        expect(body).to_contain_text("Something went wrong")
        expect(body).not_to_contain_text("Payments Processing")
        expect(page.get_by_test_id("capability-total")).to_have_count(0)
        expect(body).not_to_contain_text("0 capabilities")
        _shot(page, "count-failed", viewport["width"], scheme)
    finally:
        page.close()


@pytest.mark.parametrize("viewport,scheme", VIEWS)
def test_cto_sees_an_error_not_zero_applications_when_the_mapping_count_fails(
        browser, mapping_down_server, estate, viewport, scheme):
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, mapping_down_server, estate["emails"]["cto"])
        with page.expect_response(lambda r: "/capability-map/api/capabilities/hierarchy" in r.url) as answer:
            page.goto(mapping_down_server + "/capability-map/", wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT)
        assert answer.value.status == 500
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("capability-map-error")).to_be_visible()
        expect(page.get_by_test_id("capability-map-error")).to_contain_text("The capability map could not load")
        body = page.locator("body")
        expect(body).not_to_contain_text("0 apps")
        expect(body).not_to_contain_text("Payments Processing")
        _shot(page, "mapping-failed", viewport["width"], scheme)
    finally:
        page.close()
