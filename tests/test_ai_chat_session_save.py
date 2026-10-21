"""Tests for chat session save, list, and load endpoints.

These tests verify that:
1. A saved session appears in the list endpoint
2. A saved session can be loaded by ID with its messages
3. A failed save returns an error, not success
4. Tenant isolation: users in different organisations cannot access each other's sessions
"""

import pytest
import uuid

from tests.test_ai_write_approval import _login as login_user
from tests.test_ai_write_approval import _make_user


def _make_viewer(db_session, org):
    """Create a user with Viewer role (no write permission)."""
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"viewer-{suffix}@example.com",
        first_name="Viewer",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="viewer",
    )
    role = Role.query.filter_by(name="Viewer").first()
    assert role is not None, "Viewer role must exist"
    user.role_id = role.id
    db_session.add(user)
    db_session.flush()
    return user


class TestChatSessionSaveListLoad:
    """Test the chat session save/list/load cycle."""

    def test_save_then_list_shows_session(self, app, client, db_session, make_org):
        """A saved session appears in the list endpoint."""
        org = make_org("save-list")
        user = _make_user(db_session, org)

        # Enable AI chat for this test
        app.config["AI_CHAT_ENABLED"] = True

        # Log in the user
        login_user(client, user.id)

        # Save a session
        save_resp = client.post(
            "/ai-chat/session/save",
            json={
                "session_name": "Test Session",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
                "context": {"domain": "general"},
            },
        )
        assert save_resp.status_code == 200, f"Save failed: {save_resp.get_json()}"
        save_data = save_resp.get_json()
        assert save_data["success"] is True
        session_id = save_data["session_id"]
        assert session_id is not None

        # List sessions
        list_resp = client.get("/ai-chat/sessions")
        assert list_resp.status_code == 200
        list_data = list_resp.get_json()
        assert list_data["success"] is True
        sessions = list_data["sessions"]
        assert len(sessions) >= 1

        # Find our saved session
        saved = [s for s in sessions if s["id"] == session_id]
        assert len(saved) == 1, f"Saved session {session_id} not found in list"
        assert saved[0]["name"] == "Test Session"
        assert saved[0]["message_count"] == 2

    def test_save_then_load_returns_messages(self, app, client, db_session, make_org):
        """A saved session can be loaded by ID with its messages."""
        org = make_org("save-load")
        user = _make_user(db_session, org)

        app.config["AI_CHAT_ENABLED"] = True
        login_user(client, user.id)

        # Save a session with specific messages
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
        ]
        save_resp = client.post(
            "/ai-chat/session/save",
            json={
                "session_name": "Load Test Session",
                "messages": messages,
                "context": {"domain": "architecture"},
            },
        )
        assert save_resp.status_code == 200
        save_data = save_resp.get_json()
        assert save_data["success"] is True
        session_id = save_data["session_id"]

        # Load the session
        load_resp = client.get(f"/ai-chat/session/{session_id}")
        assert load_resp.status_code == 200, f"Load failed: {load_resp.get_json()}"
        load_data = load_resp.get_json()
        assert load_data["success"] is True
        session = load_data["session"]
        assert session["id"] == session_id
        assert session["name"] == "Load Test Session"
        assert len(session["messages"]) == 3
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][0]["content"] == "First message"
        assert session["messages"][1]["role"] == "assistant"
        assert session["messages"][1]["content"] == "First response"
        assert session["messages"][2]["role"] == "user"
        assert session["messages"][2]["content"] == "Second message"
        assert session["context"]["domain"] == "architecture"

    def test_failed_save_returns_error_not_success(self, app, client, db_session, make_org):
        """A failed save answers an error, not success."""
        org = make_org("save-fail")
        user = _make_user(db_session, org)

        app.config["AI_CHAT_ENABLED"] = True
        login_user(client, user.id)

        # Test that missing/empty JSON returns an error response (not success)
        resp = client.post("/ai-chat/session/save", data="", content_type="application/json")
        assert resp.status_code in (400, 500)
        data = resp.get_json()
        assert "error" in data
        assert data.get("success") is not True  # Never reports success for a failure

        # Test that empty JSON object returns an error
        resp2 = client.post("/ai-chat/session/save", json={})
        assert resp2.status_code in (400, 500)
        data2 = resp2.get_json()
        assert "error" in data2
        assert data2.get("success") is not True

        # A real DB failure would return 500 with error (tested via mocking if needed)
        # The route's try/except returns {"error": "Failed to save session", ...}, 500

    def test_tenant_isolation_sessions(self, app, client, db_session, make_org):
        """Users in different organisations cannot access each other's sessions."""
        org_a = make_org("tenant-a")
        org_b = make_org("tenant-b")
        user_a = _make_user(db_session, org_a)
        user_b = _make_user(db_session, org_b)

        app.config["AI_CHAT_ENABLED"] = True

        # User A saves a session
        login_user(client, user_a.id)
        save_resp = client.post(
            "/ai-chat/session/save",
            json={
                "session_name": "Org A Session",
                "messages": [{"role": "user", "content": "Secret from A"}],
                "context": {},
            },
        )
        assert save_resp.status_code == 200
        save_data = save_resp.get_json()
        assert save_data["success"] is True
        session_id_a = save_data["session_id"]

        # User A can list their session
        list_resp_a = client.get("/ai-chat/sessions")
        assert list_resp_a.status_code == 200
        sessions_a = list_resp_a.get_json()["sessions"]
        assert any(s["id"] == session_id_a for s in sessions_a)

        # User A can load their session
        load_resp_a = client.get(f"/ai-chat/session/{session_id_a}")
        assert load_resp_a.status_code == 200
        assert load_resp_a.get_json()["success"] is True

        # User B logs in
        login_user(client, user_b.id)

        # User B cannot see User A's session in list
        list_resp_b = client.get("/ai-chat/sessions")
        assert list_resp_b.status_code == 200
        sessions_b = list_resp_b.get_json()["sessions"]
        assert not any(s["id"] == session_id_a for s in sessions_b), "Tenant isolation violated: User B sees User A's session"

        # User B cannot load User A's session
        load_resp_b = client.get(f"/ai-chat/session/{session_id_a}")
        assert load_resp_b.status_code == 404
        assert load_resp_b.get_json()["error"] == "Session not found"

        # User B saves their own session
        save_resp_b = client.post(
            "/ai-chat/session/save",
            json={
                "session_name": "Org B Session",
                "messages": [{"role": "user", "content": "Secret from B"}],
                "context": {},
            },
        )
        assert save_resp_b.status_code == 200
        save_data_b = save_resp_b.get_json()
        assert save_data_b["success"] is True
        session_id_b = save_data_b["session_id"]

        # User B can see their own session
        list_resp_b2 = client.get("/ai-chat/sessions")
        sessions_b2 = list_resp_b2.get_json()["sessions"]
        assert any(s["id"] == session_id_b for s in sessions_b2)

        # User A cannot see User B's session
        login_user(client, user_a.id)
        list_resp_a2 = client.get("/ai-chat/sessions")
        sessions_a2 = list_resp_a2.get_json()["sessions"]
        assert not any(s["id"] == session_id_b for s in sessions_a2), "Tenant isolation violated: User A sees User B's session"