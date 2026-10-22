"""An enterprise architect works from one prioritised action list.

In an organisation with a critical gap, a late work package and an open risk,
the architect opens the impact analysis page, reads the one-sentence ranking
rule and the first rows in order, clicks a gap row to open that gap, comes
back, opens a risk row and sees that risk, then reloads the list and finds the
same order. The Architecture Intelligence page shows the same list.
"""

import re
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LIST_PAGES = ("/strategic/impact-analysis", "/enterprise/analysis/ai-architecture")


def _seed(org_id, suffix):
    from app import create_app, db
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    names = {
        "gap": "Event resilience gap %s" % suffix,
        "work": "Deploy field app %s" % suffix,
        "risk": "Relay single point of failure %s" % suffix,
    }
    with app.app_context():
        db.session.add(Gap(name=names["gap"], severity="critical", organization_id=org_id))
        db.session.add(UnifiedWorkPackage(
            name=names["work"], organization_id=org_id, status="in_progress", priority="critical",
            progress_percentage=58, end_date=datetime.utcnow() - timedelta(days=10),
        ))
        db.session.add(Risk(title=names["risk"], likelihood=4, impact=5, status=RiskStatus.OPEN,
                            organization_id=org_id))
        db.session.commit()
        db.session.remove()
    return names


def _order(page):
    return [t.strip() for t in page.locator('[data-testid="priority-action-link"]').all_inner_texts()]


