"""The public site's product screenshots are shown 1:1 (marketing review v1, S3 / P-03 / P-06).

A screenshot captured at 2x and displayed with its own CSS width keeps the UI's
12-14px text at 12-14px; one squeezed into a narrower column (the 60% scale the
review measured) does not. These tests pin the width/height the templates declare
to the pixels actually on disk, so a re-capture that changes a crop's size cannot
silently put the shrinking back.
"""

from __future__ import annotations

import re

import pytest
from PIL import Image

from app.services import public_pages
from app.services.public_pages import IMG_MODULES_DIR, STATIC_ROOT, TWO_X_MODULE_IMAGES, get_page_screenshot, load_page

HOME_IMG = STATIC_ROOT / "img" / "home"
# Narrowest the public figure column can be while still showing the desktop crop at 1:1
# (72rem container less 2 x 1.5rem padding).
FIGURE_WIDTH = 72 * 16 - 48
MIN_UI_TEXT_PX = 12


def _css_size(path, density=2):
    with Image.open(path) as im:
        return im.size[0] // density, im.size[1] // density


@pytest.mark.parametrize("slug", sorted(TWO_X_MODULE_IMAGES))
def test_module_screenshot_is_two_x_and_reported_at_css_size(slug):
    page = load_page("module", slug=slug)
    shot = get_page_screenshot(page)
    assert shot is not None
    assert (shot["width"], shot["height"]) == _css_size(IMG_MODULES_DIR / f"{slug}.webp")
    # Shown at 1:1 inside the public figure column, never shrunk to fit it.
    assert shot["width"] <= FIGURE_WIDTH, f"{slug}: {shot['width']}px crop would be shrunk in a {FIGURE_WIDTH}px column"
    assert shot["width"] >= 900, f"{slug}: crop is too narrow to be the whole panel"


@pytest.mark.parametrize("slug", sorted(TWO_X_MODULE_IMAGES))
def test_module_screenshot_has_a_phone_width_variant_shown_at_natural_size(slug):
    shot = get_page_screenshot(load_page("module", slug=slug))
    mobile = shot["mobile"]
    assert mobile is not None
    assert (mobile["width"], mobile["height"]) == _css_size(IMG_MODULES_DIR / f"{slug}-mobile.webp")
    assert mobile["width"] <= 390 - 32, "phone crop must fit a 390px screen with 16px gutters, unshrunk"


def test_other_module_screenshots_keep_their_one_x_size():
    shot = get_page_screenshot(load_page("module", slug="vendors"))
    assert shot["mobile"] is None
    with Image.open(IMG_MODULES_DIR / "vendors.webp") as im:
        assert (shot["width"], shot["height"]) == im.size


def test_home_hero_declares_the_captured_pixels(app, client):
    html = client.get("/").get_data(as_text=True)
    assert 'data-testid="home-hero-figure"' in html
    for name, width_attr in (("impact-answer.webp", 644), ("impact-answer-mobile.webp", 358)):
        w, h = _css_size(HOME_IMG / name)
        assert w == width_attr
        tag = re.search(r'(?:width="%d"\s+height="(\d+)"|height="(\d+)"\s+width="%d")' % (w, w), html)
        assert tag, f"{name}: hero markup does not declare {w}px width"
        assert int(tag.group(1) or tag.group(2)) == h, f"{name}: declared height differs from the file's {h}px"


def test_home_hero_chip_is_short_on_phones_and_has_one_primary_action(app, client):
    html = client.get("/").get_data(as_text=True)
    assert "Open source &middot; Self-host free" in html
    hero = html[html.index('data-testid="home-hero"'): html.index('data-testid="home-hero-figure"')]
    assert hero.count("public-btn") == 1, "one primary button; 'See pricing' is a text link"
    assert 'href="/pricing"' in hero


def test_capture_script_keeps_its_refusals():
    """The capture script must refuse to photograph an empty-looking demo."""
    source = (public_pages.STATIC_ROOT.parent.parent / "scripts" / "capture_proof_assets.py").read_text(encoding="utf-8")
    assert "run seed-demo-company first" in source
    assert "device_scale_factor=DPR" in source
