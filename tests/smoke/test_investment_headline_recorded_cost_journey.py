"""A CTO opens Investment Analysis and sees a headline built from recorded money.

Two real organisations each map applications to the same capability and record
annual costs. The signed-in CTO's headline equals the sum of their own
organisation's recorded costs; the other organisation's money never appears. A
third organisation maps an application but records no cost: the headline is an
explained empty value that says what to record and whose link lands on the
Applications page with content, not a fabricated figure.

This is the browser proof the secinv01 brief asked for: it was red on the
first-round head (the headline was placeholder arithmetic of a fixed figure per
capability, unrelated to any recorded cost) and is green after.
"""

import os
import pathlib
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _shot_dir():
    target = os.environ.get(
        "SMOKE_SCREENSHOT_DIR",
        str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "investment-headline"),
    )
    path = pathlib.Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return path

INVESTMENT_PATH = "/architecture/investment-priorities"


def _shared_capability(code_suffix, name):
    """A capability visible to every tenant (no organization_id), created once."""
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        cap = UnifiedCapability(
            name=name,
            code="INV-%s" % code_suffix,
            strategic_importance="critical",
        )
        db.session.add(cap)
        db.session.commit()
        cap_id = cap.id
        db.session.remove()
    return cap_id


def _seed_org(org_id, cap_id, costs):
    """Map one application per entry in *costs* to *cap_id* for *org_id*, each
    carrying the given recorded annual cost (None means nothing recorded)."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.services.application_cost_accessor import set_annual_cost

    app = create_app("testing")
    with app.app_context():
        for i, cost in enumerate(costs):
            component = ApplicationComponent(
                name="App %d %s" % (i, uuid.uuid4().hex[:6]),
                organization_id=org_id,
                lifecycle_status="active",
            )
            db.session.add(component)
            db.session.commit()
            if cost is not None:
                set_annual_cost(component, cost)
                db.session.commit()
            db.session.add(
                UnifiedApplicationCapabilityMapping(
                    unified_capability_id=cap_id,
                    application_component_id=component.id,
                    support_level="full",
                    coverage_percentage=90,
                )
            )
            db.session.commit()
        db.session.remove()


def _open_investment(page, base):
    page.goto(base + INVESTMENT_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _headline_card(page):
    # The "Total Investment" metric card: the title text sits in the card next
    # to its value.
    return page.locator("div[data-slot='card']", has_text="Total Investment").first


def test_investment_headline_is_recorded_money_and_isolated(live_server, browser):
    cap_name = "Settlements %s" % uuid.uuid4().hex[:8]
    cap = _shared_capability(uuid.uuid4().hex[:6], cap_name)

    # Org A records 180,000 + 20,000 = 200,000 -> headline £0.2M.
    # Org B records 900,000 -> headline £0.9M. Neither sees the other's money.
    org_a = create_fresh_org("cto")
    org_b = create_fresh_org("cto")
    _seed_org(org_a["org_id"], cap, [180000, 20000])
    _seed_org(org_b["org_id"], cap, [900000])

    base = live_server

    # --- Org A: headline equals its own recorded total, labelled recorded ---
    ctx_a = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx_a.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page_a = ctx_a.new_page()
    sign_in(page_a, base, org_a["emails"]["cto"])
    _open_investment(page_a, base)

    card_a = _headline_card(page_a)
    expect(card_a).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(card_a).to_contain_text("£0.2M")
    # The figure is explained as recorded money, not a guess.
    expect(card_a).to_contain_text("Recorded annual cost")
    # Not the first-round fabricated headline: org A's two apps total 200,000,
    # so £0.9M (org B's money) must never show here.
    assert "£0.9M" not in card_a.inner_text()

    # Headline and matrix agree on the same screen: filter the matrix to this
    # organisation's capability and assert its row shows the two covered
    # applications and a recorded figure (not 0 apps, not an invented number).
    search = page_a.get_by_placeholder("Search capabilities...")
    search.fill(cap_name)
    row = page_a.locator("tbody tr", has_text=cap_name).first
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("2 apps")
    expect(row).to_contain_text("Recorded")
    assert "No cost recorded" not in row.inner_text()
    page_a.screenshot(path=str(_shot_dir() / "investment-org-a-1440.png"), full_page=True)

    # Narrow view still shows the real headline.
    page_a.set_viewport_size({"width": 390, "height": 900})
    _open_investment(page_a, base)
    expect(_headline_card(page_a)).to_contain_text("£0.2M", timeout=PAGE_TIMEOUT)
    page_a.screenshot(path=str(_shot_dir() / "investment-org-a-390.png"), full_page=True)
    ctx_a.close()

    # --- Org B: sees only its own recorded money ---
    ctx_b = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx_b.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page_b = ctx_b.new_page()
    sign_in(page_b, base, org_b["emails"]["cto"])
    _open_investment(page_b, base)
    card_b = _headline_card(page_b)
    expect(card_b).to_contain_text("£0.9M", timeout=PAGE_TIMEOUT)
    assert "£0.2M" not in card_b.inner_text()
    page_b.screenshot(path=str(_shot_dir() / "investment-org-b-1440.png"), full_page=True)
    ctx_b.close()


def test_investment_headline_empty_value_is_explained_and_links(live_server, browser):
    cap = _shared_capability(uuid.uuid4().hex[:6], "Ledger %s" % uuid.uuid4().hex[:4])

    # One mapped application, no recorded cost: an explained empty value.
    org = create_fresh_org("cto")
    _seed_org(org["org_id"], cap, [None])

    base = live_server
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = ctx.new_page()
    sign_in(page, base, org["emails"]["cto"])
    _open_investment(page, base)

    card = _headline_card(page)
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    # No invented number: an explained empty value that says what to record.
    expect(card).to_contain_text("No cost recorded")
    expect(card).to_contain_text("Record an annual cost")
    # No fabricated figure anywhere on the headline.
    assert "M" not in card.locator("div[data-slot='card-title']").inner_text()
    page.screenshot(path=str(_shot_dir() / "investment-empty-1440.png"), full_page=True)

    # The headline links to Applications; the link lands on a page with content.
    # With an explained empty value the whole card is a link to Applications,
    # so the anchor wraps the card rather than sitting inside it.
    link = page.locator("a", has_text="Total Investment").first
    expect(link).to_have_attribute("href", "/applications/", timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    body = page.locator("body")
    expect(body).not_to_contain_text("Page not found")
    expect(body).not_to_contain_text("403")
    # The Applications page names its own subject — not an empty 200.
    expect(body).to_contain_text("Application", timeout=PAGE_TIMEOUT)
    page.screenshot(path=str(_shot_dir() / "investment-empty-applications-landing.png"), full_page=True)
    ctx.close()
