"""SharePoint changes go through the review path and the one role check; nothing here applies a change directly.

The save route proposes the change (``propose_connector_change``) and stops. These tests read the source of every
SharePoint file and route and fail if any of them names the direct-apply functions or hand-rolls a role check.
"""

from __future__ import annotations

import glob
import inspect
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
FORBIDDEN = (
    "propose_or_apply_connector_change",
    "org_has_delegated_action",
    "delegated=True",
    "apply_connector_change",
    "run_connector_sync",
)


def _sharepoint_files():
    files = glob.glob(os.path.join(ROOT, "app", "connectors", "sharepoint*.py"))
    files.append(os.path.join(ROOT, "app", "services", "api_clients", "graph_client.py"))
    return sorted(files)


def _sharepoint_views(app):
    views = {
        rule.endpoint: app.view_functions[rule.endpoint]
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("m365_connector.sharepoint_")
    }
    assert views, "the SharePoint routes are not registered"
    return views


def _unwrap(view):
    chain = []
    while view is not None:
        chain.append(view)
        view = getattr(view, "__wrapped__", None)
    return chain


def test_the_sharepoint_files_exist():
    assert len(_sharepoint_files()) >= 2
    assert all(os.path.exists(path) for path in _sharepoint_files())


@pytest.mark.parametrize("path", _sharepoint_files(), ids=os.path.basename)
def test_no_sharepoint_file_applies_a_change_directly(path):
    with open(path) as handle:
        source = handle.read()
    for name in FORBIDDEN:
        assert name not in source, f"{os.path.basename(path)} names {name}"


def test_no_sharepoint_route_applies_a_change_directly(app):
    for endpoint, view in _sharepoint_views(app).items():
        source = inspect.getsource(_unwrap(view)[-1])
        for name in FORBIDDEN:
            assert name not in source, f"{endpoint} names {name}"


def test_the_save_route_proposes_the_change(app):
    source = inspect.getsource(_unwrap(_sharepoint_views(app)["m365_connector.sharepoint_save"])[-1])
    assert "propose_connector_change(" in source


def test_every_sharepoint_route_is_gated_by_connections_access_and_hand_rolls_no_role_check(app):
    for endpoint, view in _sharepoint_views(app).items():
        chain = _unwrap(view)
        assert any(
            getattr(getattr(step, "__code__", None), "co_qualname", "") == "connections_access.<locals>.decorated"
            for step in chain
        ), f"{endpoint} is not decorated with connections_access"
        source = inspect.getsource(chain[-1])
        assert "is_org_admin" not in source and "is_admin" not in source, endpoint
