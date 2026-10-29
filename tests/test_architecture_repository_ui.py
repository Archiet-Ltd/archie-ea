"""User-visible contract for the shared Architecture Repository workspace.

These tests pin the presentation boundary that all ArchiMate layers share.  The
Motivation layer is the production acceptance case, but the template and client
controller must remain one implementation for every configured layer.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "app" / "templates" / "archimate_crud" / "dashboard.html"
WORKSPACE_PATH = ROOT / "app" / "templates" / "archimate_crud" / "partials" / "_repository_workspace.html"
SCRIPT_PATH = ROOT / "app" / "static" / "js" / "archimate_crud" / "dashboard.js"


@pytest.fixture
def client(app):
    previous = app.config.get("LOGIN_DISABLED", False)
    app.config["LOGIN_DISABLED"] = True
    try:
        yield app.test_client()
    finally:
        app.config["LOGIN_DISABLED"] = previous


def _dashboard_context(client, query: str = "") -> dict:
    from app.modules.architecture.routes.archimate_crud import routes as routes_module

    captured: dict = {}

    def fake_render(template_name, **context):
        captured["template"] = template_name
        captured.update(context)
        return ""

    with patch.object(routes_module, "render_template", fake_render):
        response = client.get("/architecture/dashboard" + query)
    assert response.status_code == 200
    return captured


def _template() -> str:
    """The repository screen as a user meets it: the page plus its workspace partial.

    The header used to live inside _repository_workspace.html; it now sits in
    dashboard.html so the page owns its own header like every other screen, and it
    renders through the page_shell() macro rather than a literal <h1>. So the whole
    dashboard is included here, not just the trailing panel region.
    """
    return (
        TEMPLATE_PATH.read_text(encoding="utf-8")
        + "\n"
        + WORKSPACE_PATH.read_text(encoding="utf-8")
    )


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_motivation_route_selects_layer_specific_repository_heading(client):
    """A generic heading must not hide which architecture domain is open."""
    context = _dashboard_context(client, "?layer=motivation")

    assert context["template"] == "archimate_crud/dashboard.html"
    assert context["selected_layer"]["key"] == "motivation"
    assert context["selected_layer"]["title"] == "Motivation Architecture"
    assert "Goals, drivers, requirements and constraints" in context["selected_layer"]["description"]

    template = _template()
    # The heading now renders through page_shell(), so there is no literal <h1>
    # in the source. What this test guards is unchanged: exactly ONE heading
    # source, fed by the selected layer, with no competing hard-coded title.
    assert template.count("<h1") == 0, "the heading must come from page_shell, not a literal <h1>"
    assert template.count("page_shell(") == 1
    assert "title=selected_layer.title" in "".join(template.split())
    assert "Architecture Elements</h1>" not in template
    assert "{% block breadcrumb %}" not in template

    default_context = _dashboard_context(client)
    assert default_context["selected_layer"]["key"] == "motivation"


def test_rendered_workspace_keeps_primary_controls_and_modal_targets(client):
    """The new shell must not hide the existing CRUD and AI modal DOM."""
    response = client.get("/architecture/dashboard?layer=motivation")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert html.count("<h1") == 1
    assert html.count('aria-label="Breadcrumb"') == 1
    assert 'data-testid="btn-create-element"' in html
    assert 'id="archimate-form-modal"' in html
    # Generate with AI sits in the header's More menu
    assert 'data-testid="dropdown-ai-generate-element"' in html
    assert 'data-testid="ai-generate-modal"' in html
    assert 'data-testid="detail-edit-btn"' in html


def test_layer_spine_names_every_layer_and_exposes_selection():
    """Removing a layer, its name, or aria-current would break keyboard orientation."""
    template = _template()
    from app.modules.architecture.routes.archimate_crud.routes import (
        LAYER_CONFIG,
        LAYER_PRESENTATION,
    )

    assert 'data-testid="layer-spine"' in template
    assert 'aria-current="page"' in template
    assert 'aria-label="Open {{ layer_info.title }}"' in template
    assert 'aria-label="Action"' not in template
    assert "layer_info.color_token" in template
    for key in LAYER_CONFIG:
        assert LAYER_PRESENTATION[key]["color_token"].startswith("--layer-")


def test_actions_and_data_surface_follow_the_applications_hierarchy():
    """The repository gets one primary action and a wrapping, neutral data surface.

    The action buttons now render through the button() macro, which turns its
    ``testid`` argument into ``data-testid`` at render time — so the source-level
    declaration is ``testid='...'``, and the guaranteed rendered testid follows
    from the macro contract. Each action is still declared exactly once.
    """
    template = _template()

    assert template.count("testid='btn-create-element'") == 1
    for test_id in (
        "btn-import-oef",
        "btn-export-oef",
        "btn-run-validation",
        "btn-repository-health",
        "btn-vendor-templates",
        "btn-ai-generate-element",
    ):
        # the secondary actions sit in the header's More menu as dropdown-<name>
        assert f"testid='{test_id}'" in template or f"'dropdown-{test_id[4:]}'" in template, test_id
    assert 'data-testid="architecture-data-card"' in template
    assert 'data-testid="architecture-filter-bar"' in template
    assert 'data-testid="filter-search"' in template
    assert 'data-testid="filter-type"' in template
    assert 'data-testid="filter-source"' in template
    assert 'data-testid="filter-viewpoint"' in template
    assert "h-10" not in template


def test_loading_error_true_empty_and_filtered_empty_are_distinct():
    """A request failure must never be presented as an empty system of record."""
    template = _template()

    for test_id in (
        "architecture-loading-state",
        "architecture-error-state",
        "architecture-true-empty-state",
        "architecture-filtered-empty-state",
    ):
        assert template.count(f'data-testid="{test_id}"') == 1
    assert "Retry loading architecture elements" in template
    assert "Create element" in template
    assert "Clear filters" in template


def test_desktop_table_and_mobile_cards_expose_equivalent_repository_facts():
    """Mobile users must retain provenance, documentation, relationships and actions."""
    template = _template()
    desktop = template.split('data-testid="architecture-elements-table"', 1)[1].split(
        'data-testid="architecture-mobile-cards"', 1
    )[0]
    mobile = template.split('data-testid="architecture-mobile-cards"', 1)[1].split(
        'data-testid="architecture-pagination"', 1
    )[0]

    for field in ("element-name", "element-type", "element-source", "element-documentation", "element-relationships", "element-actions"):
        marker = f'data-field="{field}"'
        assert marker in desktop
        assert marker in mobile
    assert 'aria-label="Sort architecture elements by name"' in desktop
    assert 'aria-label="Sort architecture elements by type"' in desktop


def test_motivation_vocabulary_is_complete_and_not_application_specific():
    """The shared shell must not collapse Motivation into portfolio semantics."""
    from app.modules.architecture.routes.archimate_crud.routes import LAYER_CONFIG

    assert LAYER_CONFIG["motivation"]["elements"] == [
        "Stakeholder",
        "Driver",
        "Assessment",
        "Goal",
        "Outcome",
        "Principle",
        "Requirement",
        "Constraint",
        "Meaning",
        "Value",
    ]
    template = _template()
    assert "Lifecycle Status" not in template
    assert "Application Criticality" not in template


def test_repository_state_is_url_backed_and_restores_on_history_navigation():
    """Removing URL sync would break bookmarks and browser back/forward."""
    script = _script()

    assert "syncUrlState()" in script
    assert "restoreUrlState()" in script
    assert "window.addEventListener('popstate'" in script
    assert "window.history.pushState" in script
    for key in ("layer", "search", "element_type", "source", "viewpoint", "page", "per_page", "sort_by", "sort_order"):
        assert re.search(rf"(?:get|set)\(['\"]{key}['\"]", script), key


def test_unavailable_layer_counts_are_not_fabricated_as_zero():
    """A malformed count response is unknown, not a measured empty layer."""
    script = _script()

    assert "payload.total || 0" not in script
    assert "(d2.pagination && d2.pagination.total) || 0" not in script
    assert "typeof payload.total === 'number'" in script
