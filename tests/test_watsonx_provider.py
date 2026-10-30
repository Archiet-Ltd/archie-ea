"""IBM watsonx.ai as an AI provider: the token, the chat call, the Granite list, who may use it.

All HTTP is mocked; no key or address in this file is real.
"""

from __future__ import annotations

import time

import pytest

from app.models.models import APISettings
from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
from app.modules.ai_chat.services import agent_runner as ar
from app.modules.ai_chat.services import watsonx_client as wx

URL = "https://watsonx.example.test"
KEY_A = "ibm-key-org-a-0001"
KEY_B = "ibm-key-org-b-0002"


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def clean_tokens(monkeypatch):
    wx.forget_tokens()
    for name in ("WATSONX_API_KEY", "WATSONX_PROJECT_ID", "WATSONX_URL", "WATSONX_IAM_URL", "LLM_PROVIDER_ORDER", "AI_PROVIDER_ORDER"):
        monkeypatch.delenv(name, raising=False)
    yield
    wx.forget_tokens()


@pytest.fixture
def http(monkeypatch):
    """Records every POST and GET; replies come from a queue per URL kind."""
    calls = {"iam": [], "chat": [], "specs": []}
    replies = {"iam": [], "chat": [], "specs": []}

    def pop(kind, default):
        queue = replies[kind]
        return queue.pop(0) if queue else default

    def post(url, **kwargs):
        if "identity/token" in url:
            calls["iam"].append((url, kwargs))
            return pop("iam", FakeResponse(200, {"access_token": "tok-%d" % len(calls["iam"]), "expiration": time.time() + 3600}))
        calls["chat"].append((url, kwargs))
        return pop("chat", FakeResponse(200, {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}))

    def get(url, **kwargs):
        calls["specs"].append((url, kwargs))
        return pop("specs", FakeResponse(200, {"resources": []}))

    monkeypatch.setattr(wx.requests, "post", post)
    monkeypatch.setattr(wx.requests, "get", get)
    http_calls = type("Http", (), {"calls": calls, "replies": replies})
    return http_calls


def chat(key=KEY_A, **kw):
    return wx.chat(key, URL, "proj-1", "ibm/granite-3-8b-instruct", [{"role": "user", "content": "hi"}], **kw)


# --- acceptance 1: token, request shape, model list, refresh, plain failures -------------------------


def test_token_is_fetched_from_iam_with_the_api_key_and_reused(http):
    chat()
    chat()
    assert len(http.calls["iam"]) == 1
    url, kwargs = http.calls["iam"][0]
    assert url == "https://iam.cloud.ibm.com/identity/token"
    assert kwargs["data"] == {"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": KEY_A}
    assert [c[1]["headers"]["Authorization"] for c in http.calls["chat"]] == ["Bearer tok-1", "Bearer tok-1"]


def test_chat_request_carries_project_model_and_messages(http):
    messages = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    wx.chat(KEY_A, URL + "/", "proj-1", "ibm/granite-3-8b-instruct", messages, tools=[{"type": "function", "function": {"name": "t"}}])
    url, kwargs = http.calls["chat"][0]
    assert url.startswith(URL + "/ml/v1/text/chat?version=")
    body = kwargs["json"]
    assert body["project_id"] == "proj-1" and body["model_id"] == "ibm/granite-3-8b-instruct"
    assert body["messages"] == messages
    assert body["tools"][0]["function"]["name"] == "t"


def test_an_expired_token_is_refreshed_once(http):
    http.replies["iam"] += [
        FakeResponse(200, {"access_token": "old", "expiration": time.time() + 10}),  # inside the safety margin
        FakeResponse(200, {"access_token": "new", "expiration": time.time() + 3600}),
    ]
    chat()
    chat()
    chat()
    assert len(http.calls["iam"]) == 2
    assert [c[1]["headers"]["Authorization"] for c in http.calls["chat"]] == ["Bearer old", "Bearer new", "Bearer new"]


def test_a_401_on_chat_renews_the_token_once_then_succeeds(http):
    http.replies["chat"] += [FakeResponse(401), FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})]
    assert wx.normalise(chat())["text"] == "ok"
    assert len(http.calls["iam"]) == 2


