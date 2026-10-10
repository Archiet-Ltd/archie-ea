"""A slow answer can be followed through the logs by one id.

Covers the request path (id in, id out, one finished line), a deliberately slow
path whose slow query and model call are both logged under the request's id
and organisation, a scheduled job visiting two organisations (each line carries
only the organisation it ran for), and the assistant run decorator inheriting
the request's id.
"""

from __future__ import annotations

import logging
import re
import shlex
import time
import uuid

import pytest
from flask import g
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")


def _trace_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.trace"]


def _fields(line):
    return dict(part.split("=", 1) for part in shlex.split(line) if "=" in part)


def test_request_id_is_returned_and_logged(client, caplog):
    caplog.set_level(logging.INFO, logger="app.trace")
    supplied = "probe-" + uuid.uuid4().hex[:12]

    response = client.get("/health", headers={"X-Request-ID": supplied})

    assert response.headers.get("X-Request-ID") == supplied
    finished = [_fields(line) for line in _trace_lines(caplog) if "trace=finished" in line]
    assert any(
        f["request_id"] == supplied and f["kind"] == "request" and f.get("status") == "200"
        for f in finished
    ), finished


def test_unsafe_inbound_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id <script>"})
    returned = response.headers.get("X-Request-ID")
    assert returned and re.fullmatch(r"[0-9a-f]{32}", returned), returned


def test_every_response_gets_an_id(client):
    first = client.get("/health").headers.get("X-Request-ID")
    second = client.get("/health").headers.get("X-Request-ID")
    assert first and second and first != second


def test_slow_request_is_followed_to_its_queries_and_model_call(
    app, db_session, make_org, caplog, monkeypatch
):
    from app.extensions import db
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.services import llm_cost_tracker
    from app.utils import tracing

    org = make_org("trace")
    db_session.commit()
    monkeypatch.setitem(app.config, "TRACE_SLOW_QUERY_MS", 20)

    def _slow_provider(**kwargs):
        time.sleep(0.03)
        return "stub answer", None

    monkeypatch.setattr(LLMService, "_call_llm_with_failover", staticmethod(_slow_provider))
    monkeypatch.setattr(
        llm_cost_tracker.LLMCostTracker,
        "check_budget_before_call",
        lambda self, **kwargs: (True, None),
    )
    caplog.set_level(logging.INFO)
    supplied = "slowpath-" + uuid.uuid4().hex[:12]

    with app.test_request_context("/health", headers={"X-Request-ID": supplied}):
        g.current_org_id = org.id
        tracing._before_request()
        db.session.execute(text("SELECT pg_sleep(0.05)"))
        db.session.execute(text("SELECT 1"))
        answer, _ = LLMService._call_llm(prompt="hello", model="trace-model", provider="openai")
        logging.getLogger("app.somewhere").info("ordinary line inside the request")
        response = tracing._after_request(app.response_class("ok"))
        tracing._teardown_request(None)

    assert answer == "stub answer"
    assert response.headers["X-Request-ID"] == supplied

    lines = [_fields(line) for line in _trace_lines(caplog)]
    mine = [f for f in lines if f.get("request_id") == supplied]
    slow = [f for f in mine if f["trace"] == "slow-query"]
    calls = [f for f in mine if f["trace"] == "model-call"]
    done = [f for f in mine if f["trace"] == "finished"]

    assert slow and "pg_sleep" in " ".join(f["sql"] for f in slow), lines
    assert all(f["org"] == str(org.id) for f in slow)
    assert len(calls) == 1 and calls[0]["provider"] == "openai" and calls[0]["model"] == "trace-model"
    assert calls[0]["outcome"] == "ok" and float(calls[0]["duration_ms"]) >= 30
    assert len(done) == 1
    assert int(done[0]["queries"]) >= 2 and done[0]["model_calls"] == "1"
    assert float(done[0]["duration_ms"]) >= 80

    ordinary = [r for r in caplog.records if r.name == "app.somewhere"]
    assert ordinary and ordinary[0].request_id == supplied
    assert ordinary[0].organization_id == org.id
    # The slow SQL is identified, its parameters and the prompt are not logged.
    assert not any("hello" in line for line in _trace_lines(caplog))


def test_job_trace_lines_carry_only_the_organisation_they_ran_for(
    app, db_session, make_org, caplog
):
    from app.jobs.tenant_safe_job import run_for_each_tenant

    org_a, org_b = make_org("ja"), make_org("jb")
    db_session.commit()
    caplog.set_level(logging.INFO)
    seen = {}

    def _work(organization_id):
        from app.extensions import db

        db.session.execute(text("SELECT 1"))
        logging.getLogger("app.job-body").info("working")
        seen[organization_id] = [
            r for r in caplog.records if r.name == "app.job-body"
        ][-1].organization_id

    run = run_for_each_tenant(
        app, "trace-probe", _work, organization_ids=[org_a.id, org_b.id], use_lock=False
    )
    assert run.failed == 0
    assert seen == {org_a.id: org_a.id, org_b.id: org_b.id}

    # Other jobs running in the same process can log trace lines too, so keep
    # only the job this test ran.
    finished = [
        f
        for f in (
            _fields(line)
            for line in _trace_lines(caplog)
            if "trace=finished" in line and "kind=job" in line
        )
        if f.get("name") == "trace-probe"
    ]
    by_org = {f["org"]: f for f in finished}
    assert set(by_org) == {str(org_a.id), str(org_b.id)}, finished
    assert by_org[str(org_a.id)]["request_id"] != by_org[str(org_b.id)]["request_id"]
    assert all(int(f["queries"]) >= 1 for f in finished)


def test_assistant_run_keeps_the_request_id(app, caplog):
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.utils import tracing

    caplog.set_level(logging.INFO, logger="app.trace")
    assert hasattr(AgentRunner.run, "__wrapped__"), "the assistant run is not traced"

    @tracing.traced("agent-run", "assistant")
    def _run():
        return tracing.current_trace_id()

    with tracing.trace_scope("request", "GET /ai-chat", trace_id="req-" + "a" * 12):
        inner = _run()
    assert inner == "req-" + "a" * 12

    outside = _run()
    assert outside and outside != inner
    kinds = [_fields(line)["kind"] for line in _trace_lines(caplog) if "trace=finished" in line]
    assert kinds.count("agent-run") == 2
