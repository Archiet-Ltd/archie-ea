"""Typed property panel values persist and invalid edits are refused in the browser."""

from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def property_panel_solution(app, seeded):
    from app import db
    from app.commands.seed_viewpoints import seed_property_templates
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
    from app.models.solution_domain_spec import SolutionDomainSpec
    from app.models.solution_models import Solution
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        seed_property_templates()
        owner = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()
        solution = Solution(
            name=f"Property panel journey {suffix}",
            description="Browser fixture for typed property persistence.",
            organization_id=owner.organization_id,
            created_by_id=owner.id,
            governance_status="draft",
            has_acm_domains=True,
            journey_state={"current_step": 3, "domains_populated": True},
        )
        db.session.add(solution)
        db.session.flush()

        db.session.add(
            SolutionDomainSpec(
                solution_id=solution.id,
                organization_id=owner.organization_id,
                domain_code="COM",
                status="confirmed",
            )
        )
        proposal = SolutionBlueprintProposal(
            solution_id=solution.id,
            organization_id=owner.organization_id,
            archimate_type="ApplicationInterface",
            name=f"Browser Payments API {suffix}",
            description="Typed property browser fixture.",
            source="user",
            status="accepted",
            acm_domain="COM",
            acm_properties={},
        )
        db.session.add(proposal)
        db.session.commit()
        payload = {"solution_id": solution.id, "proposal_name": proposal.name}

    yield payload

    with app.app_context():
        proposals = SolutionBlueprintProposal.query.filter_by(solution_id=payload["solution_id"]).all()
        for row in proposals:
            db.session.delete(row)
        specs = SolutionDomainSpec.query.filter_by(solution_id=payload["solution_id"]).all()
        for row in specs:
            db.session.delete(row)
        solution = db.session.get(Solution, payload["solution_id"])
        if solution is not None:
            db.session.delete(solution)
        db.session.commit()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_property_panel_persists_typed_value_and_refuses_unparseable_text(
    browser, live_server, seeded, property_panel_solution
):
    page = browser.new_page(viewport={"width": 1600, "height": 1100})
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(
            live_server + f"/architecture-journey/{property_panel_solution['solution_id']}",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        page.wait_for_function(
        """
            async ({ solutionId, proposalName }) => {
              const promotedResp = await fetch(`/architecture-journey/${solutionId}/promoted-elements`, {
                credentials: 'same-origin',
              });
              const promotedJson = await promotedResp.json();
              const promoted = promotedJson.data || promotedJson;
              const application = (promoted.elements_by_layer || {}).application || [];
              const target = application.find((entry) => entry.name === proposalName);
              if (!target) return false;

              const templatesResp = await fetch(
                `/architecture-journey/${solutionId}/property-templates/ApplicationInterface?tier=differentiating`,
                { credentials: 'same-origin' }
              );
              const templatesJson = await templatesResp.json();
              const templates = ((templatesJson.data || templatesJson).properties || []);
              return templates.some((entry) => entry.property_key === 'rate_limit');
            }
            """,
            arg={"solutionId": property_panel_solution["solution_id"], "proposalName": property_panel_solution["proposal_name"]},
            timeout=PAGE_TIMEOUT,
        )

        page.evaluate(
            """
            async ({ solutionId, proposalName, value }) => {
              const promotedResp = await fetch(`/architecture-journey/${solutionId}/promoted-elements`, {
                credentials: 'same-origin',
              });
              const promotedJson = await promotedResp.json();
              const promoted = promotedJson.data || promotedJson;
              const application = (promoted.elements_by_layer || {}).application || [];
              const target = application.find((entry) => entry.name === proposalName);
              if (!target) throw new Error('proposal missing');

              const response = await fetch(
                `/architecture-journey/${solutionId}/proposals/${target.proposal_id}/properties`,
                {
                  method: 'PATCH',
                  credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ properties: { rate_limit: value } }),
                }
              );
              if (!response.ok) throw new Error(`HTTP ${response.status}`);
            }
            """,
            {
                "solutionId": property_panel_solution["solution_id"],
                "proposalName": property_panel_solution["proposal_name"],
                "value": "1200 req/min",
            },
        )

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        value_state = page.evaluate(
            """
            async ({ solutionId, proposalName }) => {
              const promotedResp = await fetch(`/architecture-journey/${solutionId}/promoted-elements`, {
                credentials: 'same-origin',
              });
              const promotedJson = await promotedResp.json();
              const promoted = promotedJson.data || promotedJson;
              const application = (promoted.elements_by_layer || {}).application || [];
              const target = application.find((entry) => entry.name === proposalName);
              if (!target) return null;
              return target.acm_properties.rate_limit || null;
            }
            """,
            {"solutionId": property_panel_solution["solution_id"], "proposalName": property_panel_solution["proposal_name"]},
        )
        assert value_state == {"value": 1200, "unit": "req/min", "source": "user"}

        error_text = page.evaluate(
            """
            async ({ solutionId, proposalName, value }) => {
              const promotedResp = await fetch(`/architecture-journey/${solutionId}/promoted-elements`, {
                credentials: 'same-origin',
              });
              const promotedJson = await promotedResp.json();
              const promoted = promotedJson.data || promotedJson;
              const application = (promoted.elements_by_layer || {}).application || [];
              const target = application.find((entry) => entry.name === proposalName);
              if (!target) throw new Error('proposal missing');

              const response = await fetch(
                `/architecture-journey/${solutionId}/proposals/${target.proposal_id}/properties`,
                {
                  method: 'PATCH',
                  credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ properties: { rate_limit: value } }),
                }
              );
              const payload = await response.json();
              return payload.error || payload.message || '';
            }
            """,
            {
                "solutionId": property_panel_solution["solution_id"],
                "proposalName": property_panel_solution["proposal_name"],
                "value": "plain text",
            },
        )
        assert error_text == "Enter a number for this property."

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        value_state = page.evaluate(
            """
            async ({ solutionId, proposalName }) => {
              const promotedResp = await fetch(`/architecture-journey/${solutionId}/promoted-elements`, {
                credentials: 'same-origin',
              });
              const promotedJson = await promotedResp.json();
              const promoted = promotedJson.data || promotedJson;
              const application = (promoted.elements_by_layer || {}).application || [];
              const target = application.find((entry) => entry.name === proposalName);
              if (!target) return null;
              return target.acm_properties.rate_limit || null;
            }
            """,
            {"solutionId": property_panel_solution["solution_id"], "proposalName": property_panel_solution["proposal_name"]},
        )
        assert value_state == {"value": 1200, "unit": "req/min", "source": "user"}
    finally:
        page.close()
