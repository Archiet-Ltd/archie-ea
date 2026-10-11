"""MCP Streamable HTTP endpoint — read-only lens tools.

Ten tools wrapping existing REST endpoints:
  ask_impact, ask_strategy, ask_portfolio, ask_programme, ask_risk,
  ask_accountability, search_elements, get_element, list_canvases, get_canvas
"""

from app.modules.mcp.blueprint import mcp_bp

__all__ = ["mcp_bp"]