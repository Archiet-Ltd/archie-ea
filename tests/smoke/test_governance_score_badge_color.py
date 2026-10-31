"""The Capability Governance Dashboard's "Governance Score" tile icon was
hardcoded green (bg-success, a shield-check icon) regardless of the actual
score. Found 14 Sep 2026 in a full-app design pass: a seeded org with 3
capabilities, all needing attention, showed "Governance Score 0%" next to
a green success badge -- directly contradicting the "Need Attention: 3"
tile right beside it. Fixed by driving the icon's background color off the
actual numeric score (>=80 success, >=50 warning, else destructive, muted
when there is nothing to measure).
"""
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
def capability_needing_attention(seeded):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability
    import uuid

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        cap = UnifiedCapability(
            organization_id=org_id,
            name="Governance badge smoke %s" % uuid.uuid4().hex[:8],
            level=1,
        )
        db.session.add(cap)
        db.session.commit()
        cap_id = cap.id

    yield cap_id

    with app.app_context():
        row = db.session.get(UnifiedCapability, cap_id)
        if row is not None:
            db.session.delete(row)
            db.session.commit()


def test_zero_score_does_not_show_success_color(browser, live_server, seeded, capability_needing_attention):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/capability-governance/governance-dashboard",
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        score_text = page.locator("text=Governance Score").locator("..").locator("..").inner_text()
        if "0%" not in score_text:
            pytest.skip("seeded capability did not produce a 0%% governance score in this environment")

        # The tile's icon was replaced by the library's band badge (the card no longer carries icons).
        icon_wrapper = page.get_by_test_id("gov-score-band")
        class_attr = icon_wrapper.get_attribute("class") or ""
        assert "bg-success" not in class_attr, (
            "the Governance Score tile shows a green success icon at a 0%% "
            "score -- the color must reflect the actual score, not a "
            "hardcoded value (class=%r)" % class_attr
        )
    finally:
        page.close()
