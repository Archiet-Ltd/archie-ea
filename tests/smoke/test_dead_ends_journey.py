"""An architect opens the pages an inventory sweep flagged and each one is whole.

In a new organisation with a capability that has no importance rating, a
two-level capability tree, and twenty-one applications, the architect:

* opens Capability Health and reads a plain "Not rated", never the word None;
* follows the two old dashboard addresses (impact analysis, gap analysis) and
  lands on the live page each name promises;
* opens the vendor mapping tool and its back link lands on the vendor list;
* opens the element list, runs Tech Debt Analysis and sees the result without a
  script error;
* opens the capability tree, clicks "All" and sees the tree with no policy error;
* opens the connectors page and gets a page, not a server error;
* asks for the application patterns and gets an answer in under five seconds.

Each page is walked at 1440 wide in light and 390 wide in dark; no console
error or uncaught script error is allowed on any of them.
"""

import os
import time

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = (("light", 1440, 900), ("dark", 390, 844))
PATTERNS_BUDGET_SECONDS = 5


def _seed(org_id, tag):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        db.session.add(BusinessCapability(name="Asset Lifecycle %s" % tag, level=1, organization_id=org_id))
        root = UnifiedCapability(name="Tree root %s" % tag, code="DE-R-%s" % tag[:4], level=1, organization_id=org_id)
        db.session.add(root)
        db.session.flush()
        db.session.add(UnifiedCapability(name="Tree leaf %s" % tag, code="DE-L-%s" % tag[:4], level=2,
                                         parent_capability_id=root.id, organization_id=org_id))
        for number in range(21):
            db.session.add(ApplicationComponent(name="Dead end app %d %s" % (number, tag), organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _walk_dir(tmp_path_factory):
    return os.environ.get("DEADEND_WALK_DIR") or str(tmp_path_factory.mktemp("deadend-walk"))


def test_swept_pages_are_whole(browser, live_server, tmp_path_factory):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    tag = org["suffix"]
    _seed(org["org_id"], tag)
    walk = _walk_dir(tmp_path_factory)
    os.makedirs(walk, exist_ok=True)

    for scheme, width, height in VIEWPORTS:
        context = browser.new_context(viewport={"width": width, "height": height},
                                      color_scheme=scheme)
        page = context.new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        label = "%s%d" % (scheme, width)

        def goto(path):
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        def shot(name):
            page.screenshot(path=os.path.join(walk, "%s-%s.png" % (label, name)), full_page=True)

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])

            goto("/enterprise/strategic/capability-health")
            body = page.locator("main").inner_text()
            assert "Asset Lifecycle %s" % tag in body
            assert "Not Rated" in body
            assert "None" not in body
            shot("capability-health")

            goto("/dashboard/impact-analysis")
            assert page.url.endswith("/enterprise/analysis/impact-analysis"), page.url
            assert page.locator("main").inner_text().strip()
            assert "Applications Portfolio Dashboard" not in page.content()
            shot("impact-analysis")

            goto("/dashboard/portfolio/gap-analysis")
            assert page.url.endswith("/enterprise/implementation/gap-analysis"), page.url
            assert "Gap" in page.locator("main").inner_text()
            assert "Applications Portfolio Dashboard" not in page.content()
            shot("gap-analysis")

            goto("/vendors/integration/mapping")
            back = page.get_by_role("link", name="Back to Vendors")
            assert back.count() == 1
            back.click()
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            assert page.url.rstrip("/").endswith("/vendors"), page.url
            assert page.locator("main").inner_text().strip()
            shot("vendors")

            goto("/architecture/elements")
            page.get_by_role("button", name="Tech Debt Analysis").click()
            page.get_by_text("Technology Debt Scores").wait_for(timeout=PAGE_TIMEOUT)
            page.get_by_text("elements analyzed").wait_for(timeout=PAGE_TIMEOUT)
            assert page.get_by_text("Could not load debt scores").count() == 0
            shot("tech-debt")

            goto("/capability-map/tree/capability")
            page.get_by_role("button", name="All", exact=True).click()
            page.wait_for_timeout(500)
            assert "Tree leaf %s" % tag in page.locator("#tree-container").inner_text()
            shot("capability-tree")

            response = page.goto(live_server + "/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200, response.status
            assert page.locator("main").inner_text().strip()
            shot("connectors")

            started = time.monotonic()
            status, payload = api(page, "GET", "/api/ea-workflows/sa/application-patterns")
            elapsed = time.monotonic() - started
            assert status == 200, (status, payload)
            assert payload["patterns"]["classified"] == 21, payload
            assert elapsed < PATTERNS_BUDGET_SECONDS, "patterns took %.1fs" % elapsed

            assert problems == [], problems
        finally:
            context.close()
