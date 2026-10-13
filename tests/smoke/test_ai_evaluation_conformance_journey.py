"""AI evaluation gates and design conformance review, driven in a browser.

enterprise_architect (the nearest archetype to an AI Architect): finds an
unevaluated AI system, sees the refusal naming the missing evaluation, sets the
plan, records a failing run (the refusal now names the metric), records a
passing run, and after the administrator releases it, reloads and sees it
released. The Release action stays administrator-only (it is not widened here),
so the last step signs in as the platform administrator.

arb_member: opens a submitted AI design, sees the deviations listed, records a
decision, reloads and sees it recorded.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_authorisation_matrix import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _row(page, name):
    return page.locator("tr", has_text=name)


def test_enterprise_architect_evaluates_an_ai_system_then_it_is_released(
    page, live_server, seeded
):
    from app import create_app, db
    from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

    app = create_app("testing")
    name = "Evaluation journey system %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        register_ai_system(
            db.session, seeded["ids"]["org"], name=name,
            provider="anthropic", model_id="claude-opus-5",
        )
        db.session.commit()

    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/genome/ai-systems", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    # The register already says why this system cannot be released.
    assert "no evaluation plan" in _row(page, name).inner_text()
    _row(page, name).locator('[data-testid="evaluation-link"]').click()
    page.wait_for_url("**/evaluation", timeout=PAGE_TIMEOUT)

    # Attempt the release check: refused, naming the missing evaluation.
    assert "no evaluation plan" in page.locator('[data-testid="release-refusal"]').inner_text()
    page.locator('[data-testid="release-check-button"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert "no evaluation plan" in page.locator('[data-testid="release-refusal"]').inner_text()

    # Set the plan.
    page.fill('input[name="test_set_name"]', "Claims golden set")
    page.fill('input[name="test_set_size"]', "40")
    page.fill('input[name="threshold_groundedness"]', "0.8")
    page.locator('[data-testid="save-plan-button"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert "Claims golden set" in page.locator('[data-testid="plan-summary"]').inner_text()
    assert "no evaluation run" in page.locator('[data-testid="release-refusal"]').inner_text()

    # A failing run: the refusal names the metric.
    page.fill('[data-testid="run-form"] input[name="metric_groundedness"]', "0.6")
    page.locator('[data-testid="record-run-button"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert "groundedness 0.6 is below the threshold 0.8" in page.locator(
        '[data-testid="release-refusal"]').inner_text()

    # A passing run clears the refusal.
    page.fill('[data-testid="run-form"] input[name="metric_groundedness"]', "0.9")
    page.locator('[data-testid="record-run-button"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert page.locator('[data-testid="release-ready"]').count() == 1

    # Release is the administrator's action; reload shows it released.
    page.context.clear_cookies()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/genome/ai-systems", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    _row(page, name).get_by_role("button", name="Release", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    page.goto(live_server + "/genome/ai-systems", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    assert "released" in _row(page, name).inner_text().lower()


def test_arb_member_sees_the_deviations_and_records_a_decision(page, live_server, seeded):
    from app import create_app, db
    from app.modules.ai_chat.services import ai_design_conformance_service as svc
    from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system

    app = create_app("testing")
    title = "Journey design %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        element = register_ai_system(
            db.session, seeded["ids"]["org"], name="Design journey system %s" % uuid.uuid4().hex[:6],
            provider="anthropic", model_id="claude-opus-5",
        )
        row = svc.submit_design(
            element_id=element.id, organization_id=seeded["ids"]["org"],
            submitter_id=seeded["ids"]["solution_architect_user"], title=title,
            context="Triage inbound claims.",
            declaration={"pattern": "human-in-the-loop-copilot",
                         "ingests_untrusted_content": True, "untrusted_input_fenced": False},
        )
        db.session.commit()
        review_id = row.id

    _login(page, live_server, seeded["emails"]["arb_member"])
    page.goto(live_server + "/architecture/ai-design-reviews/", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name=title).click()
    page.wait_for_url("**/ai-design-reviews/%d" % review_id, timeout=PAGE_TIMEOUT)
    assert page.locator('[data-testid="deviation-unfenced-untrusted-input"]').count() == 1

    page.select_option('[data-testid="decision-form"] select[name="decision"]', "accept")
    page.fill('[data-testid="decision-form"] textarea[name="rationale"]',
              "Accepted: the fence ships before launch.")
    page.locator('[data-testid="record-decision-button"]').click()
    page.wait_for_load_state("domcontentloaded")

    page.goto(live_server + "/architecture/ai-design-reviews/%d" % review_id,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.locator('[data-testid="review-status"]').inner_text().strip() == "accepted"
    assert "the fence ships before launch" in page.locator(
        '[data-testid="review-outcome"]').inner_text()
    assert page.locator('[data-testid="decision-form"]').count() == 0
