"""The investment matrix's Total Capabilities is the organisation's real number of capabilities.

The matrix read every capability row the compatibility window lets a tenant see, which includes rows with
no owner and no shared-catalogue mark (rows left from before capabilities were given owners, some of them
another organisation's). It now reads the one visibility rule (its own capabilities and the shared
catalogue), counts them with a count query, and says how many of the total are shared.
"""
import re
import uuid

import pytest

from app.models.unified_capability import UnifiedCapability


def _capabilities(db_session, count, label, organization_id, scope=None, source_org_id=None):
    db_session.add_all(
        UnifiedCapability(name="%s %d" % (label, n), code="%s-%d" % (label[:3].upper(), n), level=1,
                          organization_id=organization_id, scope=scope, source_org_id=source_org_id)
        for n in range(count)
    )
    db_session.flush()


@pytest.fixture
def two_orgs(db_session, make_org):
    mine, theirs = make_org("mine"), make_org("theirs")
    _capabilities(db_session, 350, "Mine", mine.id, scope="tenant")
    _capabilities(db_session, 7, "Theirs", theirs.id, scope="tenant")
    # Rows left from before owners were recorded: no owner, not marked shared, written by the other organisation.
    _capabilities(db_session, 4, "Unowned", None, source_org_id=theirs.id)
    return mine, theirs


def test_total_is_the_organisations_capabilities_by_a_count_query(app, db_session, tenant_ctx, two_orgs):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    mine, theirs = two_orgs
    with tenant_ctx(mine.id):
        analysis = InvestmentPrioritizationService().analyze_investment_priorities()
    with tenant_ctx(theirs.id):
        their_total = InvestmentPrioritizationService().analyze_investment_priorities()["portfolio_metrics"]

    metrics = analysis["portfolio_metrics"]
    assert metrics["total_capabilities"] == 350
    assert (metrics["own_capabilities"], metrics["shared_capabilities"]) == (350, 0)
    assert len(analysis["capability_scores"]) == 350
    assert not any(row["capability_name"].startswith(("Theirs", "Unowned"))
                   for row in analysis["capability_scores"])
    assert their_total["total_capabilities"] == 7


def test_shared_catalogue_capabilities_are_counted_and_named(app, db_session, tenant_ctx, make_org):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    org = make_org("shared")
    _capabilities(db_session, 5, "Own", org.id, scope="tenant")
    _capabilities(db_session, 2, "Catalogue %s" % uuid.uuid4().hex[:6], None, scope="reference")
    with tenant_ctx(org.id):
        metrics = InvestmentPrioritizationService().analyze_investment_priorities()["portfolio_metrics"]
        shared = UnifiedCapability.query.filter(UnifiedCapability.scope == "reference",
                                                UnifiedCapability.organization_id.is_(None)).count()
    assert metrics["own_capabilities"] == 5
    assert metrics["shared_capabilities"] == shared >= 2
    assert metrics["total_capabilities"] == 5 + shared


def test_the_page_shows_the_total_under_the_sidebar_trail(app, db_session, login_as, client, two_orgs):
    from app.models.user import Role, User

    mine, _ = two_orgs
    user = User(email="matrix-%s@example.com" % uuid.uuid4().hex[:8], first_name="Matrix", last_name="Reader",
                organization_id=mine.id, confirmed=True, enterprise_role="enterprise_architect")
    user.role = Role.query.filter_by(name="Architect").first()
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    html = client.get("/strategic/investment-matrix").get_data(as_text=True)

    card = html.split('data-testid="total-capabilities"', 1)[1].split('data-testid="critical-priorities"', 1)[0]
    assert re.search(r">\s*350\s*<", card), card
    assert "Your organisation&#39;s capabilities" in card or "Your organisation's capabilities" in card
    crumbs = html.split('aria-label="Breadcrumb"', 1)[1].split("</nav>", 1)[0]
    assert "Spend" in crumbs and "Investments" in crumbs
    assert "Strategy Layer" not in crumbs and "Strategic Planning" not in crumbs
    assert "Prioritization" not in html
    assert "Investment prioritisation matrix" in html
