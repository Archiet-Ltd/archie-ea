"""Routes that exist in the source must exist in the URL map.

Where two Flask blueprints share a name, only one can win: Flask 3 rejects the
second registration, ``app/_bootstrap/blueprints.py`` catches it non-fatally, and
the loser's routes vanish with nothing raised. Boot-health still sees a registered
blueprint of that name, so the page simply 404s. See the "blueprint name
collisions" section of ``docs/known-issues/unreachable-pages.md``.

Two pages were reachable in the source and not on the web:

* ``/admin/security`` — its blueprint was declared and imported by nothing at all.
* ``/dashboard/capability-heatmap`` — served by dashboard v1, dropped by the v2
  rewrite that superseded it, with the template and its API both still live.

Database-free, like ``tests/test_boot_health.py``: everything here is url_map.
"""

import os

import pytest


@pytest.fixture(scope="module")
def booted_app():
    os.environ.setdefault("FLASK_CONFIG", "testing")
    from app import create_app

    return create_app()


def _endpoint_for(app, rule, method="GET"):
    for r in app.url_map.iter_rules():
        if str(r) == rule and method in r.methods:
            return r.endpoint
    return None


def test_admin_security_page_is_reachable(booted_app):
    assert _endpoint_for(booted_app, "/admin/security") == (
        "admin_security.security_dashboard"
    ), "the /admin/security route exists in app/modules/admin/security_routes.py"


def test_admin_security_blueprint_name_is_distinct(booted_app):
    """It must not be named 'security' — app/routes/security_api.py owns that."""
    assert "admin_security" in booted_app.blueprints
    assert booted_app.blueprints["security"].import_name.endswith("security_api")


def test_capability_heatmap_page_is_reachable(booted_app):
    assert _endpoint_for(booted_app, "/dashboard/capability-heatmap") == (
        "dashboard_pages.capability_heatmap_page"
    ), "the address stays registered: it answers 302 to the capability map (see test_capability_heatmap_honesty)"


def test_capability_heatmap_api_is_reachable(booted_app):
    """The page's investment view mode is useless without the API behind it."""
    assert _endpoint_for(booted_app, "/dashboard/api/capability-heatmap") == (
        "dashboard_pages.api_capability_heatmap"
    )


@pytest.mark.parametrize(
    "rule",
    [
        # capability_map: app/routes/capability_map_routes.py is a superseded
        # duplicate of app/modules/capabilities/routes/*. Every one of its 24
        # endpoints is served by the winner at the same URL — these four are the
        # pages, and they are the ones a user would notice.
        "/capability-map/dashboard",
        "/capability-map/hierarchy",
        "/capability-map/network",
        "/capability-map/simple",
        # deprecation and sidebar_mgmt: admin v1 vs v2, chosen by
        # USE_ADMIN_GUARDRAILS. v2 wins and serves every v1 URL.
        "/admin/deprecation/",
        "/api/admin/sidebar/items",
    ],
)
def test_superseded_blueprints_lose_nothing(booted_app, rule):
    assert _endpoint_for(booted_app, rule) is not None, (
        f"{rule} is declared by a blueprint whose name is taken by another; "
        "the winner is supposed to serve it"
    )
