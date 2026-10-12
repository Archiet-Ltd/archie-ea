"""Collection rules for the connector tests; the shared fixtures come from app/modules/conftest.py."""

from __future__ import annotations

import os

# Needs the connector switched on at app creation; a dedicated CI step runs these
# with MCP_ENABLED and PUBLIC_BASE_URL set, so the ordinary suite run skips them.
_ENABLED = os.environ.get("MCP_ENABLED", "").strip().lower() in ("1", "true", "yes")
collect_ignore_glob = [] if _ENABLED else ["test_*.py"]
