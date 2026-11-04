"""A business architect hands a model to the Archi desktop tool and finds the choice named plainly.

The business model page offers "Archi (.archimate)" in its Download list, and choosing it
brings a real model file. The composer's Export menu names the same choice the same way.
"""
import os
import pathlib
import re
import xml.etree.ElementTree as ET

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
LABEL = "Archi (.archimate)"
ARCHI_MODEL = "{http://www.archimatetool.com/archimate}model"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _shot(page, name, dark=False):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if not folder:
        return
    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
    page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
    page.screenshot(path=str(pathlib.Path(folder) / f"{name}-{page.viewport_size['width']}-{'dark' if dark else 'light'}.png"))
    page.evaluate("document.documentElement.classList.remove('dark')")


def _make_canvas(org_id):
    from app import create_app, db
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name="Archi Export Canvas", organization_id=org_id)
        db.session.add(canvas)
        db.session.flush()
        _seed_canvas_model(org_id, canvas)
        db.session.commit()
        return canvas.id


@pytest.mark.smoke
@pytest.mark.journey
def test_business_model_download_offers_archi_by_name_and_delivers_the_file(browser, live_server, seeded):
    email = seeded["emails"]["business_architect"]
    canvas_id = _make_canvas(seeded["ids"]["org"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(f"{live_server}/business-model/{canvas_id}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        select = page.get_by_test_id("bmc-export-format")
        expect(select).to_be_visible(timeout=PAGE_TIMEOUT)
        assert LABEL in select.locator("option").all_inner_texts()
        select.select_option(label=LABEL)
        _shot(page, "business-model-download")
        with page.expect_download(timeout=PAGE_TIMEOUT) as info:
            page.get_by_test_id("bmc-export-file").click()
        download = info.value
        assert download.suggested_filename.endswith(".archimate"), download.suggested_filename
        root = ET.parse(download.path()).getroot()
        assert root.tag == ARCHI_MODEL, "the file is not an Archi model: %s" % root.tag
        names = [el.get("name") for el in root.iter() if el.get("name")]
        assert "Archi Export Canvas" in names, "the seeded canvas is missing from the model"
        assert len(names) > 1, "the model holds no elements of the canvas"
        assert "Municipal water utilities" in names, "a box entry seeded on the canvas is missing from the exported file"
        page.set_viewport_size({"width": 390, "height": 844})
        expect(select).to_be_visible()
        _shot(page, "business-model-download", dark=True)
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_composer_export_menu_names_archi_the_same_way(browser, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1960, "height": 1080})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/archimate/composer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        toolbar = page.locator("nav[aria-label='Composer toolbar']")
        expect(toolbar).to_be_visible(timeout=PAGE_TIMEOUT)
        toolbar.get_by_role("button", name=re.compile("^Export$")).dispatch_event("click")
        item = page.get_by_role("menuitem", name=LABEL, exact=True)
        expect(item).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("menuitem", name="Archi model", exact=True)).to_have_count(0)
        _shot(page, "composer-export-menu")
        _shot(page, "composer-export-menu", dark=True)
        # An empty canvas says why nothing is exported instead of doing nothing.
        item.click()
        expect(page.get_by_text("Nothing on the canvas to export").first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
