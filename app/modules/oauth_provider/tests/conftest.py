"""Collection rules for the connector tests; the shared fixtures come from app/modules/conftest.py."""

from __future__ import annotations

import os

# These tests need the connector switched on (MCP_ENABLED and PUBLIC_BASE_URL are
# read when the app is created), which the ordinary suite run leaves off. A
# dedicated CI step runs them with the switch on; in an ordinary run only the
# switched-off test is collected, and with the switch on it is left out.
_ENABLED = os.environ.get("MCP_ENABLED", "").strip().lower() in ("1", "true", "yes")
collect_ignore_glob = ["test_mcp_flag.py"] if _ENABLED else ["test_[!m]*.py", "test_mcp_[!f]*.py"]
