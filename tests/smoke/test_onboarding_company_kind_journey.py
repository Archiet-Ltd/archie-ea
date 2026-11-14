"""
Onboarding company kind journey test.

Journey:
1. Sign in and find the company kind question on Account settings, with Startup chosen
2. Choose Scale-up in one click
3. Reload and see it retained
4. Change it to Enterprise and see that kept too
5. Ensure another organization's choice is never visible to the first
6. From the first screen, GitHub connect is still one click and lands on a page with content
"""

import pytest
from playwright.sync_api import Page, expect

from .test_try_sample_company_journey import _empty_organisation_user, _login


def _sign_in(page, base, email):
    _login(page, base, email)


def _selected(page, kind):
    return page.locator('[data-testid="company-kind-%s"]' % kind).get_attribute("aria-pressed") == "true"


def _stored(org_id):
    from app import create_app, db
    from app.models.organization import Organization

    with create_app("testing").app_context():
        org = db.session.get(Organization, org_id)
        return ((org.settings or {}).get("company_context") or {}).get("kind")


@pytest.mark.journey
def test_onboarding_company_kind_journey(page: Page, live_server, seeded):
    """A user chooses their company kind, it persists, and another organization is unaffected."""
    email1, org1 = _empty_organisation_user(live_server)
    email2, org2 = _empty_organisation_user(live_server)
    base = live_server

    _sign_in(page, base, email1)
    page.goto(f"{base}/account/manage")
    page.wait_for_load_state("networkidle")
    expect(page.locator('[data-testid="company-kind-card"]')).to_be_visible(timeout=5000)
    assert _selected(page, "startup")

    page.locator('[data-testid="company-kind-scaleup"]').click()
    expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Saved", timeout=5000)
    assert _selected(page, "scaleup") and not _selected(page, "startup")
    assert _stored(org1) == "scaleup"

    page.reload()
    page.wait_for_load_state("networkidle")
    assert _selected(page, "scaleup")

    page.locator('[data-testid="company-kind-enterprise"]').click()
    expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Enterprise", timeout=5000)
    assert _stored(org1) == "enterprise"

    # Organization 2 starts from the default, not from organization 1's choice
    page.goto(f"{base}/account/logout")
    page.wait_for_load_state("networkidle")
    _sign_in(page, base, email2)
    page.goto(f"{base}/account/manage")
    page.wait_for_load_state("networkidle")
    assert _selected(page, "startup") and not _selected(page, "enterprise")
    page.locator('[data-testid="company-kind-startup"]').click()
    expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Startup", timeout=5000)

    assert _stored(org2) == "startup"
    assert _stored(org1) == "enterprise"


@pytest.mark.journey
def test_github_connect_still_one_click(page: Page, live_server, seeded):
    """Reaching GitHub connect from the first screen is one click and lands on a page with content."""
    email, _org = _empty_organisation_user(live_server)
    base = live_server

    _sign_in(page, base, email)
    page.goto(f"{base}/dashboard/overview")
    page.wait_for_load_state("networkidle")
    link = page.locator('[data-testid="overview-connect"]')
    expect(link).to_be_visible(timeout=5000)
    link.click()
    page.wait_for_load_state("networkidle")
    assert "github" in page.url.lower(), page.url
    expect(page.get_by_text("GitHub").first).to_be_visible()
    expect(page.get_by_text("404", exact=False)).to_have_count(0)
