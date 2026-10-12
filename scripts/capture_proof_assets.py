#!/usr/bin/env python
"""Capture the proof screenshots the public site shows: home hero, Applications, ARB.

Why this exists (marketing visual design review v1, S3 / P-03 / P-06): nine of the
eleven revenue pages showed no product at all, and the two that did showed a
whole 1200x750 application window squeezed into a 720px column -- 60% scale,
7px text, four zero tiles. This script takes the replacement crops.

Rules it enforces, so the next re-shoot cannot quietly regress:

* the demonstration organisation is seeded first (``flask seed-demo-company``);
  a crop whose headline tiles read zero is refused, not shipped;
* every crop is a *region* of the real screen (no app sidebar), taken at 2x
  device pixel ratio and written with a CSS width equal to the region's own CSS
  width, so the template displays it at 1:1 and the UI's 12-14px text stays
  12-14px;
* a desktop crop and a mobile crop are taken from the product at its own
  viewport width (not shrunk), because a 1000px crop shrunk to a 390px phone is
  the original defect again.

Usage (the app must already be running against the seeded database):

    DEMO_USER_PASSWORD=... python scripts/capture_proof_assets.py \
        --base http://127.0.0.1:5100

Writes WebP files under app/static/img/ and prints their CSS width/height.
"""
from __future__ import annotations

import argparse
import io
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from capture_screenshots import _dismiss_onboarding, _login  # noqa: E402

DEMO_EMAIL = "demo@lantern-quay.example.com"
IMG = REPO / "app" / "static" / "img"
DPR = 2
SUBJECT = "Calibration Ledger"


def _webp(png: bytes, dest: pathlib.Path) -> tuple[int, int]:
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        im.save(dest, "WEBP", quality=82, method=6)
        return im.size[0] // DPR, im.size[1] // DPR


def _tidy(page) -> None:
    """Hide floating furniture that is not part of the screen being shown."""
    page.evaluate(
        """() => {
          for (const e of document.querySelectorAll('body *')) {
            const s = getComputedStyle(e);
            if (s.position === 'fixed' && e.getBoundingClientRect().width < 80 &&
                e.getBoundingClientRect().top > window.innerHeight / 2) e.style.display = 'none';
          }
        }"""
    )


def _shoot(page, clip) -> bytes:
    page.evaluate("window.scrollTo(0, 0)")
    return page.screenshot(clip=clip, full_page=True)


def _card(page, text, last=False):
    """Bounding box of the rounded card that contains exactly this text."""
    node = page.get_by_text(text, exact=True)
    node = node.last if last else node.first
    box = node.locator("xpath=ancestor::*[contains(@class,'rounded')][1]").bounding_box()
    if box is None:
        raise SystemExit(f"card containing {text!r} not found on {page.url}")
    return box


def _box(page, selector, nth=0):
    box = page.locator(selector).nth(nth).bounding_box()
    if box is None:
        raise SystemExit(f"selector {selector!r} not found on {page.url}")
    return box


def capture_applications(browser, base, password, wide: bool) -> tuple[int, int]:
    vw, vh = (1408, 1600) if wide else (390, 1400)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    _login(page, base, DEMO_EMAIL, password)
    page.goto(base + "/applications/", wait_until="networkidle")
    page.wait_for_timeout(1500)
    _dismiss_onboarding(page)
    _tidy(page)
    text = page.inner_text("body")
    if re.search(r"(?<!\d)0 of \d+ applications in the portfolio have a vendor", text) or "Not mapped" in text:
        raise SystemExit("applications: vendor or capability columns are empty; run seed-demo-company first")
    main = _box(page, "main")
    # A 1408px viewport leaves a 1104px content column: exactly the public page figure width
    # (72rem less padding), so the crop is shown 1:1 and never shrunk.
    pad = 24 if wide else 16
    if wide:
        h1 = _box(page, "h1")
        rows = page.locator("[data-testid^='app-row-']")
        last = _box(page, "[data-testid^='app-row-']", min(rows.count(), 6) - 1)
        top, bottom = h1["y"] - 16, last["y"] + last["height"] + 4
    else:
        tiles = _card(page, "Active Portfolio")
        banner = _card(page, "Data Quality")
        top, bottom = tiles["y"] - 8, banner["y"] + banner["height"] + 8
    clip = {"x": main["x"] + pad, "y": top, "width": main["width"] - 2 * pad, "height": bottom - top}
    name = "applications.webp" if wide else "applications-mobile.webp"
    size = _webp(_shoot(page, clip), IMG / "modules" / name)
    ctx.close()
    return size


