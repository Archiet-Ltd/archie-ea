"""The selected tab reads clearly in dark mode, and in light mode, on every page with a tab strip.

A person opening a page with a row of tabs must be able to read the tab they are on:
WCAG 2.2 AA colour contrast, at least 4.5:1, between its text and its background.

The test opens pages signed in, in light and in dark mode (emulated colour scheme plus the
page's dark class), runs axe-core colour contrast on the tab strip, reads the selected tab's
computed text and background colours and checks the ratio itself, then clicks another tab and
checks that one the same way.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

STRIP = "[data-slot=tabs-list]"
SELECTED = "[data-slot=tabs-trigger][aria-selected=true]"

# (archetype, name, path template, name of the tab that is open on arrival)
PAGES = [
    ("enterprise_architect", "backlog prioritisation", "/backlog/prioritisation", "MoSCoW"),
    ("enterprise_architect", "application", "/applications/{application}", "Overview"),
    ("enterprise_architect", "application roadmap", "/applications/{application}/roadmap", "Timeline"),
    ("enterprise_architect", "governance dashboard", "/governance/dashboard", None),
    ("platform_admin", "settings", "/settings", None),
]

THEMES = [("light", False), ("dark", True)]
SIZES = [(1440, 900), (390, 800)]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _channel(value):
    value = value / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(rgba):
    r, g, b = rgba[:3]
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _parse(colour):
    nums = [float(n) for n in re.findall(r"[\d.]+", colour)]
    if len(nums) == 3:
        nums.append(1.0)
    return nums


def _over(top, below):
    a = top[3]
    return [top[i] * a + below[i] * (1 - a) for i in range(3)] + [1.0]


def _selected_ratio(page, tab):
    """Contrast of the tab's computed text on its computed background, laid over what is behind it."""
    layers = page.evaluate(
        """el => { const out = []; for (let n = el; n; n = n.parentElement) {
            out.push(getComputedStyle(n).backgroundColor); } return {
            text: getComputedStyle(el).color, layers: out}; }""",
        tab.element_handle(),
    )
    backdrop = [255.0, 255.0, 255.0, 1.0]
    for layer in reversed(layers["layers"]):
        backdrop = _over(_parse(layer), backdrop)
    text = _over(_parse(layers["text"]), backdrop)
    lighter, darker = sorted((_luminance(text), _luminance(backdrop)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _axe_strip(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, context={"include": [[STRIP]]}, options={"runOnly": {"type": "rule", "values": ["color-contrast"]}})
    data = report.response if hasattr(report, "response") else report
    return data.get("violations", [])


@pytest.mark.parametrize("archetype,name,path,open_tab", PAGES)
@pytest.mark.parametrize("theme,dark", THEMES)
@pytest.mark.parametrize("width,height", SIZES)
def test_selected_tab_reads(browser, live_server, seeded, archetype, name, path, open_tab, theme, dark, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme="dark" if dark else "light")
    ctx.add_init_script("localStorage.setItem('theme', '%s');" % theme)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"][archetype])
        response = page.goto(
            live_server + path.format(application=seeded["ids"]["application"]),
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, "%s returned %s" % (name, response.status)
        page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
        selected = page.locator(SELECTED).first
        expect(selected).to_be_visible()
        if open_tab:
            expect(selected).to_have_text(re.compile(open_tab))
        shots = os.environ.get("SELECTED_TAB_SHOTS_DIR")
        if shots and name in ("backlog prioritisation", "application"):
            os.makedirs(shots, exist_ok=True)
            page.screenshot(path="%s/%s-%dpx-%s.png" % (shots, name.replace(" ", "-"), width, theme))
        violations = _axe_strip(page)
        assert not violations, "%s (%s): axe color-contrast on the tab strip: %s" % (
            name, theme, "; ".join(
                "%s %s" % (n["target"], n["any"][0]["message"]) for v in violations for n in v["nodes"][:3]))
        ratio = _selected_ratio(page, selected)
        assert ratio >= 4.5, "%s (%s): selected tab %r reads at %.2f:1" % (name, theme, selected.inner_text(), ratio)
        # Move to another tab on the same strip: it becomes the selected one and reads as well.
        other = page.locator("[data-slot=tabs-trigger][aria-selected=false]:not([aria-disabled=true]):not([disabled])").first
        if other.count():
            label = other.inner_text()
            other.click()
            now = page.locator(SELECTED).first
            expect(now).to_have_text(re.compile(re.escape(label.strip())))
            assert _selected_ratio(page, now) >= 4.5, "%s (%s): tab %r reads below 4.5:1 once opened" % (name, theme, label)
    finally:
        ctx.close()
