"""An enterprise architect sees two risks scored alike on likelihood and impact but treated to
different degrees. Each screen that shows a risk shows the score its band is computed from, named as
residual or inherent, so the same number never carries two bands: the register, the risk assessment
and the risk's own page agree, and the counts on the assessment add up to the rows beside them.

The organisation holds a risk treated down to low, one treated only to high, one never treated, and
a second organisation holds a critical risk whose title must never appear.

A fourth risk has a base pair of 2 x 2 but an inherent pair of 4 x 4 recorded. It is linked to a
solution and to an element, so the ranking sentence, the solution's heat map and the assistant's
risk answer must all read "Inherent 16", critical, and none may fall back to its base score of 4.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .chat_ask import ask
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, prefix, user_id=None):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.risk import Risk, RiskStatus
    from app.models.solution_models import Solution

    app = create_app("testing")
    ids = {}
    with app.app_context():
        solution = Solution(name="%s solution" % prefix, organization_id=org_id, created_by_id=user_id)
        element = ArchiMateElement(name="%s ledger" % prefix, type="ApplicationComponent",
                                   layer="application", organization_id=org_id)
        db.session.add_all([solution, element])
        db.session.commit()
        ids["solution"] = solution.id
        ids["element_name"] = element.name

        def risk(key, title, likelihood, impact, residual=None, inherent=None):
            row = Risk(title="%s %s" % (prefix, title), likelihood=likelihood, impact=impact,
                       status=RiskStatus.OPEN, organization_id=org_id, owner="Risk Owner",
                       solution_id=solution.id,
                       archimate_element_id=element.id if inherent else None,
                       residual_likelihood=residual[0] if residual else None,
                       residual_impact=residual[1] if residual else None,
                       inherent_likelihood=inherent[0] if inherent else None,
                       inherent_impact=inherent[1] if inherent else None)
            db.session.add(row)
            db.session.commit()
            ids[key] = row.id
            ids[key + "_title"] = row.title

        # Likelihood 2 x impact 5 = 10 for all three; the bands differ because the residual scores do.
        risk("low", "failover treated well", 2, 5, residual=(1, 2))       # residual 2: low
        risk("high", "failover treated a little", 2, 5, residual=(3, 3))  # residual 9: high
        risk("raw", "failover untreated", 2, 5)                           # inherent 10: high
        risk("pair", "failover recorded inherent", 2, 2, inherent=(4, 4))  # inherent 16: critical, base 4
        db.session.remove()
    return ids


def test_each_risk_shows_the_score_its_band_is_computed_from(live_server, browser):
    prefix = "Band%s" % uuid.uuid4().hex[:6]
    other = "Rival%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    ids = _seed(org["org_id"], prefix, org["user_ids"]["enterprise_architect"])
    rival = create_fresh_org("enterprise_architect")
    _seed(rival["org_id"], other, rival["user_ids"]["enterprise_architect"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, org["emails"]["enterprise_architect"])
    expected = {"low": ("Residual 2", "Low"), "high": ("Residual 9", "High"), "raw": ("Inherent 10", "High"),
                "pair": ("Inherent 16", "Critical")}

    # The register: each row carries the score its band comes from.
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for key, (score, band) in expected.items():
        row = page.locator("tr", has_text=ids[key + "_title"]).first
        expect(row).to_contain_text(score, timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text(band)
    assert other not in page.locator("body").inner_text()

    # The ranked list's sentence names the score the band is read from, never the base pair.
    ranked = page.locator("#ranked-risks")
    reasons = ranked.locator("[data-risk-reason]")
    expect(reasons.filter(has_text="inherent score of 16 (inherent likelihood 4 of 5, impact 4 of 5)")).to_have_count(
        1, timeout=PAGE_TIMEOUT)
    expect(reasons.filter(has_text="residual score of 2")).to_have_count(1)
    assert "recorded score of 4" not in ranked.inner_text()

    # The assessment: the same score and band per row, and the counts add up to the rows.
    page.goto(base + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for key, (score, band) in expected.items():
        row = page.locator('[data-testid="risk-assessment-row"]', has_text=ids[key + "_title"])
        expect(row).to_contain_text(score + " of 25")
        expect(row).to_contain_text(band)
    assert int(page.locator('[data-testid="assess-high-value"]').first.inner_text()) == 2
    assert int(page.locator('[data-testid="assess-critical-value"]').first.inner_text()) == 1
    assert int(page.locator('[data-testid="assess-medium-value"]').first.inner_text()) == 0
    assert other not in page.locator("body").inner_text()

    # Rows run from the highest shown score down, so a Residual 2 never sits above an Inherent 10.
    shown = [page.locator('[data-testid="risk-assessment-row"]').nth(i).inner_text()
             for i in range(page.locator('[data-testid="risk-assessment-row"]').count())]
    order = [next(k for k in expected if ids[k + "_title"] in text) for text in shown]
    assert order.index("pair") < order.index("raw") < order.index("high") < order.index("low")

    # The solution's Risks tab prints each risk's band with the score it is computed from, and each
    # row opens the register with that risk on it.
    page.goto(base + "/solutions/%d?tab=risks" % ids["solution"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for key, (score, band) in expected.items():
        row = page.get_by_test_id("risk-row").filter(has_text=ids[key + "_title"])
        expect(row).to_contain_text("%s · %s" % (band, score), timeout=PAGE_TIMEOUT)
    page.get_by_test_id("risk-link").filter(has_text=ids["pair_title"]).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("body")).to_contain_text(ids["pair_title"], timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).to_contain_text("Inherent 16")

    # The assistant's risk answer for the element the inherent risk sits on.
    answer = ask(page, base, "What could hurt %s and what does it touch" % ids["element_name"])
    row = answer.locator("[data-lens-row]")
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text(ids["pair_title"])
    expect(row).to_contain_text("Inherent 16 of 25")
    expect(row).to_contain_text("critical")
    assert "= 4" not in row.inner_text()

    # Each risk opens its own page with the same score and band.
    for key, (score, band) in expected.items():
        page.goto(base + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=ids[key + "_title"]).click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator('[data-testid="risk-score"]')).to_have_text(score + " of 25", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="risk-level"]')).to_have_text(band, ignore_case=True)
        expect(page.locator("body")).to_contain_text(ids[key + "_title"])
    context.close()