def capture_arb(browser, base, password, wide: bool) -> tuple[int, int]:
    vw, vh = (1280, 1400) if wide else (390, 2000)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    _login(page, base, DEMO_EMAIL, password)
    page.goto(base + "/arb/", wait_until="networkidle")
    page.wait_for_timeout(2500)
    _dismiss_onboarding(page)
    _tidy(page)
    text = page.inner_text("body")
    if re.search(r"Cycle time\s*—", text):
        raise SystemExit("arb: cycle time reads an em dash; run seed-demo-company first")
    main = _box(page, "main")
    if wide:
        h1 = _box(page, "h1")
        chart = _card(page, "Review status")
        x = h1["x"]
        width = (main["x"] + main["width"]) - x - 24
        top, bottom = h1["y"] - 16, chart["y"] + chart["height"] + 4
    else:
        first = _card(page, "Total reviews")
        last = _card(page, "Cycle time")
        x = first["x"] - 4
        width = first["width"] + 8
        top, bottom = first["y"] - 8, last["y"] + last["height"] + 8
    clip = {"x": x, "y": top, "width": width, "height": bottom - top}
    name = "arb.webp" if wide else "arb-mobile.webp"
    size = _webp(_shoot(page, clip), IMG / "modules" / name)
    ctx.close()
    return size


def capture_home_impact(browser, base, password, wide: bool) -> tuple[int, int]:
    """The 'what breaks if this fails' answer: the twin map centred on one system.

    Taken at 740px viewport (desktop) so the crop is 644 CSS px wide -- the width
    of the hero's figure column -- and shown there at 1:1.
    """
    vw, vh = (740, 1400) if wide else (390, 1800)
    ctx = browser.new_context(viewport={"width": vw, "height": vh}, device_scale_factor=DPR)
    page = ctx.new_page()
    _login(page, base, DEMO_EMAIL, password)
    page.goto(base + "/intelligence/twin-map", wait_until="networkidle")
    page.wait_for_timeout(1500)
    _dismiss_onboarding(page)
    page.fill("#twin-picker-input", SUBJECT)
    page.wait_for_selector("#twin-picker-listbox [role=option]", timeout=20000)
    page.locator("#twin-picker-listbox [role=option]").first.click()
    page.wait_for_timeout(2500)
    button = page.get_by_role("button", name="Work them out now")
    if button.count():
        button.click()
        page.wait_for_timeout(10000)
    page.locator("body").click(position={"x": 2, "y": 2})
    _tidy(page)
    top = page.locator("#twin-picker-input").locator("xpath=ancestor::*[contains(@class,'rounded')][1]").bounding_box()
    tech = page.get_by_text("Technology", exact=True).last.bounding_box()
    main = _box(page, "main")
    pad = 48 if wide else 16
    bottom = tech["y"] - 6
    if not wide:
        # Phone: the map alone (the picker and controls would make the crop two screens tall).
        goals = page.get_by_text("Goals", exact=True).last.bounding_box()
        top = {"y": goals["y"] - 10}
    clip = {
        "x": main["x"] + pad,
        "y": top["y"],
        "width": main["width"] - 2 * pad,
        "height": bottom - top["y"],
    }
    name = "impact-answer.webp" if wide else "impact-answer-mobile.webp"
    size = _webp(_shoot(page, clip), IMG / "home" / name)
    ctx.close()
    return size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5100")
    parser.add_argument("--chromium", default=os.environ.get("PROOF_CHROMIUM"))
    args = parser.parse_args()
    password = os.environ.get("DEMO_USER_PASSWORD")
    if not password:
        raise SystemExit("DEMO_USER_PASSWORD is not set")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launch = {"executable_path": args.chromium, "args": ["--no-sandbox"]} if args.chromium else {}
        browser = pw.chromium.launch(**launch)
        try:
            for label, fn in (
                ("home impact answer", capture_home_impact),
                ("applications", capture_applications),
                ("arb", capture_arb),
            ):
                for wide in (True, False):
                    w, h = fn(browser, args.base, password, wide)
                    print(f"{label:20s} {'desktop' if wide else 'mobile ':8s} {w}x{h} css px")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
