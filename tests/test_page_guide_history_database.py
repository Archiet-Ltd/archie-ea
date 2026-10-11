"""Real authenticated history routes and rows, isolated by shared rollback fixtures.

Requires PostgreSQL; collected but not executed on the no-database workstation.
No provider or generation boundary is invoked. User/session isolation is tested
explicitly with organisation-scoped ChatMessageEmbedding rows.
"""
import uuid
from datetime import datetime, timedelta

import pytest

CONTEXT = {'page_key': 'applications.detail', 'scope_key': 'applications.detail:32'}


@pytest.mark.parametrize('chat_override', [None, True])
def test_saved_history_read_clear_remain_user_and_scope_isolated(
    app, db_session, make_org, client, login_as, monkeypatch, chat_override
):
    from app.models.user import User
    from app.models.vector_embeddings import ChatMessageEmbedding
    from app.services.llm_service import LLMService

    monkeypatch.setitem(app.config, 'AI_PAGE_GUIDE_ENABLED', True)
    monkeypatch.setitem(app.config, 'AI_CHAT_ENABLED', chat_override)

    def no_resolver():
        raise AssertionError('Saved-history operations must not inspect a provider')

    monkeypatch.setattr(LLMService, '_get_configured_provider', staticmethod(no_resolver))
    org_a, org_b = make_org('guide-a'), make_org('guide-b')
    users = []
    for label, org in [('owner', org_a), ('colleague', org_a), ('foreign', org_b)]:
        user = User(email=f'guide-{label}-{uuid.uuid4().hex[:12]}@example.com',
                    first_name=label, confirmed=True, organization_id=org.id,
                    enterprise_role='enterprise_architect')
        db_session.add(user)
        users.append(user)
    db_session.flush()
    user_ids = [user.id for user in users]
    rows = []
    org_by_user_id = {user.id: user.organization_id for user in users}

    def add(user_id, page, scope, content, role='assistant'):
        row = ChatMessageEmbedding(
            user_id=user_id, chat_session_id=f'guide_user_{user_id}_{page}_{scope}',
            message_role=role, message_text=content, domain='guide',
            created_at=datetime(2026, 1, 1) + timedelta(seconds=len(rows)),
            metadata_json={'page_key': page, 'scope_key': scope, 'guide_mode': True},
            organization_id=org_by_user_id[user_id],
        )
        db_session.add(row)
        rows.append(row)

    for label, user_id in zip(['owner', 'colleague', 'foreign'], user_ids):
        add(user_id, 'applications.detail', 'applications.detail:32', f'{label} question', 'user')
        add(user_id, 'applications.detail', 'applications.detail:32', f'{label} saved answer')
    add(user_ids[0], 'applications.detail', 'applications.detail:33', 'owner other scope')
    add(user_ids[0], 'dashboard.overview', 'global', 'owner other page')
    # Defense in depth: even a foreign-user row with a colliding/corrupt session
    # identifier must not be exposed or deleted by the owner's history request.
    add(user_ids[2], 'applications.detail', 'applications.detail:32', 'foreign colliding session')
    rows[-1].chat_session_id = f'guide_user_{user_ids[0]}_applications.detail_applications.detail:32'
    db_session.commit()  # shared fixture releases a savepoint, never the outer transaction
    row_ids = [row.id for row in rows]
    target_ids = row_ids[:2]
    db_session.expunge_all()

    # Each real user sees only their own persisted history, even at identical
    # page/scope strings. login_as clears Flask-Login and tenant g caches.
    for label, user_id in zip(['owner', 'colleague', 'foreign'], user_ids):
        login_as(client, user_id)
        response = client.get('/ai-chat/guide/history', query_string=CONTEXT)
        assert response.status_code == 200
        assert [message['content'] for message in response.get_json()['messages']] == [
            f'{label} question', f'{label} saved answer']
        db_session.expunge_all()

    login_as(client, user_ids[0])
    cleared = client.post('/ai-chat/guide/history/clear', json=CONTEXT)
    assert cleared.status_code == 200
    assert cleared.get_json()['cleared_count'] == 2
    db_session.expunge_all()
    survivors = ChatMessageEmbedding.query.filter(ChatMessageEmbedding.id.in_(row_ids)).all()
    assert {row.id for row in survivors} == set(row_ids) - set(target_ids)

    login_as(client, user_ids[0])
    after = client.get('/ai-chat/guide/history', query_string=CONTEXT)
    assert after.status_code == 200
    assert after.get_json()['messages'] == []
    login_as(client, user_ids[0])
    repeated = client.post('/ai-chat/guide/history/clear', json=CONTEXT)
    assert repeated.status_code == 200
    assert repeated.get_json()['cleared_count'] == 0

    # Other users, the other tenant, and the owner's other scope/page survive.
    for label, user_id in zip(['colleague', 'foreign'], user_ids[1:]):
        db_session.expunge_all()
        login_as(client, user_id)
        response = client.get('/ai-chat/guide/history', query_string=CONTEXT)
        assert response.status_code == 200
        assert [message['content'] for message in response.get_json()['messages']] == [
            f'{label} question', f'{label} saved answer']
    for page, scope, content in [('applications.detail', 'applications.detail:33', 'owner other scope'),
                                 ('dashboard.overview', 'global', 'owner other page')]:
        db_session.expunge_all()
        login_as(client, user_ids[0])
        response = client.get('/ai-chat/guide/history', query_string={'page_key': page, 'scope_key': scope})
        assert response.status_code == 200
        assert [message['content'] for message in response.get_json()['messages']] == [content]
