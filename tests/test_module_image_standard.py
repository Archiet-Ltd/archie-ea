"""The capture standard for public product images (scripts/capture/clip_kit.py, capture_standard_image).

Two halves. The recipe's refusals are tested on their own (no browser): a date, the signed-in person, a build id,
an empty state or an oversize file never becomes an image. The shipped images are then measured from the files:
every module image in STANDARD_MODULE_IMAGES has a desktop and a phone version, each captured at 2x, shown at
half its pixel width, no wider than the page can show it (so it is never shrunk below 1:1 and its 12 px text stays
12 px), under 300 KB, and recorded in captures.json with its measured smallest text.
"""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from app.services.public_pages import (
    IMG_MODULES_DIR,
    MODULE_CAPTURES_MANIFEST,
    STANDARD_MODULE_IMAGES,
    load_page,
    get_page_screenshot,
)
from app.commands.seed_demo_company import _DEMO_USER_FIRST, _DEMO_USER_LAST
from scripts.capture import clip_kit

DESKTOP_CSS_MAX = clip_kit.STANDARD_DESKTOP["content_max"]
PHONE_CSS_MAX = clip_kit.STANDARD_PHONE["content_max"]


@pytest.mark.parametrize("text", ["Updated 7 Nov 2026", "2026-11-07", "Nov 7, 2026", f"{_DEMO_USER_FIRST} {_DEMO_USER_LAST}",
                                  "demo@lantern.example",
                                  "Build d65ba903"])
def test_recipe_refuses_a_date_a_name_or_a_build_id(text):
    assert clip_kit.NOT_IN_STANDARD_FRAME.search(text)


@pytest.mark.parametrize("text", ["No typed ARB reviews yet", "Not recorded", "Readiness\t0", "unavailable",
                                  "Lantern Quay\tNone\t3 systems", "PagerDuty\t2\tNone", "None renew within 90 days"])
def test_recipe_refuses_empty_and_unset_states(text):
    assert clip_kit.FORBIDDEN_IN_FRAME.search(text)


def test_recipe_accepts_real_figures():
    text = "20 applications, £3,630,000 approved budget, 33.3% approval rate"
    assert not clip_kit.NOT_IN_STANDARD_FRAME.search(text) and not clip_kit.FORBIDDEN_IN_FRAME.search(text)


def test_recipe_keeps_full_size_and_refuses_an_oversize_file(tmp_path):
    noise = Image.effect_noise((2208, 1400), 90).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, format="PNG")
    with pytest.raises(clip_kit.ClipRefused):
        clip_kit.write_webp_at_size(buf.getvalue(), tmp_path / "x.webp", max_bytes=20 * 1024)
    flat = io.BytesIO()
    Image.new("RGB", (2208, 1400), "white").save(flat, format="PNG")
    assert clip_kit.write_webp_at_size(flat.getvalue(), tmp_path / "y.webp") == (2208, 1400)


@pytest.fixture(scope="module")
def manifest():
    path = IMG_MODULES_DIR / MODULE_CAPTURES_MANIFEST
    assert path.is_file(), "captures.json is missing: no module image has been made to the standard"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("slug", sorted(STANDARD_MODULE_IMAGES))
def test_module_image_meets_the_measurable_lines(slug, manifest):
    entry = manifest.get(slug)
    assert entry, f"{slug} is not recorded in captures.json"
    assert entry["density"] == 2
    for variant, name, css_max in (("desktop", f"{slug}.webp", DESKTOP_CSS_MAX),
                                   ("phone", f"{slug}-mobile.webp", PHONE_CSS_MAX)):
        path = IMG_MODULES_DIR / name
        assert path.is_file(), f"{name}: the {variant} version is missing"
        assert path.stat().st_size <= clip_kit.STANDARD_IMAGE_BYTES, f"{name} is over 300 KB"
        with Image.open(path) as im:
            width, height = im.size
        assert width % 2 == 0 and height % 2 == 0
        assert width // 2 <= css_max, f"{name} is {width // 2} CSS px wide; the page shows {css_max} at most"
        assert (width // 2, height // 2) == (entry[variant]["css_width"], entry[variant]["css_height"])
        assert entry[variant]["min_text_px"] >= clip_kit.STANDARD_MIN_TEXT_PX


@pytest.mark.parametrize("slug", sorted(STANDARD_MODULE_IMAGES))
def test_module_page_serves_both_versions_at_half_pixel_width(slug, manifest):
    shot = get_page_screenshot(load_page("module", slug))
    assert shot and shot["mobile"], f"/modules/{slug} serves no phone version"
    assert shot["width"] == manifest[slug]["desktop"]["css_width"]
    assert shot["mobile"]["width"] == manifest[slug]["phone"]["css_width"]
