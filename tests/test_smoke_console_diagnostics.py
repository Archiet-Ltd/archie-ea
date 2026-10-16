"""Regression tests for actionable cross-browser console diagnostics."""

import pytest

from tests.smoke.test_accessibility_audit import _violation_evidence
from tests.smoke.test_archetype_journeys import (
    STRUCTURED_ERROR_PROBE,
    _format_console_error,
    _format_page_error,
)


def _pump_until(page, condition, timeout_ms=10000):
    """Let Playwright deliver browser events until ``condition()`` holds.

    The console and pageerror callbacks run only while the sync API pumps
    events, so a fixed sleep races a slow machine: the context can be closed
    before the callback reads the console arguments. Waiting on the observed
    condition is deterministic; the timeout only bounds a real failure.
    """
    waited = 0
    while not condition() and waited < timeout_ms:
        page.wait_for_timeout(25)
        waited += 25


class _Handle:
    def __init__(self, value):
        self._value = value

    def json_value(self):
        return self._value


class _Message:
    type = "error"
    text = "Object"
    args = [_Handle({"status": 503, "url": "/architecture/api/layer/counts"})]
    location = {
        "url": "http://127.0.0.1/static/js/core/03-fetch.js",
        "lineNumber": 152,
        "columnNumber": 20,
    }


def test_console_object_is_serialized_with_source_location():
    diagnostic = _format_console_error(_Message())

    assert '"status": 503' in diagnostic
    assert '"url": "/architecture/api/layer/counts"' in diagnostic
    assert "03-fetch.js:152:20" in diagnostic
    assert diagnostic != "Object"


def test_accessibility_violation_preserves_affected_selectors():
    evidence = _violation_evidence({
        "impact": "serious",
        "nodes": [
            {"target": ["#model-select"], "failureSummary": "Fix contrast"},
            {"target": [".chat-status", "span"], "failureSummary": "Fix contrast"},
        ],
    })

    assert evidence == {
        "impact": "serious",
        "count": 2,
        "targets": ["#model-select", ".chat-status > span"],
    }


class _PageError:
    name = "Error"
    message = "Object"
    stack = "Object@http://127.0.0.1/static/js/dashboard.js:123:9"


def test_page_error_preserves_stack_when_message_is_opaque():
    diagnostic = _format_page_error(_PageError())

    assert "Object" in diagnostic
    assert "dashboard.js:123:9" in diagnostic


def test_plain_uncaught_objects_are_mirrored_to_structured_console_capture():
    assert "unhandledrejection" in STRUCTURED_ERROR_PROBE
    assert "event.error" in STRUCTURED_ERROR_PROBE
    assert "event.reason" in STRUCTURED_ERROR_PROBE
    assert "console.error" in STRUCTURED_ERROR_PROBE
    assert "event.filename" in STRUCTURED_ERROR_PROBE
    assert "event.lineno" in STRUCTURED_ERROR_PROBE
    assert "Object.getOwnPropertyNames" in STRUCTURED_ERROR_PROBE


def test_timer_callback_errors_are_serialized_before_being_rethrown():
    """An async plain-object throw must retain context before Firefox flattens it."""
    playwright = pytest.importorskip("playwright.sync_api")
    diagnostics = []
    page_errors = []

    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - actionable environment failure
            pytest.fail("Chromium is required for the diagnostic contract: %s" % exc)
        context = browser.new_context()
        context.add_init_script(STRUCTURED_ERROR_PROBE)
        page = context.new_page()

        def capture(message):
            if message.type == "error":
                diagnostics.append(_format_console_error(message))

        page.on("console", capture)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        # Init scripts are not guaranteed to run for the browser-created initial
        # about:blank document; navigate to a real document boundary first.
        page.goto("data:text/html,<body>diagnostic target</body>")
        page.evaluate("""() => setTimeout(() => {
            throw {code: 'timer-probe', expression: 'broken()', el: document.body};
        }, 0)""")
        _pump_until(
            page,
            lambda: page_errors
            and any("qualification async callback error" in d for d in diagnostics),
        )
        context.close()
        browser.close()

    timer_diagnostics = [
        item for item in diagnostics if "qualification async callback error" in item
    ]
    assert timer_diagnostics, diagnostics
    assert '"code": "timer-probe"' in timer_diagnostics[0]
    assert '"expression": "broken()"' in timer_diagnostics[0]
    assert '"element": "<body' in timer_diagnostics[0]
    assert page_errors, "the diagnostic wrapper swallowed the original exception"


def _run_unhandled_rejection(reason, wait_for_diagnostic=False):
    playwright = pytest.importorskip("playwright.sync_api")
    diagnostics = []
    page_errors = []

    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - actionable environment failure
            pytest.fail("Chromium is required for the diagnostic contract: %s" % exc)
        context = browser.new_context()
        context.add_init_script(STRUCTURED_ERROR_PROBE)
        page = context.new_page()
        page.on(
            "console",
            lambda message: diagnostics.append(_format_console_error(message))
            if message.type == "error" else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto("data:text/html,<body>rejection target</body>")
        page.evaluate("reason => { void Promise.reject(reason); }", reason)
        if wait_for_diagnostic:
            _pump_until(page, lambda: diagnostics)
        else:
            # Absence cannot be awaited; give a suppressed rejection time to
            # (not) surface.
            page.wait_for_timeout(100)
        context.close()
        browser.close()

    return diagnostics, page_errors


def test_cancelled_alpine_transition_rejection_is_suppressed():
    diagnostics, page_errors = _run_unhandled_rejection(
        {"isFromCancelledTransition": True}
    )

    assert not diagnostics
    assert not page_errors


def test_non_transition_plain_object_rejection_remains_diagnostic():
    diagnostics, _ = _run_unhandled_rejection(
        {"code": "real-failure", "expression": "loadElements()"},
        wait_for_diagnostic=True,
    )

    assert any(
        "qualification unhandled rejection" in item
        and '"code": "real-failure"' in item
        and '"expression": "loadElements()"' in item
        for item in diagnostics
    ), diagnostics
