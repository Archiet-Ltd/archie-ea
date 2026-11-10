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


@pytest.mark.parametrize(
    "path",
    ["/", "/account/login", "/dashboard/impact-analysis", "/dashboard/portfolio/gap-analysis", "/no-such-page"],
)
def test_a_raw_invalid_byte_in_the_query_is_a_bad_request_on_every_route(app, db_session, make_org, path):
    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "rawbyte", app_count=0)
    signed_in = client.get(path, environ_overrides={"QUERY_STRING": "a=\xff&ref=\xfe"})
    assert signed_in.status_code == 400, (path, signed_in.status_code)
    anonymous = app.test_client().get(path, environ_overrides={"QUERY_STRING": "a=\xff"})
    assert anonymous.status_code == 400, (path, anonymous.status_code)
    # the same bytes as a browser sends them are still an ordinary request
    assert client.get(path + "?a=%FF").status_code != 400


def test_old_address_without_a_query_adds_none(app, db_session, make_org):
    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "redirq", app_count=0)
    assert _location(client, "/dashboard/impact-analysis").endswith("/enterprise/analysis/impact-analysis")


def test_patterns_a_one_second_budget_gives_a_plain_504_behind_a_stuck_provider(
    app, db_session, make_org, monkeypatch
):
    from app.api import sa_phase_c_routes
    from app.modules.ai_chat.services import llm_service_impl as llm_impl

    monkeypatch.setattr(sa_phase_c_routes, "PATTERNS_REQUEST_BUDGET_SECONDS", 1.0)
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


def test_patterns_one_budget_covers_the_whole_request_and_keeps_finished_batches(
    app, db_session, make_org, monkeypatch
):
    """Two batches, the second one slow: the answer arrives inside the one budget with the
    finished batch kept and a plain line for the apps that were not reached."""
    import re

    from app.api import sa_phase_c_routes
    from app.modules.ai_chat.services import llm_service_impl as llm_impl

    import json
    import threading
    import types

    from app.services import application_pattern_classifier_service as classifier

    monkeypatch.setattr(sa_phase_c_routes, "PATTERNS_REQUEST_BUDGET_SECONDS", 2.0)
    client, _org, apps = _make_logged_in_client(app, db_session, make_org, "budgetpat", app_count=60)
    calls = []
    # An injected clock: the first model call "takes" 1.5 of the 2.0 second budget.
    now = [1000.0]
    monkeypatch.setattr(
        classifier, "time", types.SimpleNamespace(monotonic=lambda: now[0], sleep=time.sleep)
    )
    release_slow_call = threading.Event()
    slow_call_finished = threading.Event()

    def _first_fast_then_slow(prompt, *args, **kwargs):
        calls.append(kwargs.get("time_limit"))
        if len(calls) > 1:
            release_slow_call.wait(30)
            slow_call_finished.set()
            return "[]"
        now[0] += 1.5
        ids = [int(i) for i in re.findall(r"- ID (\d+):", prompt)]
        return json.dumps([{"id": i, "arch_pattern": "saas", "confidence": 0.9, "reasoning": "x"} for i in ids])

    monkeypatch.setattr(llm_impl.LLMService, "generate_from_prompt", staticmethod(_first_fast_then_slow))
    try:
        response = client.get("/api/ea-workflows/sa/application-patterns")
        # Was `elapsed < 4.0` on a wall clock. Same property, no clock: the answer came back
        # while the slow second call was still running, so the request did not wait for it,
        # and that call was given exactly what the injected clock says was left of the budget.
        assert not slow_call_finished.is_set()
    finally:
        release_slow_call.set()
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert calls[0] == pytest.approx(2.0) and calls[1] == pytest.approx(0.5), calls
    patterns = response.get_json()["patterns"]
    assert patterns["classified"] == 60
    assert patterns["by_source"]["llm"] == 50
    assert patterns["by_source"]["rules"] == 10
    assert patterns["llm_truncated"] is True
    assert "10 applications" in patterns["note"] and "rules" in patterns["note"]
    # every model call carries what is left of the one budget, never more
    assert all(limit is not None and limit <= 2.0 for limit in calls), calls


def test_patterns_a_provider_slower_than_the_old_per_call_bound_still_answers(
    app, db_session, make_org, monkeypatch
):
    import json
    import re

    from app.modules.ai_chat.services import llm_service_impl as llm_impl

    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "fivesec", app_count=2)

    def _five_seconds(prompt, *args, **kwargs):
        time.sleep(5.0)
        ids = [int(i) for i in re.findall(r"- ID (\d+):", prompt)]
        return json.dumps([{"id": i, "arch_pattern": "saas", "confidence": 0.9, "reasoning": "x"} for i in ids])

    monkeypatch.setattr(llm_impl.LLMService, "generate_from_prompt", staticmethod(_five_seconds))
    response = client.get("/api/ea-workflows/sa/application-patterns")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    assert response.get_json()["patterns"]["by_source"]["llm"] == 2


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
    assert rows["Old ledger"]["factors"] == [
        "End of life has passed",
        "Major version 3 (version 7 or lower counts as old)",
    ]

    assert rows["Bare record"]["debt_score"] is None
    assert rows["Bare record"]["factors"] == []
    assert "no end-of-life date, version, support status recorded" in rows["Bare record"]["reason"]
    assert body["summary"]["not_rated"] == 1
    assert body["summary"]["critical"] == 1


