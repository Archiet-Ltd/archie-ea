"""An agent's connector sessions are not refused, and a refusal it does get is one it can read.

A scheduled agent opens a session, sends the start message, the initialized notification, asks for
the tool list and runs a handful of tool calls. Every one of those is a POST to /mcp.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.modules.mcp.tests.test_scope_enforcement import _clear_cached_identity, _mint_token_with_scope
from app.modules.mcp.tests.test_tools import _make_element, _make_user

SESSIONS = 60
CALLS_PER_SESSION = 10


class SteppedClock:
    """Stands in for the time module inside the shared limiter: real time plus a step the test sets."""

    def __init__(self, start=0.0):
        self.offset = start

    def step(self, seconds):
        self.offset += seconds

    def time(self):
        import time

        return time.time() + self.offset

    def monotonic(self):
        import time

        return time.monotonic() + self.offset

    def __getattr__(self, name):
        import time

        return getattr(time, name)


@pytest.fixture
def limiting_on(app, monkeypatch):
    from app.services import rate_limiter

    clock = SteppedClock()
    monkeypatch.setattr(rate_limiter, "time", clock)
    was = app.config.get("RATE_LIMITING_ENABLED")
    app.config["RATE_LIMITING_ENABLED"] = True
    yield clock
    app.config["RATE_LIMITING_ENABLED"] = was


class _QuickExecutor:
    """Answers a tool call at once, so a test that sends hundreds measures the limit, not the tools."""

    def __init__(self, user_id=None):
        self.user_id = user_id

    def execute(self, call, propagate_errors=False):
        return {"success": True, "result": {"tool": call.name}}


@pytest.fixture
def quick_tools(monkeypatch):
    from app.modules.mcp import blueprint

    monkeypatch.setattr(blueprint, "ToolExecutor", _QuickExecutor)
    monkeypatch.setattr(blueprint, "_audit_tool_call", lambda *args, **kwargs: None)
    from app.services.usage_metering_service import UsageMeteringService

    monkeypatch.setattr(UsageMeteringService, "record", staticmethod(lambda **kwargs: None))


def forget_counters(app, org_ids):
    """Remove only the counters this test created, found by the organisation ids in their keys."""
    from app import db

    with app.app_context(), db.engine.begin() as connection:
        for org_id in org_ids:
            connection.execute(text("DELETE FROM rate_limit_counters WHERE key LIKE :k"),
                               {"k": "%mcp-org:" + str(org_id) + ":%"})


def post(app, token, method, params=None, request_id=1, address="10.9.9.9"):
    _clear_cached_identity()
    message = {"jsonrpc": "2.0", "method": method}
    if not method.startswith("notifications/"):
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return app.test_client().post("/mcp", data=json.dumps(message), content_type="application/json",
                                  headers=headers, environ_overrides={"REMOTE_ADDR": address})


def run_session(app, token, element_id, address="10.9.9.9"):
    """One agent session; returns the status of every message it sent."""
    sent = [
        post(app, token, "initialize", {"protocolVersion": "2025-11-25"}, address=address),
        post(app, token, "notifications/initialized", address=address),
        post(app, token, "tools/list", address=address),
    ]
    for n in range(CALLS_PER_SESSION):
        sent.append(post(app, token, "tools/call",
                         {"name": "ask_impact", "arguments": {"element_id": element_id}},
                         request_id=10 + n, address=address))
    return sent


@pytest.fixture
def org_with_token(app, db_session, make_org):
    org = make_org("mcp-budget")
    user = _make_user(db_session, org, f"budget-{org.id}@example.com")
    element = _make_element(db_session, org.id, "budget")
    token = _mint_token_with_scope(app, user, org, scope="mcp:read")
    yield org, token, element
    forget_counters(app, [org.id])


def test_an_agent_opening_a_session_every_minute_for_an_hour_is_never_refused(
    app, limiting_on, quick_tools, org_with_token
):
    org, token, element = org_with_token
    for minute in range(SESSIONS):
        limiting_on.step(60)
        sent = run_session(app, token, element.id)
        assert [r.status_code for r in sent if r.status_code == 429] == [], (
            "session %d was refused: %s" % (minute, sent[-1].get_data(as_text=True)[:200])
        )
    assert sent[0].get_json()["result"]["serverInfo"]["name"] == "entelim"


def test_a_burst_over_the_budget_is_refused_in_a_form_the_agent_can_read(
    app, limiting_on, quick_tools, org_with_token
):
    org, token, element = org_with_token
    refused = None
    for n in range(200):
        reply = post(app, token, "tools/call", {"name": "ask_impact", "arguments": {"element_id": element.id}},
                     request_id=500 + n)
        if reply.status_code == 429:
            refused = (n, reply)
            break
    assert refused is not None, "200 tool calls in one minute were never refused"
    n, reply = refused
    assert n >= 100  # an agent's ordinary session is nowhere near the budget
    body = reply.get_json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 500 + n
    assert isinstance(body["error"]["code"], int)
    assert "try again" in body["error"]["message"].lower()
    assert int(reply.headers["Retry-After"]) > 0
    # Starting a session is a separate budget: the agent can still begin, and read the tool list.
    assert post(app, token, "initialize", {"protocolVersion": "2025-11-25"}).status_code == 200
    assert post(app, token, "tools/list").status_code == 200


def test_an_unauthenticated_burst_is_still_refused_at_today_limit(app, limiting_on):
    address = "10.77.%d.%d" % (id(app) % 200, 1)
    codes = []
    for n in range(40):
        reply = post(app, None, "initialize", {"protocolVersion": "2025-11-25"}, address=address)
        codes.append(reply.status_code)
    assert codes[:30] == [401] * 30
    assert set(codes[30:]) == {429}
    body = reply.get_json()
    assert body["jsonrpc"] == "2.0" and body["error"]["message"]
    assert int(reply.headers["Retry-After"]) > 0
