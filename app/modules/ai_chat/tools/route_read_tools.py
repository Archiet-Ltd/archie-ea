"""Read tools that answer by calling an existing JSON route in-process, and the
assistant connector's view of the tool catalogue.

The catalogue (``registry.TOOL_SCHEMAS``) is the one list of tools. The
connector (the ``/mcp`` endpoint) does not keep a list of its own: it serves
``connector_tools()``, which is the catalogue's read-only entries that carry the
``mcp`` surface, plus any alias in ``CONNECTOR_ALIASES``. Execution goes through
``ToolExecutor.execute``, the one dispatch point, so the handlers below are
installed on ``ToolExecutor`` as ``_tool_<name>`` exactly like every other tool.

Every handler is a thin wrapper around a route the product already serves. It
imports no model and no service: the request goes through the full middleware
stack (tenant context, tenant isolation, login) with the caller already resolved.
"""

from __future__ import annotations

import re

from typing import Any, Callable

from app.utils.internal_api import call_internal_api
from app.utils.text_sanitization import neutralize_fence_lookalikes

from .registry import TOOL_SCHEMAS

CONNECTOR_SURFACE = "mcp"

# Old tool name -> catalogue tool name, for a tool the connector used to
# publish under a different name. One line per alias, covered by a test.
# Empty today: every name the connector published is itself a catalogue tool,
# because none of the catalogue's other read tools takes the same arguments.
CONNECTOR_ALIASES: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Credential scrubbing and fencing                                             #
# --------------------------------------------------------------------------- #

_SENSITIVE_KEY_PARTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "private_key", "authorization", "client_secret",
)
REDACTED = "[withheld]"


def scrub_credentials(value: Any) -> Any:
    """Remove anything credential-shaped from a tool result before it leaves.

    A tool wraps an ordinary REST route whose payload may carry a field a
    browser user never sees on screen; the assistant connector is a wider
    door, so any key that names a secret is withheld whatever the route sent.
    """
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and any(p in k.lower() for p in _SENSITIVE_KEY_PARTS)
                else scrub_credentials(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub_credentials(v) for v in value]
    return value


# Fields that are safe (enums, numbers, ids, booleans) - skip fencing.
_FENCE_SAFE_KEYS = {
    "id", "element_id", "risk_id", "work_package_id", "initiative_id",
    "application_component_id", "organization_unit_id", "depth",
    "explicit_count", "derived_count", "stale_count", "latency_ms",
    "likelihood", "impact", "risk_score", "progress_percentage",
    "completion_percentage", "cost_variance_pct", "budget_variance_pct",
    "target_value", "actual_value", "kind", "confidence", "stale",
    "derived_id", "engine_version", "chain", "chain_elements",
    "is_overdue", "capacity_not_available", "unresolved",
    "organization_id", "ratio", "sample_count",
}


def _fence_response_text(payload: dict) -> None:
    """Apply neutralize_fence_lookalikes to every free-text field in the payload."""
    if not isinstance(payload, dict):
        return
    for key, value in list(payload.items()):
        if isinstance(value, str) and key not in _FENCE_SAFE_KEYS:
            payload[key] = neutralize_fence_lookalikes(value)
        elif isinstance(value, dict):
            _fence_response_text(value)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _fence_response_text(item)
                elif isinstance(item, str) and key not in _FENCE_SAFE_KEYS:
                    value[index] = neutralize_fence_lookalikes(item)


def _fence_keys(item: Any, keys: tuple[str, ...]) -> None:
    if not isinstance(item, dict):
        return
    for key in keys:
        if key in item and isinstance(item[key], str):
            item[key] = neutralize_fence_lookalikes(item[key])


# --------------------------------------------------------------------------- #
# Handlers                                                                     #
# --------------------------------------------------------------------------- #


