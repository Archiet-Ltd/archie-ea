"""An enterprise architect asks "what is at risk, and how healthy is it?" on three
screens and gets one answer.

The risk register counts critical risks; the risk assessment must count the same
risks, reached through the capabilities they threaten, with each capability's
owner read from the owner record. The capability map's health lens and the
capability health page must list the same capabilities and show one capability
with one figure. Every risk in the register shows what it is linked to, and each
of those links lands on a page with content.

The organisation is seeded with linked data (capabilities, an application mapped
to one, risks linked to a capability, to an application and to a solution, one
risk linked to nothing); a second organisation holds its own critical risk so a
leak between organisations would change the counts.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, owner_id, noun, *, critical_title_prefix):
    """Capabilities, one mapped application and six risks; returns their names."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.risk import Risk, RiskStatus
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.solution_models import Solution
    from app.models.unified_capability import UnifiedCapability
    from app.services.capability_ownership_service import set_capability_owner

    app = create_app("testing")
    names = {}
    with app.app_context():
        def capability(label, level, current=None, target=None):
            row = BusinessCapability(
                name="%s %s" % (noun, label), level=level, organization_id=org_id,
                current_maturity_level=current, target_maturity_level=target,
                strategic_importance="critical" if label == "Alpha" else "medium")
            db.session.add(row)
            db.session.commit()
            return row

        alpha = capability("Alpha", 1, 1, 4)          # three levels short: health 15
        beta = capability("Beta", 1, 3, 3)            # mapped to an application: health 100
        gamma = capability("Gamma", 1)                # nothing recorded
        delta = capability("Delta", 2)                # a sub-level capability with no parent
        billing = ApplicationComponent(name="%s Billing" % noun, organization_id=org_id)
        db.session.add(billing)
        db.session.commit()
        db.session.add(ApplicationCapabilityMapping(
            application_component_id=billing.id, business_capability_id=beta.id,
            organization_id=org_id, coverage_percentage=90))
        solution = Solution(name="%s Portal" % noun, organization_id=org_id)
        db.session.add(solution)
        db.session.commit()
        # Owners are recorded against the unified capability the business capability is projected into.
        projected = UnifiedCapability.query.filter_by(
            organization_id=org_id, source_table="business_capability", source_id=str(alpha.id)).one()
        set_capability_owner(capability_id=projected.id, user_id=owner_id, organization_id=org_id)

        def risk(title, likelihood, impact, status, kind, entity_id):
            row = Risk(title=title, description="%s is what could go wrong." % title,
                       likelihood=likelihood, impact=impact, status=RiskStatus(status),
                       owner="Risk Owner", mitigation_plan="Mitigation for %s." % title,
                       organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            if kind:
                db.session.add(RiskEntityLink(organization_id=org_id, risk_id=row.id,
                                              entity_type=kind, entity_id=entity_id))
                db.session.commit()
            return row.id

        names["alpha"], names["beta"] = alpha.name, beta.name
        names["gamma"], names["delta"] = gamma.name, delta.name
        names["billing"], names["solution"] = billing.name, solution.name
        names["risks"] = {
            "direct": risk("%s direct critical" % critical_title_prefix, 5, 5, "open", "capability", alpha.id),
            "via_app": risk("%s via application critical" % critical_title_prefix, 4, 4, "open", "application", billing.id),
            "closed": risk("%s mitigated critical" % critical_title_prefix, 5, 5, "mitigated", "capability", alpha.id),
            "high": risk("%s high" % critical_title_prefix, 3, 3, "open", "capability", gamma.id),
            "solution": risk("%s solution risk" % critical_title_prefix, 2, 2, "open", "solution", solution.id),
            "loose": risk("%s loose" % critical_title_prefix, 1, 1, "open", None, None),
        }
        db.session.remove()
    return names


def _number(page, testid):
    figure = page.locator('[data-testid="%s"]' % testid)
    assert figure.count() >= 1, "no %s on %s: %s" % (testid, page.url, page.locator("body").inner_text()[:400])
    return int(figure.first.inner_text().strip())


def test_risk_assessment_and_capability_health_agree(live_server, browser):
    noun = "Agree%s" % uuid.uuid4().hex[:6]
    other = "Other%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    names = _seed(org["org_id"], org["user_ids"]["enterprise_architect"], noun, critical_title_prefix=noun)
    # A second organisation with a critical risk of its own, linked to its own capability.
    rival = create_fresh_org("enterprise_architect")
    _seed(rival["org_id"], rival["user_ids"]["enterprise_architect"], other, critical_title_prefix=other)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, org["emails"]["enterprise_architect"])

    # The register: two open critical risks (the mitigated one is not current exposure).
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # The figure itself must be on the page before it is compared with anything.
    assert page.locator('[data-testid="register-critical"]').count() == 1, (
        "no register-critical figure on %s" % page.url)
    assert page.locator('[data-testid="register-open"]').count() == 1, (
        "no register-open figure on %s" % page.url)
    register_critical = _number(page, "register-critical")
    register_open = _number(page, "register-open")
    assert register_critical == 2, register_critical
    assert register_open == 5, register_open
    assert other not in page.locator("body").inner_text()
    # Every risk says what it is linked to; only the deliberately loose one says it is not.
    for key, risk_id in names["risks"].items():
        row = page.locator('[data-risk-row="%d"]' % risk_id)
        if key == "loose":
            expect(row.locator("[data-risk-unlinked]")).to_contain_text("Not linked")
        else:
            expect(row.locator("[data-risk-unlinked]")).to_have_count(0)
            expect(row.locator("[data-risk-touches]")).to_be_visible()
    # The links name the capability, the application and the solution, and each lands on a page with content.
    for key, name in (("direct", names["alpha"]), ("via_app", names["billing"]), ("solution", names["solution"])):
        page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.locator('[data-risk-row="%d"] [data-risk-touches]' % names["risks"][key]).get_by_role(
            "link", name=name, exact=True)
        link.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).not_to_contain_text("Page not found")

    # The assessment counts the same risks as the register.
    page.goto(base + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert _number(page, "assess-critical-value") == register_critical
    assert _number(page, "assess-high-value") == 1
    assert _number(page, "assess-medium-value") == 0
    assert _number(page, "assess-open-value") == register_open
    body = page.locator("body").inner_text()
    assert "No risk factors identified" not in body
    assert "Unassigned" not in body
    assert other not in body
    rows = page.locator('[data-testid="risk-assessment-row"]')
    expect(rows).to_have_count(6)  # every risk in the register, each with the capabilities it reaches
    direct = rows.filter(has_text="%s direct critical" % noun)
    # The capability's owner is read from the owner record, by name.
    expect(direct).to_contain_text(names["alpha"])
    expect(direct).to_contain_text("Launch")
    assessment_owner = re.search(r"Owner:\s*(.+)", direct.inner_text()).group(1).strip()
    assert assessment_owner and assessment_owner != "No owner recorded", direct.inner_text()
    # A capability with no recorded owner says so.
    expect(rows.filter(has_text="%s high" % noun)).to_contain_text("No owner recorded")
    # A risk linked through an application reaches that application's capability.
    expect(rows.filter(has_text="%s via application critical" % noun)).to_contain_text(names["beta"])
    # A risk linked to no capability says so and still opens.
    expect(rows.filter(has_text="%s loose" % noun)).to_contain_text("Not linked to a capability yet")
    expect(rows.filter(has_text="%s solution risk" % noun)).to_contain_text("Not linked to a capability yet")
    # Each row opens the risk it is about.
    direct.get_by_role("link", name="%s direct critical" % noun).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator('[data-testid="risk-description"]')).to_have_text(
        "%s direct critical is what could go wrong." % noun, timeout=PAGE_TIMEOUT)
    # A risk's description is its own words, not its mitigation plan.
    expect(page.locator('[data-testid="risk-mitigation"]')).to_have_text("Mitigation for %s direct critical." % noun)

    # Capability health: the page says how many capabilities it covers and shows Alpha's figure.
    page.goto(base + "/strategic/capability-health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    health_text = page.locator("body").inner_text()
    assert "of 4 capabilities" in health_text, health_text[:600]
    alpha_row = page.locator("tr", has_text=names["alpha"]).first
    health_figure = alpha_row.inner_text()
    assert "15%" in health_figure, health_figure

    # The lens lists the same four capabilities, Alpha with the same figure.
    page.goto(base + "/capability-map/?lens=health&view=list", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    lens_rows = page.locator("tbody tr", has_text=noun)
    expect(lens_rows).to_have_count(4, timeout=PAGE_TIMEOUT)
    expect(lens_rows.filter(has_text=names["alpha"])).to_contain_text("15%")
    expect(lens_rows.filter(has_text=names["beta"])).to_contain_text("100%")
    expect(lens_rows.filter(has_text=names["delta"])).to_contain_text("Not assessed")
    # The lens names the owner the assessment names, and says so when there is none.
    expect(lens_rows.filter(has_text=names["alpha"]).locator('[data-testid="lens-owner"]')).to_have_text(
        assessment_owner, timeout=PAGE_TIMEOUT)
    expect(lens_rows.filter(has_text=names["gamma"]).locator('[data-testid="lens-owner"]')).to_have_text(
        "No owner recorded")
    assert other not in page.locator("body").inner_text()

    # The map view counts the same four capabilities, in its bands, and shows Alpha's figure on its card.
    page.goto(base + "/capability-map/?lens=health", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    badges = page.locator("span", has_text="capabilities").filter(has_text=" capabilities")
    expect(page.get_by_text(names["alpha"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
    band_total = sum(int(t.split()[0]) for t in badges.all_inner_texts() if t.strip().split()[0].isdigit())
    assert band_total == 4, badges.all_inner_texts()
    expect(page.locator("[role=button]", has_text=names["alpha"]).first).to_contain_text("15%")

    # A risk linked to nothing can be linked from the register's picker, which searches the one
    # capability search; the link then shows on the register and the assessment reaches the capability.
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    loose = page.locator('[data-risk-row="%d"]' % names["risks"]["loose"])
    loose.get_by_role("button", name="Link it").click()
    page.get_by_label("Entity type to link").select_option("capability")
    page.get_by_label("Search entities to link").fill(names["gamma"])
    page.get_by_role("dialog", name="Risk detail").get_by_role("button", name=names["gamma"], exact=True).click()
    expect(page.get_by_text("Risk mapped to %s." % names["gamma"])).to_be_visible(timeout=PAGE_TIMEOUT)
    page.goto(base + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="risk-assessment-row"]').filter(has_text="%s loose" % noun)).to_contain_text(
        names["gamma"], timeout=PAGE_TIMEOUT)
    context.close()
