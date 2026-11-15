"""The /ai-chat stream must carry a failing backend's error to the client.

Design-review P0 wave, Task 3. The observed defect: with a failing LLM
backend, POST /ai-chat/message/stream answered 200 and the UI showed
nothing — no typing indicator, no error, 15s+ of dead air. The client half
(indicator, error card, 30s inactivity timeout) lives in
app/static/js/ai_chat/{transport,app}.js; this file pins the server half:
whatever the agent's failure mode, the SSE body must contain a ``done``
event that names the error, because that event is the only thing the
client can render.

Uses the shared fixtures in tests/conftest.py (db_session rolls back).
"""

from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_user_id(db_session, make_org, client):
    from app.models.user import User

    org = make_org("ai-stream")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"ai-stream-{suffix}@example.com",
        first_name="Stream",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()

    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    # The db_session fixture holds an app context open for the whole test, and
    # test-client requests REUSE it (Flask only pushes a fresh app context when
    # none is active for this app). Anything that touched current_user during
    # setup — the tenant flush listener does — cached an anonymous user in the
    # shared `g`, and flask-login then never consults the session cookie. Same
    # trap as tests/test_ba_tenant_and_authz.py::_login; clear the caches.
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    return user.id


@pytest.fixture
def ai_enabled(monkeypatch):
    """Pretend an LLM provider is configured so the route runs the agent."""
    from app.services.feature_flag_service import FeatureFlagService

    monkeypatch.setattr(
        FeatureFlagService, "is_ai_enabled", staticmethod(lambda *_a, **_k: True)
    )


def _sse_events(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw == "[DONE]":
            continue
        events.append(json.loads(raw))
    return events


def _post_stream(client, monkeypatch, runner_cls):
    import app.modules.ai_chat.services.agent_runner as agent_runner_module

    monkeypatch.setattr(agent_runner_module, "AgentRunner", runner_cls)
    response = client.post(
        "/ai-chat/message/stream",
        json={"message": "hello", "domain": "general"},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    return _sse_events(body)


def test_stream_carries_error_when_agent_raises(
    client, logged_in_user_id, ai_enabled, monkeypatch
):
    """An agent crash must surface as done-with-error, not an empty stream."""

    class ExplodingRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise RuntimeError("provider connection refused")

    events = _post_stream(client, monkeypatch, ExplodingRunner)
    done = [e for e in events if e.get("type") == "done"]
    assert done, f"no done event in stream: {events}"
    # The done event must NAME a failure — but not by echoing the raw upstream
    # string. That string is the provider's error body, which for OpenRouter's
    # 402 carries the provider account's user_id; it was reaching every chat
    # user's browser. Since 31 Aug 2026 the client gets a category (see
    # agent_runner.sanitize_agent_error) and the full reason stays in the log.
    error = done[0].get("error") or ""
    assert error, f"no error on the done event: {done[0]}"
    assert "provider connection refused" not in error


def test_stream_carries_error_and_message_from_agent_fallback(
    client, logged_in_user_id, ai_enabled, monkeypatch
):
    """The agent's own failure dict (persisted assistant message + error
    reason) must both ride the done event — the client renders the message
    as an error card and must not have to guess it."""

    persisted = (
        "The AI request couldn't be completed. See the error detail below "
        "or check Admin → AI settings."
    )

    class FallbackRunner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return {
                "response": persisted,
                "actions_taken": [],
                "pending_approvals": [],
                "error": "401 invalid api key",
            }

    events = _post_stream(client, monkeypatch, FallbackRunner)
    done = [e for e in events if e.get("type") == "done"]
    assert done, f"no done event in stream: {events}"
    # Sanitised, not echoed — see the note in the test above.
    error = done[0].get("error") or ""
    assert "credentials" in error
    assert "401" in error
    assert "invalid api key" not in error
    assert done[0].get("response") == persisted