def test_debt_rows_say_which_inputs_they_were_scored_from(app, db_session, make_org):
    from datetime import date

    from app.models.application_portfolio import ApplicationComponent

    client, org, _apps = _make_logged_in_client(app, db_session, make_org, "debtpart", app_count=0)
    other = make_org("debtpart-other")
    future = date(2040, 1, 1)
    db_session.add_all(
        [
            ApplicationComponent(name="All three", organization_id=org.id, end_of_life_date=future,
                                 version="10.2", support_level="supported"),
            ApplicationComponent(name="Only date", organization_id=org.id, end_of_life_date=future),
            ApplicationComponent(name="Only version", organization_id=org.id, version="10.2"),
            ApplicationComponent(name="Only support", organization_id=org.id, support_level="basic"),
            ApplicationComponent(name="Date and version", organization_id=org.id, end_of_life_date=future,
                                 version="10.2"),
            ApplicationComponent(name="Unreadable only", organization_id=org.id, version="R12"),
            ApplicationComponent(name="Unreadable with date", organization_id=org.id,
                                 end_of_life_date=future, version="R12"),
            ApplicationComponent(name="Two point oh", organization_id=org.id, end_of_life_date=future,
                                 version="2.0", support_level="supported"),
            ApplicationComponent(name="Nothing", organization_id=org.id),
            ApplicationComponent(name="Other org only date", organization_id=other.id, end_of_life_date=future),
        ]
    )
    db_session.flush()

    response = client.get("/api/ea/phase-d/debt-scores")
    assert response.status_code == 200
    rows = {row["app_name"]: row for row in response.get_json()["scores"]}
    assert "Other org only date" not in rows

    full = rows["All three"]
    assert full["debt_score"] == 0
    assert "all in good standing" in full["detail"]
    assert full["missing"] == [] and full["unreadable"] == []

    for name, said, not_said in [
        ("Only date", "Scored from the end-of-life date only; version and support status not recorded", "all in good standing"),
        ("Only version", "Scored from the version only; end-of-life date and support status not recorded", "all in good standing"),
        ("Only support", "Scored from the support status only; end-of-life date and version not recorded", "all in good standing"),
        ("Date and version", "Scored from the end-of-life date and version only; support status not recorded", "all in good standing"),
        ("Unreadable with date", "Scored from the end-of-life date only; support status not recorded; version could not be read", "all in good standing"),
    ]:
        row = rows[name]
        assert row["debt_score"] == 0, name
        assert said in row["detail"], (name, row["detail"])
        assert not_said not in row["detail"], (name, row["detail"])

    unreadable = rows["Unreadable only"]
    assert unreadable["debt_score"] is None
    assert "version could not be read" in unreadable["reason"]
    assert "end-of-life date and support status not recorded" in unreadable["reason"]

    assert rows["Nothing"]["debt_score"] is None
    assert rows["Nothing"]["reason"] == "Not rated: no end-of-life date, version, support status recorded"

    old = rows["Two point oh"]
    assert old["debt_score"] == 60
    assert "Old major version" not in old["detail"]
    assert "Major version 2 (version 7 or lower counts as old)" in old["detail"]


def test_debt_row_for_a_scorer_failure_says_the_score_could_not_be_worked_out(
    app, db_session, make_org, monkeypatch
):
    from datetime import date

    from app.models.application_portfolio import ApplicationComponent
    from app.services.technology_roadmap_service import TechnologyRoadmapService

    client, org, _apps = _make_logged_in_client(app, db_session, make_org, "debtfail", app_count=0)
    db_session.add(ApplicationComponent(name="Fully recorded", organization_id=org.id,
                                        end_of_life_date=date(2040, 1, 1), version="10.2",
                                        support_level="supported"))
    db_session.flush()
    monkeypatch.setattr(TechnologyRoadmapService, "score_technology_debt", lambda self, app_id: {})
    row = client.get("/api/ea/phase-d/debt-scores").get_json()["scores"][0]
    assert row["debt_score"] is None
    assert "could not be worked out" in row["reason"]
    assert "recorded" not in row["reason"]


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


def _answer_every_id(prompt):
    import json
    import re

    ids = [int(i) for i in re.findall(r"- ID (\d+):", prompt)]
    return json.dumps([{"id": i, "arch_pattern": "saas", "confidence": 0.9, "reasoning": "x"} for i in ids])


