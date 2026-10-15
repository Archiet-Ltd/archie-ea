"""Saved-guide UI journey against the real server and database.

Dedicated invocation: AI_PAGE_GUIDE_ENABLED=true, explicit disposable
TEST_DATABASE_URL, marker guide_history. Missing mode fails; it never skips.
The journey does not require an unhealthy provider state: it asserts that
history-only actions never call inference endpoints, whether the environment has
no provider or a deterministic stub. Local work qualifies collection only;
PostgreSQL/browser execution is required in CI.
"""
import os
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _require_explicit_test_database, boot_live_server
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey, pytest.mark.guide_history]


@pytest.fixture(scope='session')
def providerless_guide_configuration():
    _require_explicit_test_database(dict(os.environ))


@pytest.fixture(scope='session')
def live_server(request, ai_protocol_stub, app):
    return boot_live_server(
        request,
        ai_protocol_stub,
        app,
        extra_env={"AI_PAGE_GUIDE_ENABLED": "true"},
    )


@pytest.fixture
def history_records(providerless_guide_configuration, app, seeded):
    """Commit only unique fixture rows visible to the subprocess; remove exact IDs.

    Shared app/seeding facilities are used, but db_session's uncommitted outer
    transaction cannot expose rows to a separate server connection. Therefore
    this narrowly scoped fixture uses real commits and verified ID-only cleanup.
    """
    from app import db
    from app.models.models import APISettings
    from app.models.user import User
    from app.models.vector_embeddings import ChatMessageEmbedding

    marker = uuid.uuid4().hex
    ids = []
    user_ids = []
    target_ids = []
    provider_ids = []
    scope = f"applications.detail:{seeded['ids']['application']}"
    other_scope = f"solutions.detail:{seeded['ids']['solution']}"
    texts = {key: f'Saved guide fixture {marker}: {key}'
             for key in ['question', 'answer', 'other user', 'other scope']}
    org_by_user_id = {}

    def snapshot():
        with app.app_context():
            db.session.remove()
            try:
                return {row.id: (row.user_id, row.chat_session_id, row.message_text)
                        for row in ChatMessageEmbedding.query.filter(
                            ChatMessageEmbedding.id.in_(ids)).all()}
            finally:
                db.session.remove()

    try:
        with app.app_context():
            db.session.remove()
            provider_ids = [row.id for row in APISettings.query.filter_by(enabled=True).all()]
            if provider_ids:
                APISettings.query.filter(APISettings.id.in_(provider_ids)).update(
                    {'enabled': False}, synchronize_session=False
                )
                db.session.commit()
                db.session.remove()
                assert APISettings.query.filter_by(enabled=True).count() == 0, (
                    'Providerless journey requires enabled providers to be disabled during the history-only run')
            # The same owner must be authorized for BOTH fixture pages. The
            # seeded solution belongs to solution_architect; enterprise_architect
            # is neither its creator nor a named stakeholder and correctly gets403.
            for persona in ['solution_architect', 'enterprise_architect']:
                user = User.query.filter_by(email=seeded['emails'][persona],
                                            organization_id=seeded['ids']['org']).one()
                user_ids.append(user.id)
                org_by_user_id[user.id] = user.organization_id
            definitions = [
                (user_ids[0], 'applications.detail', scope, 'user', texts['question']),
                (user_ids[0], 'applications.detail', scope, 'assistant', texts['answer']),
                (user_ids[1], 'applications.detail', scope, 'assistant', texts['other user']),
                (user_ids[0], 'solutions.detail', other_scope, 'assistant', texts['other scope']),
            ]
            sessions = {f'guide_user_{user_id}_{page_key}_{scope_key}'
                        for user_id, page_key, scope_key, _, _ in definitions}
            assert ChatMessageEmbedding.query.filter(
                ChatMessageEmbedding.chat_session_id.in_(sessions)).count() == 0, (
                    'Refusing to seed into or clear an existing guide conversation')
            for index, (user_id, page_key, scope_key, role, text) in enumerate(definitions):
                row = ChatMessageEmbedding(
                    user_id=user_id, chat_session_id=f'guide_user_{user_id}_{page_key}_{scope_key}',
                    message_role=role, message_text=text, domain='guide',
                    created_at=datetime(2026, 1, 1) + timedelta(seconds=index),
                    metadata_json={'guide_mode': True, 'page_key': page_key, 'scope_key': scope_key},
                    organization_id=org_by_user_id[user_id],
                )
                db.session.add(row)
                db.session.flush()
                ids.append(row.id)
            target_ids = ids[:2]
            db.session.commit()
            db.session.remove()
        before = snapshot()
        assert len(before) == 4
        yield {'scope': scope, 'other_scope': other_scope, 'texts': texts,
               'snapshot': snapshot, 'before': before, 'target_ids': target_ids}
    finally:
        with app.app_context():
            db.session.rollback()
            if provider_ids:
                APISettings.query.filter(APISettings.id.in_(provider_ids)).update(
                    {'enabled': True}, synchronize_session=False
                )
                db.session.commit()
            if ids:
                ChatMessageEmbedding.query.filter(
                    ChatMessageEmbedding.id.in_(ids),
                    ChatMessageEmbedding.user_id.in_(user_ids),
                    ChatMessageEmbedding.message_text.in_(list(texts.values())),
                ).delete(synchronize_session=False)
                db.session.commit()
                assert ChatMessageEmbedding.query.filter(
                    ChatMessageEmbedding.id.in_(ids)).count() == 0, 'Exact guide fixture cleanup failed'
            db.session.remove()


