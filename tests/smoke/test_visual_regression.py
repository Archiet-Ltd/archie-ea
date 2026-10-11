"""Pixel-level visual regression across a representative screen matrix.

None of the existing gates catch a rendered layout defect that leaves the DOM
and the accessibility tree intact -- a clipped element, dead whitespace where
a chart should be, a card that renders half off-screen. Every one of those was
found this session (the ARB status chart, the AI-chat persona-switch banner)
by a human looking at a screenshot, not by any check in this suite. This is
the mechanical version of that look: capture a screen, compare it pixel-by-
pixel against a committed baseline, and fail on anything beyond antialiasing
noise.

Gated as a ratchet against committed baseline PNGs, exactly like a11y_baseline
.json's pattern: a check that fails on day one gets disabled on day two. A new
route starts with no baseline (skipped, loudly, with instructions), not a
fabricated pass.

This is a floor, not a design review. It proves nothing moved that shouldn't
have; it says nothing about whether the layout was well designed to begin
with. That is still a human's job -- see the design pass in this session's
history for what that looks like.
"""

import os

import pytest
from PIL import Image, ImageChops

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visual_baselines")

# One representative screen per major module, at desktop and one at mobile
# width, chosen from the routes touched or reviewed this session plus the
# highest-traffic screens in the sidebar. Deliberately not exhaustive: a wide
# net on day one is a suite nobody maintains. Grow this list as new screens
# get a real design review, not as a blanket capture-everything pass.
SCREENS = [
    ("solution_architect", "/dashboard/overview", 1440),
    ("solution_architect", "/dashboard/overview", 400),
    ("portfolio_manager", "/applications/", 1440),
    ("portfolio_manager", "/applications/", 400),
    ("business_architect", "/capability-map/", 1440),
    ("business_architect", "/capability-map/", 400),
    ("solution_architect", "/solutions/", 1440),
    ("cto", "/arb/", 1440),
    ("procurement", "/procurement/compliance", 1440),
    ("enterprise_architect", "/ai-chat/", 1440),
]

# Fraction of pixels allowed to differ before a screen counts as regressed.
# Font hinting, subpixel antialiasing and animated/pseudo-random elements
# (spinners, gradients) shift a small number of pixels between otherwise
# identical renders even with no code change -- 0.5% absorbs that noise
# without hiding a real layout shift, which typically moves large contiguous
# regions (tens of percent) rather than a light scatter.
DIFF_THRESHOLD = 0.005


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


def _screen_slug(path, width):
    return "%s-%d" % (path.strip("/").replace("/", "-") or "root", width)


def _diff_fraction(baseline_path, current_img):
    baseline_img = Image.open(baseline_path).convert("RGB")
    current_img = current_img.convert("RGB")
    if baseline_img.size != current_img.size:
        return None, "dimensions changed: baseline %dx%d vs current %dx%d" % (
            baseline_img.size[0], baseline_img.size[1],
            current_img.size[0], current_img.size[1],
        )
    diff = ImageChops.difference(baseline_img, current_img)
    # A per-channel bounding-box + histogram pass rather than pixel-by-pixel
    # Python iteration, which is too slow for a 1440x900+ image run per test.
    hist = diff.convert("L").histogram()
    total_pixels = baseline_img.size[0] * baseline_img.size[1]
    # Anything with a max channel delta under 20 is antialiasing/compression
    # noise, not a real content change -- count only pixels brighter than that
    # in the collapsed luminance diff.
    changed = sum(hist[20:])
    return changed / total_pixels, None


VISUAL_ORG_SUFFIX = "visualreg"

# A second dict, not a second pytest scope, guards the one-seed-per-process
# guarantee below. pytest's own fixture cache turned out not to be enough:
# `scope="session"` only dedupes calls to a fixture *as resolved from one
# particular module*, so a second test module that also requested
# `visual_org` (verified while building this) re-ran the fixture body and
# tried to insert the same fixed-suffix organisation a second time,
# UniqueViolation on the slug. Nothing else in this suite imports
# `visual_org` today, but the whole point of this cache is to not depend on
# that staying true.
_visual_org_cache = {}


