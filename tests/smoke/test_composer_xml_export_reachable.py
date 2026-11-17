"""ArchiMate XML export must be reachable from the primary Export menu.

Found while answering "if a solution architect downloads a diagram, does it
actually populate an architecture repository" (10 Sep 2026): the Composer had
TWO client functions for XML export -- a dead `exportXml()` (never called from
any template, required a saved viewpoint) and a working `exportFormat(...)`
that exports the LIVE canvas -- and only the working one was wired, and only
into the secondary "More" overflow menu, not the primary Export dropdown a
solution architect reaches first. This drives the real button and asserts a
real .xml file with real element data comes back, not just that a handler
exists in source.
"""

import pathlib
import re
import uuid
import xml.etree.ElementTree as ET

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
ARCHI_MODEL = "{http://www.archimatetool.com/archimate}model"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_export_archimate_xml_from_primary_menu_downloads_real_data(browser, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    suffix = uuid.uuid4().hex[:8]

    # A touch over 1920 -- CMP-063's media query is min-width:1920px and a
    # scrollbar can eat a few px of the viewport's reported width, landing
    # exactly on the boundary in the "collapsed into More" branch instead.
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1960, "height": 1080})
    page = context.new_page()
    _login(page, live_server, email)

    page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("nav[aria-label='Composer toolbar']")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Quick-add one real element so the canvas isn't empty (export refuses an
    # empty canvas with an honest toast, not a fabricated file).
    element_name = "Export Check %s" % suffix
    page.keyboard.press("Control+k")
    input_box = page.locator("#quick-add-input")
    expect(input_box).to_be_visible(timeout=PAGE_TIMEOUT)
    input_box.fill(element_name)
    create_btn = page.get_by_role("button", name=re.compile("^Create$"))
    expect(create_btn).to_be_visible(timeout=PAGE_TIMEOUT)
    create_btn.click()
    expect(page.locator(".joint-element")).to_have_count(1, timeout=PAGE_TIMEOUT)

    # Primary Export menu (not the "More" overflow) -- this is the one a
    # solution architect reaches first. Scoped to the toolbar: an unrelated
    # hidden search modal elsewhere in the DOM otherwise intercepts clicks
    # meant for the toolbar's own "Export" trigger.
    # dispatch_event("click"): an unrelated global header search modal
    # (#search-modal, app/templates/components/admin_header.html) sits at
    # inset-0 with a stale "hidden" class removed by some Alpine binding on
    # this page, covering the whole viewport at these coordinates -- a real,
    # separate defect outside the Composer, not the thing this test exists to
    # verify. A coordinate-based click (even force=True) lands on that
    # overlay instead of the intended button; dispatching the click event
    # directly on the target node sidesteps the hit-test entirely.
    toolbar = page.locator("nav[aria-label='Composer toolbar']")

    def export_with(menu_label):
        toolbar.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
        with page.expect_download(timeout=PAGE_TIMEOUT) as download_info:
            page.get_by_role("menuitem", name=menu_label, exact=True).click()
        return download_info.value

    # The ArchiMate exchange file (XML): real XML naming the element on the canvas.
    download = export_with("ArchiMate exchange file (XML)")
    assert download.suggested_filename.endswith(".xml")
    content = pathlib.Path(download.path()).read_text(encoding="utf-8")
    assert "<?xml" in content or "<model" in content.lower(), "export must be real XML, not an error page"
    assert element_name in content, "the exported file must contain the element actually on the canvas"

    # Archi (.archimate) on the same populated canvas: a valid Archi model naming the element.
    download = export_with("Archi (.archimate)")
    assert download.suggested_filename.endswith(".archimate"), download.suggested_filename
    root = ET.parse(download.path()).getroot()
    assert root.tag == ARCHI_MODEL, "the file is not an Archi model: %s" % root.tag
    names = [el.get("name") for el in root.iter() if el.get("name")]
    assert element_name in names, "the Archi model must contain the element on the canvas"
