"""A startup CTO on a phone maps a capability to an application that has none.

The goal: on the applications list at 390 wide an application with no capability
says "No capability mapped yet: map it" (the same words as at 1440) and that
control opens the application's mapping dialog; choosing a capability in the
dialog selects it once, so the card stays selected and the count reads 1.
"""

import pytest

from .conftest import PAGE_TIMEOUT
from .test_applications_list_says_what_to_do_journey import _login, _make_org, _open_list

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WORDING = "No capability mapped yet: map it"


@pytest.fixture
def world(app, live_server):
    return {"a": _make_org(app, "Alpha", with_apps=True), "b": _make_org(app, "Bravo", with_apps=True)}


def _page(browser, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    return ctx, ctx.new_page()


@pytest.mark.parametrize("width", [390, 360])
def test_phone_unmapped_application_says_map_it_and_the_dialog_selects_once(browser, live_server, world, width):
    a = world["a"]
    ctx, page = _page(browser, width, 844)
    try:
        _login(page, live_server, a["email"])
        _open_list(page, live_server)
        delta = a["ids"]["Delta Orphan"]

        cell = page.locator('[data-testid="app-capability-mobile-%s"]' % delta)
        assert cell.is_visible(), "no visible capability control on the phone card"
        assert " ".join(cell.inner_text().split()) == WORDING
        # An application with a capability shows it, not the prompt.
        mapped = page.locator('[data-testid="mobile-cards"] :text("%s")' % a["capability"])
        assert mapped.count() >= 1
        assert "Use the actions menu" not in page.locator("body").inner_text()

        # The control's text fits inside the card that holds it: nothing is clipped
        # or pushed past the card edge, and it is big enough to tap.
        fit = cell.evaluate(
            """el => {
                const card = el.closest('[data-testid="mobile-card-%s"]');
                const r = document.createRange(); r.selectNodeContents(el);
                const t = r.getBoundingClientRect(); const c = card.getBoundingClientRect();
                return {scroll: el.scrollWidth, client: el.clientWidth, textRight: t.right,
                        cardRight: c.right, textLeft: t.left, cardLeft: c.left,
                        height: el.getBoundingClientRect().height};
            }""" % delta
        )
        assert fit["scroll"] <= fit["client"], fit
        assert fit["textRight"] <= fit["cardRight"] + 0.5 and fit["textLeft"] >= fit["cardLeft"] - 0.5, fit
        assert fit["height"] >= 24, fit
        assert page.evaluate("document.documentElement.scrollWidth") <= width

        cell.click()
        modal = page.locator("#unified-mapping-modal")
        modal.wait_for(state="visible")
        assert a["names"]["Delta Orphan"] in modal.locator("#unified-modal-target-name").inner_text()
        card = modal.locator('#unified-targets-list [data-action="selectTarget"]', has_text=a["capability"]).first
        card.wait_for(state="visible")

        # Count every selectTarget call from here on.
        page.evaluate(
            "() => { window.__calls = 0; const f = window.selectTarget;"
            " window.selectTarget = function() { window.__calls++; return f.apply(this, arguments); }; }"
        )
        card.click()
        page.wait_for_timeout(400)
        assert page.evaluate("window.__calls") == 1
        assert "1 selected" in modal.inner_text()
        # Another organisation's capability never appears in the dialog.
        assert world["b"]["capability"] not in modal.inner_text()

        # Save, reload, and the capability is on the phone card.
        modal.locator("#unified-reverse-save-btn").click()
        page.wait_for_timeout(1500)
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        saved = page.locator('[data-testid="app-capability-mobile-%s"]' % delta)
        saved.wait_for(state="visible")
        assert " ".join(saved.inner_text().split()) == a["capability"]
    finally:
        ctx.close()


def test_desktop_unmapped_application_uses_the_same_wording_and_selects_once(browser, live_server, world):
    a = world["a"]
    ctx, page = _page(browser, 1440, 900)
    try:
        _login(page, live_server, a["email"])
        _open_list(page, live_server)
        cell = page.locator('[data-testid="app-capability-%s"]' % a["ids"]["Delta Orphan"])
        assert " ".join(cell.inner_text().split()) == WORDING
        cell.click()
        modal = page.locator("#unified-mapping-modal")
        modal.wait_for(state="visible")
        card = modal.locator('#unified-targets-list [data-action="selectTarget"]', has_text=a["capability"]).first
        card.wait_for(state="visible")
        page.evaluate(
            "() => { window.__calls = 0; const f = window.selectTarget;"
            " window.selectTarget = function() { window.__calls++; return f.apply(this, arguments); }; }"
        )
        card.click()
        page.wait_for_timeout(400)
        assert page.evaluate("window.__calls") == 1
        assert "1 selected" in modal.inner_text()
    finally:
        ctx.close()
