"""Shared utility for calling internal Flask routes in-process.

Used by MCP tools and the AI chat NL query router to call REST endpoints
through Flask's test client, avoiding external HTTP calls while still going
through the full middleware stack.
"""

from __future__ import annotations

from typing import Any


def call_internal_api(
    method: str,
    path: str,
    *,
    query_string: dict | None = None,
    json_body: dict | None = None,
    pass_session: bool = False,
) -> tuple[int, Any]:
    """Call an internal Flask route in-process and return (status_code, data).

    Uses Flask's test client so the request goes through the full middleware
    stack (tenant context, tenant isolation, login_required) without a real
    network call.

    Args:
        method: HTTP method (GET or POST).
        path: The route path (e.g. "/api/v1/intelligence/impact/42").
        query_string: Optional query parameters dict.
        json_body: Optional JSON body for POST requests.
        pass_session: If True, copy the current Flask session cookie into the
            test client so the route sees the authenticated user.

    Returns:
        A tuple of (status_code, data) where data is the parsed JSON response
        or an empty dict if parsing fails.
    """
    from flask import current_app, session

    with current_app.test_client() as client:
        if pass_session:
            with client.session_transaction() as sess:
                sess.update(session)

        if method.upper() == "GET":
            resp = client.get(path, query_string=query_string or {})
        else:
            resp = client.post(path, json=json_body or {})

        try:
            data = resp.get_json()
        except Exception:
            data = {}
        return resp.status_code, data if data is not None else {}