"""MCP tool registry and base handler.

Every tool is a thin argument-and-response wrapper around a REST endpoint
the product already serves. The MCP layer imports no model and no service
directly — it calls the existing REST routes through Flask's test client.
"""

from __future__ import annotations

from typing import Any, Callable

TOOL_REGISTRY: dict[str, ToolHandler] = {}


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


class ToolHandler:
    """A registered MCP tool — name, schema, and execution callback."""

    def __init__(self, name: str, description: str, input_schema: dict,
                 annotations: dict, execute: Callable[[dict], Any],
                 required_scope: str = "mcp:read"):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.annotations = annotations
        self._execute = execute
        self.required_scope = required_scope

    def execute(self, arguments: dict) -> Any:
        return scrub_credentials(self._execute(arguments))


def register_tool(name: str, description: str, input_schema: dict,
                  annotations: dict | None = None,
                  required_scope: str = "mcp:read"):
    """Decorator to register a tool handler.

    ``required_scope`` defaults to "mcp:read" — every tool registered today
    is read-only. A future write tool (an "mcp:propose" grant) passes
    ``required_scope="mcp:propose"`` explicitly; the blueprint's tools/call
    handling checks this against the calling bearer token's granted scope
    before the handler ever runs.
    """
    if annotations is None:
        annotations = {"readOnlyHint": True}

    def decorator(fn: Callable[[dict], Any]):
        TOOL_REGISTRY[name] = ToolHandler(
            name=name,
            description=description,
            input_schema=input_schema,
            annotations=annotations,
            execute=fn,
            required_scope=required_scope,
        )
        return fn

    return decorator


# Import tool modules to trigger @register_tool decorators
from app.modules.mcp.tools import lens_tools      # noqa: F401, E402
from app.modules.mcp.tools import element_tools    # noqa: F401, E402
from app.modules.mcp.tools import canvas_tools     # noqa: F401, E402