"""A visitor opens a module page and sees its picture at the size it was made, described for the screen they use.

At desktop width the wide picture shows at half its pixel width (never stretched) and carries the desktop
description; at phone width the phone picture shows instead, with its own description, also at half its pixel width.
"""
import json

import pytest

from app.services.public_pages import (
    IMG_MODULES_DIR,
    MODULE_CAPTURES,
    MODULE_CAPTURES_MANIFEST,
    MODULE_PHONE_ALTS,
    STANDARD_MODULE_IMAGES,
)

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ALT = {slug: alt for slug, _p, _persona, _c, alt in MODULE_CAPTURES}


def _shown(live_server, browser, slug, width):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
    page = context.new_page()
    page.goto(live_server + "/modules/" + slug, wait_until="load", timeout=PAGE_TIMEOUT)
    img = page.locator("[data-testid=page-screenshot] img:visible")
    img.first.scroll_into_view_if_needed()
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=page-screenshot] img')]"
                           ".filter(i => i.offsetParent).every(i => i.complete && i.naturalWidth)")
    shown = img.evaluate_all("(els) => els.map(e => ({alt: e.alt, width: e.getBoundingClientRect().width,"
                             " natural: e.naturalWidth}))")
    context.close()
    return shown


@pytest.mark.parametrize("slug", sorted(STANDARD_MODULE_IMAGES))
def test_desktop_picture_is_shown_at_half_its_pixels_with_the_desktop_description(live_server, browser, slug):
    manifest = json.loads((IMG_MODULES_DIR / MODULE_CAPTURES_MANIFEST).read_text(encoding="utf-8"))
    shown = _shown(live_server, browser, slug, 1440)

    assert len(shown) == 1, "exactly one picture is shown at desktop width"
    assert shown[0]["alt"] == ALT[slug]
    # its own CSS width, give or take the 1 px frame: never stretched to the column
    assert abs(shown[0]["width"] - manifest[slug]["desktop"]["css_width"]) <= 2
    assert shown[0]["natural"] == 2 * manifest[slug]["desktop"]["css_width"]


@pytest.mark.parametrize("slug", sorted(STANDARD_MODULE_IMAGES))
def test_phone_picture_is_shown_with_its_own_description(live_server, browser, slug):
    manifest = json.loads((IMG_MODULES_DIR / MODULE_CAPTURES_MANIFEST).read_text(encoding="utf-8"))
    shown = _shown(live_server, browser, slug, 390)

    assert len(shown) == 1, "exactly one picture is shown at phone width"
    assert shown[0]["alt"] == MODULE_PHONE_ALTS.get(slug, ALT[slug])
    assert shown[0]["natural"] == 2 * manifest[slug]["phone"]["css_width"]
    assert round(shown[0]["width"]) <= manifest[slug]["phone"]["css_width"] + 2
