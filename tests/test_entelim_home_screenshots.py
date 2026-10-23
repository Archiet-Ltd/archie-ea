"""Playwright screenshots of the Entelim public pages at mobile widths.

Saves screenshots under .task/ for acceptance criterion:
"The page renders without horizontal scroll at 375 px and 390 px, in light and dark."
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
    boot_timeout = int(os.environ.get("SMOKE_BOOT_TIMEOUT", "180"))
    deadline = time.time() + boot_timeout
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
        pytest.fail("flask dev server did not start within %ds" % boot_timeout)

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


# Page configurations: (path, cta_selector, cta_name)
PAGES = [
    ("/", "a.public-btn--primary:has-text('Start free')", "Start free button"),
    ("/pricing", "a[data-testid='buy-community']", "Community Start free button"),
    ("/register", "input[type='submit']", "Create account button"),
    ("/connectors", "header a.public-btn:has-text('Sign Up')", "Sign Up button"),
]

WIDTHS = [375, 390]
THEMES = ["light", "dark"]


def _check_page(page, base_url, path, cta_selector, cta_name, width, theme):
    """Check a single page at a given width and theme."""
    viewport_height = 812 if width == 375 else 844
    page.set_viewport_size({"width": width, "height": viewport_height})
    if theme == "dark":
        page.emulate_media(color_scheme="dark")
    page.goto(base_url + path, wait_until="networkidle")
    page.wait_for_timeout(500)

    scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
    viewport_width = page.evaluate("() => window.innerWidth")
    assert scroll_width <= viewport_width, (
        f"Horizontal scroll detected on {path} at {width}px {theme}: "
        f"scrollWidth={scroll_width}, innerWidth={viewport_width}"
    )

    # Check main CTA is fully inside viewport
    cta = page.locator(cta_selector).first
    assert cta.count() > 0, f"CTA '{cta_name}' not found on {path}"
    cta_box = cta.bounding_box()
    assert cta_box is not None, f"CTA '{cta_name}' has no bounding box on {path}"
    assert cta_box["x"] >= 0, f"CTA '{cta_name}' left edge outside viewport on {path} at {width}px {theme}"
    assert cta_box["x"] + cta_box["width"] <= viewport_width, (
        f"CTA '{cta_name}' right edge outside viewport on {path} at {width}px {theme}: "
        f"right={cta_box['x'] + cta_box['width']}, viewport={viewport_width}"
    )

    # Screenshot
    theme_suffix = "-dark" if theme == "dark" else ""
    out = _shot_dir() / f"entelim{path.replace('/', '-')}-{width}px{theme_suffix}.png"
    page.screenshot(path=str(out), full_page=True)
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.parametrize("path,cta_selector,cta_name", PAGES)
@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", THEMES)
def test_public_page_no_horizontal_scroll(browser, live_server, path, cta_selector, cta_name, width, theme):
    """Public pages render without horizontal scroll at 375/390px in light/dark."""
    page = browser.new_page()
    try:
        _check_page(page, live_server, path, cta_selector, cta_name, width, theme)
    finally:
        page.close()


# Keep the original 1440px test for desktop regression
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