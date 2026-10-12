"""Portfolio key figures: a count that could not be measured is a dash, never 0.

A zero the reader cannot tell from a measured zero is a number the system made
up. These tests fail the counts on purpose and check what each screen then says.
"""

import re
import types

import pytest


def _visible_text(resp):
    html = resp.get_data(as_text=True)
    html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
    html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html))


class _CountsFail:
    """Stands in for ``db.func``: every other function works, ``count`` raises."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        if name == "count":
            raise RuntimeError("count unavailable")
        return getattr(self._real, name)


def _user(db_session, org, role="enterprise_architect"):
    import uuid

    from app.models.user import User

    user = User(
        email=f"figures-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Fig",
        last_name="Ures",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    user.password = "TestPass123!"
    db_session.add(user)
    db_session.flush()
    return user


def test_portfolio_stats_are_none_when_every_query_fails(app, db_session, make_org, monkeypatch):
    from app import db
    from app.modules.dashboard.v2.services import executive_dashboard_service as svc

    org = make_org("figures-service")
    monkeypatch.setattr(svc, "db", types.SimpleNamespace(session=db.session, func=_CountsFail(db.func)))
    from flask import g

    g.current_org_id = org.id
    stats = svc.ExecutiveDashboardService()._get_portfolio_stats()
    assert stats == {"solutions": None, "applications": None, "vendors": None, "archimate_elements": None}


def _five_applications(db_session, org):
    from app.models.application_portfolio import ApplicationComponent

    for index in range(5):
        db_session.add(ApplicationComponent(name="Fig app %d" % index, organization_id=org.id))
    db_session.flush()


def test_measured_zero_stays_zero(app, db_session, make_org, tenant_ctx):
    from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService

    org = make_org("figures-empty")
    with tenant_ctx(org.id):
        stats = ExecutiveDashboardService()._get_portfolio_stats()
    assert stats["solutions"] == 0 and stats["applications"] == 0 and stats["archimate_elements"] == 0


def test_executive_summary_reports_null_not_zero_when_counts_fail(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app import db
    from app.modules.dashboard.v2.services import executive_dashboard_service as svc

    org = make_org("figures-api")
    user = _user(db_session, org)
    monkeypatch.setattr(svc, "db", types.SimpleNamespace(session=db.session, func=_CountsFail(db.func)))
    login_as(client, user)
    resp = client.get("/dashboard/api/executive-summary")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["Solutions"] is None
    assert data["Applications"] is None
    assert data["ArchiMate Elements"] is None


def test_overview_shows_a_dash_not_a_zero_when_counts_fail(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app import db
    from app.modules.dashboard.v2.routes import dashboard_views

    org = make_org("figures-overview")
    user = _user(db_session, org)
    _five_applications(db_session, org)
    monkeypatch.setattr(
        dashboard_views, "db", types.SimpleNamespace(session=db.session, func=_CountsFail(db.func))
    )
    login_as(client, user)
    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200
    text = _visible_text(resp)
    assert "— elements" in text
    assert "— relationships" in text
    assert "0 elements" not in text


def test_overview_shows_the_measured_count_when_the_queries_work(
    app, db_session, make_org, client, login_as
):
    org = make_org("figures-measured")
    user = _user(db_session, org)
    _five_applications(db_session, org)
    login_as(client, user)
    text = _visible_text(client.get("/dashboard/overview"))
    assert re.search(r"\d[\d,]* elements", text)
    assert "— elements" not in text


def test_unreadable_application_count_does_not_claim_an_empty_estate(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app import db
    from app.modules.dashboard.v2.routes import dashboard_views

    org = make_org("figures-guided")
    user = _user(db_session, org)
    monkeypatch.setattr(
        dashboard_views, "db", types.SimpleNamespace(session=db.session, func=_CountsFail(db.func))
    )
    login_as(client, user)
    text = _visible_text(client.get("/dashboard/overview"))
    assert "Import applications" not in text
