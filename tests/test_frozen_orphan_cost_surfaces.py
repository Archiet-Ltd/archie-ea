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


_BARE_NAMES = ["_detail_phase_b", "_detail_ai_panel", "vendor_discovery.js", "solution_explainability_service", "explain_cost_estimate"]
_COMMENT_BLOCK = [r"\{#.*?#\}", r"/\*.*?\*/", r"<!--.*?-->"]
# Line comments must not use re.S: with it `.*` runs to the end of the file and blinds the guard.
_COMMENT_LINE = [r"(?m)^[ \t]*//[^\n]*$", r"(?m)^[ \t]*#[^\n]*$"]


def _frozen_paths():
    return {ROOT / v for v in FROZEN.values() if v}


def _strip_comments(text):
    for pat in _COMMENT_BLOCK:
        text = re.sub(pat, "", text, flags=re.S)
    for pat in _COMMENT_LINE:
        text = re.sub(pat, "", text)
    return text


def find_bare_name_references(files):
    """Any mention of a frozen name outside comments, so an include through a variable is caught too."""
    own = _frozen_paths()
    hits = []
    for p in files:
        if p in own:
            continue
        text = _strip_comments(p.read_text(encoding="utf-8", errors="ignore"))
        for name in _BARE_NAMES:
            if name in text:
                hits.append("%s names %s" % (p.name, name))
    return hits


def test_no_bare_reference_to_a_frozen_surface_under_app():
    hits = find_bare_name_references(_files(".html", ".js", ".jinja", ".py"))
    assert not hits, "a frozen cost surface is named outside comments (an indirect include?): %s" % hits


def test_bare_name_guard_catches_an_include_through_a_variable(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("{% set p = 'solutions/partials/_detail_phase_b.html' %}{% include p %}")
    ok = tmp_path / "ok.html"
    ok.write_text("{# _detail_phase_b is frozen #}")
    assert find_bare_name_references([f]) == ["page.html names _detail_phase_b"]
    assert find_bare_name_references([ok]) == []


_USE_AFTER_COMMENT = {
    "a.py": "# helper\nfrom app.x import solution_explainability_service\n",
    "b.js": "// load panel\nconst s = 'js/dashboards/' + 'vendor_discovery.js';\n",
    "c.html": "<script>\n  // init\n</script>\n{% set p = 'solutions/partials/_detail_ai_panel.html' %}{% include p %}\n",
    "d.html": "<style>\n#x { color: red }\n</style>\n{% include ['a.html', 'solutions/partials/_detail_phase_b.html'] %}\n",
}


def test_bare_name_guard_sees_a_use_placed_below_a_comment_line(tmp_path):
    for name, body in _USE_AFTER_COMMENT.items():
        f = tmp_path / name
        f.write_text(body)
        assert find_bare_name_references([f]), "guard blinded by a comment line in %s" % name


def test_bare_name_guard_still_ignores_a_name_only_in_comments(tmp_path):
    f = tmp_path / "e.py"
    f.write_text("# solution_explainability_service is frozen\nx = 1\n")
    g = tmp_path / "f.html"
    g.write_text("{#\n  _detail_ai_panel is frozen\n#}\n")
    assert find_bare_name_references([f, g]) == []
