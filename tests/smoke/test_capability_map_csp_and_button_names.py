"""Capability map pages: no Content-Security-Policy console errors, and
every button carries an accessible name.

Both findings trace to the same cause in
app/modules/capabilities/routes/map_views.py: hierarchy(), simple_view()
and dashboard() were decorated with @cached in a way that cached the
*rendered* HTML, nonce baked in by CspNonceExtension at render time. A
cache hit on a later request served that stale nonce inside a response
whose Content-Security-Policy header carried a different, freshly
generated nonce (app/_bootstrap/security.py generates one per request), so
the browser refused every nonce'd <script>/<style> tag on the page — which
also stopped Alpine (itself nonce'd) from ever running, so the sidebar
toggle button's :aria-label binding was never applied and it had no
accessible name either.

The fix caches only the query data, not the rendered HTML, so every
response (cache hit or not) carries a matching nonce. Reproducing the
original bug needs a cache hit, so this test loads each page twice before
asserting anything.
"""

import importlib

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_accessibility_audit import TAGS

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

AXE_MODULE = "axe_playwright_python.sync_playwright"

PAGES = [
    "/capability-map/hierarchy",
    "/capability-map/simple",
    "/capability-map/dashboard",
]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.fixture(scope="module")
def axe_module(browser):
    """The axe-playwright-python module, or a hard failure — never a skip.

    Same reasoning as tests/smoke/test_accessibility_audit.py's fixture of
    the same name: a skipped check reports as a pass, which would let a run
    say "no button-name violations" when no check ran at all.
    """
    try:
        return importlib.import_module(AXE_MODULE)
    except ImportError as exc:
        pytest.fail(
            "the button-name check could not run: the axe-playwright-python "
            "package is not installed (%s). Install it with: "
            "pip install -r requirements.txt" % exc,
            pytrace=False,
        )


@pytest.mark.parametrize("path", PAGES)
def test_no_csp_console_errors_and_every_button_is_named(
    axe_module, browser, live_server, seeded, path
):
    email = seeded["emails"]["business_architect"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    console_errors = []
    page.on(
        "console",
        lambda m: console_errors.append(m.text) if m.type == "error" else None,
    )
    try:
        _login(page, live_server, email)

        # The bug only reproduced on a cache hit: load once to prime the
        # cache, then again before checking anything.
        page.goto(live_server + path, wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        console_errors.clear()
        page.goto(live_server + path, wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)

        csp_errors = [m for m in console_errors if "content security policy" in m.lower()]
        assert not csp_errors, (
            "%s logged a Content-Security-Policy console error on a cache-hit "
            "load:\n  %s" % (path, "\n  ".join(csp_errors))
        )

        # Run the same WCAG 2.2 AA tag set as tests/smoke/test_accessibility_audit.py
        # (test_every_axe_run_in_tests_smoke_uses_the_audit_tag_set enforces this
        # across every axe run under tests/smoke), then read just the button-name
        # rule out of the result — that rule is this test's whole concern, not a
        # full-page conformance claim these pages have not been baselined for.
        report = axe_module.Axe().run(
            page, options={"runOnly": {"type": "tag", "values": TAGS}}
        )
        data = report.response if hasattr(report, "response") else report
        button_name_violations = [
            v for v in data.get("violations", []) if v.get("id") == "button-name"
        ]
        assert not button_name_violations, (
            "%s has a button with no accessible name: %r"
            % (
                path,
                [
                    node.get("target")
                    for violation in button_name_violations
                    for node in violation.get("nodes", [])
                ],
            )
        )
    finally:
        ctx.close()
