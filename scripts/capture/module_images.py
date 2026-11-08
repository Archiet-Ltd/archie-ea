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
import os
import pathlib
import sys

from app.services.public_pages import IMG_MODULES_DIR, PROCUREMENT_PERSONA
from scripts.capture import clip_kit
from scripts.capture.clip_kit import ClipRefused, capture_standard_image, sign_in_state
from scripts.capture.image_descriptions import CannotDescribe, describe


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


def _gather(page, *parts):
    """Crop to several parts of the page, in the order given, one above the other.

    Each part is a function returning a locator, a CSS selector, or a tuple of texts naming the innermost div that holds them all; the parts are
    moved into one wrapper marked as the element to crop to.
    """
    for number, part in enumerate(parts):
        if callable(part):
            locator = part(page)
        elif isinstance(part, str):
            locator = page.locator(part)
        else:
            locator = page.locator("div")
            for text in part:
                locator = locator.filter(has_text=text)
        locator.last.evaluate("(el, n) => el.setAttribute('data-gather', n)", number)
    page.locator("main > div").first.evaluate("""(host) => {
        const box = document.createElement('div');
        box.setAttribute('data-capture', '');
        box.className = 'flex flex-col gap-6';
        Array.from(host.querySelectorAll('[data-gather]'))
            .sort((a, b) => Number(a.dataset.gather) - Number(b.dataset.gather))
            .forEach((part) => box.appendChild(part));
        host.appendChild(box);
    }""")


def _only(page, *parts):
    """Crop to the parts of a page, hiding everything else around them.

    Unlike ``_gather`` nothing is moved, so a page whose controls are bound to an enclosing component keeps its
    values. Each part is a function returning a locator; at each level between the parts' common parent and
    the parts themselves, the other children are hidden.
    """
    for part in parts:
        part(page).evaluate("(el) => el.setAttribute('data-keep', '')")
    page.locator("[data-keep]").first.evaluate("""() => {
        const kept = Array.from(document.querySelectorAll('[data-keep]'));
        let host = kept[0].parentElement;
        while (!kept.every((el) => host.contains(el))) host = host.parentElement;
        const prune = (node) => Array.from(node.children).forEach((child) => {
            if (kept.includes(child)) return;
            if (kept.some((el) => child.contains(el))) prune(child); else child.style.display = 'none';
        });
        prune(host);
        host.setAttribute('data-capture', '');
    }""")


def _card_of(test_id):
    """The card holding the field with this test id."""
    return lambda page: page.get_by_test_id(test_id).locator("xpath=ancestor::div[contains(@class,'rounded')][1]")


def _open_business_case(page, variant):
    """Open the first business case, and show its title, options, recommendation and costs together."""
    page.locator("main a[href^='/business-case/']").filter(has_text="managed message broker").first.click()
    page.get_by_test_id("bc-input-capex").wait_for(state="visible")
    _only(page, lambda p: p.locator("h1").locator("xpath=.."), _card_of("bc-textarea-options_considered"),
          _card_of("bc-textarea-recommended_option"), _card_of("bc-input-capex"))


TILES = "[data-testid=figure-cards]"


def _arb_queue(page, variant):
    """The board's figures and its queue of reviews awaiting a decision, without the date and person columns."""
    if variant == "phone":
        page.locator("ul[role=list].md\\:hidden").wait_for(state="visible")
        _gather(page, TILES, "[data-testid=arb-outside-queue]", "ul[role=list].md\\:hidden")
        page.locator("[data-capture] ul li").evaluate_all("""(cards) => cards.forEach((card) => {
            Array.from(card.querySelectorAll('*')).filter((part) => /^REV-[0-9A-Z-]+$/.test(part.textContent.trim())
                && !part.children.length).forEach((part) => part.remove());
            Array.from(card.children).filter((part) => /^Submitted/.test(part.textContent.trim()))
                .forEach((part) => part.remove());
        })""")
        return
    page.locator("table").first.wait_for(state="visible")
    _gather(page, TILES, "[data-testid=arb-outside-queue]", "div.overflow-hidden:has(table)")
    page.locator("[data-capture] table").evaluate("""(table) => {
        ['submitted', 'review'].forEach((name) => {
            const col = Array.from(table.querySelectorAll('thead th')).findIndex((th) => th.textContent.trim().toLowerCase() === name);
            if (col < 0) return;
            table.querySelectorAll('tr').forEach((row) => { const cell = row.children[col]; if (cell) cell.remove(); });
        });
    }""")


