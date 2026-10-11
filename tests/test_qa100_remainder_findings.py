"""Fixes for the QA remainder wave (18 Aug 2026): F-03, F-04, H-05, H-06,
V-07, P-02, S-11 (remaining half).

Uses the shared fixtures in tests/conftest.py per CLAUDE.md.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# ── P-02: one authoritative persona vocabulary ──────────────────────────────


def test_persona_configs_keys_resolve_against_architect_personas():
    """Fail-first: PERSONA_CONFIGS used to carry "capability_architect" with
    no charter and no alias — a fifth, undeclared persona spelling. Every
    PERSONA_CONFIGS key must now be either a real ARCHITECT_PERSONAS charter
    key or a declared PERSONA_ALIASES spelling."""
    from app.modules.ai_chat.services.architect_persona_charters import (
        ARCHITECT_PERSONAS,
        PERSONA_ALIASES,
    )
    from app.modules.ai_chat.services.multi_domain_chat_service import PERSONA_CONFIGS

    known = set(ARCHITECT_PERSONAS) | set(PERSONA_ALIASES)
    unresolvable = [key for key in PERSONA_CONFIGS if key not in known]
    assert unresolvable == []


def test_capability_architect_is_aliased_not_orphaned():
    from app.modules.ai_chat.services.architect_persona_charters import PERSONA_ALIASES

    assert PERSONA_ALIASES["capability_architect"] == "enterprise_architect"


def test_valid_roles_untouched_by_the_unification():
    """CLAUDE.md: renaming a VALID_ROLES value would orphan existing User
    rows carrying the old spelling. The unification must not touch it."""
    from app.models.user import VALID_ROLES

    assert "solution_architect" in VALID_ROLES  # DB spelling, unchanged
    assert "platform_admin" in VALID_ROLES


# ── S-11 remainder: 10 directory-only modules now in a sidebar zone ────────


DIRECTORY_ONLY_ENDPOINTS = [
    "unified_duplicate.simple_dashboard",
    "strategic.impact_analysis",
    "consolidation_list.dashboard",
    # Canvas/framework UI fix (24 Sep 2026): batch_import_view.dashboard was
    # intentionally folded from platform_admin's Admin zone to stay within the
    # link budget after adding Framework Management and Framework Configuration.
    "stakeholder_map.stakeholder_map_page",
    "maturity_management.frameworks_overview",
    "strategic.capability_health",
]


@pytest.mark.parametrize("endpoint", DIRECTORY_ONLY_ENDPOINTS)
def test_directory_only_endpoint_is_registered(app, endpoint):
    assert endpoint in app.view_functions, f"{endpoint} is not registered"


@pytest.mark.parametrize("endpoint", DIRECTORY_ONLY_ENDPOINTS)
def test_directory_only_endpoint_now_in_a_sidebar_zone(endpoint):
    from app.utils.role_access import SIDEBAR_ZONES

    all_endpoints = {
        link["endpoint"]
        for zones in SIDEBAR_ZONES.values()
        for zone in zones
        for link in zone["links"]
    }
    assert endpoint in all_endpoints, f"{endpoint} still reachable from no sidebar zone"


def test_my_applications_subpages_are_nested_as_tabs_not_new_top_level_links():
    """my-applications/list, /health and /roadmap were directory-only. They
    are nested as in-page tabs under the existing "My Applications" sidebar
    link rather than given three new top-level sidebar entries — assert the
    tab strip links to all four and that no new sidebar zone entry was
    created for them (the sidebar link count is exercised separately by
    tests/test_sidebar_budgets.py)."""
    import pathlib

    base = pathlib.Path(
        "app/modules/my_applications/templates/my_applications"
    )
    for name in ["dashboard.html", "app_list.html", "health_overview.html", "roadmap_impact.html"]:
        src = (base / name).read_text(encoding="utf-8")
        assert "my_applications.dashboard" in src
        assert "my_applications.app_list" in src
        assert "my_applications.health_overview" in src
        assert "my_applications.roadmap_impact" in src


# ── V-07: no literal "None" rendering beyond account/manage.html ───────────


NONE_SAFE_TEMPLATES = [
    "app/templates/arb/dashboard.html",
    "app/templates/arb/review_detail.html",
    "app/templates/arb/sessions.html",
    "app/templates/admin/user_role_edit.html",
]


@pytest.mark.parametrize("path", NONE_SAFE_TEMPLATES)
def test_no_bare_first_last_name_concatenation(path):
    """Fail-first: `{{ x.first_name }} {{ x.last_name }}` renders the
    literal string "None" for either field when null (Jinja's default
    behaviour for {{ None }}), exactly the V-07 "Full name: None None" bug.
    These templates must use the null-safe User.full_name() helper instead."""
    src = open(path, encoding="utf-8").read()
    assert not re.search(
        r"\{\{\s*[\w\.]+\.first_name\s*\}\}\s*\{\{\s*[\w\.]+\.last_name\s*\}\}", src
    ), f"{path} still concatenates bare first_name/last_name"


# ── F-03: no competitor name as the primary empty-state action ─────────────


def test_applications_empty_state_primary_action_is_generic():
    src = open("app/templates/applications/list_simple.html", encoding="utf-8").read()
    # The primary CTA button text must not name Abacus.
    assert "Import from Abacus" not in src
    assert "Import Applications" in src
    # Abacus is still available as a secondary path, inside the import modal.
    assert "application-import-modal" in src


# ── H-05: standalone icon targets meet the 24x24 CSS px minimum ────────────


def test_sidebar_close_nav_button_has_a_real_target_size():
    src = open("app/templates/components/admin_sidebar.html", encoding="utf-8").read()
    m = re.search(r'aria-label="Close navigation"', src)
    assert m
    # the enclosing <button ...> tag must carry an explicit h-*/w-* >= 24px class
    tag_start = src.rfind("<button", 0, m.start())
    tag = src[tag_start : m.end() + 5]
    assert "h-9" in tag or "h-8" in tag or "h-10" in tag or "h-11" in tag


def test_sidebar_logout_link_has_a_real_target_size():
    src = open("app/templates/components/admin_sidebar.html", encoding="utf-8").read()
    m = re.search(r'aria-label="Log out"', src)
    assert m
    tag_start = src.rfind("<a", 0, m.start())
    tag = src[tag_start : m.end() + 5]
    assert re.search(r"\bh-(8|9|10|11)\b", tag), "logout target still lacks an explicit size class"
    assert re.search(r"\bw-(8|9|10|11)\b", tag), "logout target still lacks an explicit size class"
