"""Regression tests for sweep01g template fixes.

WHAT WAS BROKEN
---------------
1. /enterprise/implementation/gap-analysis: Jinja template syntax error
   (unknown tag 'endmacro') because _gap_actions macro was replaced by
   status_label macro.

2. Python None printed as "None" text on four pages:
   - /hybrid-mapping-dashboard: mapping_strength rendered as "None/5"
   - /strategic/capability-health: status badge rendered "None"
   - /enterprise/strategic/investment-matrix: strategic_importance badge
     and maturity level cells rendered "None"
   - /strategic/risk-assessment: risk_factors cell rendered "None"

Each test asserts status 200 and that "None" does not appear in the
main content area (allowing it in scripts, meta tags, etc.).
"""

import uuid
import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id, org_id, app):
    """Standard Flask-Login test-client pattern with session minting."""
    from tests._session_test_helpers import mint_test_sid
    from flask import g, has_app_context

    sid = mint_test_sid(user_id, organization_id=org_id, app=app)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = sid
    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_logged_in_client(app, db_session, make_org, enterprise_role="enterprise_architect"):
    from app.models.user import User, Role

    org = make_org("sweep01g")
    suffix = uuid.uuid4().hex[:8]
    Role.insert_roles()
    architect_role = Role.query.filter_by(name="Architect").one()
    user = User(
        email=f"sweep01g-{suffix}@example.com",
        first_name="Sweep",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    user.role = architect_role
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id, org.id, app)
    return client, org, user


def _extract_main_content(html: str) -> str:
    """Extract the main content area from rendered HTML.

    The main content is between <main> tags or the page-shell content block.
    This avoids false positives from scripts, meta tags, or navigation.
    """
    # Try to find <main>...</main>
    import re
    main_match = re.search(r"<main[^>]*>(.*?)</main>", html, re.DOTALL | re.IGNORECASE)
    if main_match:
        return main_match.group(1)

    # Fallback: look for page-shell content area
    content_match = re.search(r'data-page-content="true"[^>]*>(.*?)</div>\s*</div>\s*$', html, re.DOTALL | re.IGNORECASE)
    if content_match:
        return content_match.group(1)

    # Last resort: return everything (will catch more false positives but safe)
    return html


class TestGapAnalysisRenders:
    """Gap analysis page renders without template syntax error."""

    def test_gap_analysis_returns_200(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/enterprise/implementation/gap-analysis")
        assert resp.status_code == 200, (
            f"gap-analysis returned {resp.status_code}: {resp.get_data(as_text=True)[:2000]}"
        )

    def test_gap_analysis_no_none_in_main(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/enterprise/implementation/gap-analysis")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        main_content = _extract_main_content(html)
        # "None" should not appear as rendered text in main content
        assert "None" not in main_content, (
            f"'None' text found in gap-analysis main content:\n{main_content[:3000]}"
        )


class TestHybridMappingDashboard:
    """Hybrid mapping dashboard does not render 'None' for mapping_strength."""

    def test_hybrid_mapping_dashboard_returns_200(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/hybrid-mapping-dashboard")
        assert resp.status_code == 200, (
            f"hybrid-mapping-dashboard returned {resp.status_code}: {resp.get_data(as_text=True)[:2000]}"
        )

    def test_hybrid_mapping_dashboard_no_none_in_main(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/hybrid-mapping-dashboard")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        main_content = _extract_main_content(html)
        assert "None" not in main_content, (
            f"'None' text found in hybrid-mapping-dashboard main content:\n{main_content[:3000]}"
        )


class TestCapabilityHealth:
    """Capability health page does not render 'None' for status badge."""

    def test_capability_health_returns_200(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/strategic/capability-health")
        assert resp.status_code == 200, (
            f"capability-health returned {resp.status_code}: {resp.get_data(as_text=True)[:2000]}"
        )

    def test_capability_health_no_none_in_main(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/strategic/capability-health")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        main_content = _extract_main_content(html)
        assert "None" not in main_content, (
            f"'None' text found in capability-health main content:\n{main_content[:3000]}"
        )


class TestInvestmentMatrix:
    """Investment matrix page does not render 'None' for strategic_importance or maturity levels."""

    def test_investment_matrix_returns_200(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/enterprise/strategic/investment-matrix")
        assert resp.status_code == 200, (
            f"investment-matrix returned {resp.status_code}: {resp.get_data(as_text=True)[:2000]}"
        )

    def test_investment_matrix_no_none_in_main(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/enterprise/strategic/investment-matrix")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        main_content = _extract_main_content(html)
        assert "None" not in main_content, (
            f"'None' text found in investment-matrix main content:\n{main_content[:3000]}"
        )


class TestRiskAssessment:
    """Risk assessment page does not render 'None' for risk_factors cell."""

    def test_risk_assessment_returns_200(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/strategic/risk-assessment")
        assert resp.status_code == 200, (
            f"risk-assessment returned {resp.status_code}: {resp.get_data(as_text=True)[:2000]}"
        )

    def test_risk_assessment_no_none_in_main(self, app, db_session, make_org):
        client, org, user = _make_logged_in_client(app, db_session, make_org)
        resp = client.get("/strategic/risk-assessment")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        main_content = _extract_main_content(html)
        assert "None" not in main_content, (
            f"'None' text found in risk-assessment main content:\n{main_content[:3000]}"
        )