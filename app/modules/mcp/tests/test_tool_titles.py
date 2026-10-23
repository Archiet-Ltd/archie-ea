"""Connector tool titles.

The test asserts that every tool published through the connector carries a
non-empty title with no underscores, at most 40 characters long, that the
annotations carry readOnlyHint: true, and that titles are unique across all
published tools, aliases included.
"""

from __future__ import annotations

from app.modules.ai_chat.tools.route_read_tools import (
    CONNECTOR_ALIASES,
    connector_tools,
)
from app.modules.mcp.tests.test_scope_enforcement import (
    _mint_token_with_scope,
)
from app.modules.mcp.tests.test_tools import _make_user, _mcp_tools_list


def test_tool_titles_present_and_read_only_hint(client, db_session, make_org):
    """Every tool carries a human-readable title and readOnlyHint: true."""
    org = make_org("tool-titles")
    user = _make_user(db_session, org, "tool-titles@example.com")
    from flask import current_app

    token = _mint_token_with_scope(current_app, user, org, "mcp:read")

    tools = _mcp_tools_list(client, token)
    assert len(tools) > 0

    titles_seen = set()
    for tool in tools:
        assert "title" in tool, f"Tool {tool['name']} missing title"
        title = tool["title"]
        assert isinstance(title, str), f"Tool {tool['name']} title is not a string"
        assert len(title) > 0, f"Tool {tool['name']} has empty title"
        assert "_" not in title, f"Tool {tool['name']} title contains underscore: {title}"
        assert len(title) <= 40, f"Tool {tool['name']} title too long: {title}"
        assert "annotations" in tool, f"Tool {tool['name']} missing annotations"
        assert tool["annotations"].get("readOnlyHint") is True, (
            f"Tool {tool['name']} missing readOnlyHint: {tool['annotations']}"
        )
        assert title not in titles_seen, f"Duplicate title '{title}' for tool {tool['name']}"
        titles_seen.add(title)


def test_alias_gets_its_own_title(monkeypatch):
    """An alias carries its own title, different from its target's, and no
    two published tools share a title."""
    monkeypatch.setitem(CONNECTOR_ALIASES, "find_elements", "search_elements")

    tools = connector_tools()
    by_name = {tool["name"]: tool for tool in tools}

    assert "find_elements" in by_name
    assert "search_elements" in by_name
    assert by_name["find_elements"]["title"], "Alias has no title"
    assert by_name["find_elements"]["title"] != by_name["search_elements"]["title"]

    titles = [tool["title"] for tool in tools]
    assert len(titles) == len(set(titles)), "Titles are not unique across tools"
