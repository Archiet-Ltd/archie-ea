"""An organisation with no estimated savings or investment is told so in words, never shown £0.

The rationalization page and the duplicate-detection page both turned "no decision carries a figure"
into a confident £0, and the Disposition Actions card printed the raw code for an application that has
too little evidence to be dispositioned.
"""
import os
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]



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


@pytest.fixture
def unestimated_org(live_server):
    """An organisation whose scored applications carry no savings and no investment figure."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.models.organization import Organization
    from app.models.unified_duplicate_detection import UnifiedDetectionRun, UnifiedDuplicateGroup
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Unestimated Org %s" % suffix, slug="unest-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="unest.%s@example.com" % suffix, first_name="Unest", last_name="Tenant",
            organization_id=org.id, enterprise_role="platform_admin", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        db.session.add(user)
        apps = []
        for n in range(2):
            component = ApplicationComponent(name="Ledger %s-%d" % (suffix, n), organization_id=org.id)
            db.session.add(component)
            db.session.flush()
            apps.append(component)
            db.session.add(ApplicationRationalizationScore(
                application_component_id=component.id, organization_id=org.id,
                overall_health_score=55.0, rationalization_action="TOLERATE",
                disposition_action="insufficient_evidence",
            ))
        run = UnifiedDetectionRun(
            run_name="unest-%s" % suffix, strategy="fast", status="completed",
            groups_found=1, estimated_savings=None,
        )
        db.session.add(run)
        db.session.flush()
        group = UnifiedDuplicateGroup(name="Ledger pair %s" % suffix, detection_run_id=run.id)
        db.session.add(group)
        db.session.commit()
        ids = (run.id, group.id)
        email = user.email
    yield email
    with app.app_context():
        from sqlalchemy import text
        db.session.execute(text("DELETE FROM unified_duplicate_groups WHERE id = :i"), {"i": ids[1]})
        db.session.execute(text("DELETE FROM unified_detection_runs WHERE id = :i"), {"i": ids[0]})
        db.session.commit()


def test_rationalization_says_not_estimated_and_names_the_disposition(browser, live_server, unestimated_org):
    page = browser.new_page()
    try:
        _login(page, live_server, unestimated_org)
        page.goto(live_server + "/dashboard/rationalization", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1200)
        body = page.locator("body").inner_text()
        assert "Financial Impact" in body
        for label in ("Projected Savings", "Investment Needed", "Net Benefit"):
            block = page.locator("p", has_text=label).first.locator("xpath=..").inner_text()
            assert "Not estimated yet" in block, "%s reads %r" % (label, block)
            assert "£0" not in block and "0.00" not in block, block
        savings = page.locator("#savings-stat").inner_text()
        assert "Not estimated yet" in savings, savings
        assert "insufficient_evidence" not in body
        card = page.locator("h3", has_text="Disposition Actions").locator("xpath=..").inner_text()
        assert "insufficient evidence" in card.lower() and "_" not in card, card
    finally:
        page.close()


def test_duplicate_detection_says_no_saving_estimated(browser, live_server, unestimated_org):
    page = browser.new_page()
    try:
        _login(page, live_server, unestimated_org)
        page.goto(live_server + "/duplicate-detection/simple", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1200)
        tile = page.locator("p", has_text="Estimated Savings").first.locator("xpath=..").inner_text()
        assert "Not estimated yet" in tile, tile
        assert "£0" not in tile and "0.00" not in tile, tile
    finally:
        page.close()


@pytest.mark.parametrize("width,theme", [(1440, "light"), (390, "dark")])
def test_both_pages_walked_at_each_width_and_theme(browser, live_server, unestimated_org, tmp_path, width, theme):
    """Each page is drawn and the words, not a £0, are what a person reads; shots go to SMOKE_WALK_DIR."""
    out = os.environ.get("SMOKE_WALK_DIR") or str(tmp_path)
    os.makedirs(out, exist_ok=True)
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.add_init_script("localStorage.setItem('theme', %r);" % theme)
    page = ctx.new_page()
    try:
        _login(page, live_server, unestimated_org)
        for slug, path in (("rationalization", "/dashboard/rationalization"),
                           ("duplicate-detection", "/duplicate-detection/simple")):
            page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(1200)
            assert "Not estimated yet" in page.locator("body").inner_text(), slug
            page.screenshot(path=os.path.join(out, "%s-%d-%s.png" % (slug, width, theme)), full_page=True)
    finally:
        ctx.close()


# The words that replace a missing figure, measured as drawn: the text must sit inside every
# clipping ancestor, and the block that holds it must not scroll sideways.
_FIT_JS = """() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.textContent.trim() !== 'Not estimated yet') continue;
    const el = node.parentElement;
    if (!el.offsetParent) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const text = range.getBoundingClientRect();
    let block = el;
    while (block && getComputedStyle(block).display === 'inline') block = block.parentElement;
    const problems = [];
    if (block.scrollWidth > block.clientWidth) problems.push('block scrolls sideways');
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      if (getComputedStyle(a).overflowX !== 'visible' && text.right > a.getBoundingClientRect().right + 0.5) {
        problems.push('text runs past ' + a.tagName.toLowerCase());
      }
    }
    if (text.right > window.innerWidth) problems.push('text runs past the screen');
    out.push({classes: block.className, problems});
  }
  return out;
}"""


@pytest.mark.parametrize("width,theme", [(1440, "light"), (390, "dark"), (390, "light"), (1440, "dark")])
def test_the_words_for_a_missing_figure_fit_their_tile(browser, live_server, unestimated_org, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.add_init_script("localStorage.setItem('theme', %r);" % theme)
    page = ctx.new_page()
    try:
        _login(page, live_server, unestimated_org)
        for path, expected in (("/dashboard/rationalization", 4), ("/duplicate-detection/simple", 1)):
            page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(1200)
            found = page.evaluate(_FIT_JS)
            assert len(found) >= expected, (path, found)
            for item in found:
                assert not item["problems"], (path, width, theme, item)
                # Words are muted, not drawn in a figure's colour.
                assert "text-muted-foreground" in item["classes"], (path, item["classes"])
                assert "text-success" not in item["classes"] and "text-warning" not in item["classes"], item["classes"]
    finally:
        ctx.close()


def test_one_label_for_a_disposition_in_the_card_the_list_the_filter_and_the_dossier(browser, live_server, unestimated_org):
    page = browser.new_page()
    try:
        _login(page, live_server, unestimated_org)
        page.goto(live_server + "/dashboard/rationalization", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        card = page.locator("h3", has_text="Disposition Actions").locator("xpath=..").inner_text()
        assert "Insufficient Evidence" in card, card
        badges = page.locator("span.rounded-md", has_text="Insufficient").all_inner_texts()
        assert badges and all(b.strip() == "Insufficient Evidence" for b in badges), badges
        options = page.locator("option", has_text="Insufficient").all_inner_texts()
        assert options == ["Insufficient Evidence (2)"], options
        page.fill("#dossier-app-search", "Ledger")
        page.locator("#dossier-app-dropdown [role=option]").first.click()
        page.get_by_role("button", name="Load decision dossier for selected application").click()
        recommendation = page.locator("h4", has_text="Recommendation").locator("xpath=..")
        recommendation.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        text = recommendation.inner_text()
        assert "Insufficient Evidence" in text and "insufficient_evidence" not in text, text
    finally:
        page.close()