@pytest.fixture(scope="session")
def visual_org(request, ai_protocol_stub, live_server):
    """A fresh organisation, seeded exactly like every other smoke test's
    shared `seeded` fixture (same function, see conftest._seed_standard_org)
    but not shared with them, and with fixed rather than random names.

    `seeded` is session-scoped: every other smoke test in the run writes
    into the one organisation it returns, and several of them (creating an
    application, a licence, a capability) are meant to leave what they
    create behind. A pixel comparison against a baseline captured from a
    freshly seeded organisation cannot use that fixture and stay
    deterministic - the screens below would show a different portfolio,
    licence and capability count depending on how much of the suite had
    already run first, which is exactly what made this file's regressions
    unreproducible in isolation. A dedicated organisation, seeded the same
    way and touched by nothing else, renders the same screens every time.

    The ordinary random suffix is not enough for every screen, though:
    ai-chat's own Context Panel lists several of this organisation's
    ArchiMate elements by name, and a random suffix makes that name -- real,
    rendered content, not just a count -- different every run. A fixed
    suffix makes every name this organisation's records carry identical
    from one run to the next, which is what that screen actually needs.

    A fixed suffix reused for a second `_seed_standard_org` call in the same
    database collides on the organisation's slug (and the seeded users'
    emails) -- `_visual_org_cache` above makes that call happen at most once
    per test process regardless of how many fixture requests reach here, and
    the whole suite runs against a freshly created database in both CI and
    this file's own verification, so "once per process" is also "once per
    database" in practice.
    """
    if "org" not in _visual_org_cache:
        _visual_org_cache["org"] = _seed_standard_org(
            request, ai_protocol_stub, fixed_suffix=VISUAL_ORG_SUFFIX)
    return _visual_org_cache["org"]


@pytest.fixture(scope="module")
def captured(browser, live_server, visual_org):
    """Screenshot every screen in the matrix once, keyed by its slug."""
    os.makedirs(BASELINE_DIR, exist_ok=True)
    results = {}
    for archetype, path, width in SCREENS:
        slug = _screen_slug(path, width)
        ctx = browser.new_context(viewport={"width": width, "height": 900})
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = ctx.new_page()
        try:
            _login(page, live_server, visual_org["emails"][archetype])
            page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            try:
                page.eval_on_selector_all(
                    "[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
            except Exception:
                pass
            # Let in-flight fetches (dashboards, chart data) settle so the
            # capture reflects the real steady state, not a loading spinner
            # frame -- an animated spinner would also fail the diff on every
            # run for reasons unrelated to any actual regression.
            page.wait_for_timeout(1200)
            png_bytes = page.screenshot(full_page=True)
            results[slug] = png_bytes
        except Exception as exc:
            results[slug] = exc
        finally:
            ctx.close()
    return results


def test_the_capture_actually_ran(captured):
    """A crashed capture leaves a screen missing, which would read as skipped
    rather than failed -- assert every screen produced *something*."""
    assert captured, "no screens were captured"
    expected = {_screen_slug(p, w) for _a, p, w in SCREENS}
    assert set(captured) == expected, (
        "expected %d screens, captured %d" % (len(expected), len(captured))
    )


def test_no_visual_regressions(captured):
    """Every screen's render may not drift beyond DIFF_THRESHOLD from its
    committed baseline.

    A route with no baseline yet is reported, not silently skipped into a
    pass -- run with SMOKE_VISUAL_UPDATE_BASELINE=1 to record it once you have
    reviewed the current render and confirmed it is correct.
    """
    from io import BytesIO

    failures = []
    missing = []
    for slug, result in sorted(captured.items()):
        if isinstance(result, Exception):
            failures.append("%s: capture failed (%s)" % (slug, result))
            continue
        baseline_path = os.path.join(BASELINE_DIR, slug + ".png")
        if not os.path.exists(baseline_path):
            missing.append(slug)
            continue
        img = Image.open(BytesIO(result))
        frac, dim_error = _diff_fraction(baseline_path, img)
        if dim_error:
            failures.append("%s: %s" % (slug, dim_error))
        elif frac > DIFF_THRESHOLD:
            failures.append("%s: %.2f%% of pixels changed (threshold %.2f%%)"
                             % (slug, frac * 100, DIFF_THRESHOLD * 100))

    if missing:
        pytest.fail(
            "%d screen(s) have no baseline yet: %s\n\n"
            "Review the current render (screenshots were captured but not "
            "compared), then record it with:\n"
            "  SMOKE_VISUAL_UPDATE_BASELINE=1 pytest tests/smoke/test_visual_regression.py"
            % (len(missing), ", ".join(missing))
        )

    assert not failures, (
        "%d visual regression(s):\n  %s\n\n"
        "If the new render is correct (an intentional design change), accept "
        "it with:\n"
        "  SMOKE_VISUAL_UPDATE_BASELINE=1 pytest tests/smoke/test_visual_regression.py"
        % (len(failures), "\n  ".join(failures))
    )


def test_write_baseline_when_asked(captured):
    """Regenerate every baseline:  SMOKE_VISUAL_UPDATE_BASELINE=1 pytest ...

    Deliberately a test rather than a script, same reasoning as the a11y
    audit's equivalent: the capture needs a live server, a seeded database
    and a browser, all of which the fixtures already stand up.
    """
    if os.environ.get("SMOKE_VISUAL_UPDATE_BASELINE") != "1":
        return
    written = 0
    for slug, result in captured.items():
        if isinstance(result, Exception):
            continue
        with open(os.path.join(BASELINE_DIR, slug + ".png"), "wb") as fh:
            fh.write(result)
        written += 1
    print("visual baseline written: %d screen(s)" % written)
