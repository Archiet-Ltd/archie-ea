"""MCP tool registry and base handler.

Every tool is a thin argument-and-response wrapper around a REST endpoint
the product already serves. The MCP layer imports no model and no service
directly — it calls the existing REST routes through Flask's test client.
"""

from __future__ import annotations

from typing import Any, Callable

TOOL_REGISTRY: dict[str, ToolHandler] = {}


class ToolHandler:
    """A registered MCP tool — name, schema, and execution callback."""

    def __init__(self, name: str, description: str, input_schema: dict,
                 annotations: dict, execute: Callable[[dict], Any]):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.annotations = annotations
        self._execute = execute

    def execute(self, arguments: dict) -> Any:
        return self._execute(arguments)


def register_tool(name: str, description: str, input_schema: dict,
                  annotations: dict | None = None):
    """Decorator to register a tool handler."""
    if annotations is None:
        annotations = {"readOnlyHint": True}

    def decorator(fn: Callable[[dict], Any]):
        TOOL_REGISTRY[name] = ToolHandler(
            name=name,
            description=description,
            input_schema=input_schema,
            annotations=annotations,
            execute=fn,
        )
        return fn

    return decorator


# Import tool modules to trigger @register_tool decorators
from app.modules.mcp.tools import lens_tools      # noqa: F401, E402
from app.modules.mcp.tools import element_tools    # noqa: F401, E402
from app.modules.mcp.tools import canvas_tools     # noqa: F401, E402