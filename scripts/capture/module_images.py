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


def _wrap_page(page):
    """Mark the page's own content, without the footer, as the element to crop to.

    The content sits in a padded wrapper whose box is wider than the 1104 px a picture may be, so the
    children are moved into one element of exactly the content's width.
    """
    page.locator("main > div").first.evaluate("""(host) => {
        const box = document.createElement('div');
        box.setAttribute('data-capture', '');
        Array.from(host.children).forEach((child) => {
            if (child.tagName === 'FOOTER') child.remove(); else box.appendChild(child);
        });
        host.appendChild(box);
    }""")


def _open_layered_diagram(page, variant):
    page.get_by_role("button", name="Diagram View").click()
    page.locator("#elements-viewpoint-diagram svg g.joint-cell").nth(8).wait_for(state="attached")
    page.wait_for_timeout(600)


def _mark(page, selector):
    """Mark the element to crop to, for a job whose crop differs between desktop and phone."""
    page.locator(selector).first.evaluate("(el) => el.setAttribute('data-capture', '')")


def _open_filled_canvas(page, variant):
    """Open the business model canvas that has all nine blocks filled, from the canvas library.

    The nine blocks need more than a phone's width and scroll sideways, so the phone picture shows one whole
    block (the value propositions) rather than a canvas cut at the edge.
    """
    page.locator("main a[href^='/business-model/']").filter(has_text="makes, calibrates").first.click()
    page.locator("[data-canvas=business-model-canvas] [data-entelim-form=C4]").wait_for(state="visible")
    if variant == "phone":
        _mark(page, "#box-value-propositions + *")
        page.locator("[data-capture]").evaluate(
            "(el) => { const r = el.closest('[role=region]'); r.scrollLeft = el.getBoundingClientRect().left"
            " - r.getBoundingClientRect().left + r.scrollLeft - 4; window.scrollTo(0, 0); }")
        page.wait_for_timeout(300)
    else:
        _mark(page, "[data-canvas=business-model-canvas] [data-entelim-form=C4]")


def _top_risks(page, variant):
    """The ranked list shows its first six risks beside the matrix, which counts all of them."""
    page.locator("#risk-matrix-card").wait_for(state="visible")
    page.locator("#ranked-risks li[data-risk-row]").evaluate_all(
        "(rows) => rows.slice(6).forEach((row) => row.remove())")


def _first_applications(page, variant):
    """Every application on the page, each without the evidence line under it."""
    if variant == "phone":
        page.locator("[data-testid=mobile-cards] [data-app-id]").first.wait_for(state="visible")
        page.locator("[data-testid=mobile-cards]").evaluate("""(list) => {
            list.querySelectorAll('[data-testid=result-footer]').forEach((footer) => footer.remove());
        }""")
        _wrap_page(page)
        return
    page.locator("[data-testid=applications-table] tbody tr:visible").first.wait_for(state="visible")
    page.locator("[data-testid=applications-table] tbody").evaluate("""(body) => {
        body.querySelectorAll('tr:has([data-testid=result-footer])').forEach((row) => row.remove());
    }""")
    _wrap_page(page)


# slug -> (path, crop selector, prepare, selectors hidden from the crop)
IMAGE_JOBS = {
    "architecture-model": ("/architecture/elements", "div.rounded-lg:has(#elements-viewpoint-diagram)",
                           _open_layered_diagram, ()),
    "business-model-canvas": ("/business-model/", "[data-capture]", _open_filled_canvas, ()),
    "risk-register": ("/risks/", "div.grid:has(#ranked-risks)", _top_risks, ("[data-testid='result-footer']",)),
    "applications": ("/applications/", "[data-capture]", _first_applications, ()),
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
