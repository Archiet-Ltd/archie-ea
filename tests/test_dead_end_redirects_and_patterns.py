"""Old dashboard addresses keep the visitor's query exactly, and the application
patterns address answers within a few seconds behind a slow AI provider."""
import time

import pytest

from tests.test_ai_endpoints_bounded import _make_logged_in_client


def _location(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 301, (path, response.status_code)
    return response.headers["Location"]


@pytest.mark.parametrize(
    "old, target",
    [
        ("/dashboard/impact-analysis", "/enterprise/analysis/impact-analysis"),
        ("/dashboard/portfolio/gap-analysis", "/enterprise/implementation/gap-analysis"),
    ],
)
@pytest.mark.parametrize(
    "query",
    [
        "a=1&a=2",
        "endpoint=x",
        "_method=POST",
        "severity=high&sort=severity&dir=desc",
    ],
)
def test_old_address_lands_on_the_live_page_with_the_query_unchanged(
    app, db_session, make_org, old, target, query
):
    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "redir", app_count=0)
    location = _location(client, "%s?%s" % (old, query))
    assert location.endswith(target + "?" + query), location


def test_old_address_without_a_query_adds_none(app, db_session, make_org):
    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "redirq", app_count=0)
    assert _location(client, "/dashboard/impact-analysis").endswith("/enterprise/analysis/impact-analysis")


def test_patterns_answer_within_five_seconds_behind_a_slow_provider(
    app, db_session, make_org, monkeypatch
):
    from app.modules.ai_chat.services import llm_service_impl as llm_impl

    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "slowpat", app_count=2)

    def _slow(prompt, *args, **kwargs):
        time.sleep(9.0)
        return "[]"

    monkeypatch.setattr(llm_impl.LLMService, "generate_from_prompt", staticmethod(_slow))
    started = time.monotonic()
    response = client.get("/api/ea-workflows/sa/application-patterns")
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, elapsed
    assert response.status_code == 504
    body = response.get_json()
    assert "did not answer in time" in body["error"]
    assert "detail" not in body


def test_debt_scores_carry_factors_and_mark_applications_without_data_not_rated(
    app, db_session, make_org
):
    from datetime import date

    from app.models.application_portfolio import ApplicationComponent

    client, org, _apps = _make_logged_in_client(app, db_session, make_org, "debtfac", app_count=0)
    other = make_org("debtfac-other")
    db_session.add_all(
        [
            ApplicationComponent(name="Old ledger", organization_id=org.id,
                                 end_of_life_date=date(2020, 1, 1), version="3.0"),
            ApplicationComponent(name="Bare record", organization_id=org.id),
            ApplicationComponent(name="Other org ledger", organization_id=other.id,
                                 end_of_life_date=date(2020, 1, 1)),
        ]
    )
    db_session.flush()

    response = client.get("/api/ea/phase-d/debt-scores")
    assert response.status_code == 200
    body = response.get_json()
    rows = {row["app_name"]: row for row in body["scores"]}
    assert "Other org ledger" not in rows

    assert rows["Old ledger"]["debt_score"] == 100
    assert rows["Old ledger"]["factors"] == ["End of life has passed", "Old major version"]

    assert rows["Bare record"]["debt_score"] is None
    assert rows["Bare record"]["factors"] == []
    assert "no end-of-life date, version, support status recorded" in rows["Bare record"]["reason"]
    assert body["summary"]["not_rated"] == 1
    assert body["summary"]["critical"] == 1


def test_debt_scores_error_names_no_exception(app, db_session, make_org, monkeypatch):
    from app.services.technology_roadmap_service import TechnologyRoadmapService

    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "debterr", app_count=1)

    def _boom(self, app_id):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(TechnologyRoadmapService, "score_technology_debt", _boom)
    response = client.get("/api/ea/phase-d/debt-scores")
    assert response.status_code == 500
    assert "secret internal detail" not in response.get_data(as_text=True)
    assert "detail" not in response.get_json()
