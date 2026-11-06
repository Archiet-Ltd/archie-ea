"""Journey, in a browser, on a phone: nothing inside the admin page frame is cut off.

A person opening a roadmap, a scorecard, a capability map or a connector page on a phone must be able to
reach everything on it. Content wider than the frame has to sit in its own sideways-scrolling region, and
the last column or card of that region has to be reachable by scrolling it. Before the fix, tab rows,
breadcrumb trails, boards and timelines were wider than the frame and the frame clipped them with nothing
to scroll.

The organisation is the demonstration company, seeded by the product's own seed, so the pages carry
linked applications, capabilities, roadmaps and risks.
"""

import os

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_USER = "demo@lantern-quay.example.com"

# Routes from the phone sweep whose admin page frame held content wider than the screen.
ROUTES = [
    "/adm-kanban/plateau-roadmap",
    "/admin/connectors/m365",
    "/applications/rationalization/gaps",
    "/applications/roadmap",
    "/architecture-assistant/",
    "/architecture-assistant/model-viewer",
    "/batch-import/new",
    "/capability-map/archimate/review-queue",
    "/capability-map/dashboard",
    "/capability-maturity/search",
    "/capability-roadmap",
    "/dashboard/rationalization/assessment",
    "/dashboard/rationalization/scorecard",
    "/dashboard/vendor-analysis/new",
    "/ea-workflows",
    "/enterprise/capability-map",
    "/genome/roadmap/",
    "/governance/dashboard",
    "/market-intelligence/",
    "/my-applications/",
    "/my-applications/health",
    "/my-applications/roadmap",
    "/solutions/api-registry",
    "/solutions/data-stewardship",
    "/stakeholders/map",
    "/strategic/capability-health",
    "/strategic/impact-analysis",
    "/strategic/risk-assessment",
]

# Elements inside main that stick out past main's right edge with no sideways scroller between them and main.
WIDE = """() => {
  const main = document.querySelector('main#main-content');
  const edge = main.getBoundingClientRect().right;
  const visible = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const scrolls = e => { for (let a = e.parentElement; a && a !== main; a = a.parentElement) {
    const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'scroll') return true; } return false; };
  const out = [];
  for (const e of main.querySelectorAll('*')) {
    if (!visible(e) || e.closest('svg') || getComputedStyle(e).position === 'fixed') continue;
    const r = e.getBoundingClientRect();
    if (r.right > edge + 1 && !scrolls(e)) {
      out.push(e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\\s+/).slice(0, 3).join('.') +
               ' right=' + Math.round(r.right) + ' main=' + Math.round(edge));
    }
  }
  return out.slice(0, 6);
}"""

# Every sideways scroller in main whose content is wider than its box: scroll to the end and report any
# whose last child is still outside the box.
UNREACHABLE = """() => {
  const main = document.querySelector('main#main-content');
  const bad = [];
  for (const e of main.querySelectorAll('*')) {
    const o = getComputedStyle(e).overflowX;
    if ((o !== 'auto' && o !== 'scroll') || e.scrollWidth <= e.clientWidth + 1 || !e.clientWidth) continue;
    e.scrollLeft = e.scrollWidth;
    const box = e.getBoundingClientRect(), last = e.lastElementChild;
    if (!last) continue;
    const r = last.getBoundingClientRect();
    if (r.right > box.right + 1 || box.right > innerWidth + 1) {
      bad.push(e.tagName.toLowerCase() + '.' + String(e.className).trim().split(/\\s+/).slice(0, 3).join('.') +
               ' last right=' + Math.round(r.right) + ' box right=' + Math.round(box.right));
    }
  }
  return bad;
}"""


@pytest.fixture(scope="module")
def demo_org(live_server):
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with create_app("testing").app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_USER


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 360])
def test_nothing_in_the_admin_frame_is_cut_off_on_a_phone(browser, live_server, demo_org, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 800}, color_scheme=scheme,
                                  is_mobile=True, has_touch=True)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    problems = []
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", demo_org)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, wait_until="commit", timeout=PAGE_TIMEOUT)
        for route in ROUTES:
            response = page.goto(live_server + route, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle")
            assert response.status == 200, "%s answered %s" % (route, response.status)
            assert page.locator("main#main-content h1, main#main-content h2").count() > 0, \
                "%s shows no heading in the admin frame" % route
            for item in page.evaluate(WIDE):
                problems.append("%s: wider than the frame: %s" % (route, item))
            for item in page.evaluate(UNREACHABLE):
                problems.append("%s: cannot be scrolled to its end: %s" % (route, item))
    finally:
        context.close()
    assert not problems, "\n".join(problems)