def _intelligence(lens: str) -> Callable[[dict], dict]:
    def handler(args: dict) -> dict:
        element_id = int(args["element_id"])
        _status, data = call_internal_api(
            "GET", f"/api/v1/intelligence/{lens}/{element_id}", pass_session=True
        )
        if data and data.get("success") and data.get("data"):
            _fence_response_text(data["data"])
        return data

    handler.__name__ = f"ask_{lens}"
    return handler


def _impact_answer(args: dict) -> dict:
    """``ask_impact`` returns the envelope the canonical answer service stores:
    rows, truth class, confidence, citations and freshness, at a permanent
    address. The organisation is the caller's, taken from the token or session
    by the middleware; it is not an argument. The only write is the answer
    service's own immutable answer record."""
    from flask_login import current_user

    from .executor import ToolCall, ToolExecutor

    return ToolExecutor(user_id=current_user.id).execute(
        ToolCall(
            id="mcp-ask-impact",
            name="answer",
            arguments={"question": "impact", "element_id": args["element_id"]},
        ),
        propagate_errors=True,
    )


def _data_flow(args: dict) -> dict:
    """``ask_data_flow``: the data entities overview from the data architecture
    route, or, for one data object, the answer service's data-flow envelope
    (stored like ``ask_impact``'s)."""
    if args.get("element_id") is None:
        _status, data = call_internal_api("GET", "/architecture/api/data-flows", pass_session=True)
        if isinstance(data, dict):
            _fence_response_text(data)
        return data

    from flask_login import current_user

    from .executor import ToolCall, ToolExecutor

    return ToolExecutor(user_id=current_user.id).execute(
        ToolCall(
            id="mcp-ask-data-flow",
            name="answer",
            arguments={"question": "data_flow", "element_id": args["element_id"]},
        ),
        propagate_errors=True,
    )


# Tools whose result is the answer service's envelope. It names records and
# carries free text (element names, reasons, messages), so on the connector it
# is fenced like every route tool's payload. Chat does not go through here.
ANSWER_ENVELOPE_TOOLS = frozenset({"answer", "ask_impact", "ask_data_flow"})


_URL_KEYS = ("url", "link", "href")
_OPEN_AT = re.compile(r"(open at )(/[^\s)\]\"']*)")


def absolutise_connector_urls(result: Any, base: str) -> Any:
    """Make every address the connector returns absolute, built from the public base URL.

    A client outside the product (an assistant in another app) cannot open a path such as
    /intelligence/answers/<id>. Only values under a url/link/href key, and the 'open at <path>'
    phrase in a message, are changed: a name that happens to start with a slash is never touched.
    """
    base = (base or "").rstrip("/")
    if not base:
        return result

    def fix(value):
        if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
            return base + value
        return value

    def walk(node):
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if isinstance(value, str):
                    lowered = key.lower()
                    if lowered in _URL_KEYS or lowered.endswith(("_url", "_link", "_href")):
                        node[key] = fix(value)
                    elif lowered == "message":
                        node[key] = _OPEN_AT.sub(lambda m: m.group(1) + base + m.group(2), value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result)
    return result


def fence_connector_result(tool_name: str, result: Any) -> Any:
    """Neutralise fence look-alikes in an answer-envelope tool's result."""
    if tool_name in ANSWER_ENVELOPE_TOOLS and isinstance(result, dict):
        _fence_response_text(result)
    return result


def _search_elements(args: dict) -> dict:
    query = str(args["query"])
    limit = int(args.get("limit", 30))
    _status, result = call_internal_api(
        "GET", "/archimate/api/elements/search",
        query_string={"q": query, "limit": limit},
        pass_session=True,
    )
    if result and result.get("data"):
        for item in result["data"]:
            _fence_keys(item, ("name", "description"))
    return result


