"""A chat turn with no LLM provider must reach the user as an ADMIN state.

Archie is a system of record. With no provider configured the only honest
outcomes are "here is what an administrator must do" or a real error — never a
fabricated answer, and never a bare machine code the user cannot act on.

What the server already does right, and what these tests pin
------------------------------------------------------------
`FeatureFlagService.require_ai_for_route` (app/services/feature_flag_service.py)
short-circuits the chat endpoints with

    503 {"error": "service_unavailable",
         "feature": "chat",
         "message": "AI feature 'chat' is not available. LLM provider must be
                     configured."}

That is correct and actionable, and it fires *before* the model allow-list
check in `send_message`, so the no-provider condition is never misreported as
`invalid_model`. An earlier draft of this module asserted the opposite — that
the turn returned 200 with the AgentRunner fallback text. That expectation was
simply wrong about this codebase: the gate runs first. The test was corrected
to the real contract rather than the product being changed to match it.

The defect was one layer up, in the client
------------------------------------------
`app/static/js/ai_chat/app.js` read `data.error` (the machine code
`"service_unavailable"`) and never `data.message`, so the user was shown
"Request Error (HTTP 503): service_unavailable" with a Retry button that could
never succeed and no route to the remedy. The streaming path was worse: it
threw `Error('stream ' + status)`, discarding the body entirely.

The `message` field is therefore load-bearing UI contract, not decoration —
these tests exist so it cannot be dropped or renamed without a failure here.

The session conftest deliberately blanks every `*_API_KEY` env var, so these
tests run in the genuine no-provider condition without patching anything.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.modules.ai_chat.services.llm_service_impl import LLMService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def chat_user(db_session, make_org):
    """A logged-in-able user in a fresh org, with no enabled provider row."""
    from app.models.models import APISettings
    from app.models.user import User

    org = make_org("aichat")

    # Assert the precondition rather than assume it: the point of these tests
    # is the no-provider condition, and a stray enabled row would silently
    # turn them green for the wrong reason.
    assert APISettings.query.filter_by(enabled=True).count() == 0, (
        "these tests require the no-provider condition; an enabled "
        "APISettings row is present"
    )

    user = User(
        first_name="Chat",
        last_name="Tester",
        email=f"chat-{uuid.uuid4().hex[:8]}@example.test",
        organization_id=org.id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"message": "What applications do we own?", "domain": "general"},
            id="no-model-named",
        ),
        pytest.param(
            {
                "message": "What applications do we own?",
                "domain": "general",
                "model": "gpt-4o",
            },
            id="model-named",
        ),
    ],
)
def test_no_provider_turn_is_an_actionable_administrative_state(
    client, login_as, chat_user, payload
):
    """503 naming the remedy — and never a misdiagnosis of the model."""
    login_as(client, chat_user)
    resp = client.post("/ai-chat/message", json=payload)

    body = resp.get_json()
    assert resp.status_code == 503, resp.get_data(as_text=True)
    assert body["error"] == "service_unavailable"

    # The human-readable remedy. This is what the chat client renders; if it
    # is dropped or renamed, the user is left with a bare machine code.
    assert "message" in body, (
        "the client renders body['message']; without it the user sees only "
        f"the machine code. got: {body!r}"
    )
    assert "provider must be configured" in body["message"].lower(), (
        f"the 503 must name the remedy, got: {body['message']!r}"
    )

    # A missing provider is an administrative condition, not bad user input.
    # Blaming the model would send an administrator to the model picker
    # instead of to Admin > API Settings.
    assert body["error"] != "invalid_model"

    # And nothing may claim the system of record changed.
    assert not body.get("actions_taken")
    assert not body.get("response")


def test_no_provider_streaming_turn_is_also_an_administrative_state(
    client, login_as, chat_user
):
    """The streaming endpoint is the primary path — it must gate identically.

    If only the non-streaming endpoint gated, the UI's preferred path would
    open a stream that produced no tokens, and the user would watch an empty
    bubble instead of being told a provider is missing.
    """
    login_as(client, chat_user)
    resp = client.post(
        "/ai-chat/message/stream",
        json={"message": "What applications do we own?", "domain": "general"},
    )

    assert resp.status_code == 503, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["error"] == "service_unavailable"
    assert "provider must be configured" in body["message"].lower()


def test_client_only_classifies_an_explicit_chat_provider_configuration_fault():
    """A generic 503 is transient; status alone must never prescribe admin setup."""
    transport = (ROOT / "app/static/js/ai_chat/transport.js").read_text(encoding="utf-8")
    app = (ROOT / "app/static/js/ai_chat/app.js").read_text(encoding="utf-8")

    assert "function isProviderConfigurationFault(detail)" in transport
    assert "detail.error === 'service_unavailable'" in transport
    assert "detail.feature === 'chat'" in transport
    assert "_err.isConfigFault = isProviderConfigurationFault(_detail);" in transport
    assert "resp.status === 503 ||" not in transport

    assert "ArchieChat.transport.isProviderConfigurationFault(data)" in app
    assert "data.error === 'service_unavailable' || response.status === 503" not in app


def test_model_catalogue_includes_the_environment_provider_for_a_tenant_without_rows(
    app, client, login_as, chat_user, monkeypatch
):
    """The picker, the endpoint and the resolver must name the same provider.

    Commit 1d2bd1e81 (``app/modules/ai_chat/services/llm_service_impl.py``) made the platform's
    own keys (keys in the environment) usable only for a provider named in ``LLM_PROVIDER_ORDER``
    and within a spend ceiling (``LLM_SPEND_CEILING_USD_<PROVIDER>``); a bare key with no order
    and no ceiling now resolves to nothing, which a previous draft of this test did not assert
    under. OpenAI stands in as the environment-configured provider, placed in the order with a
    ceiling, as the rule requires: DeepSeek left the provider set on 24 Oct 2026, so a DeepSeek
    key configures nothing and an even earlier draft asserted a provider that no longer exists.
    """
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_OPENAI", "5.00")
    login_as(client, chat_user)

    response = client.get("/ai-chat/models")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["current_provider"] == "openai"
    assert body["current_model"] == "gpt-4o"
    assert [(m["provider"], m["model"]) for m in body["models"]] == [("openai", "gpt-4o")]

    with app.app_context():
        resolved_provider, resolved_model = LLMService._get_configured_provider()
    assert (resolved_provider, resolved_model) == (body["current_provider"], body["current_model"])


def test_model_catalogue_names_no_provider_for_a_platform_key_outside_the_order(
    app, client, login_as, chat_user, monkeypatch
):
    """Under the same rule, a platform key for a provider the order never calls configures
    nothing on the picker, the endpoint and the resolver alike -- never a stale "configured"
    state on one surface while another has moved on. See the docstring above for the commit
    and the environment names the rule reads.
    """
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    login_as(client, chat_user)

    response = client.get("/ai-chat/models")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["current_provider"] is None
    assert body["current_model"] is None
    assert body["models"] == []

    with app.app_context():
        with pytest.raises(ValueError):
            LLMService._get_configured_provider()


def test_chat_page_does_not_claim_an_environment_provider_is_unavailable(
    client, login_as, chat_user, monkeypatch
):
    """The first painted health state must use the runtime provider resolver."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    login_as(client, chat_user)

    response = client.get("/ai-chat/")

    assert response.status_code == 200
    assert "AI provider unavailable" not in response.get_data(as_text=True)


