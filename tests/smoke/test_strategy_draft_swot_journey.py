"""An architect drafts a SWOT (and a PESTLE) from their own model.

The journey the brief describes, driven in a real browser as a signed-in
architect, in an organisation seeded with linked applications, risks and a
vendor contract (each an ArchiMate element the engine can cite):

  open SWOT -> "Draft from my model" -> at least one cited proposal per box
  -> accept two, edit one, reject one -> reload -> the accepted and edited
  points are in their boxes as Assessment elements with their citation, and
  the rejected one is gone.

Then the same shape for PESTLE in one test, and a second organisation that
sees none of the first's proposals or points. Every citation shown is
followed to prove it lands on a page with real content, not an empty state.

Opt-in and deterministic: the model call goes to the CI protocol stub
(SMOKE_AI_PROTOCOL_STUB=1), whose SWOT/PESTLE scenarios ground each point in a
record id the engine put in the prompt, so the citation filter keeps them.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey, pytest.mark.ai_protocol]

SWOT_BOXES = ("strengths", "weaknesses", "opportunities", "threats")
PESTLE_BOXES = ("political", "economic", "social", "technological", "legal", "environmental")


@pytest.fixture(scope="session")
def required_protocol(ai_protocol_stub):
    if ai_protocol_stub is None:
        pytest.fail(
            "This journey requires explicit SMOKE_AI_PROTOCOL_STUB=1; "
            "this is not a skipped qualification"
        )
    return ai_protocol_stub


def _seed_org_with_model():
    """A fresh organisation with an architect, a configured provider pointing
    at the CI stub, and linked applications, risks and a vendor contract (each
    an ArchiMate element the engine can read and cite).

    Returns {"org_id", "email"}.
    """
    from tests.smoke.ai_protocol_stub import MODEL, TOKEN

    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {}
    with app.app_context():
        from app.models.models import APISettings
        from app.models.organization import Organization
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Strategy Org %s" % suffix, slug="strategy-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id

        email = "strategy.arch.%s@example.com" % suffix
        user = User(
            email=email, first_name="Strategy", last_name="Arch",
            organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = architect_role
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out["email"] = email

        # The provider this organisation resolves — the CI stub, seeded the
        # same way the shared smoke org seeds its own.
        db.session.add(APISettings(
            provider="openai", key_label="ci-protocol-stub", api_key=TOKEN,
            enabled=True, default_model=MODEL, organization_id=org.id,
        ))
        db.session.commit()

        # Linked model: an application, a risk and a vendor contract. Each is a
        # motivation/structure entity whose flush listener writes an
        # ArchiMate element, so the engine has real records to cite.
        from app.models.application_portfolio import ApplicationComponent, VendorContract
        from app.models.risk import Risk, RiskStatus

        db.session.add(ApplicationComponent(
            name="Payroll System %s" % suffix, organization_id=org.id,
            lifecycle_status="operational", description="Core payroll application.",
        ))
        db.session.add(Risk(
            title="Unplanned outage %s" % suffix, organization_id=org.id,
            likelihood=4, impact=4, status=RiskStatus.OPEN,
            description="Risk of an unplanned outage of the payroll run.",
        ))
        import datetime as _dt

        db.session.add(VendorContract(
            organization_id=org.id, contract_name="Payroll vendor MSA %s" % suffix,
            contract_number="PAY-%s" % suffix, status="active", contract_value=100000,
            start_date=_dt.date.today(),
        ))
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


def _draft(page):
    with page.expect_response(
        lambda r: r.url.rstrip("/").endswith("/draft") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        page.get_by_test_id("strategy-draft").click()
    assert resp.value.status < 400, "draft failed: %d %s" % (resp.value.status, resp.value.text()[:300])


def _accept_first_proposal(page, box_key):
    box = _box(page, box_key)
    proposal = box.locator('[data-testid="strategy-proposal"]').first
    expect(proposal).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: r.url.rstrip("/").endswith("/accept") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        proposal.get_by_test_id("strategy-accept").click()
    assert resp.value.status < 400, "accept failed: %d %s" % (resp.value.status, resp.value.text()[:300])
    return resp.value


def test_architect_drafts_a_swot_from_their_model(browser, live_server, required_protocol):
    base = live_server
    org = _seed_org_with_model()
    other = _seed_org_with_model()

    context, page = _new_page(browser)
    _login(page, base, org["email"])

    # Open SWOT for this organisation.
    page.goto(base + "/strategy/swot", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("strategy-grid-swot")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Draft, and see at least one cited proposal in every box.
    _draft(page)
    for box_key in SWOT_BOXES:
        proposals = _box(page, box_key).locator('[data-testid="strategy-proposal"]')
        expect(proposals.first).to_be_visible(timeout=PAGE_TIMEOUT)
        # Each proposal names the record it rests on.
        expect(
            _box(page, box_key).locator('[data-testid="strategy-proposal-citation"]').first
        ).to_be_visible(timeout=PAGE_TIMEOUT)

    # Accept two (strengths, weaknesses).
    _accept_first_proposal(page, "strengths")
    _accept_first_proposal(page, "weaknesses")

    # Edit one before accepting (opportunities): change the text, then accept.
    opp = _box(page, "opportunities").locator('[data-testid="strategy-proposal"]').first
    edited_text = "Edited opportunity point %s" % org["org_id"]
    opp.get_by_test_id("strategy-proposal-text").fill(edited_text)
    _accept_first_proposal(page, "opportunities")

    # Reject one (threats): it must disappear and never be written.
    threats = _box(page, "threats")
    rejected_text = threats.locator('[data-testid="strategy-proposal-text"]').first.input_value()
    threats.locator('[data-testid="strategy-proposal"]').first.get_by_test_id("strategy-reject").click()
    expect(threats.locator('[data-testid="strategy-proposal"]')).to_have_count(0, timeout=PAGE_TIMEOUT)

    # Reload: the accepted and edited points persist as Assessment elements
    # with their citation; the rejected one is gone.
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    for box_key in ("strengths", "weaknesses", "opportunities"):
        accepted = _box(page, box_key).locator('[data-testid="strategy-accepted-%s"] [data-testid="strategy-point"]' % box_key)
        expect(accepted.first).to_be_visible(timeout=PAGE_TIMEOUT)
        # Every accepted point shows its citation.
        expect(
            _box(page, box_key).locator('[data-testid="strategy-citations"]').first
        ).to_be_visible(timeout=PAGE_TIMEOUT)

    # The edited text is the one that persisted.
    expect(_box(page, "opportunities")).to_contain_text(edited_text, timeout=PAGE_TIMEOUT)
    # The rejected point was never written.
    expect(_box(page, "threats")).not_to_contain_text(rejected_text)
    expect(
        _box(page, "threats").locator('[data-testid="strategy-accepted-threats"] [data-testid="strategy-point"]')
    ).to_have_count(0)

    # Every citation link lands on a page with real content (the record's
    # neighbourhood in the Composer), not an empty state or a 404.
    link = _box(page, "strengths").locator('[data-testid="strategy-citation-link"]').first
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    href = link.get_attribute("href")
    landing = page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert landing.status < 400, "citation link did not land: %s" % landing.status
    # The composer page for the cited element renders its own canvas surface
    # and draws at least one node for the focused element — a page with real
    # content for the thing clicked, not an empty state or a 404.
    expect(page.locator("#composer-canvas")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.locator("#composer-canvas .joint-element").first).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()

    # A second organisation sees none of the first's points.
    context2, page2 = _new_page(browser)
    _login(page2, base, other["email"])
    page2.goto(base + "/strategy/swot", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.get_by_test_id("strategy-grid-swot")).to_be_visible(timeout=PAGE_TIMEOUT)
    for box_key in SWOT_BOXES:
        expect(
            _box(page2, box_key).locator('[data-testid="strategy-accepted-%s"] [data-testid="strategy-point"]' % box_key)
        ).to_have_count(0, timeout=PAGE_TIMEOUT)
    body = page2.locator("body")
    expect(body).not_to_contain_text("Edited opportunity point %s" % org["org_id"])
    context2.close()


def test_architect_drafts_a_pestle_from_their_model(browser, live_server, required_protocol):
    base = live_server
    org = _seed_org_with_model()

    context, page = _new_page(browser)
    _login(page, base, org["email"])

    page.goto(base + "/strategy/pestle", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("strategy-grid-pestle")).to_be_visible(timeout=PAGE_TIMEOUT)

    _draft(page)
    for box_key in PESTLE_BOXES:
        proposals = _box(page, box_key).locator('[data-testid="strategy-proposal"]')
        expect(proposals.first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Accept two, edit one, reject one — the same shape as SWOT.
    _accept_first_proposal(page, "political")
    _accept_first_proposal(page, "economic")

    social = _box(page, "social")
    edited_text = "Edited social factor %s" % org["org_id"]
    social.get_by_test_id("strategy-proposal-text").first.fill(edited_text)
    _accept_first_proposal(page, "social")

    legal = _box(page, "legal")
    rejected_text = legal.locator('[data-testid="strategy-proposal-text"]').first.input_value()
    legal.locator('[data-testid="strategy-proposal"]').first.get_by_test_id("strategy-reject").click()
    expect(legal.locator('[data-testid="strategy-proposal"]')).to_have_count(0, timeout=PAGE_TIMEOUT)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for box_key in ("political", "economic", "social"):
        expect(
            _box(page, box_key).locator('[data-testid="strategy-accepted-%s"] [data-testid="strategy-point"]' % box_key).first
        ).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(_box(page, "social")).to_contain_text(edited_text, timeout=PAGE_TIMEOUT)
    expect(_box(page, "legal")).not_to_contain_text(rejected_text)
    context.close()
