"""A link to a row past the first page of a list opens the page holding that row.

An enterprise architect follows a link to one gap among thirty. The gap list shows 25 rows to a page; the gap the
link names is on the second page, and the person must see that gap, not an empty table.
"""

import os
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

def _shot_dir():
    os.makedirs("test-results/visible-lows", exist_ok=True)
    return "test-results/visible-lows/"


pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _gaps(org_id, suffix, count):
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        rows = [Gap(name="Gap %02d %s" % (n, suffix), severity="medium", organization_id=org_id)
                for n in range(count)]
        db.session.add_all(rows)
        db.session.commit()
        ids = [r.id for r in rows]
        db.session.remove()
    return ids


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_a_link_to_a_row_on_the_second_page_shows_that_row(browser, live_server, width, scheme):
    org = create_fresh_org("enterprise_architect")
    ids = _gaps(org["org_id"], org["suffix"], 30)
    target = ids[27]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900},
                                  color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/implementation/gap-analysis#gap-%d" % target,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("#gap-%d" % target)
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text("Gap 27 %s" % org["suffix"])
        expect(page.get_by_text("Page 2 of 2")).to_be_visible()
        # The first row of the list is on the other page and is not shown.
        expect(page.locator("#gap-%d" % ids[0])).to_be_hidden()
        # Paging back finds the rows that were hidden.
        page.get_by_role("button", name="Go to previous page").click()
        expect(page.locator("#gap-%d" % ids[0])).to_be_visible()
        page.screenshot(path=_shot_dir() + "anchor-%d-%s.png" % (width, scheme))
    finally:
        context.close()