def _get_element(args: dict) -> dict:
    element_id = int(args["element_id"])
    _status, result = call_internal_api(
        "GET", f"/archimate/api/elements/{element_id}/detail", pass_session=True,
    )
    if isinstance(result, dict):
        _fence_keys(result, ("name", "description"))
        for group in ("linked_solutions", "linked_capabilities", "connected_elements"):
            for item in result.get(group) or []:
                _fence_keys(item, ("name",))
    return result


def _list_canvases(args: dict) -> dict:
    _status, bmc_data = call_internal_api(
        "GET", "/business-model/api/list", pass_session=True
    )
    _status, bc_data = call_internal_api(
        "GET", "/business-case/api/list", pass_session=True
    )
    bmc_list = (bmc_data.get("data") or []) if isinstance(bmc_data, dict) else []
    for canvas in bmc_list:
        _fence_keys(canvas, ("name", "description"))
    bc_list = (bc_data.get("data") or []) if isinstance(bc_data, dict) else []
    for case in bc_list:
        _fence_keys(case, ("title",))
    return {"business_model_canvases": bmc_list, "business_cases": bc_list}


_CASE_TEXT_KEYS = ("title", "description", "problem_statement", "options_considered",
                   "recommended_option", "expected_benefits", "key_risks")


def _get_canvas(args: dict) -> dict:
    canvas_type = str(args["canvas_type"])
    canvas_id = int(args["canvas_id"])

    if canvas_type == "business_model":
        status, data = call_internal_api(
            "GET", f"/business-model/api/{canvas_id}", pass_session=True
        )
        if status != 200 or not (isinstance(data, dict) and data.get("success")):
            return {"success": False, "error": {"code": "NOT_FOUND", "message": "Business Model Canvas not found"}}
        canvas_data = data["data"]
        _fence_keys(canvas_data, ("name", "description"))
        for block_key, block_content in (canvas_data.get("blocks") or {}).items():
            if isinstance(block_content, str):
                canvas_data["blocks"][block_key] = neutralize_fence_lookalikes(block_content)
        return {"success": True, "data": canvas_data}

    if canvas_type == "business_case":
        status, data = call_internal_api(
            "GET", f"/business-case/api/{canvas_id}", pass_session=True
        )
        if status != 200 or not (isinstance(data, dict) and data.get("success")):
            return {"success": False, "error": {"code": "NOT_FOUND", "message": "Business Case not found"}}
        case_data = data["data"]
        _fence_keys(case_data, _CASE_TEXT_KEYS)
        return {"success": True, "data": case_data}

    return {"success": False, "error": {"code": "INVALID_PARAMETER",
            "message": f"Unknown canvas_type: {canvas_type}"}}


ROUTE_READ_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "ask_impact": _impact_answer,
    "ask_data_flow": _data_flow,
    "ask_strategy": _intelligence("strategy"),
    "ask_portfolio": _intelligence("portfolio"),
    "ask_programme": _intelligence("programme"),
    "ask_risk": _intelligence("risk"),
    "ask_accountability": _intelligence("accountability"),
    "search_elements": _search_elements,
    "get_element": _get_element,
    "list_canvases": _list_canvases,
    "get_canvas": _get_canvas,
}


def install_route_read_tools(executor_cls) -> int:
    """Install ``_tool_<name>`` on the executor for each route-backed read tool.

    Same mechanism the generated ArchiMate element tools use. A hand-written
    handler of the same name is an error, not a silent skip.
    """
    installed = 0
    for name, fn in ROUTE_READ_HANDLERS.items():
        attribute = f"_tool_{name}"
        if hasattr(executor_cls, attribute):
            # A hand-written handler of the same name would silently replace or
            # be replaced by the route-backed one: two tools, one name.
            raise RuntimeError(
                f"ToolExecutor already defines {attribute}; a connector tool name "
                "must have exactly one implementation"
            )

        def _make(handler):
            def _tool(self, args: dict) -> dict:
                return handler(args)
            return _tool

        method = _make(fn)
        method.__name__ = attribute
        setattr(executor_cls, attribute, method)
        installed += 1
    return installed


