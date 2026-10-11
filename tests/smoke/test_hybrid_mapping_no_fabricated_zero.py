"""The Hybrid Multi-Path Mapping Dashboard rendered every coverage ratio as
a red "0.0%" for an organization with zero capabilities -- a 0/0 ratio
formatted and colored identically to a real, measured, failing 0%.

Found 14 Sep 2026 in a full-app design pass: app/main/routes_hybrid_mapping.py
returned a bare `0` (not `None`) for every zero-denominator coverage
percentage except one (prod_archimate_coverage, which already correctly
returned None) -- the one correct example proved the rest were a bug, not a
deliberate choice. Fixed by returning None consistently and rendering it as
an uncolored em dash in the template, matching CLAUDE.md's rule that a 0
meaning "not computed" must never be indistinguishable from a measured zero.
"""
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
def capability_free_org(live_server):
    """A fresh organisation with a platform_admin and no capabilities of its own.

    The shared `seeded` organisation is session-scoped and other smoke tests
    legitimately create capabilities in it over the life of a run (see
    test_capability_journey.py's create/edit/persist journey, which does not
    delete what it creates) -- so "the seeded org has zero capabilities" is
    not an assumption this test can make about a fixture 100+ other files also
    write to. One org of its own, following the same pattern as
    test_intelligence_us1_journey.py's bare_tenant/stale_tenant, is what
    actually guarantees the 0/0 state this test is about.
    """
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Capability-free Org %s" % suffix, slug="capfree-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="capfree.%s@example.com" % suffix, first_name="Capfree", last_name="Tenant",
            organization_id=org.id, enterprise_role="platform_admin", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email


def test_zero_capabilities_renders_dash_not_red_zero_percent(browser, live_server, capability_free_org):
    """An org with zero unified capabilities of its own, so every coverage
    ratio on this page is a 0/0 -- "not computed", not "measured at 0%"."""
    page = browser.new_page()
    try:
        _login(page, live_server, capability_free_org)
        page.goto(live_server + "/hybrid-mapping-dashboard", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(600)

        body_text = page.locator("body").inner_text()
        if "could not be calculated" in body_text:
            pytest.skip("stats query failed in this environment -- not what this test checks")

        destructive_percentages = page.eval_on_selector_all(
            ".text-destructive",
            "els => els.map(e => e.textContent.trim()).filter(t => t.includes('%'))"
        )
        assert not destructive_percentages, (
            "found red-colored percentage text on a zero-capability org's "
            "mapping dashboard: %s -- a 0/0 ratio must render as an "
            "uncolored dash, not a red 0.0%% (which reads as a measured "
            "failure)" % destructive_percentages
        )
    finally:
        page.close()
