"""The assistant connector's tool list is derived from the one tool catalogue.

The connector keeps no list of its own: ``connector_tools()`` is the catalogue's
read-only entries that carry the ``mcp`` surface. These tests fail if a second
list appears, if a mutating tool reaches the connector, or if a name is defined
twice.
"""

from __future__ import annotations

import copy
import os
import re

import pytest

from app.modules.ai_chat.tools import registry, route_read_tools
from app.modules.ai_chat.tools.executor import ToolExecutor
from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME, TOOL_SCHEMAS
from app.modules.ai_chat.tools.route_read_tools import (
    CONNECTOR_ALIASES,
    ROUTE_READ_HANDLERS,
    connector_tools,
    resolve_connector_tool,
)

# The ten names an assistant may already depend on. Removing or renaming one
# is a deliberate, documented decision, never a side effect.
PUBLISHED_NAMES = {
    "ask_impact", "ask_strategy", "ask_portfolio", "ask_programme",
    "ask_risk", "ask_accountability", "search_elements", "get_element",
    "list_canvases", "get_canvas",
}


def _mcp_violations(schemas):
    """Names of catalogue tools marked for the connector that are not read-only."""
    return [s["name"] for s in schemas
            if "mcp" in s.get("surfaces", [])
            and (s.get("mutates") is not False or s.get("risk_class") != "read")]


def test_connector_tools_are_exactly_the_published_names():
    assert {t["name"] for t in connector_tools()} == PUBLISHED_NAMES


def test_every_connector_tool_is_a_catalogue_read_tool():
    for tool in connector_tools():
        schema = TOOL_SCHEMA_BY_NAME.get(CONNECTOR_ALIASES.get(tool["name"], tool["name"]))
        assert schema is not None, f"{tool['name']} is not in the catalogue"
        assert schema["risk_class"] == "read"
        assert schema["mutates"] is False
        assert tool["inputSchema"] == schema["parameters"]
        assert tool["description"] == schema["description"]
        assert tool["annotations"] == {"readOnlyHint": True}


def test_no_catalogue_tool_marked_mcp_mutates():
    bad = _mcp_violations(TOOL_SCHEMAS)
    assert bad == [], f"tools marked for the connector that are not read-only: {bad}"


def test_no_tool_name_appears_twice():
    names = [s["name"] for s in TOOL_SCHEMAS]
    assert len(names) == len(set(names))
    published = [t["name"] for t in connector_tools()]
    assert len(published) == len(set(published))


def test_every_connector_tool_has_an_executor_handler():
    for tool in connector_tools():
        assert callable(getattr(ToolExecutor, f"_tool_{tool['name']}", None))
    assert set(ROUTE_READ_HANDLERS) == PUBLISHED_NAMES


def test_a_write_tool_marked_mcp_never_reaches_the_connector(monkeypatch):
    """Marking a mutating tool for the connector does not publish it, and the
    divergence check fails on the catalogue."""
    write = copy.deepcopy(next(s for s in TOOL_SCHEMAS if s["name"] == "create_solution"))
    write["surfaces"] = ["chat", "blueprint", "mcp"]
    monkeypatch.setattr(route_read_tools, "TOOL_SCHEMAS", [*TOOL_SCHEMAS, write])
    assert "create_solution" not in {t["name"] for t in connector_tools()}
    assert resolve_connector_tool("create_solution") is None
    assert _mcp_violations([*TOOL_SCHEMAS, write]) == ["create_solution"]


def test_a_read_tool_marked_mcp_appears_without_any_other_edit(monkeypatch):
    read = copy.deepcopy(TOOL_SCHEMA_BY_NAME["get_completeness_score"])
    assert "mcp" not in read["surfaces"]
    read["surfaces"] = [*read["surfaces"], "mcp"]
    monkeypatch.setattr(
        route_read_tools, "TOOL_SCHEMAS",
        [s for s in TOOL_SCHEMAS if s["name"] != "get_completeness_score"] + [read],
    )
    assert "get_completeness_score" in {t["name"] for t in connector_tools()}
    assert resolve_connector_tool("get_completeness_score") == "get_completeness_score"


def test_an_alias_serves_the_old_name_from_the_catalogue_tool(monkeypatch):
    monkeypatch.setitem(CONNECTOR_ALIASES, "find_elements", "search_elements")
    tools = {t["name"]: t for t in connector_tools()}
    assert "find_elements" in tools and "search_elements" in tools
    assert tools["find_elements"]["inputSchema"] == tools["search_elements"]["inputSchema"]
    assert resolve_connector_tool("find_elements") == "search_elements"


def test_an_alias_to_a_non_connector_tool_is_not_served(monkeypatch):
    monkeypatch.setitem(CONNECTOR_ALIASES, "make_solution", "create_solution")
    assert "make_solution" not in {t["name"] for t in connector_tools()}
    assert resolve_connector_tool("make_solution") is None


def test_unknown_and_non_connector_names_do_not_resolve():
    assert resolve_connector_tool("nope") is None
    assert resolve_connector_tool("create_solution") is None
    # A catalogue read tool that does not carry the connector surface stays chat-only.
    assert resolve_connector_tool("find_applications") is None


def test_no_second_tool_list_remains():
    """A grep for the old registration decorator in app/ finds nothing."""
    root = os.path.join(os.path.dirname(__file__), "..", "app")
    pattern = re.compile(r"@register_tool\(|\bTOOL_REGISTRY\b")
    offenders = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if fname.endswith(".py"):
                path = os.path.join(dirpath, fname)
                with open(path, encoding="utf-8") as fh:
                    if pattern.search(fh.read()):
                        offenders.append(os.path.relpath(path, root))
    assert offenders == [], offenders
    assert not os.path.exists(os.path.join(root, "modules", "mcp", "tools"))


def test_catalogue_module_is_the_only_definer_of_tool_schemas():
    assert registry.TOOL_SCHEMAS is route_read_tools.TOOL_SCHEMAS
