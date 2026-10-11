"""Capture the two canvas pages as evidence for a design review.

Not an assertion suite beyond "the page actually rendered" — a deliberate
evidence producer, following tests/smoke/test_architecture_journey_screenshots.py's
convention. There is no projection yet, so every box renders its empty
hint; there is no "loading" or "could-not-answer" state to capture until a
later change wires a fetch, which the review re-runs against once it
exists. Both themes and both widths (1280px, 360px) are captured for the
one real state there is.

Set SMOKE_SCREENSHOT_DIR to control the destination.
"""

import os
import pathlib

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = {"1280px": (1280, 900), "360px": (360, 800)}
THEMES = ("light", "dark")


def _shot_dir():
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "canvas-pages"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def canvas_records(seeded):
    """One empty BusinessModelCanvas and one empty BusinessCase to screenshot."""
    from app import create_app, db
    from app.models.business_case import BusinessCase
    from app.models.business_model import BusinessModelCanvas

    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name="Screenshot Canvas", organization_id=seeded["ids"]["org"])
        case = BusinessCase(title="Screenshot Case", organization_id=seeded["ids"]["org"])
        db.session.add_all([canvas, case])
        db.session.commit()
        return {"bmc": canvas.id, "case": case.id}


PAGES = [
    ("bmc", "business-model-canvas"),
    ("case", "business-case"),
]


@pytest.mark.parametrize("kind,slug", PAGES)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("viewport_name", sorted(VIEWPORTS))
def test_capture_canvas_page(browser, live_server, seeded, canvas_records, viewport_name, theme, kind, slug):
    width, height = VIEWPORTS[viewport_name]
    path = {
        "bmc": "/business-model/%d" % canvas_records["bmc"],
        "case": "/business-case/%d" % canvas_records["case"],
    }[kind]

    ctx = browser.new_context(viewport={"width": width, "height": height})
    if theme == "dark":
        # darkMode: ["class"] (tailwind.config.js) — no admin-page toggle exists
        # yet (composer_base.html is the only layout with one today), so the
        # class is set directly for the review; this proves nothing about
        # whether a toggle exists, only how the page renders under it.
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status < 400, f"{path} returned {response.status}"
        page.wait_for_timeout(600)
        out = _shot_dir() / f"{slug}-{viewport_name}-{theme}.png"
        page.screenshot(path=str(out), full_page=True)
        assert out.exists() and out.stat().st_size > 0
        print(f"[screenshot] {out}")
    finally:
        ctx.close()
