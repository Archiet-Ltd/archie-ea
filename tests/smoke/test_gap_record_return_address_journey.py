"""Gaps page: after saving a gap record the page returns only to this site.

A person follows a link that asks the board to open the gap form and come back
afterwards. The return address is honoured only when it is a path on this site;
an address that a browser would read as another site falls back to the gap list.
"""
import re
import uuid
from urllib.parse import quote

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_gap_record_journey import PATH, _fresh_org_with_computed_gap, _seed_phase

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _save_gap_and_land(browser, live_server, back):
    from .fresh_org import sign_in

    _seed_phase()
    org = _fresh_org_with_computed_gap()
    name = "Return gap %s" % uuid.uuid4().hex[:6]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    # No request may leave the test machine; an off-site navigation shows up as a
    # request to a host that is not the live server.
    off_site = []

    def _route(route):
        if route.request.url.startswith(live_server):
            route.continue_()
            return
        off_site.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body="<html><body>off-site</body></html>")

    context.route("**/*", _route)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto("%s/adm-kanban/?new=Gap&next=%s" % (live_server, quote(back, safe="")),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        dialog = page.get_by_role("dialog", name="New Architecture Task")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        dialog.get_by_label("Title").fill(name)
        dialog.get_by_role("button", name=re.compile(r"^(Create|Save|Add)", re.I)).last.click()
        page.wait_for_url(lambda u: "/adm-kanban/?" not in u and not u.rstrip("/").endswith("/adm-kanban"),
                          timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("domcontentloaded")
        return page.url, off_site, page
    finally:
        context.close()


@pytest.mark.parametrize("back", ["/\\evil.com", "//evil.com", "https://evil.com"])
def test_off_site_return_address_falls_back_to_the_gap_list(browser, live_server, back):
    url, off_site, _ = _save_gap_and_land(browser, live_server, back)
    assert off_site == [], "left the site for %s" % off_site
    assert url.startswith(live_server) and url.split("?")[0].rstrip("/").endswith(PATH)


def test_same_site_return_address_is_kept(browser, live_server):
    url, off_site, _ = _save_gap_and_land(browser, live_server, "/gaps/123")
    assert off_site == []
    assert url == live_server + "/gaps/123"