def test_a_401_that_persists_gives_a_plain_message_naming_watsonx(http):
    http.replies["chat"] += [FakeResponse(401), FakeResponse(401)]
    with pytest.raises(wx.WatsonxError) as caught:
        chat()
    assert "IBM watsonx.ai" in str(caught.value) and "rejected" in str(caught.value)
    assert KEY_A not in str(caught.value) and "tok-" not in str(caught.value)
    assert len(http.calls["iam"]) == 2  # renewed once, not in a loop


def test_a_rejected_api_key_at_sign_in_names_watsonx(http):
    http.replies["iam"].append(FakeResponse(400, {"errorMessage": "apikey " + KEY_A}))
    with pytest.raises(wx.WatsonxError) as caught:
        chat()
    assert "IBM watsonx.ai rejected the API key" in str(caught.value) and KEY_A not in str(caught.value)


def test_a_server_error_gives_a_plain_message_naming_watsonx(http):
    http.replies["chat"].append(FakeResponse(503, {"detail": "upstream secret detail"}))
    with pytest.raises(wx.WatsonxError) as caught:
        chat()
    assert str(caught.value).startswith("IBM watsonx.ai is not responding (HTTP 503)")
    assert "secret detail" not in str(caught.value)


def test_model_list_keeps_only_granite_instruct_models(http):
    http.replies["specs"].append(FakeResponse(200, {"resources": [
        {"model_id": "ibm/granite-3-8b-instruct", "label": "granite-3-8b-instruct"},
        {"model_id": "ibm/granite-13b-chat-v2", "label": "chat"},
        {"model_id": "ibm/granite-embedding-107m-multilingual"},
        {"model_id": "meta-llama/llama-3-3-70b-instruct"},
        {"model_id": "ibm/granite-3-3-8b-instruct"},
    ]}))
    models = wx.list_granite_models(URL)
    assert [m["id"] for m in models] == ["ibm/granite-3-3-8b-instruct", "ibm/granite-3-8b-instruct"]
    assert http.calls["specs"][0][0] == URL + "/ml/v1/foundation_model_specs"


def test_tool_calls_come_back_in_the_agent_loop_shape():
    body = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "find_apps", "arguments": '{"q": "crm"}'}}]}}]}
    result = wx.normalise(body)
    assert result["tool_calls"] == [{"id": "c1", "name": "find_apps", "arguments": {"q": "crm"}}]
    assert result["text"] is None


def test_a_watsonx_failure_is_not_retried_on_another_provider():
    assert ar.is_retryable_llm_error(wx.WatsonxError("IBM watsonx.ai is not responding (HTTP 503).", 503)) is False
    assert ar.sanitize_agent_error("LLM call failed: IBM watsonx.ai is not responding (HTTP 503). Try again shortly.") == (
        "IBM watsonx.ai is not responding (HTTP 503). Try again shortly."
    )


# --- acceptance 2 and 4: who may use it ---------------------------------------------------------------


def _org_with(make_org, tenant_ctx, db_session, label, key=None, plan=SubscriptionPlan.free):
    org = make_org(label)
    db_session.add(Subscription(organization_id=org.id, plan=plan, status=SubscriptionStatus.active))
    if key:
        with tenant_ctx(org.id):
            db_session.add(APISettings(provider="watsonx", api_key=key, enabled=True, default_model="ibm/granite-3-8b-instruct",
                                       custom_endpoint_url=URL, watsonx_project_id="proj-" + label))
            db_session.flush()
    db_session.flush()
    return org


def _providers(tenant_ctx, org):
    with tenant_ctx(org.id):
        return [c[0] for c in ar.build_provider_chain("watsonx", "ibm/granite-3-8b-instruct")]


@pytest.fixture
def platform_key(monkeypatch):
    monkeypatch.setenv("WATSONX_API_KEY", "ibm-platform-key-0003")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "platform-project")
    monkeypatch.setenv("WATSONX_URL", URL)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "watsonx")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_WATSONX", "50")


def test_a_free_plan_organisation_never_routes_to_watsonx(db_session, make_org, tenant_ctx, platform_key):
    free = _org_with(make_org, tenant_ctx, db_session, "free")
    assert "watsonx" not in _providers(tenant_ctx, free)