def test_patterns_address_passes_the_shipped_twenty_second_budget_to_the_provider(
    app, db_session, make_org, monkeypatch
):
    from app.api import sa_phase_c_routes
    from app.modules.ai_chat.services import llm_service_impl as llm_impl

    assert sa_phase_c_routes.PATTERNS_REQUEST_BUDGET_SECONDS == 20
    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "shipbudget", app_count=2)
    calls = []

    def _record(prompt, *args, **kwargs):
        calls.append(kwargs.get("time_limit"))
        return _answer_every_id(prompt)

    monkeypatch.setattr(llm_impl.LLMService, "generate_from_prompt", staticmethod(_record))
    assert client.get("/api/ea-workflows/sa/application-patterns").status_code == 200
    assert len(calls) == 1 and 19.0 < calls[0] <= 20.0, calls


def test_a_caller_with_no_budget_makes_the_provider_call_it_always_made(
    app, db_session, make_org, monkeypatch
):
    from app.modules.ai_chat.services import llm_service_impl as llm_impl
    from app.services.application_pattern_classifier_service import ApplicationPatternClassifierService

    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "nobudget", app_count=2)
    seen = []

    def _record(prompt, *args, **kwargs):
        seen.append((args, kwargs))
        return _answer_every_id(prompt)

    monkeypatch.setattr(llm_impl.LLMService, "generate_from_prompt", staticmethod(_record))
    with client.application.test_request_context():
        from flask import g

        g.current_org_id = _org.id
        result = ApplicationPatternClassifierService().classify_portfolio()
    assert result["by_source"] == {"llm": 2}
    assert seen == [((), {"use_cache": True, "timeout": 60})], seen


def test_patterns_note_counts_a_batch_that_errored_as_rules(app, db_session, make_org, monkeypatch):
    """120 applications: the first batch errors, the second answers, the third is slow."""
    from app.api import sa_phase_c_routes
    from app.modules.ai_chat.services import llm_service_impl as llm_impl

    monkeypatch.setattr(sa_phase_c_routes, "PATTERNS_REQUEST_BUDGET_SECONDS", 2.0)
    client, _org, _apps = _make_logged_in_client(app, db_session, make_org, "errbatch", app_count=120)
    calls = []

    def _error_answer_slow(prompt, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "this is not json"
        if len(calls) == 2:
            return _answer_every_id(prompt)
        time.sleep(6.0)
        return "[]"

    monkeypatch.setattr(llm_impl.LLMService, "generate_from_prompt", staticmethod(_error_answer_slow))
    response = client.get("/api/ea-workflows/sa/application-patterns")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    patterns = response.get_json()["patterns"]
    assert patterns["by_source"]["llm"] == 50
    assert patterns["by_source"]["rules"] + patterns["by_source"].get("rule_fallback", 0) == 70
    assert "70 applications" in patterns["note"], patterns["note"]


SUPPORT_VALUES = [
    ("supported", "good"), ("  Supported ", "good"), ("BASIC", "good"),
    ("unsupported", 80), ("Not supported", 80), ("End of support", 80), ("expired", 80),
    ("limited", 60),
    ("none", "unrecognised"), ("unknown", "unrecognised"), ("TBC", "unrecognised"),
    ("   ", "blank"), ("", "blank"),
]


def test_debt_panel_calls_support_good_only_for_a_known_supported_value(app, db_session, make_org):
    from datetime import date

    from app.models.application_portfolio import ApplicationComponent

    client, org, _apps = _make_logged_in_client(app, db_session, make_org, "supval", app_count=0)
    other = make_org("supval-other")
    future = date(2040, 1, 1)
    db_session.add(ApplicationComponent(name="Other org TBC", organization_id=other.id,
                                        end_of_life_date=future, version="10.2", support_level="TBC"))
    for index, (value, _expected) in enumerate(SUPPORT_VALUES):
        db_session.add(ApplicationComponent(name="Support case %d" % index, organization_id=org.id,
                                            end_of_life_date=future, version="10.2", support_level=value))
    db_session.flush()

    rows = {r["app_name"]: r for r in client.get("/api/ea/phase-d/debt-scores").get_json()["scores"]}
    assert "Other org TBC" not in rows
    for index, (value, expected) in enumerate(SUPPORT_VALUES):
        row = rows["Support case %d" % index]
        if expected == "good":
            assert row["debt_score"] == 0 and "all in good standing" in row["detail"], (value, row)
        elif isinstance(expected, int):
            assert row["debt_score"] == expected, (value, row)
            assert "good standing" not in row["detail"], (value, row)
        else:
            assert "good standing" not in row["detail"], (value, row)
            assert "support status" not in row["scored_from"], (value, row)
            if expected == "unrecognised":
                assert "support status not recognised (recorded as “%s”)" % value in row["detail"], (value, row)
            else:
                assert "support status not recorded" in row["detail"], (value, row)
