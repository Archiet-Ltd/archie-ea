"""One organisation, one number: the Applications list, the Overview, owner coverage, the portfolio cost tiers,
the v1 dashboard and the chat's portfolio summary all count the same applications, and a person's choice of
kind is read first everywhere."""
import re
import uuid

import pytest

from app import db
from app.models import ArchiMateRelationship
from app.models.application_portfolio import ApplicationComponent
from app.models.user import Role, User


def _row(org, name, **fields):
    row = ApplicationComponent(name=name, organization_id=org.id, lifecycle_status="operational", **fields)
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def seeded(db_session, make_org):
    Role.insert_roles()
    mine, theirs = make_org("figures-a"), make_org("figures-b")
    architect = User(email="figures.%s@example.com" % uuid.uuid4().hex[:10], first_name="Fay", last_name="Architect",
                     organization_id=mine.id, confirmed=True, enterprise_role="portfolio_manager")
    architect.role = Role.query.filter_by(name="Architect").one()
    architect.password = "Correct-Horse-9"
    db.session.add(architect)
    instantly = _row(mine, "Instantly", legacy_application_owner="Dana Reed", total_cost_of_ownership=1164)
    apollo = _row(mine, "Apollo", legacy_application_owner="Dana Reed", total_cost_of_ownership=2400)
    chosen = _row(mine, "launch-repo", repository_type="GitHub", application_kind="application", total_cost_of_ownership=500)
    _row(mine, "billing-repo", component_type="repository", repository_type="GitHub", total_cost_of_ownership=7000)
    _row(mine, "site-repo", component_type="repository", total_cost_of_ownership=9000)
    part = _row(mine, "Instantly mailer", total_cost_of_ownership=800)
    payroll = _row(mine, "Payroll", legacy_application_owner="Dana Reed", repository_type="GitHub", total_cost_of_ownership=100)
    _row(theirs, "Other tenant app", legacy_application_owner="Sam Lee")
    _row(theirs, "other-repo", repository_type="GitHub")
    db.session.add(ArchiMateRelationship(type="composition", source_id=instantly.archimate_element_id,
                                         target_id=part.archimate_element_id, organization_id=mine.id))
    db.session.flush()
    return {"org": mine, "architect": architect, "names": {instantly.name, apollo.name, chosen.name, payroll.name}}


def test_every_surface_counts_the_same_four_applications(seeded, client, login_as, tenant_ctx):
    org, architect = seeded["org"], seeded["architect"]
    figures = {}

    login_as(client, architect)
    page = client.get("/applications/?page_size=100")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    shown = {name for name in seeded["names"] | {"billing-repo", "site-repo", "Instantly mailer"} if name in html}
    assert shown == seeded["names"], "the list shows the applications and none of the repositories or parts"
    figures["list"] = len(shown)

    login_as(client, architect)
    tiers = client.get("/dashboard/api/tco/cost-tiers")
    assert tiers.status_code == 200
    tier_body = tiers.get_json()
    figures["cost tiers"] = tier_body["summary"]["total_portfolio"]
    assert sum(t["application_count"] for t in tier_body["data"]) == figures["cost tiers"]
    assert tier_body["summary"]["total_tco"] == 1164 + 2400 + 500 + 100, "the cost totals leave out repositories and parts"

    login_as(client, architect)
    metrics = client.get("/api/v1/dashboard/metrics")
    assert metrics.status_code == 200
    body = metrics.get_json()
    figures["v1 dashboard"] = (body.get("data") or body)["applications"]["total"]

    with tenant_ctx(org.id):
        from app.modules.applications.routes.coverage_routes import coverage_figures
        from app.modules.dashboard.services.overview_figures import build_overview
        from app.modules.ai_chat.services.command_parser_service import CommandParserService

        figures["overview"] = build_overview(org.id, architect, with_cost=False)["applications"]["total"]
        figures["owner coverage"] = coverage_figures(org.id)["total_apps"]
        reply = CommandParserService()._handle_health([], architect.id, "general")["response"]
        figures["chat"] = int(re.search(r"\| Applications \| (\d+) \|", reply).group(1))

    assert figures == {"list": 4, "cost tiers": 4, "v1 dashboard": 4, "overview": 4, "owner coverage": 4, "chat": 4}