# --------------------------------------------------------------------------- #
# The connector's view of the catalogue                                        #
# --------------------------------------------------------------------------- #


def _is_connector_tool(schema: dict) -> bool:
    return (
        CONNECTOR_SURFACE in schema.get("surfaces", [])
        and schema.get("risk_class") == "read"
        and schema.get("mutates") is False
    )


# The route-rule pattern(s) (the wave map's own key format -- a `<name>`
# placeholder collapses to a bare "<x>" inside entry_for, so the exact
# parameter name here does not matter) that the wave map gates each tool
# through. A tool is released only when every one of its rules is OPEN.
#
# Most tools call their rule directly (see ROUTE_READ_HANDLERS). ask_impact is
# the one exception: _impact_answer stores its result at the canonical answer
# service's own permanent address rather than calling a route of its own, so
# that address -- not a route ask_impact never calls -- is what the wave map
# actually gates for it.
TOOL_ROUTE_PATHS: dict[str, tuple[str, ...]] = {
    "list_canvases": ("/business-model/api/list", "/business-case/api/list"),
    "get_canvas": ("/business-model/api/<x>", "/business-case/api/<x>"),
    "get_element": ("/archimate/api/elements/<x>/detail",),
    "search_elements": ("/archimate/api/elements/search",),
    "ask_strategy": ("/api/v1/intelligence/strategy/<x>",),
    "ask_portfolio": ("/api/v1/intelligence/portfolio/<x>",),
    "ask_programme": ("/api/v1/intelligence/programme/<x>",),
    "ask_risk": ("/api/v1/intelligence/risk/<x>",),
    "ask_accountability": ("/api/v1/intelligence/accountability/<x>",),
    "ask_impact": ("/intelligence/answers/<x>",),
}


def tool_released(tool_name: str) -> bool:
    """True when every route the tool reads is open in the released waves (always true with the switch off).

    Reads the wave map directly (``entry_for``/``state_for``), not
    ``is_released_path``: that needs a live app's ``url_map`` to resolve a
    concrete URL to a rule, which is more than this needs when the rule
    pattern is already known, and it requires a request-bound app context
    this function's callers (including plain unit tests) do not always have.
    """
    from app.release_waves import OPEN, configured_wave, entry_for, state_for

    wave = configured_wave()
    if wave is None:
        return True
    rules = TOOL_ROUTE_PATHS.get(CONNECTOR_ALIASES.get(tool_name, tool_name))
    if not rules:
        # No rule is recorded for this tool (a generic primitive like `answer`,
        # or one with no page/API equivalent of its own): nothing in the wave
        # map holds it back.
        return True
    return all(state_for(entry_for(rule), wave) == OPEN for rule in rules)


def connector_tools() -> list[dict]:
    """The tools the connector publishes, in its wire format, sorted by name.

    Derived from the catalogue on every call: a read tool that gains the
    ``mcp`` surface appears here with no other edit. A tool that mutates or is
    not classified read never appears, whatever its surfaces say.
    """
    tools = []
    for schema in TOOL_SCHEMAS:
        if _is_connector_tool(schema) and tool_released(schema["name"]):
            tools.append({
                "name": schema["name"],
                "description": schema["description"],
                "inputSchema": schema["parameters"],
                "annotations": {
                    "title": schema["name"].replace("_", " ").capitalize(),
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
            })
    for alias, target in CONNECTOR_ALIASES.items():
        for tool in list(tools):
            if tool["name"] == target:
                tools.append({**tool, "name": alias})
    return sorted(tools, key=lambda t: t["name"])


def resolve_connector_tool(name: str) -> str | None:
    """The catalogue tool name a connector tool name maps to, or None."""
    canonical = CONNECTOR_ALIASES.get(name, name)
    for schema in TOOL_SCHEMAS:
        if schema["name"] == canonical and _is_connector_tool(schema):
            return canonical
    return None
