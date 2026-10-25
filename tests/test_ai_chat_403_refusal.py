"""Test that AI-chat admin routes show the plain refusal page for browsers,
not raw JSON, after the legacy_compat 403 handler fix.

Before the fix: GET /ai-chat/admin/analytics returned {"error":"Access forbidden"}
as JSON for every caller (blueprint-level handler beat the app-level handler).
After the fix: a browser (Accept: text/html) receives the HTML refusal page
through render_plain_error, while an API caller (/api/ in path or AJAX) still
gets JSON.
"""

import uuid

import pytest


def _make_user(db_session, make_org, label, role_name="User"):
    """Helper to create a non-admin user."""
    from app.models.user import User, Role

    org = make_org(f"aichat-403-{label}")
    suffix = uuid.uuid4().hex[:8]
    role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"aichat-403-{label}-{suffix}@example.com",
        first_name="Chat",
        last_name="Tester",
        password="password",
        organization_id=org.id,
        enterprise_role="viewer",
        role_id=role.id if role else None,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user, org


class TestAiChat403Refusal:
    """AI-chat 403 routes return the plain HTML refusal page for browsers."""

    ROUTES = [
        "/ai-chat/admin/analytics",
        "/ai-chat/admin/prompts",
        "/ai-chat/run-records",
    ]
    # /ai-chat/approvals/inbox uses current_user.can(Permission.GENERAL) which
    # the default "User" role passes, so it renders the page (200) rather than
    # returning 403.  Tested separately if needed.

    @pytest.mark.parametrize("path", ROUTES)
    def test_browser_gets_html_refusal(self, path, app, db_session, make_org, login_as):
        """A non-admin user gets 403 with HTML body (the plain refusal page),
        not JSON."""
        user, _org = _make_user(db_session, make_org, "browser")
        client = app.test_client()
        login_as(client, user)

        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 403
        content_type = response.content_type or ""
        assert "text/html" in content_type, (
            f"Expected HTML for browser request to {path}, "
            f"got {content_type}"
        )
        body = response.data.decode("utf-8")
        # The HTML refusal page contains the title from PLAIN[403]
        assert ("lock" in body
                or "don't have access" in body.lower()
                or "403" in body), (
            f"Expected plain error page content for {path}, got: {body[:500]}"
        )

    @pytest.mark.parametrize("path", ROUTES)
    def test_ajax_caller_still_gets_json(self, path, app, db_session, make_org, login_as):
        """An AJAX caller (X-Requested-With: XMLHttpRequest) still gets JSON 403."""
        user, _org = _make_user(db_session, make_org, "ajax")
        client = app.test_client()
        login_as(client, user)

        response = client.get(
            path,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        assert response.status_code == 403
        content_type = response.content_type or ""
        assert "application/json" in content_type, (
            f"Expected JSON for AJAX request to {path}, "
            f"got {content_type}"
        )