def _open_history(page, expected_page, expected_scope):
    trigger = page.locator('#page-guide-trigger')
    expect(trigger).to_be_visible()
    with page.expect_response(lambda response: '/ai-chat/guide/history?' in response.url
                              and response.request.method == 'GET') as loaded:
        trigger.click()
    assert loaded.value.status == 200, loaded.value.text()
    body = loaded.value.json()
    assert body['success'] is True
    assert body['page_key'] == expected_page and body['scope_key'] == expected_scope
    expect(page.locator('#page-guide-clear')).to_be_visible()
    expect(page.locator('#page-guide-error')).to_be_hidden()
    return body['messages']


def test_providerless_saved_guide_can_be_opened_and_cleared_without_affecting_other_history(
    browser, live_server, seeded, history_records
):
    fixture = history_records
    contexts = []
    errors = []
    inference_requests = []

    def signed_in(persona):
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        contexts.append(context)
        context.set_default_timeout(PAGE_TIMEOUT)
        context.set_default_navigation_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.on('response', lambda response: errors.append(f'HTTP {response.status}: {response.url}')
                if response.status >= 400 else None)
        page.on('request', lambda request: inference_requests.append(request.url)
                if '/ai-chat/guide/message' in request.url or '/ai-chat/message' in request.url else None)
        _login(page, live_server, seeded['emails'][persona])
        return page

    try:
        owner = signed_in('solution_architect')
        # A real read-only diagnostic proves the health endpoint itself is live.
        # This journey's contract is that history operations never call
        # inference endpoints, whether the backing environment reports healthy
        # or unavailable.
        health = owner.request.get(live_server + '/ai-chat/api/health/llm')
        assert health.status in {200, 503}, health.text()
        body = health.json()
        assert body['status'] in {'healthy', 'unhealthy'}

        app_url = live_server + f"/applications/{seeded['ids']['application']}"
        assert owner.goto(app_url, wait_until='domcontentloaded').status == 200
        messages = _open_history(owner, 'applications.detail', fixture['scope'])
        assert [message['content'] for message in messages] == [fixture['texts']['question'], fixture['texts']['answer']]
        expect(owner.locator('#page-guide-messages')).to_contain_text(fixture['texts']['answer'])
        expect(owner.locator('#page-guide-messages')).not_to_contain_text(fixture['texts']['other user'])
        assert fixture['snapshot']() == fixture['before'], 'Opening history mutated saved rows'

        with owner.expect_response(lambda response: response.url.endswith('/ai-chat/guide/history/clear')
                                   and response.request.method == 'POST') as cleared:
            owner.get_by_role('button', name='Clear history', exact=True).click()
        assert cleared.value.status == 200, cleared.value.text()
        body = cleared.value.json()
        assert body['success'] is True and body['cleared_count'] == 2
        assert body['page_key'] == 'applications.detail' and body['scope_key'] == fixture['scope']
        expect(owner.locator('#page-guide-messages')).to_contain_text('No messages yet.')
        expect(owner.locator('#page-guide-error')).to_be_hidden()
        survivors = {key: value for key, value in fixture['before'].items()
                     if key not in fixture['target_ids']}
        assert fixture['snapshot']() == survivors

        assert owner.reload(wait_until='domcontentloaded').status == 200
        assert _open_history(owner, 'applications.detail', fixture['scope']) == []
        expect(owner.locator('#page-guide-messages')).to_contain_text('No messages yet.')

        # Verify the other page/scope and other user's conversation through the
        # real Guide UI as well as the exact persisted survivor measurement.
        assert owner.goto(live_server + f"/solutions/{seeded['ids']['solution']}",
                          wait_until='domcontentloaded').status == 200
        messages = _open_history(owner, 'solutions.detail', fixture['other_scope'])
        assert [message['content'] for message in messages] == [fixture['texts']['other scope']]
        expect(owner.locator('#page-guide-messages')).to_contain_text(fixture['texts']['other scope'])
        colleague = signed_in('enterprise_architect')
        assert colleague.goto(app_url, wait_until='domcontentloaded').status == 200
        messages = _open_history(colleague, 'applications.detail', fixture['scope'])
        assert [message['content'] for message in messages] == [fixture['texts']['other user']]
        expect(colleague.locator('#page-guide-messages')).to_contain_text(fixture['texts']['other user'])
        assert fixture['snapshot']() == survivors
    finally:
        for context in contexts:
            context.close()
    assert not inference_requests, f'History-only journey requested inference: {inference_requests}'
    assert not errors, 'Providerless history page errors:\n' + '\n'.join(errors)
