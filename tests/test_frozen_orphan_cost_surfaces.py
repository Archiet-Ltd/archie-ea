"""Freeze guard for three cost surfaces that no person can reach (review rvcurfmt05, defects 2, 4 and 5).

They are frozen: nothing may include, load or call them until the freeze is lifted by a brief that wires one
through to a screen, a writer and a browser journey. The freeze record is the addendum to the scoping bucket's
ADR-003 (orphan mapping tables and fixed-value stubs). Replaces the source-shape tests that pinned their
figures, which is allowed only because these surfaces are frozen.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"

FROZEN = {
    "vendor_discovery.js": "app/static/js/dashboards/vendor_discovery.js",
    "_detail_phase_b.html": "app/templates/solutions/partials/_detail_phase_b.html",
    "_detail_ai_panel.html": "app/templates/solutions/partials/_detail_ai_panel.html",
    "solution_explainability_service": "app/modules/solutions_strategic/v2/services/solution_explainability_service.py",
    "explain_cost_estimate": None,
}

# Templates and scripts that may name a frozen file in a comment only are matched on include/script syntax.
_TEMPLATE_USE = [
    r"""\{%-?\s*(?:include|extends|import|from)\s+['"][^'"]*(?:_detail_phase_b|_detail_ai_panel)\.html""",
    r"""<script[^>]+src=[^>]*vendor_discovery\.js""",
    r"""filename\s*=\s*['"][^'"]*vendor_discovery\.js""",
    r"""url_for\([^)]*vendor_discovery\.js""",
]
_PY_USE = [
    r"""(?:from|import)\s+[\w.]*solution_explainability_service""",
    r"""\bexplain_cost_estimate\s*\(""",
]


def _files(*suffixes):
    return [p for p in APP.rglob("*") if p.is_file() and p.suffix in suffixes]


def test_frozen_surfaces_still_exist_so_the_freeze_is_about_something():
    for key, path in FROZEN.items():
        if path:
            assert (ROOT / path).exists(), "%s is gone: update the freeze record and this guard together" % path


def test_no_template_includes_or_loads_a_frozen_surface():
    hits = []
    for p in _files(".html", ".js", ".jinja"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in _TEMPLATE_USE:
            if re.search(pat, text):
                hits.append("%s matches %s" % (p.relative_to(ROOT), pat))
    assert not hits, "a frozen cost surface is wired in before the freeze is lifted: %s" % hits


def test_nothing_imports_or_calls_the_frozen_explainability_service():
    own = ROOT / FROZEN["solution_explainability_service"]
    hits = []
    for p in _files(".py"):
        if p == own:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in _PY_USE:
            if re.search(pat, text):
                hits.append("%s matches %s" % (p.relative_to(ROOT), pat))
    assert not hits, "a frozen service has a caller before the freeze is lifted: %s" % hits