def test_architect_works_from_the_prioritised_action_list(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    names = _seed(org["org_id"], org["suffix"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        expect(page.get_by_test_id("priority-actions-rule")).to_contain_text("Ranked by severity, critical first")
        expected = [
            "Convert the %s to a work package" % names["gap"],
            "Finish %s: due %s, 58 percent done" % (
                names["work"], (datetime.utcnow() - timedelta(days=10)).strftime("%-d %b")),
            "Reduce the risk: %s" % names["risk"],
        ]
        assert _order(page) == expected

        # The gap row names an action; the page it opens must offer that action on that gap.
        page.get_by_test_id("priority-action-link").first.click()
        page.wait_for_url(lambda url: "/implementation/gap-analysis" in url, timeout=PAGE_TIMEOUT)
        gap_row = page.locator("tr").filter(has_text=names["gap"]).first
        expect(gap_row).to_be_visible()
        expect(gap_row.get_by_role("button", name="Convert this gap to a work package")).to_be_visible()
        expect(gap_row.get_by_role("button", name="Convert this gap to a work package")).to_be_enabled()

        page.go_back(wait_until="domcontentloaded")
        page.get_by_test_id("priority-action-link").nth(2).click()
        page.wait_for_url(lambda url: "/risks/" in url, timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(names["risk"]).first).to_be_visible()

        page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        assert _order(page) == expected

        page.goto(live_server + LIST_PAGES[1], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _order(page) == expected
    finally:
        context.close()


def test_a_list_cut_at_ten_has_a_link_to_all_of_it(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        for n in range(13):
            db.session.add(Gap(name="Cut-off gap %02d %s" % (n, suffix), severity="high",
                               organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        for path in LIST_PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert len(_order(page)) == 10
            page.get_by_role("link", name="Show all 13 actions").click()
            page.wait_for_url(lambda url: "all_actions=1" in url, timeout=PAGE_TIMEOUT)
            assert len(_order(page)) == 13
            expect(page.get_by_test_id("priority-actions")).to_contain_text("Cut-off gap 12 %s" % suffix)
    finally:
        context.close()


def test_a_critical_row_lands_on_a_critical_highlighted_gap(browser, live_server):
    """The row the list calls Critical is, on the page it opens, Critical and marked."""
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        # Only severity is set: priority and impact keep their stored default of "medium".
        critical = Gap(name="Event backbone resilience %s" % suffix, severity="critical",
                       organization_id=org["org_id"])
        calm = Gap(name="Minor reporting gap %s" % suffix, severity="low", organization_id=org["org_id"])
        db.session.add_all([critical, calm])
        db.session.commit()
        critical_id, calm_id = critical.id, calm.id
        db.session.remove()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("priority-action-row").filter(has_text="Event backbone resilience")
        expect(row).to_contain_text("Critical")
        row.get_by_test_id("priority-action-link").click()
        page.wait_for_url(lambda url: url.endswith("/implementation/gap-analysis#gap-%d" % critical_id),
                          timeout=PAGE_TIMEOUT)

        landing = page.locator("#gap-%d" % critical_id)
        expect(landing).to_be_visible()
        expect(landing).to_contain_text("Critical")
        # The page's own summary agrees with the list: one critical gap.
        summary = page.get_by_text("By the severity recorded on each gap").locator(
            "xpath=ancestor::*[@data-slot='card'][1]")
        expect(summary).to_contain_text(re.compile(r"Critical\s+1\b"))

        # The landing row is the browser's target and is marked; the other row is not.
        assert page.evaluate("document.querySelector(':target') && document.querySelector(':target').id")             == "gap-%d" % critical_id
        shade = lambda sel: page.evaluate(  # noqa: E731
            "s => getComputedStyle(document.querySelector(s)).backgroundColor", sel)
        assert shade("#gap-%d" % critical_id) != shade("#gap-%d" % calm_id)
        # ... across the whole row, the pinned first and last cells included.
        for cell in ("td:first-child", "td:last-child"):
            assert shade("#gap-%d %s" % (critical_id, cell)) != shade("#gap-%d %s" % (calm_id, cell)), cell

        # No bare dashes: an empty Type or Work packages cell says why and what to do.
        expect(landing.get_by_test_id("gap-type-unset")).to_contain_text("open the gap to classify it")
        expect(landing.get_by_test_id("gap-no-work-package")).to_contain_text("Convert to Work Package")
        expect(landing.get_by_role("cell", name="—", exact=True)).to_have_count(0)
    finally:
        context.close()


_PIXEL_JS = """
(css) => {
  const c = document.createElement('canvas'); c.width = 1; c.height = 1;
  const x = c.getContext('2d', {willReadFrequently: true});
  x.clearRect(0, 0, 1, 1); x.fillStyle = css; x.fillRect(0, 0, 1, 1);
  const d = x.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
}
"""

_CONTRAST_JS = """
(el) => {
  const px = (css) => {
    const c = document.createElement('canvas'); c.width = 1; c.height = 1;
    const x = c.getContext('2d', {willReadFrequently: true});
    x.clearRect(0, 0, 1, 1); x.fillStyle = css; x.fillRect(0, 0, 1, 1);
    const d = x.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map(i => top[i] * a + under[i] * (1 - a)).concat([1]);
  };
  const layers = [];
  for (let n = el; n; n = n.parentElement) {
    const bg = px(getComputedStyle(n).backgroundColor);
    if (bg[3] > 0) layers.push(bg);
    if (bg[3] === 1) break;
  }
  let base = [255, 255, 255, 1];
  if (layers.length && layers[layers.length - 1][3] === 1) base = layers.pop();
  while (layers.length) base = over(layers.pop(), base);
  const fg = over(px(getComputedStyle(el).color), base);
  const lum = (c) => {
    const f = c.slice(0, 3).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  };
  const a = lum(fg), b = lum(base);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
"""


def _landing_page(browser, live_server, width, scheme):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        critical = Gap(name="Event backbone resilience %s" % suffix, severity="critical",
                       organization_id=org["org_id"])
        db.session.add(critical)
        db.session.commit()
        gap_id = critical.id
        db.session.remove()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900},
                                  color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    if scheme == "dark":
        context.add_init_script(
            "localStorage.setItem('theme', 'dark');"
            "document.documentElement.classList.add('dark');")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.goto(live_server + LIST_PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("priority-action-row").filter(has_text="Event backbone resilience") \
        .get_by_test_id("priority-action-link").click()
    page.wait_for_url(lambda url: url.endswith("/implementation/gap-analysis#gap-%d" % gap_id),
                      timeout=PAGE_TIMEOUT)
    page.evaluate("() => document.documentElement.classList.toggle('dark', %s)"
                  % ("true" if scheme == "dark" else "false"))
    page.wait_for_timeout(500)
    return context, page, gap_id


def test_on_a_phone_the_landing_row_shows_its_severity(browser, live_server):
    """At 390 wide the row a link lands on names its severity where the person can see it."""
    context, page, gap_id = _landing_page(browser, live_server, 390, "light")
    try:
        landing = page.locator("#gap-%d" % gap_id)
        expect(landing).to_be_visible()
        seen = page.evaluate(
            """(sel) => {
              const row = document.querySelector(sel);
              const vw = document.documentElement.clientWidth;
              const out = [];
              for (const el of row.querySelectorAll('span, div')) {
                if (el.children.length || el.textContent.trim() !== 'Critical') continue;
                const r = el.getBoundingClientRect();
                if (!r.width || !r.height) continue;
                const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                const top = document.elementFromPoint(cx, cy);
                out.push({inView: r.left >= 0 && r.right <= vw && r.top >= 0 && r.bottom <= innerHeight,
                          uncovered: !!top && (top === el || el.contains(top) || top.contains(el)) &&
                                     !top.closest('button')});
              }
              return out;
            }""", "#gap-%d" % gap_id)
        assert any(s["inView"] and s["uncovered"] for s in seen), \
            "the landing row's Critical is not visible at 390: %s" % seen
    finally:
        context.close()


def test_in_dark_mode_the_critical_badge_is_readable(browser, live_server):
    context, page, gap_id = _landing_page(browser, live_server, 1440, "dark")
    try:
        badge = page.locator("#gap-%d [data-testid='gap-severity-narrow'], #gap-%d td:nth-child(3) span"
                             % (gap_id, gap_id)).filter(has_text=re.compile("^\s*Critical\s*$")).last
        expect(badge).to_be_visible()
        ratio = badge.evaluate(_CONTRAST_JS)
        assert ratio >= 4.5, "Critical badge contrast in dark mode is %.2f:1" % ratio
    finally:
        context.close()