def _renewals(page, variant):
    """The contracts coming up for renewal, soonest first: the dated column is left out, the status column says
    how many days ahead each one is."""
    page.locator("main table tbody tr").first.wait_for(state="visible")
    page.locator("main table").first.evaluate("""(table, phone) => {
        const names = phone ? ['Vendor', 'Annual cost', 'Status', 'Renews'] : ['Renews'];
        names.forEach((name) => {
            const col = Array.from(table.querySelectorAll('thead th')).findIndex((th) => th.textContent.trim().includes(name));
            if (col >= 0) table.querySelectorAll('tr').forEach((row) => { const cell = row.children[col]; if (cell) cell.remove(); });
        });
    }""", variant == "phone")
    # a contract's own number is a record id, not something the picture needs
    page.locator("main table tbody tr td span.block.truncate").evaluate_all("(els) => els.forEach((el) => el.remove())")
    _wrap_page(page)


def _whole_page(page, variant):
    _wrap_page(page)


def _vendors(page, variant):
    """The first ten vendors with their contracts and systems: the evidence line under each row, the cost column
    (not shown to this person's role), the dated renewal column and the line promising cost and renewals are
    left out, as is the paging that would count the rest."""
    page.locator("main table tbody tr:visible").first.wait_for(state="visible")
    page.locator("main table").first.evaluate("""(table, phone) => {
        table.querySelectorAll('[data-testid=result-footer]').forEach((footer) => footer.remove());
        (phone ? ['Annual cost', 'Next renewal', 'Active contracts'] : ['Annual cost', 'Next renewal']).forEach((name) => {
            const col = Array.from(table.querySelectorAll('thead th')).findIndex((th) => th.textContent.includes(name));
            if (col < 0) return;
            table.querySelectorAll('tr').forEach((row) => { const cell = row.children[col]; if (cell) cell.remove(); });
        });
        table.querySelectorAll('tbody tr').forEach((row, index) => { if (index >= 10) row.remove(); });
    }""", variant == "phone")
    page.locator("main h1 + p").evaluate("(el) => el.remove()")
    page.locator("main div").filter(has_text="Rows per page").last.evaluate("(el) => el.remove()")
    page.locator("main div").filter(has_text="Page 1 of").filter(has_text="rows").last.evaluate("(el) => el.remove()")
    _wrap_page(page)


def _compliance(page, variant):
    """The framework figures and the controls not yet in place: the first ten rows, without the paging line."""
    page.locator("main table tbody tr").first.wait_for(state="visible")
    tiles = lambda p: p.locator("div.grid.grid-cols-1").filter(has_text="Active frameworks").last  # noqa: E731
    if variant == "phone":
        # The findings table is wider than a phone and scrolls sideways, which would cut each control's name at the
        # edge, so the phone picture is the framework figures.
        _only(page, tiles)
        return
    _only(page, tiles,
          lambda p: p.locator("div.rounded-lg").filter(has_text="Controls that are mapped").last)
    page.locator("[data-capture]").evaluate(r"""(box) => {
        const card = Array.from(box.children).find((child) => /Controls that are mapped/.test(child.textContent));
        card.querySelectorAll('tbody tr').forEach((row, index) => { if (index >= 10) row.remove(); });
        Array.from(card.querySelectorAll('div')).filter((el) => /\d+ rows/.test(el.textContent) && /Page \d+ of/.test(el.textContent)
            && !el.querySelector('table')).sort((a, b) => a.textContent.length - b.textContent.length)
            .slice(0, 1).forEach((el) => el.remove());
    }""")


def _portfolio(page, variant):
    """The portfolio's figures and initiatives; on a phone the figures and the line under them, because the
    initiative table is wider than the screen and would be cut at its edge."""
    if variant == "phone":
        _only(page, lambda p: p.locator("div.grid").filter(has_text="Approved budget").last,
              lambda p: p.locator("#objective-filter"))
        return
    _wrap_page(page)


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
        page.locator("[data-capture]").evaluate("(el) => { el.style.height = 'auto'; el.style.minHeight = '0'; }")
        page.locator("[data-capture]").evaluate(
            "(el) => { const r = el.closest('[role=region]'); r.scrollLeft = el.getBoundingClientRect().left"
            " - r.getBoundingClientRect().left + r.scrollLeft - 4; window.scrollTo(0, 0); }")
        page.wait_for_timeout(300)
    else:
        _mark(page, "[data-canvas=business-model-canvas] [data-entelim-form=C4]")


def _top_risks(page, variant):
    """The ranked list shows its first five risks beside the matrix, which counts all of them."""
    page.locator("#risk-matrix-card").wait_for(state="visible")
    page.locator("#ranked-risks li[data-risk-row]").evaluate_all(
        "(rows) => rows.slice(5).forEach((row) => row.remove())")


