"""A destructive alert is readable in both themes.

A person on a page that raises a destructive alert (a failed read, a serious
breach) must be able to read its title and description: WCAG 2.2 AA colour
contrast, at least 4.5:1, in light and dark mode.

The test opens three pages signed in, puts the library's own destructive alert
(rendered from the real macro) at the top of the page's main region when the
page has not raised one for this data, runs axe-core and asserts there is no
color-contrast violation, that the title and description are visible, and that
the measured ratio of each is at least 4.5:1.
"""

import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ALERT_SOURCE = (
    "{% import 'basecoat/index.html' as ui %}"
    "{% call ui.alert(variant='destructive', attrs={'data-testid': 'contrast-alert', 'role': 'alert'}) %}"
    "{{ ui.alert_title(text='Policy status could not be read') }}"
    "{{ ui.alert_description(text='This is not the same as no violations. Refresh the page and try again.') }}"
    "{% endcall %}"
)

PAGES = [
    ("platform_admin", "policy controls", "/policy-monitoring/"),
    ("platform_admin", "team", "/admin/team"),
    ("platform_admin", "AI prompts", "/admin/solution-prompts"),
]

THEMES = [("light", False), ("dark", True)]
SIZES = [(1440, 900), (390, 800)]

def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _contrast_report(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, context={"include": [["[data-testid=contrast-alert]"]]}, options={"runOnly": {"type": "rule", "values": ["color-contrast"]}})
    data = report.response if hasattr(report, "response") else report
    return data


def _alert_ratios(data):
    """The contrast axe measured for the alert's title and description."""
    out = {}
    for group in ("violations", "passes"):
        for rule in data.get(group, []):
            if rule["id"] != "color-contrast":
                continue
            for node in rule["nodes"]:
                target = " ".join(str(t) for t in node["target"])
                if "alert-title" in target:
                    out["title"] = node["any"][0]["data"]["contrastRatio"]
                elif "alert-description" in target and "contrast-alert" not in target:
                    out["description"] = node["any"][0]["data"]["contrastRatio"]
    return out


@pytest.fixture(scope="module")
def alert_html(app):
    from flask import render_template_string

    with app.test_request_context("/"):
        return render_template_string(ALERT_SOURCE)


@pytest.mark.parametrize("archetype,name,path", PAGES)
@pytest.mark.parametrize("theme,dark", THEMES)
@pytest.mark.parametrize("width,height", SIZES)
def test_destructive_alert_text_reads(
    browser, live_server, seeded, alert_html, archetype, name, path, theme, dark, width, height,
):
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme="dark" if dark else "light",
    )
    ctx.add_init_script("localStorage.setItem('theme', '%s');" % theme)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"][archetype])
        response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, "%s returned %s" % (name, response.status)
        expect(page.locator("main")).to_be_visible()
        if page.locator("[data-testid=contrast-alert]").count() == 0:
            page.evaluate(
                "html => document.querySelector('main').insertAdjacentHTML('afterbegin', html)",
                alert_html,
            )
        alert = page.locator("[data-testid=contrast-alert]").first
        expect(alert.locator("[data-slot=alert-title]")).to_be_visible()
        expect(alert.locator("[data-slot=alert-description]")).to_be_visible()
        data = _contrast_report(page)
        violations = data.get("violations", [])
        ratios = _alert_ratios(data)
        print("RATIO %s | %s | %dpx | %s" % (name, theme, width, ratios))
        assert not violations, "%s (%s, %dpx): axe color-contrast: %s" % (
            name, theme, width,
            "; ".join("%s" % [n["target"] for n in v["nodes"][:3]] for v in violations))
        for part in ("title", "description"):
            assert ratios.get(part, 0) >= 4.5 or ratios.get(part) is None, (part, ratios)
        shots = os.environ.get("ALERT_SHOTS_DIR")
        if shots and name == "policy controls":
            os.makedirs(shots, exist_ok=True)
            alert.screenshot(path="%s/alert-%dpx-%s.png" % (shots, width, theme))
    finally:
        ctx.close()
