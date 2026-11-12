"""Every control a person tabs to shows where the keyboard is.

A person who never uses the mouse signs in, opens a page and presses Tab. On
each stop the control they have reached must show a visible outline or ring,
read from its computed style, at 1440 wide in light and at 390 wide in dark.
Pages: the overview, the gap register, the connectors, the impact analysis and
the access-denied page a person without rights reaches from billing and team.
"""
import os
import re
import tempfile
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
SHOTS = Path(os.environ.get("FOCUS_RING_SHOTS") or tempfile.gettempdir()) / "focus-ring"

# (who, path, labels the person must reach with Tab)
PAGES = [
    ("platform_admin", "/dashboard/overview", ["See every change"]),
    ("platform_admin", "/enterprise/implementation/gap-analysis", ["New gap", "@headers"]),
    ("platform_admin", "/admin/connectors", ["Save connector", "Connect GitHub"]),
    ("platform_admin", "/enterprise/analysis/impact-analysis",
     ["Run", "Element", "Connections", "Recorded by people", "Worked out", "Answer"]),
    ("application_manager", "/admin/billing/", ["Go to the dashboard"]),
    ("application_manager", "/admin/team", ["Go to the dashboard"]),
]

# Reads the focused control: does it match :focus-visible, and does it show an
# outline or a ring (a box-shadow that is not "none").
READ = """() => {
  const e = document.activeElement;
  if (!e || e === document.body) return null;
  const c = getComputedStyle(e);
  const outline = c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0
    && !/rgba\\(\\s*0,\\s*0,\\s*0,\\s*0\\s*\\)|transparent/.test(c.outlineColor);
  const shadow = c.boxShadow && c.boxShadow !== 'none'
    && !/^(rgba\\(0, 0, 0, 0\\) 0px 0px 0px 0px,?\\s*)+$/.test(c.boxShadow);
  // A ring that fades in is faint for the first moments after the key press.
  const props = c.transitionProperty.split(',').map(x => x.trim());
  const secs = c.transitionDuration.split(',').map(x => parseFloat(x));
  const fades = props.some((p, i) => ['all', 'outline', 'outline-color', 'box-shadow'].includes(p)
    && secs[i % secs.length] > 0);
  const r = e.getBoundingClientRect();
  return {label: (e.getAttribute('aria-label') || e.innerText || e.value || '').replace(/\\s+/g, ' ').trim(),
          visible: e.matches(':focus-visible'), inMain: !!e.closest('main'), shown: !!(outline || shadow), on: r.width > 0 && r.height > 0,
          tag: e.tagName, cls: String(e.className).slice(0, 300), outline: c.outline, shadow: c.boxShadow, fades: fades};
}"""


def _reach(page, label, limit=120):
    """Press Tab until the page's own control with this name (inside main, not
    the sidebar link of the same name) has focus; return its reading."""
    page.evaluate("() => { window.scrollTo(0, 0); document.activeElement && document.activeElement.blur(); }")
    for _ in range(limit):
        page.keyboard.press("Tab")
        stop = page.evaluate(READ)
        if stop and stop["inMain"] and stop["label"] == label:
            return stop
    return None


@pytest.fixture
def linked_data(app, seeded):
    """Four gaps (different status, impact and type, so every column shows) for
    the gap register, in the seeded organisation, and the element to analyse."""
    import uuid

    from app import db
    from app.models.implementation_migration import Gap

    org_id = seeded["ids"]["org"]
    suffix = uuid.uuid4().hex[:6]
    made = []
    with app.app_context():
        for i, (status, impact, kind) in enumerate([
                ("identified", "high", "capability"), ("in_progress", "medium", "technology"),
                ("resolved", "low", "process"), ("identified", "critical", "data")]):
            gap = Gap(name="Focus gap %d %s" % (i, suffix), organization_id=org_id,
                      resolution_status=status, impact=impact, gap_type=kind, priority=impact)
            db.session.add(gap)
            db.session.flush()
            made.append(gap.id)
        db.session.commit()
        db.session.remove()
    try:
        yield {"element": seeded["ids"]["radar_element"]}
    finally:
        with app.app_context():
            Gap.query.filter(Gap.id.in_(made), Gap.organization_id == org_id).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()


def _save_two_runs(page, live_server, element_id):
    for _ in range(2):
        page.goto("%s/enterprise/analysis/impact-analysis?element=%d" % (live_server, element_id),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("impact-save-run").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("impact-run-row").first.wait_for(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("scheme,width,height", VIEWS, ids=["1440-light", "390-dark"])
@pytest.mark.parametrize("who,path,labels", PAGES, ids=[p[1].strip("/").replace("/", "-") for p in PAGES])
def test_tabbing_shows_a_focus_ring(browser, live_server, seeded, linked_data, who, path, labels, scheme, width, height):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"][who])
        if "impact-analysis" in path:
            _save_two_runs(page, live_server, linked_data["element"])
        page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(800)
        if "@headers" in labels:  # the sort button of every column the page shows
            heads = page.evaluate(
                "() => [...document.querySelectorAll('[data-table-sort-trigger]')]"
                ".filter(e => e.getBoundingClientRect().width > 0)"
                ".map(e => e.innerText.replace(/\\s+/g, ' ').trim())")
            assert len(heads) >= 2, "the gap register shows too few sortable columns: %s" % heads
            labels = [x for x in labels if x != "@headers"] + heads
        missing, bare = [], []
        for label in labels:
            stop = _reach(page, label)
            if stop is None:
                missing.append(label)
                continue
            if stop["fades"]:
                bare.append("%s (its ring fades in from transparent)" % label)
                continue
            if not (stop["visible"] and stop["shown"]):
                bare.append('%s <%s class="%s"> outline=%s shadow=%s' % (label, stop['tag'], stop['cls'], stop['outline'], stop['shadow']))
                continue
            SHOTS.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^a-z0-9]+", "-", ("%s-%s-%s" % (path, label, width)).lower()).strip("-")
            page.screenshot(path=str(SHOTS / ("%s.png" % name)))
        assert not missing, "%s: Tab never reached %s" % (path, missing)
        assert not bare, "%s at %d wide (%s): no focus ring shown at once on %s" % (path, width, scheme, bare)
    finally:
        context.close()
