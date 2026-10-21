"""Can each persona finish their job using only what is on the screen?

Every other test in this repository navigates by typed URL.
`client.post("/api/risks", ...)` proves the endpoint works; it proves nothing
about whether a risk manager could FIND it. That distinction is the whole
subject of this file, and it is where this week's defects actually lived.

Two instruments already exist and neither closes this gap:

* the adversarial sweep asks "what happens if I do something hostile?" It found
  a cross-tenant counter leak, an ARB state machine that accepted `banana`, 89
  routes rendering a nonexistent entity and 55 endpoints 500ing on `page=-1`.
  Every one of those is a WRONG RESPONSE. It found nothing that returns a
  correct-but-useless response, because there is nothing wrong with the HTTP.
* the 66 journey tests assert outcomes at endpoints they address directly, so a
  screen can pass them while offering the user no route to that endpoint at all.

The defects the owner personally reported are all of the second kind: a table of
architecture gaps with no link, button or route to act on any of them (112 such
tables); a collapsed sidebar showing several destinations behind one glyph with
labels clipped to "All mo..."; a capability search that rendered perfectly and
returned HTTP 200 while saying it could not load its data. A dead end is a
valid 200.

So the rule here, and the only thing that makes this test different:

    NAVIGATE ONLY BY WHAT IS RENDERED.

Start at the persona's landing page and reach the goal by following links and
pressing controls that are actually present. Never type a URL. A step that
cannot be taken from the screen is a failure, and it is exactly the failure a
new user hits on their first afternoon.

These are ratcheted per archetype against task_completion_baseline.json rather
than asserted at zero, for the same reason every other ratchet exists: the debt
is real and being paid down, and a gate that fails on day one gets switched off.
"""

import json
import os

import pytest

from .conftest import ARCHETYPES, PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BASELINE_PATH = os.path.join(os.path.dirname(__file__), "task_completion_baseline.json")

# The job each persona exists to do, expressed as things they must be able to
# REACH from their own landing page. Deliberately phrased as destinations rather
# than endpoints: the test is about wayfinding, so the target is "a link whose
# text or href gets me there", not a URL the test already knows.
#
# `needs` is matched against the visible link text and the href, case-folded.
TASKS = {
    "enterprise_architect": [
        ("see the capability model", ["capabilit"]),
        ("trace a decision", ["traceab", "impact"]),
        ("reach the ArchiMate model", ["archimate", "architecture"]),
    ],
    "solution_architect": [
        ("open the solutions they own", ["solution"]),
        ("reach the review board", ["arb", "review"]),
    ],
    "business_architect": [
        ("see business capabilities", ["capabilit"]),
        ("see value streams", ["value stream"]),
    ],
    "arb_member": [
        ("reach the review queue", ["arb", "review"]),
        ("see governance decisions", ["decision", "governance"]),
    ],
    "portfolio_manager": [
        ("open the application portfolio", ["application", "portfolio"]),
        ("reach rationalization", ["rationalis", "rationaliz", "portfolio"]),
    ],
    "cto": [
        ("see portfolio health", ["health", "dashboard"]),
        ("reach the roadmap", ["roadmap"]),
    ],
    "procurement": [
        ("reach vendors or contracts", ["vendor", "contract", "procurement"]),
    ],
    "application_manager": [
        ("open their applications", ["application"]),
    ],
    "platform_admin": [
        ("reach administration", ["admin"]),
    ],
}

LANDING = "/dashboard/overview"

# Everything the user can actually act on, from the rendered page. An element
# that is present but invisible is not a route a person can take.
REACHABLE = """() => {
  const out = [];
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  for (const el of document.querySelectorAll('a[href], button, [role="link"]')) {
    if (!visible(el)) continue;
    const text = (el.textContent || '').trim().toLowerCase();
    const href = (el.getAttribute('href') || '').toLowerCase();
    const label = (el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
    out.push(text + ' ' + href + ' ' + label);
  }
  return out;
}"""


def _load_baseline():
    try:
        with open(BASELINE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _login(page, base, email):
    """Sign in the way a user does. Mirrors tests/smoke/test_archetype_journeys.py."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("archetype", sorted(TASKS))
def test_the_persona_can_reach_their_own_work(archetype, live_server, seeded, browser):
    """Every task must be reachable from the landing page, by link, not by URL."""
    base = live_server
    page = browser.new_page()
    try:
        _login(page, base, seeded["emails"][archetype])
        page.goto(base + LANDING, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(800)  # let the sidebar and icons render
        reachable = page.evaluate(REACHABLE)
        # The sidebar shows the twelve launch modules; every other module is one click further, on the
        # All modules page the sidebar footer links to. Both screens are "what is on the screen".
        entry = page.get_by_test_id("sidebar").get_by_role("link", name="All modules", exact=True)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            entry.click()
        page.wait_for_timeout(800)
        reachable_from_directory = page.evaluate(REACHABLE)
    finally:
        page.close()

    unreachable = []
    for description, needles in TASKS[archetype]:
        if not any(any(n in entry for n in needles) for entry in reachable + reachable_from_directory):
            unreachable.append(description)

    allowed = _load_baseline().get(archetype, 0)
    assert len(unreachable) <= allowed, (
        "%s cannot reach %d of their own tasks from %s using only what is on "
        "the screen: %s\n"
        "This is not a broken endpoint -- it is a person who cannot find their "
        "work. %d links and controls were visible."
        % (archetype, len(unreachable), LANDING, ", ".join(unreachable),
           len(reachable))
    )


@pytest.mark.parametrize("archetype", sorted(TASKS))
def test_the_landing_page_offers_somewhere_to_go(archetype, live_server, seeded, browser):
    """A landing page with nothing on it is the first hour of every new tenant.

    Separate from the task check on purpose: this one fails when the page is
    empty for a structural reason (a zone rendered no links, a guard hid
    everything), which is a different defect from a specific task being
    unreachable and would otherwise be reported as nine identical failures.
    """
    base = live_server
    page = browser.new_page()
    try:
        _login(page, base, seeded["emails"][archetype])
        page.goto(base + LANDING, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(800)
        reachable = page.evaluate(REACHABLE)
    finally:
        page.close()

    assert len(reachable) >= 5, (
        "%s lands on %s with only %d visible links or controls. A persona whose "
        "pages are reachable by URL but not from the screen does not have those "
        "pages." % (archetype, LANDING, len(reachable))
    )