def test_config_fault_shows_at_most_one_toast_and_prefers_inline():
    """L3: a missing-provider fault used to appear on three surfaces at once
    -- the persistent page-level banner (ai_chat/index.html), a toast, and
    the inline chat message -- for one condition. The banner already covers
    the whole-session case; a toast on top of the inline message (which
    carries the admin-link remedy in context) was a redundant third surface.
    Both call sites in app.js that branch on isConfigFault must render the
    inline message WITHOUT also firing a toast for that branch.
    """
    js = (ROOT / "app/static/js/ai_chat/app.js").read_text(encoding="utf-8")

    # Streaming path: the isConfigFault branch renders appendError only.
    streaming_branch = js.split("if (_streamErr && _streamErr.isConfigFault) {")[1].split(
        "return;"
    )[0]
    assert "Platform.toast.error" not in streaming_branch
    assert "appendError(_streamErr.message, null, { adminLink: true })" in streaming_branch

    # Non-streaming fallback path: same rule inside the `if (_isConfigFault)` branch.
    fallback_branch = js.split("if (_isConfigFault) {")[1].split("} else {")[0]
    assert "Platform.toast.error" not in fallback_branch
    assert "appendError(errorMessage || fullError, null, { adminLink: true })" in fallback_branch

    # The non-config (transient/retryable) branch is unaffected -- it still
    # gets a toast, since no persistent banner covers a one-off failure.
    else_branch = js.split("} else {")[1].split("}\n", 1)[0]
    assert "Platform.toast.error(fullError);" in else_branch
