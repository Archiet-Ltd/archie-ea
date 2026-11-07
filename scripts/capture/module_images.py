# -*- coding: utf-8 -*-
"""The still images of the module pages, each made to the capture standard in ``clip_kit.capture_standard_image``.

Each job names the screen a module page shows, the state to open before the picture is taken, and the element
to crop to. A screen that breaks the standard is refused and no file is written; fix the data or the product,
never the job. Run against a server on the seeded demonstration organisation::

    python -m scripts.capture.module_images --base-url http://127.0.0.1:5000 [--only slug ...]

The sign-in password comes from ``DEMO_USER_PASSWORD`` (see ``clip_kit``).
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from app.services.public_pages import IMG_MODULES_DIR
from scripts.capture.clip_kit import ClipRefused, capture_standard_image, sign_in_state


def _open_layered_diagram(page, variant):
    page.get_by_role("button", name="Diagram View").click()
    page.locator("#elements-viewpoint-diagram svg g.joint-cell").nth(8).wait_for(state="attached")
    page.wait_for_timeout(600)


# slug -> (path, crop selector, prepare, selectors hidden from the crop)
IMAGE_JOBS = {
    "architecture-model": ("/architecture/elements", "div.rounded-lg:has(#elements-viewpoint-diagram)",
                           _open_layered_diagram, ()),
}


def record_images(browser, base_url, out_dir=IMG_MODULES_DIR, only=None):
    """Capture each job; return (slugs written, one line for each that was refused)."""
    state = sign_in_state(browser, base_url)
    written, refused = [], []
    for slug, (path, selector, prepare, hide) in IMAGE_JOBS.items():
        if only and slug not in only:
            continue
        try:
            capture_standard_image(browser, base_url, slug, path, out_dir, selector=selector, prepare=prepare,
                                   hide=hide, state=state)
            written.append(slug)
        except ClipRefused as exc:
            refused.append(f"{slug}: {exc}")
    return written, refused


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--out-dir", default=str(IMG_MODULES_DIR))
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            written, refused = record_images(browser, args.base_url, pathlib.Path(args.out_dir), args.only)
        finally:
            browser.close()
    for slug in written:
        print(f"  written  {slug}")
    for line in refused:
        print(f"  REFUSED  {line}")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
