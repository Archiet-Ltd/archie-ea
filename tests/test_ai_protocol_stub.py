"""Test-only protocol fixture exercised through the real OpenAI HTTP client."""
from concurrent.futures import ThreadPoolExecutor
import os
import socket
import subprocess
import sys

import httpx
from openai import AuthenticationError, BadRequestError, OpenAI
import pytest

from tests.smoke.ai_protocol_stub import AIProtocolStub, CHAT, GUIDE, MODEL, TOKEN


def test_sdk_nonstreaming_guide_response_and_usage_cross_loopback():
    with AIProtocolStub() as stub:
        with OpenAI(api_key=TOKEN, base_url=stub.base_url, max_retries=0,
                    http_client=httpx.Client(trust_env=False)) as client:
            response = client.chat.completions.create(
                model=MODEL, messages=[{'role': 'user', 'content': GUIDE['prompt']}])
        assert response.choices[0].message.content == GUIDE['reply']
        assert response.usage.prompt_tokens == 12
        assert response.usage.completion_tokens == 8
        assert stub.records == [{'scenario': 'guide', 'method': 'POST',
                                 'path': '/v1/chat/completions', 'model': MODEL,
                                 'stream': False, 'status': 200}]


def test_sdk_streaming_chat_chunks_are_real_sse_and_finish():
    with AIProtocolStub() as stub:
        with OpenAI(api_key=TOKEN, base_url=stub.base_url, max_retries=0,
                    http_client=httpx.Client(trust_env=False)) as client:
            with client.chat.completions.create(model=MODEL, stream=True,
                    messages=[{'role': 'user', 'content': CHAT['prompt']}]) as stream:
                chunks = list(stream)
        assert ''.join(chunk.choices[0].delta.content or '' for chunk in chunks) == CHAT['reply']
        assert len([chunk for chunk in chunks if chunk.choices[0].delta.content]) >= 2
        assert chunks[-1].choices[0].finish_reason == 'stop'
        assert stub.records[0]['scenario'] == 'chat'
        assert stub.records[0]['stream'] is True


@pytest.mark.parametrize('message,model,stream', [
    ('Unknown prompt must not get an invented answer', 'ci-protocol-stub-v1', False),
    ('ARCHIE_CI_GUIDE_PROTOCOL_V1: Explain this fixture page.', 'unexpected-model', False),
    ('ARCHIE_CI_CHAT_PROTOCOL_V1: Confirm this transport fixture.', 'ci-protocol-stub-v1', False),
])
def test_unrecognized_request_fails_at_actual_sdk_boundary(message, model, stream):
    with AIProtocolStub() as stub:
        with OpenAI(api_key=TOKEN, base_url=stub.base_url, max_retries=0,
                    http_client=httpx.Client(trust_env=False)) as client:
            with pytest.raises(BadRequestError):
                client.chat.completions.create(model=model, stream=stream,
                    messages=[{'role': 'user', 'content': message}])
        assert stub.records[0]['status'] == 400


def test_child_environment_isolated_without_changing_parent():
    source = {'OPENAI_API_KEY': 'not-a-real-source-key', 'ANTHROPIC_API_KEY': 'not-real',
              'OPENAI_API_KEY_2': 'not-real', 'OPENROUTER_API_KEY_SECONDARY': 'not-real',
              'AZURE_OPENAI_API_KEY': 'not-real', 'LLM_API_KEY': 'not-real',
              'NO_PROXY': 'existing.internal'}
    before = dict(source)
    with AIProtocolStub() as stub:
        child = stub.child_environment(source)
        assert child['OPENAI_API_KEY'] == TOKEN
        assert child['OPENAI_BASE_URL'] == stub.base_url
    assert source == before
    for name in source:
        if 'API_KEY' in name and name != 'OPENAI_API_KEY':
            assert child[name] == ''
    assert child['PYTHON_DOTENV_DISABLED'] == '1'
    assert child['AI_PAGE_GUIDE_ENABLED'] == 'true'
    assert '127.0.0.1' in child['NO_PROXY']
    assert os.environ['OPENAI_API_KEY'] == ''


