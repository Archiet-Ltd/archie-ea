"""Regression contracts for controls on the solution blueprint page.

These failures were reproduced directly on production at ``/solutions/32``:
Link Existing Elements and Codegen did not open, while Phase Gate Checklist
reported ``Maximum call stack size exceeded``.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "app/templates/solutions/blueprint.html"
PHASE_GATE = ROOT / "app/templates/solutions/partials/_phase_gate_checklist.html"
GOVERNANCE = ROOT / "app/templates/solutions/partials/_governance_compliance.html"
COMPLIANCE_PANEL = ROOT / "app/templates/solutions/partials/_compliance_gap_panel.html"
DASHBOARD = ROOT / "app/templates/dashboards/overview.html"


def test_blueprint_modal_roots_have_the_ids_the_modal_registry_uses():
    template = BLUEPRINT.read_text(encoding="utf-8")

    assert 'id="bp-codegen-modal"' in template
    assert 'id="bp-link-modal"' in template


def test_solution_governance_uses_an_icon_shipped_by_the_lucide_bundle():
    # the governance partial includes the compliance panel, which holds the loading spinner
    template = GOVERNANCE.read_text(encoding="utf-8") + COMPLIANCE_PANEL.read_text(encoding="utf-8")

    assert 'data-lucide="loader-circle"' not in template
    assert 'data-lucide="loader-2"' in template


def test_dashboard_cards_never_use_coloured_edge_accents():
    template = DASHBOARD.read_text(encoding="utf-8")

    assert "border-l-4" not in template


def test_blueprint_header_actions_bind_on_the_buttons_not_before_they_exist():
    template = BLUEPRINT.read_text(encoding="utf-8")

    assert "bpHdrMoreOpen = false; openSaveAsTemplate()" in template
    assert "bpHdrMoreOpen = false; archieDownloadOef(" in template
    assert "document.getElementById('bp-header-save-template-btn')" not in template
    assert "document.getElementById('btn-export-oef-{{ solution.id }}')" not in template


def test_blueprint_modal_footer_buttons_close_the_shared_modal_store():
    template = BLUEPRINT.read_text(encoding="utf-8")

    assert re.search(
        r"@click=\"open = false; \$store\.modal\.close\('bp-codegen-modal'\)\"[^>]*>\s*Close\s*</button>",
        template,
    )
    assert re.search(
        r"@click=\"open = false; \$store\.modal\.close\('bp-link-modal'\)\"[^>]*>\s*Done\s*</button>",
        template,
    )


def test_png_export_reports_when_no_diagram_renderer_exists():
    script = (ROOT / "app/static/js/solutions/blueprint.js").read_text(encoding="utf-8")

    assert "No diagram is available to export" in script