def test_a_paid_plan_organisation_routes_to_watsonx_on_the_platform_key(db_session, make_org, tenant_ctx, platform_key):
    paid = _org_with(make_org, tenant_ctx, db_session, "paid", plan=SubscriptionPlan.team)
    assert _providers(tenant_ctx, paid) == ["watsonx"]


def test_an_organisation_with_its_own_key_routes_to_watsonx_even_on_the_free_plan(db_session, make_org, tenant_ctx):
    own = _org_with(make_org, tenant_ctx, db_session, "own", key=KEY_A)
    assert _providers(tenant_ctx, own) == ["watsonx"]


def test_watsonx_is_a_paid_provider_never_in_the_free_list():
    assert "watsonx" not in ar._FREE_PROVIDERS and "watsonx" in ar._TOOL_PROVIDERS


def test_a_free_plan_call_is_refused_before_any_request_is_made(db_session, make_org, tenant_ctx, platform_key, http):
    free = _org_with(make_org, tenant_ctx, db_session, "freecall")
    with tenant_ctx(free.id), pytest.raises(wx.WatsonxError) as caught:
        wx.complete("ibm-platform-key-0003", "ibm/granite-3-8b-instruct", [{"role": "user", "content": "hi"}])
    assert "IBM watsonx.ai" in str(caught.value) and "paid plan" in str(caught.value)
    assert http.calls["iam"] == [] and http.calls["chat"] == []


def test_one_organisations_watsonx_key_is_never_used_for_another(db_session, make_org, tenant_ctx, http):
    org_a = _org_with(make_org, tenant_ctx, db_session, "a", key=KEY_A)
    org_b = _org_with(make_org, tenant_ctx, db_session, "b")  # free, no key of its own
    with tenant_ctx(org_a.id):
        wx.complete(KEY_A, "ibm/granite-3-8b-instruct", [{"role": "user", "content": "hi"}])
    assert http.calls["iam"][0][1]["data"]["apikey"] == KEY_A
    assert http.calls["chat"][0][1]["json"]["project_id"] == "proj-a"
    before = (len(http.calls["iam"]), len(http.calls["chat"]))
    with tenant_ctx(org_b.id):
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        assert LLMService._get_all_api_keys("watsonx") == []
        assert "watsonx" not in _providers(tenant_ctx, org_b)
        with pytest.raises(wx.WatsonxError):
            wx.complete(KEY_A, "ibm/granite-3-8b-instruct", [{"role": "user", "content": "hi"}])
    assert (len(http.calls["iam"]), len(http.calls["chat"])) == before


def test_each_organisation_uses_its_own_key_project_and_token(db_session, make_org, tenant_ctx, http):
    org_a = _org_with(make_org, tenant_ctx, db_session, "a", key=KEY_A)
    org_b = _org_with(make_org, tenant_ctx, db_session, "b", key=KEY_B)
    for org, key in ((org_a, KEY_A), (org_b, KEY_B)):
        with tenant_ctx(org.id):
            wx.complete(key, "ibm/granite-3-8b-instruct", [{"role": "user", "content": "hi"}])
    assert [c[1]["data"]["apikey"] for c in http.calls["iam"]] == [KEY_A, KEY_B]
    assert [c[1]["json"]["project_id"] for c in http.calls["chat"]] == ["proj-a", "proj-b"]
    assert http.calls["chat"][0][1]["headers"]["Authorization"] != http.calls["chat"][1][1]["headers"]["Authorization"]


def test_the_agent_loop_call_returns_text_and_tool_calls(db_session, make_org, tenant_ctx, http):
    own = _org_with(make_org, tenant_ctx, db_session, "loop", key=KEY_A)
    http.replies["chat"].append(FakeResponse(200, {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "find_apps", "arguments": "{}"}}]}}]}))
    runner = ar.AgentRunner(user_id=1)
    with tenant_ctx(own.id):
        result = runner._call_watsonx("ibm/granite-3-8b-instruct", KEY_A, "system text", [{"role": "user", "content": "hi"}], [])
    assert result["tool_calls"][0]["name"] == "find_apps"
    sent = http.calls["chat"][0][1]["json"]["messages"]
    assert sent[0] == {"role": "system", "content": "system text"}
