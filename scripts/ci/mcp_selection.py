#!/usr/bin/env python
"""The one list of tests that need the assistant connector (MCP) switched on.

The OAuth and MCP blueprints register only when MCP_ENABLED and PUBLIC_BASE_URL
are set when the app is created. An ordinary pytest session leaves them off, so
a test that needs those routes fails there with a 404 or a non-JSON reply. Every
such test is therefore tagged `mcp` here, and each session runs exactly one side:

  * MCP_ENABLED unset or false: the `mcp` tests are deselected, so the shards and
    the fast lane's ordinary session never run them;
  * MCP_ENABLED true: only the `mcp` tests run, so nothing runs twice.

Loaded for every pytest run through addopts in pytest.ini. The selection:

  * every test under MCP_DIRS, except the files in NEEDS_MCP_OFF, which assert
    the connector is absent and run in the ordinary session;
  * the individual tests in MCP_TESTS, which live in files that mostly do not
    need the connector.

CI reads the file list from here, so the workflow holds no second copy:

    python -I scripts/ci/mcp_selection.py --paths
"""
from __future__ import annotations

import os
import sys

import pytest

MCP_DIRS = (
    "app/modules/mcp/tests/",
    "app/modules/oauth_provider/tests/",
)
NEEDS_MCP_OFF = ("app/modules/oauth_provider/tests/test_mcp_flag.py",)
MCP_TESTS = (
    "tests/test_api_token_migration.py::test_migration_runs_twice_without_error",
    "tests/test_api_token_migration.py::test_downgrade_removes_the_added_columns",
    "tests/test_api_token_migration.py::test_backfill_assigns_the_users_org_and_revokes_what_it_cannot_fix",
    "tests/test_row_level_security.py::test_every_api_token_route_denies_the_other_org_under_the_runtime_role",
    "tests/test_mcp_signin_next_through_sso.py::test_password_sign_in_for_an_sso_domain_keeps_next_for_the_way_back",
    "tests/test_mcp_signin_next_through_sso.py::test_finishing_an_sso_sign_in_returns_to_the_stored_page",
    "tests/test_mcp_signin_next_through_sso.py::test_an_outside_address_stored_as_next_is_refused",
    "tests/test_mcp_signin_next_through_sso.py::test_an_administrator_keeps_next_through_the_two_factor_step",
)
MARKER = "mcp"


def enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get("MCP_ENABLED", "")).strip().lower() in ("1", "true", "yes")


def is_mcp_test(nodeid: str) -> bool:
    """True when this test needs the connector switched on."""
    nodeid = nodeid.replace("\\", "/")
    path = nodeid.split("::", 1)[0]
    if path in NEEDS_MCP_OFF:
        return False
    if path.startswith(MCP_DIRS):
        return True
    return nodeid.split("[", 1)[0] in MCP_TESTS


def selection_paths() -> list[str]:
    """Directories and files to hand to pytest for the MCP session."""
    files = sorted({t.split("::", 1)[0] for t in MCP_TESTS})
    return [d.rstrip("/") for d in MCP_DIRS] + files


# --- pytest plugin ---------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{MARKER}: needs MCP_ENABLED and PUBLIC_BASE_URL at app creation; "
        "scripts/ci/mcp_selection.py is the one list",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    on = enabled()
    keep, drop = [], []
    for item in items:
        mcp = is_mcp_test(item.nodeid)
        if mcp:
            item.add_marker(getattr(pytest.mark, MARKER))
        (keep if mcp == on else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A test that skips because the connector is off, yet is not in the list,
    fails: being in the list is how it gets run at all."""
    outcome = yield
    rep = outcome.get_result()
    if rep.skipped and MARKER not in item.keywords and call.excinfo is not None:
        if "MCP_ENABLED" in str(call.excinfo.value):
            rep.outcome = "failed"
            rep.longrepr = (
                f"{item.nodeid} skips because MCP_ENABLED is off but is not in the MCP "
                "selection; add it to MCP_DIRS or MCP_TESTS in scripts/ci/mcp_selection.py"
            )


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--paths"]:
        print("\n".join(selection_paths()))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
