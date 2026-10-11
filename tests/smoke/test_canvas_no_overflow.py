"""Browser smoke test: Business Model Canvas at 1280 and 1440 px — no
horizontal overflow and no box title cut or broken mid-word.

Canvas/framework UI fix (24 Sep 2026, round 2 25 Sep 2026).
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000

# The onboarding dialog (app/templates/layouts/admin_base.html) reads this
# localStorage key before Alpine evaluates showOnboarding, so it must be set
# before the page's own script runs -- a context-level init script, not a
# page.evaluate() after load, which would be too late for the first render.
_DISMISS_ONBOARDING_SCRIPT = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.parametrize("width,height", [(1440, 900), (1280, 800)])
def test_canvas_detail_titles_wrap_only_between_words(browser, live_server, seeded, width, height):
    """At 1280 and 1440 px the nine-box grid must fit the content width with
    no horizontal overflow, and every box title must wrap only at spaces --
    never inside a word."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])

        # Navigate to the canvas list and click the first canvas, or create one.
        # Scoped to the canvas grid, not `a[href*="/business-model/"]` on the
        # whole page: the Library zone now carries a "Canvases" sidebar link
        # to this same index on every page (including this one), which an
        # unscoped selector would click instead of a canvas card, looping
        # back to the index and timing out below.
        index_url = live_server + "/business-model/"
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        canvas_links = page.locator('[data-testid="bmc-canvas-grid"] a[href*="/business-model/"]')
        if canvas_links.count() == 0:
            page.get_by_role("button", name=re.compile("New Canvas", re.I)).first.click()
            form = page.locator('[data-testid="bmc-create-form"]')
            expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
            form.locator("#bmc-name").fill("Overflow Test Canvas")
            page.get_by_test_id("bmc-create-submit").click()
            page.wait_for_url(re.compile(r"/business-model/\d+$"), timeout=PAGE_TIMEOUT)
        else:
            canvas_links.first.click()
            page.wait_for_url(re.compile(r"/business-model/\d+$"), timeout=PAGE_TIMEOUT)

        # Wait for the grid to render, and confirm the onboarding dialog is not
        # covering it (defect found in round 1: screenshots taken without
        # dismissing it first). The dialog has no test id of its own; its
        # step-1 heading text is the stable anchor (app/templates/layouts/
        # admin_base.html).
        expect(page.get_by_test_id("bmc-grid")).to_be_visible(timeout=PAGE_TIMEOUT)
        welcome_heading = page.get_by_text(re.compile("^Welcome to "))
        assert welcome_heading.count() == 0 or not welcome_heading.first.is_visible(), (
            "the onboarding dialog is covering the canvas grid"
        )

        # Assert no horizontal overflow on the document.
        scroll_width = page.evaluate("document.scrollingElement.scrollWidth")
        inner_width = page.evaluate("window.innerWidth")
        assert scroll_width <= inner_width, (
            f"Horizontal overflow detected: scrollWidth={scroll_width} > innerWidth={inner_width}"
        )

        titles = page.locator(".bmc-box-title")
        count = titles.count()
        assert count == 9, f"Expected 9 box titles, found {count}"

        for i in range(count):
            title = titles.nth(i)
            label = title.text_content()

            # No text cut off: the element is never narrower than its content.
            sw_val = title.evaluate("el => el.scrollWidth")
            cw_val = title.evaluate("el => el.clientWidth")
            assert sw_val <= cw_val, (
                f"Box title '{label}' is cut off: scrollWidth={sw_val} > clientWidth={cw_val}"
            )

            # No CSS that could split a word: only normal wrapping (at spaces)
            # is allowed on a title, on the title itself or anything it
            # inherits from.
            style = title.evaluate(
                "el => { const s = getComputedStyle(el); "
                "return {wordBreak: s.wordBreak, overflowWrap: s.overflowWrap, "
                "hyphens: s.hyphens}; }"
            )
            assert style["wordBreak"] == "normal", (
                f"Box title '{label}' has a word-break override: {style['wordBreak']!r}"
            )
            assert style["overflowWrap"] == "normal", (
                f"Box title '{label}' has an overflow-wrap override: {style['overflowWrap']!r}"
            )
            assert style["hyphens"] != "auto", (
                f"Box title '{label}' has hyphens: auto"
            )

            # Belt and braces: reconstruct the rendered lines from the text
            # node's own line boxes and confirm every line, trimmed, is a
            # whitespace-delimited run of the label -- i.e. no line both
            # starts and ends mid-word.
            lines = title.evaluate(
                """el => {
                    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
                    const node = walker.nextNode();
                    if (!node) return [];
                    const range = document.createRange();
                    range.selectNodeContents(node);
                    const rects = Array.from(range.getClientRects());
                    const text = node.textContent;
                    const byTop = new Map();
                    // Fall back to whole-text rects when per-character rects
                    // are not needed (single line): just return the text.
                    if (rects.length <= 1) return [text.trim()];
                    for (let i = 0; i < text.length; i++) {
                        const r = document.createRange();
                        r.setStart(node, i);
                        r.setEnd(node, i + 1);
                        const cr = r.getClientRects()[0];
                        if (!cr) continue;
                        const top = Math.round(cr.top);
                        if (!byTop.has(top)) byTop.set(top, []);
                        byTop.get(top).push(text[i]);
                    }
                    return Array.from(byTop.values()).map(chars => chars.join('').trim());
                }"""
            )
            words = set(label.split())
            for line in lines:
                if not line:
                    continue
                assert line in words or all(part in words for part in line.split()), (
                    f"Box title '{label}' wraps mid-word: line {line!r} is not a "
                    f"whitespace-delimited run of the title"
                )
    finally:
        context.close()
