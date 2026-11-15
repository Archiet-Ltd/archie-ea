"""A new message clears only its sender's cached list of conversations in that organisation.

Two organisations, each with its own person and conversation. When one person's conversation
gets a message, their own list is read afresh (so its message count is right), while the other
person's cached list, and the same person's list in another organisation, stay cached.
"""

import fnmatch
import uuid

import pytest


class _Store:
    """Just enough of a Redis client for the cache: keys, values and glob deletes."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        return True

    def keys(self, pattern):
        return [key for key in self.data if fnmatch.fnmatchcase(key, pattern)]

    def delete(self, *keys):
        return sum(1 for key in keys if self.data.pop(key, None) is not None)


@pytest.fixture
def store(monkeypatch):
    from app.extensions.cache import cache_manager

    fake = _Store()
    monkeypatch.setattr(cache_manager, "redis_client", fake)
    return fake


def _person(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"threads-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _lists(store):
    return sorted(key for key in store.data if key.startswith("threads:user:"))


def test_a_message_clears_only_its_senders_list_in_its_organisation(db_session, make_org, tenant_ctx, store):
    from app.services.conversation_history import get_history_service

    service = get_history_service()
    org_a, org_b = make_org("threads-a"), make_org("threads-b")
    ada, bea = _person(db_session, org_a, "ada"), _person(db_session, org_b, "bea")
    db_session.commit()

    with tenant_ctx(org_a.id):
        mine = service.create_thread(ada.id, "Ada's question", "test-model", organization_id=org_a.id)
    with tenant_ctx(org_b.id):
        theirs = service.create_thread(bea.id, "Bea's question", "test-model", organization_id=org_b.id)
        service.get_user_threads(bea.id, limit=100, organization_id=org_b.id)
        # The same person listing another organisation keeps a list of its own.
        service.get_user_threads(ada.id, limit=100, organization_id=org_b.id)
    with tenant_ctx(org_a.id):
        listed = service.get_user_threads(ada.id, limit=100, organization_id=org_a.id)
    assert [t.message_count for t in listed if t.id == mine.id] == [0]
    assert len(_lists(store)) == 3

    with tenant_ctx(org_a.id):
        service.add_message(mine.id, "user", "Who owns Ifiok?")
        kept = _lists(store)
        assert len(kept) == 2, kept
        assert all(f"organization_id={org_b.id}" in key for key in kept), kept
        # Ada's own list is read afresh and counts the new message.
        again = service.get_user_threads(ada.id, limit=100, organization_id=org_a.id)
    assert [t.message_count for t in again if t.id == mine.id] == [1]

    with tenant_ctx(org_b.id):
        service.update_thread_title(theirs.id, "Renamed")
    assert all(f"organization_id={org_b.id}" not in key or f"_{ada.id}:" in key for key in _lists(store)), _lists(store)

    with tenant_ctx(org_a.id):
        service.delete_thread(mine.id)
    assert not any(f"organization_id={org_a.id}" in key for key in _lists(store)), _lists(store)
