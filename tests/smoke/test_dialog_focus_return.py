"""Closing a dialog built from components/dialog.html puts the keyboard back on the button that opened it.

A person who opens a dialog from the keyboard and dismisses it must land where they were,
whichever way they opened it (mouse, Enter, Space) and whichever way they close it
(Escape, the close button, a click outside). The billing page's Cancel subscription
dialog is driven for all 9 combinations, 10 times each, at normal speed and with the
processor slowed fourfold; the other pages that carry a component dialog are driven
with Enter and Escape.
"""
import re
import urllib.request

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_billing_journey import _SKIP_WELCOME, _community_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REPEATS = 10


def _slow(context, page, rate):
    if rate > 1:
        context.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": rate})


def _is_active(page, opener):
    return opener.evaluate("el => document.activeElement === el")


def _active_description(page):
    return page.evaluate(
        "() => { const a = document.activeElement; return a ? a.tagName + (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 30) : 'none'; }")


def _open(page, opener, how):
    if how == "mouse":
        opener.click()
    else:
        opener.focus()
        page.keyboard.press("Enter" if how == "enter" else "Space")


def _close(page, dialog, how):
    if how == "escape":
        page.keyboard.press("Escape")
    elif how == "button":
        dialog.get_by_role("button", name="Close").click()
    else:
        page.mouse.click(4, 4)


def _drive(page, opener, dialog, how_open, how_close, repeats=REPEATS):
    """Open and close *repeats* times; return the descriptions of where focus landed when it was not the opener."""
    misses = []
    for _ in range(repeats):
        _open(page, opener, how_open)
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        try:
            page.wait_for_function("d => d.contains(document.activeElement)", arg=dialog.element_handle(),
                                   timeout=2000)
        except Exception:
            raise AssertionError("focus did not move into the dialog on open (%s); it is on %s"
                                 % (how_open, _active_description(page)))
        _close(page, dialog, how_close)
        expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)
        # The dialog is gone from view: the keyboard must already be back on the opener, no settling time.
        if not _is_active(page, opener):
            misses.append(_active_description(page))
    return misses


@pytest.fixture(scope="module")
def paid_server(request, app):
    """A server whose payment provider is the loopback stand-in, in test mode."""
    from .payment_provider_stub import PaymentProviderStub

    with PaymentProviderStub() as stub:
        yield boot_live_server(request, None, app, extra_env=stub.child_environment({})), stub


def _buy_startup(page, base):
    page.goto(base + "/admin/billing/?plan=startup", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("checkout-submit").click()
    page.wait_for_url(re.compile(r"/pay/cs_test_"), timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Pay").click()
    page.wait_for_url(re.compile(r"/admin/billing/$"), timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("throttle", [1, 4], ids=["normal", "cpu4x"])
def test_cancel_dialog_returns_focus_to_its_opener_90_times(browser, paid_server, app, throttle):
    from .test_archetype_journeys import _login

    base, _stub = paid_server
    _org, email = _community_org(app, members=0)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, base, email)
        _buy_startup(page, base)
        page.reload(wait_until="domcontentloaded")
        opener = page.get_by_role("button", name="Cancel subscription")
        expect(opener).to_be_visible()
        dialog = page.get_by_role("dialog")
        _slow(context, page, throttle)
        results = {}
        for how_open in ("mouse", "enter", "space"):
            for how_close in ("escape", "button", "outside"):
                results[(how_open, how_close)] = _drive(page, opener, dialog, how_open, how_close)
        misses = {k: v for k, v in results.items() if v}
        total = sum(len(v) for v in misses.values())
        print("DIALOG-PROBE throttle=%dx closes=%d misses=%d detail=%s"
              % (throttle, 9 * REPEATS, total, misses))
        assert not misses, "focus did not return to the opener in %d of %d closes: %s" % (total, 9 * REPEATS, misses)
    finally:
        context.close()


def test_focus_stays_trapped_in_the_dialog(browser, paid_server, app):
    from .test_archetype_journeys import _login

    base, _stub = paid_server
    _org, email = _community_org(app, members=0)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, base, email)
        _buy_startup(page, base)
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Cancel subscription").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        handle = dialog.element_handle()
        for key in ["Tab"] * 6 + ["Shift+Tab"] * 6:
            page.keyboard.press(key)
            assert page.evaluate("d => d.contains(document.activeElement)", handle), \
                "focus left the open dialog on " + key
    finally:
        context.close()


def test_rate_limits_dialog_returns_focus_after_enter_and_escape(browser, live_server, seeded):
    from .test_archetype_journeys import _login

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        opener = page.get_by_role("button", name="Add an allow or deny entry")
        expect(opener).to_be_visible()
        misses = _drive(page, opener, page.get_by_role("dialog"), "enter", "escape")
        print("DIALOG-PROBE rate-limits closes=%d misses=%d detail=%s" % (REPEATS, len(misses), misses))
        assert not misses, misses
    finally:
        context.close()

