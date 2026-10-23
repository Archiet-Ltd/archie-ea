"""The element-backed and non-draft strategy frameworks, in a real browser.

The eight frameworks open through one canvas view. The SWOT and PESTLE draft
journeys live in test_strategy_draft_swot_journey.py; this file drives the
families that are filled by adding elements rather than drafting:

  - BCG (a 2x2 over Products): open with a seeded product already in a
    quadrant, see it, add another from the canvas, reload, see it persist in
    its box, and follow its detail link to a page with the element's content.
  - VRIO (a table over Assessments): open with a seeded resource in a column,
    add one from the canvas, reload, see it.
  - A second organisation sees none of the first's elements (isolation).

Each assertion is what a person sees after a reload, not a container or a
status code. The add form is driven as a person drives it: fill the name,
submit, land back on the canvas with the element in its box.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_org_with_products():
    """A fresh organisation with an architect and a seeded Product already
    classified as a BCG star and a VRIO-valuable resource, so the canvas opens
    with real elements in their boxes. Returns {"org_id", "email"}."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {}
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Frameworks Org %s" % suffix, slug="frameworks-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id

        email = "frameworks.arch.%s@example.com" % suffix
        user = User(
            email=email, first_name="Frameworks", last_name="Arch",
            organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = architect_role
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out["email"] = email
        out["suffix"] = suffix

        # Seed a classified product (BCG star) and a VRIO-valuable resource,
        # each written through the one writer with the framework-scoped key.
        from flask import g
        g.current_org_id = org.id
        from app.services import strategy_draft_service as engine

        engine.add_element("bcg_matrix", "stars", "Seeded Star %s" % suffix, org.id,
                           element_type="Product")
        engine.add_element("vrio", "valuable", "Seeded Resource %s" % suffix, org.id,
                           element_type="Assessment")
        db.session.commit()
        db.session.remove()
    return out


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _new_page(browser, width=1440, height=900):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _box(page, box_key):
    return page.locator('[data-testid="strategy-box-%s"]' % box_key)


def test_bcg_opens_with_a_seeded_product_and_an_add_persists(browser, live_server):
    base = live_server
    org = _seed_org_with_products()
    other = _seed_org_with_products()

    context, page = _new_page(browser)
    _login(page, base, org["email"])

    # Open BCG; the seeded star is in the Stars quadrant.
    page.goto(base + "/strategy/bcg_matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("strategy-grid-bcg_matrix")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(_box(page, "stars")).to_contain_text("Seeded Star %s" % org["suffix"], timeout=PAGE_TIMEOUT)

    # Add a new product to the Question marks quadrant from the canvas.
    added = "Added Question %s" % org["suffix"]
    form = page.get_by_test_id("strategy-add-form-question_marks")
    form.locator('input[name="name"]').fill(added)
    form.get_by_test_id("strategy-add-submit-question_marks").click()

    # Back on the canvas after the add; reload and see it persist in its box.
    page.wait_for_url(lambda url: "/strategy/bcg_matrix" in url, timeout=PAGE_TIMEOUT)
    page.goto(base + "/strategy/bcg_matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_box(page, "question_marks")).to_contain_text(added, timeout=PAGE_TIMEOUT)

    # The placed element links to its own detail page — a page with its content.
    link = _box(page, "question_marks").locator('[data-testid="strategy-point-link"]').first
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    href = link.get_attribute("href")
    landing = page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert landing.status < 400, "detail link did not land: %s" % landing.status
    expect(page.locator("body")).to_contain_text(added, timeout=PAGE_TIMEOUT)
    context.close()

    # A second organisation sees none of the first's products.
    context2, page2 = _new_page(browser)
    _login(page2, base, other["email"])
    page2.goto(base + "/strategy/bcg_matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.get_by_test_id("strategy-grid-bcg_matrix")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page2.locator("body")).not_to_contain_text("Seeded Star %s" % org["suffix"])
    expect(page2.locator("body")).not_to_contain_text(added)
    context2.close()


def test_vrio_table_opens_with_a_seeded_resource_and_an_add_persists(browser, live_server):
    base = live_server
    org = _seed_org_with_products()

    context, page = _new_page(browser)
    _login(page, base, org["email"])

    page.goto(base + "/strategy/vrio", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("strategy-grid-vrio")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(_box(page, "valuable")).to_contain_text("Seeded Resource %s" % org["suffix"], timeout=PAGE_TIMEOUT)

    added = "Rare capability %s" % org["suffix"]
    form = page.get_by_test_id("strategy-add-form-rare")
    form.locator('input[name="name"]').fill(added)
    form.get_by_test_id("strategy-add-submit-rare").click()
    page.wait_for_url(lambda url: "/strategy/vrio" in url, timeout=PAGE_TIMEOUT)
    page.goto(base + "/strategy/vrio", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_box(page, "rare")).to_contain_text(added, timeout=PAGE_TIMEOUT)
    context.close()
