"""Journeys the recording tests run in a child pytest to see what the harness does with their video.

Not collected on its own (the name has no ``test_`` prefix); ``test_recording_harness.py`` names it explicitly.
"""

import pytest

pytestmark = [pytest.mark.smoke]


def _open(page, html):
    page.route("http://probe.test/**", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto("http://probe.test/page")


def test_probe_pass(page):
    _open(page, "<h1>probe</h1>")
    page.mouse.move(300, 300)
    page.wait_for_timeout(500)


def test_probe_fail(page):
    _open(page, "<h1>probe</h1>")
    page.mouse.move(400, 400)
    page.wait_for_timeout(500)
    assert False, "deliberate failure"


def test_probe_pointer(page):
    _open(page, "<button id='go' onclick='window.hit = 1'>Go</button>")
    page.locator("#go").click()
    assert page.evaluate("window.hit") == 1
    shown = page.evaluate(
        """() => { const c = document.getElementById('__cur'); if (!c) return null;
                   const s = getComputedStyle(c); return [s.pointerEvents, s.position, c.style.left]; }""")
    if shown is None:
        pytest.fail("no pointer drawn")
    assert shown[0] == "none" and shown[1] == "fixed" and shown[2] != "-100px"


def test_probe_no_pointer(page):
    _open(page, "<h1>probe</h1>")
    page.mouse.move(10, 10)
    assert page.evaluate("document.getElementById('__cur')") is None
