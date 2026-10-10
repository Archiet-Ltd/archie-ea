"""Reuse the shared fixtures from tests/conftest.py for this module's tests."""

from __future__ import annotations

import os

from tests.conftest import (  # noqa: F401
    _schema,
    app,
    client,
    db_session,
    login_as,
    make_org,
    tenant_ctx,
)

# Needs the connector switched on at app creation; a dedicated CI step runs these
# with MCP_ENABLED and PUBLIC_BASE_URL set, so the ordinary suite run skips them.
_ENABLED = os.environ.get("MCP_ENABLED", "").strip().lower() in ("1", "true", "yes")
collect_ignore_glob = [] if _ENABLED else ["test_*.py"]
