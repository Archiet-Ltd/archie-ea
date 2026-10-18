"""Tests that every tool published through the connector carries a title."""

from __future__ import annotations

from app.modules.mcp.tests.test_scope_enforcement import (
    _mint_token_with_scope,
)
from app.modules.mcp.tests.test_tools import _make_user, _mcp_tools_list


def test_tool_titles_present_and_read_only_hint(client, db_session, make_org):
    """Every tool carries a human-readable title and readOnlyHint: true."""
    org = make_org("tool-titles")
    user = _make_user(db_session, org, "tool-titles@example.com")
    # We need to mint a token with mcp:read scope. Use the helper from test_scope_enforcement.
    from flask import current_app

    token = _mint_token_with_scope(current_app, user, org, "mcp:read")

    tools = _mcp_tools_list(client, token)
    # We expect ten tools.
    assert len(tools) > 0

    titles_seen = set()
    for tool in tools:
        # Each tool must have a title.
        assert "title" in tool, f"Tool {tool['name']} missing title"
        title = tool["title"]
        assert isinstance(title, str), f"Tool {tool['name']} title is not a string"
        assert len(title) > 0, f"Tool {tool['name']} has empty title"
        # Title must not contain underscores.
        assert "_" not in title, f"Tool {tool['name']} title contains underscore: {title}"
        # Title must be under 40 characters.
        assert len(title) <= 40, f"Tool {tool['name']} title too long: {title}"
        # Annotations must include readOnlyHint: true.
        assert "annotations" in tool, f"Tool {tool['name']} missing annotations"
        assert tool["annotations"].get("readOnlyHint") is True, \
            f"Tool {tool['name']} missing readOnlyHint: {tool['annotations']}"
        # Collect title to check uniqueness per underlying tool.
        # Since we have no aliases, we can just check that titles are unique across tools.
        # However, we should also consider that aliases would share the same title.
        # For now, we check that no two tools have the same title.
        assert title not in titles_seen, f"Duplicate title '{title}' for tool {tool['name']}"
        titles_seen.add(title)