def test_real_sdk_child_uses_only_injected_loopback_environment():
    code = (
        'import sys; from openai import OpenAI; '
        'client = OpenAI(max_retries=0, timeout=5); '
        # Check before any network I/O, so a regression cannot contact a provider.
        'assert str(client.base_url) == sys.argv[1] + "/"; '
        'result = client.chat.completions.create(model=sys.argv[2], '
        'messages=[{"role":"user","content":sys.argv[3]}]); '
        'print(result.choices[0].message.content); client.close()'
    )
    with AIProtocolStub() as stub:
        result = subprocess.run([sys.executable, '-c', code, stub.base_url, MODEL, GUIDE['prompt']],
                                env=stub.child_environment(os.environ), capture_output=True,
                                text=True, timeout=30, check=True)
        assert result.stdout.strip() == GUIDE['reply']
        assert stub.records[0]['status'] == 200


def test_wrong_auth_and_unknown_routes_are_not_healthy_responses():
    with AIProtocolStub() as stub:
        with OpenAI(api_key='wrong-test-token', base_url=stub.base_url, max_retries=0,
                    http_client=httpx.Client(trust_env=False)) as client:
            with pytest.raises(AuthenticationError):
                client.chat.completions.create(model=MODEL,
                    messages=[{'role': 'user', 'content': GUIDE['prompt']}])
        with httpx.Client(trust_env=False) as client:
            response = client.get(stub.base_url + '/models')
            assert response.status_code == 404
        assert [record['status'] for record in stub.records] == [401, 404]
        assert all('prompt' not in record and 'authorization' not in record for record in stub.records)


def test_concurrent_requests_have_independent_ledger_and_socket_closes():
    def request(base):
        with OpenAI(api_key=TOKEN, base_url=base, max_retries=0,
                    http_client=httpx.Client(trust_env=False)) as client:
            return client.chat.completions.create(model=MODEL,
                messages=[{'role': 'user', 'content': GUIDE['prompt']}]).choices[0].message.content

    with AIProtocolStub() as stub:
        port = stub.port
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(request, [stub.base_url] * 4)) == [GUIDE['reply']] * 4
        assert len(stub.records) == 4
        assert all(record['status'] == 200 for record in stub.records)
        snapshot = stub.records
        snapshot.clear()
        assert len(stub.records) == 4
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(('127.0.0.1', port)) != 0


def _openai_environment(monkeypatch, platform_key, order):
    monkeypatch.setenv('OPENAI_API_KEY', platform_key)
    for number in range(1, 10):
        monkeypatch.delenv('OPENAI_API_KEY_%d' % number, raising=False)
    monkeypatch.delenv('OPENAI_API_KEY_SECONDARY', raising=False)
    monkeypatch.delenv('AI_PROVIDER_ORDER', raising=False)
    monkeypatch.setenv('LLM_PROVIDER_ORDER', order)
    monkeypatch.setenv('LLM_SPEND_CEILING_USD_OPENAI', '50')


def test_server_environment_leaves_the_stub_token_as_the_organisations_own_key(app, monkeypatch):
    """The token is a customer's key, not the platform's, so the platform-paid stop does not apply to it."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    with AIProtocolStub() as stub:
        child = stub.child_environment({}, organisation_key_only=True)
    assert child['OPENAI_API_KEY'] == ''
    assert child['OPENAI_BASE_URL'] == stub.base_url

    # Outside the provider order: the organisation's own key is still its own money and answers.
    _openai_environment(monkeypatch, child['OPENAI_API_KEY'], 'anthropic')
    with app.app_context():
        assert LLMService.platform_pays('openai', TOKEN) is False
        assert AgentRunner._platform_funded('openai', TOKEN) is False

    # The same token as the platform's own key is platform-paid, and the stop still refuses it.
    _openai_environment(monkeypatch, TOKEN, 'anthropic')
    with app.app_context():
        assert AgentRunner._platform_funded('openai', TOKEN) is True
        allowed, reason = LLMService.platform_call_allowed('openai')
    assert allowed is False
    assert 'not in the provider order' in reason
