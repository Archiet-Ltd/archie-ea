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
    "list_canvases", "get_canvas", "answer", "get_genome_slice",
    # Where data flows and who handles personal data (T-sub-04).
    "ask_data_flow",
}

# The answer tool is published to both chat and the connector so that
# answers through the connector carry citations. Its handler is the
# executor's own _tool_answer, not an entry in ROUTE_READ_HANDLERS.
SHARED_WITH_CHAT = {"answer", "get_genome_slice"}


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
        assert tool["annotations"] == {
            "title": tool["name"].replace("_", " ").capitalize(),
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }


def test_no_catalogue_tool_marked_mcp_mutates():
    bad = _mcp_violations(TOOL_SCHEMAS)
    assert bad == [], f"tools marked for the connector that are not read-only: {bad}"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_connector_tools_are_not_offered_to_the_chat_model(provider):
    """Their handlers need a request context the chat streaming path lacks, so
    the list the runner hands the model, built by the runner's own function,
    must not contain them, and must still contain the existing chat tools."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    built = AgentRunner._build_tool_schemas(provider, TOOL_SCHEMAS)
    names = {t["name"] if provider == "anthropic" else t["function"]["name"] for t in built}
    # answer is deliberately shared: it appears on both surfaces so that
    # connector answers carry citations.
    assert (names & PUBLISHED_NAMES) == SHARED_WITH_CHAT, sorted(names & PUBLISHED_NAMES)
    for kept in ("create_solution", "find_applications", "search_archimate_elements",
                 "get_executive_dashboard", "create_goal"):
        assert kept in names, kept
    # An entry with no surfaces key stays a chat tool.
    legacy = {"name": "legacy", "description": "d", "parameters": {"type": "object"}}
    only_mcp = {**legacy, "name": "only_mcp", "surfaces": ["mcp"]}
    again = AgentRunner._build_tool_schemas("anthropic", [legacy, only_mcp])
    assert [t["name"] for t in again] == ["legacy"]


def test_route_read_tool_helper_refuses_a_route_that_is_not_get():
    with pytest.raises(ValueError):
        registry._route_read_tool("x", "POST /api/v1/things", "d", {"type": "object"})
    with pytest.raises(ValueError):
        registry._route_read_tool("x", "GET /a | POST /b", "d", {"type": "object"})
    with pytest.raises(ValueError):
        registry._route_read_tool("x", "GET /a|POST /b", "d", {"type": "object"})
    ok = registry._route_read_tool("x", "GET /a + GET /b", "d", {"type": "object"})
    assert ok["mutates"] is False and ok["risk_class"] == "read"


def test_a_hand_written_handler_colliding_with_a_connector_name_is_an_error():
    class Executor:
        def _tool_ask_impact(self, args):
            return {}

    with pytest.raises(RuntimeError):
        route_read_tools.install_route_read_tools(Executor)


def test_no_tool_name_appears_twice():
    names = [s["name"] for s in TOOL_SCHEMAS]
    assert len(names) == len(set(names))
    published = [t["name"] for t in connector_tools()]
    assert len(published) == len(set(published))


def test_every_connector_tool_has_an_executor_handler():
    for tool in connector_tools():
        assert callable(getattr(ToolExecutor, f"_tool_{tool['name']}", None))
    # answer uses the executor's own _tool_answer, not a route read handler.
    assert set(ROUTE_READ_HANDLERS) == PUBLISHED_NAMES - SHARED_WITH_CHAT


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