def _first_applications(page, variant):
    """Every application on the page, each without the evidence line under it."""
    if variant == "phone":
        page.locator("[data-testid=mobile-cards] [data-app-id]").first.wait_for(state="visible")
        page.locator("[data-testid=mobile-cards]").evaluate("""(list) => {
            list.querySelectorAll('[data-testid=result-footer]').forEach((footer) => footer.remove());
            Array.from(list.querySelectorAll('[data-app-id]')).forEach((card, index) => { if (index >= 8) card.remove(); });
        }""")
        _mark(page, "[data-testid=mobile-cards]")
        return
    page.locator("[data-testid=applications-table] tbody tr:visible").first.wait_for(state="visible")
    # The row of filter controls above the table is left out: it is white space to a picture of the list.
    page.locator("main").evaluate("""(main) => {
        const filters = Array.from(main.querySelectorAll('div')).filter((el) => el.querySelector('select')
            && /Include decommissioned/.test(el.textContent) && !el.querySelector('table'));
        filters.sort((a, b) => a.textContent.length - b.textContent.length);
        if (filters[0]) filters[0].remove();
    }""")
    page.locator("[data-testid=applications-table] tbody").evaluate("""(body) => {
        body.querySelectorAll('tr:has([data-testid=result-footer])').forEach((row) => row.remove());
        Array.from(body.querySelectorAll('tr')).forEach((row, index) => { if (index >= 14) row.remove(); });
    }""")
    _wrap_page(page)


# Furniture of the page that is not the screen: the walkthrough button that floats over the foot of a phone picture.
COMMON_HIDE = ("#guided-mode-trigger", "#guided-mode-panel")

# slug -> (path, crop selector, prepare, selectors hidden from the crop)
IMAGE_JOBS = {
    "architecture-model": ("/architecture/elements", "div.rounded-lg:has(#elements-viewpoint-diagram)",
                           _open_layered_diagram, ()),
    "business-model-canvas": ("/business-model/", "[data-capture]", _open_filled_canvas, ()),
    "risk-register": ("/risks/", "div.grid:has(#ranked-risks)", _top_risks, ("[data-testid='result-footer']",)),
    "applications": ("/applications/", "[data-capture]", _first_applications, ()),
    "arb": ("/arb/", "[data-capture]", _arb_queue, ()),
    "business-case": ("/business-case/", "[data-capture]", _open_business_case, ()),
    "compliance-frameworks": ("/dashboard/compliance", "[data-capture]", _compliance, ()),
    "portfolio": ("/portfolio/", "[data-capture]", _portfolio, ()),
    "procurement": ("/procurement/renewals", "[data-capture]", _renewals, ()),
    "vendors": ("/applications/vendors", "[data-capture]", _vendors, ()),
}


# A screen the demonstration person's role may not open is made as the member of the same organisation whose
# role does: the seeded procurement person, signed in with the same password.
PERSONAS = {"procurement": PROCUREMENT_PERSONA}


def _persona_state(browser, base_url, email):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        clip_kit._sign_in(context.new_page(), base_url.rstrip("/"), email, os.environ[clip_kit.DEMO_PASSWORD_VARIABLE])
        return context.storage_state()
    finally:
        context.close()


def _under_the_module_cap():
    """Make the recipe's WebP writer hold each image to the module-image cap (the 200 KB the module screenshot
    test enforces), which is tighter than the recipe's own 300 KB ceiling."""
    original = clip_kit.write_webp_at_size
    clip_kit.write_webp_at_size = lambda png, dest, max_bytes=clip_kit.MAX_IMAGE_BYTES: original(png, dest, max_bytes)
    return original


def record_images(browser, base_url, out_dir=IMG_MODULES_DIR, only=None):
    """Capture each job; return (slugs written, one line for each that was refused)."""
    state = sign_in_state(browser, base_url)
    written, refused = [], []
    original_writer = _under_the_module_cap()
    try:
        return _record(browser, base_url, out_dir, only, state, written, refused)
    finally:
        clip_kit.write_webp_at_size = original_writer


def _record(browser, base_url, out_dir, only, state, written, refused):
    for slug, (path, selector, prepare, hide) in IMAGE_JOBS.items():
        if only and slug not in only:
            continue
        try:
            who = PERSONAS.get(slug)
            capture_standard_image(browser, base_url, slug, path, out_dir, selector=selector, prepare=prepare,
                                   hide=(*hide, *COMMON_HIDE),
                                   state=_persona_state(browser, base_url, who) if who else state,
                                   describe=lambda variant, target, text, slug=slug: describe(slug, variant, target, text))
            written.append(slug)
        except (ClipRefused, CannotDescribe) as exc:
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
