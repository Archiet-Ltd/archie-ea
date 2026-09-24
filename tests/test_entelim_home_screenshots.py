"""Playwright screenshots of the Entelim home page at mobile and desktop widths.

Saves screenshots under .task/ for acceptance criterion 4:
"The page renders without horizontal scroll at 375 px and 1440 px."
"""

import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("playwright", reason="playwright not installed")
from playwright.sync_api import sync_playwright


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _shot_dir():
    target = pathlib.Path(__file__).resolve().parents[1] / ".task"
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture(scope="module")
def live_server():
    """Boot the Flask dev server on a free port for the screenshot session."""
    port = _free_port()
    env = dict(os.environ)
    env.setdefault("FLASK_CONFIG", "testing")
    env["FLASK_DEBUG"] = "0"

    cmd = [sys.executable, "-m", "flask", "--app", "manage", "run",
           "--host", "127.0.0.1", "--port", str(port), "--no-reload"]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail("flask dev server exited during boot")
        try:
            import urllib.request
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(2)
    else:
        proc.kill()
        pytest.fail("flask dev server did not start within 60s")

    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip("chromium unavailable: %s" % str(exc)[:120])
        yield b
        b.close()


def test_home_page_no_horizontal_scroll_at_375px(browser, live_server):
    """AC 4: Page renders without horizontal scroll at 375 px."""
    page = browser.new_page()
    try:
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(live_server + "/", wait_until="networkidle")
        page.wait_for_timeout(500)

        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        viewport_width = page.evaluate("() => window.innerWidth")
        assert scroll_width <= viewport_width, (
            f"Horizontal scroll detected at 375px: "
            f"scrollWidth={scroll_width}, innerWidth={viewport_width}"
        )

        out = _shot_dir() / "entelim-home-375px.png"
        page.screenshot(path=str(out), full_page=True)
        assert out.exists() and out.stat().st_size > 0
    finally:
        page.close()


def test_home_page_no_horizontal_scroll_at_1440px(browser, live_server):
    """AC 4: Page renders without horizontal scroll at 1440 px."""
    page = browser.new_page()
    try:
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(live_server + "/", wait_until="networkidle")
        page.wait_for_timeout(500)

        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        viewport_width = page.evaluate("() => window.innerWidth")
        assert scroll_width <= viewport_width, (
            f"Horizontal scroll detected at 1440px: "
            f"scrollWidth={scroll_width}, innerWidth={viewport_width}"
        )

        out = _shot_dir() / "entelim-home-1440px.png"
        page.screenshot(path=str(out), full_page=True)
        assert out.exists() and out.stat().st_size > 0
    finally:
        page.close()