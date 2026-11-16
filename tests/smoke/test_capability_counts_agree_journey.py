"""An enterprise architect whose organisation has recorded no capability of its own, with the shared
catalogue loaded, reads the same answer on every page that heads a capability count: none of their own,
and the catalogue said once in one sentence. After adding one capability, every page says one.

Fails when a page heads the catalogue's capabilities as the organisation's own.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]
CATALOGUE = 3
SENTENCE = re.compile(r"(\d+) from the shared catalogue, not yet adopted")


def _shot(page, name, viewport, scheme):
    walk = os.environ.get("SMOKE_WALK_DIR")
    if walk:
        os.makedirs(walk, exist_ok=True)
        page.screenshot(path=os.path.join(walk, "capcount-%s-%d-%s.png" % (name, viewport["width"], scheme)),
                        full_page=True)


@pytest.fixture
def catalogue():
    """Shared-catalogue capabilities for the run, removed again by their own ids."""
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        rows = [UnifiedCapability(name="Catalogue %s %d" % (tag, n), code="CC%s%d" % (tag.upper(), n), level=1,
                                  organization_id=None, scope="reference") for n in range(CATALOGUE)]
        db.session.add_all(rows)
        db.session.commit()
        ids = [r.id for r in rows]
        db.session.remove()
    yield ids
    with app.app_context():
        UnifiedCapability.query.filter(UnifiedCapability.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def _own_and_sentence(page, base, path, testid_root):
    """The own figure and the catalogue figure a page heads, read from its words."""
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    lens = page.locator("#capability-lens")
    if lens.count():
        lens.select_option("unified")
    if "enterprise/capability-map" in path:
        # The headline figure is refreshed by the page's own request; it must settle on the same own number.
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        headline = int(page.locator("#unified-cap-count").inner_text())
        assert headline == int(re.match(r"(\d+)", page.get_by_test_id(testid_root + "-own").inner_text()).group(1))
    own = page.get_by_test_id(testid_root + "-own").first
    expect(own).to_be_visible(timeout=PAGE_TIMEOUT)
    catalogue = page.get_by_test_id(testid_root + "-catalogue").first
    expect(catalogue).to_be_visible()
    shared = int(SENTENCE.search(catalogue.inner_text()).group(1))
    return int(re.match(r"(\d+)", own.inner_text()).group(1)), shared


PAGES = [("/capability-map/", "capability-scope"), ("/capability-map/hierarchy", "capability-scope"),
         ("/enterprise/capability-map/", "dashboard-capability-scope"),
         ("/strategic/investment-matrix", "investment-capability-scope")]


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_every_capability_count_agrees_before_and_after_adding_one(browser, live_server, catalogue, viewport, scheme):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        seen = []
        for path, root in PAGES:
            seen.append(_own_and_sentence(page, live_server, path, root))
            _shot(page, path.strip("/").replace("/", "-"), viewport, scheme)
        assert {own for own, _ in seen} == {0}, seen
        assert len({shared for _, shared in seen}) == 1 and seen[0][1] >= CATALOGUE, seen

        # With none of its own, the page names the next step, and the step lands on a page that can add one.
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("capability-scope-next").click()
        page.wait_for_url(re.compile(r"/capability-map/capabilities"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("button", name="New Capability")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.locator('[data-action="openCapCreate"]').click()
        page.fill("#cap-name", "Own capability %s" % org["suffix"])
        with page.expect_response(lambda r: r.request.method == "POST" and "/capabilities" in r.url,
                                  timeout=PAGE_TIMEOUT) as created:
            page.locator('[data-action="submitCap"]').click()
        assert created.value.status < 400
        # The page reloads itself once the capability is saved; wait for it before moving on.
        expect(page.locator(".cap-card", has_text="Own capability %s" % org["suffix"])).to_be_visible(timeout=PAGE_TIMEOUT)

        after = [_own_and_sentence(page, live_server, path, root) for path, root in PAGES]
        assert {own for own, _ in after} == {1}, after
        assert len({shared for _, shared in after}) == 1, after
    finally:
        context.close()
