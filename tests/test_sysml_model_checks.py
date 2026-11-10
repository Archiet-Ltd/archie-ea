"""The system model's consistency checks, each shown to catch the failure it exists for.

These tests need no Java: they feed the checks plain data, a small throw-away code tree, and a throw-away git
repository. The command that parses the real model with the SysML v2 parser is in model/README.md.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "sysml"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import as_built  # noqa: E402
import component_inventory as ci  # noqa: E402
import model_check as mc  # noqa: E402
import sysml_model  # noqa: E402

BUILT_OLD = {"endpoint": "legacy.update_overview", "rule": "/applications/<int:id>/overview-update", "methods": "POST",
             "file": "app/legacy/legacy_save.py", "line": "197"}
BUILT_NEW = {"endpoint": "unified.update_overview", "rule": "/<int:id>/overview-update", "methods": "POST",
             "file": "app/unified/unified_save.py", "line": "31"}
WANT = {"endpoint": "unified.update_overview", "rule": "/applications/<int:id>/overview-update"}


# ------------------------------------------------------------------------------------------ check 1
def test_two_handlers_for_one_save_route_are_reported_with_both_places():
    found = mc.check_one_definition([WANT], [BUILT_OLD, BUILT_NEW])
    assert len(found) == 1
    assert "app/legacy/legacy_save.py:197" in found[0] and "unified.update_overview" in found[0]


def test_one_handler_for_the_route_is_clean_and_a_missing_one_is_reported():
    assert mc.check_one_definition([WANT], [BUILT_NEW]) == []
    missing = mc.check_one_definition([WANT], [])
    assert len(missing) == 1 and "no such handler" in missing[0]


# ------------------------------------------------------------------------------------------ check 2
def test_a_function_must_go_to_exactly_one_part_and_a_part_must_exist(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    parts = {"p::one": {"name": "one", "sourcePath": "a.py"}, "p::two": {"name": "two", "sourcePath": "gone.py"}}
    found = mc.check_allocation(
        ["p::f_none", "p::f_two", "p::f_ok"],
        [("p::f_two", "p::one"), ("p::f_two", "p::two"), ("p::f_ok", "p::one")],
        parts, tmp_path)
    text = "\n".join(found)
    assert "f_none is allocated to 0 parts" in text
    assert "f_two is allocated to 2 parts" in text
    assert "gone.py, which does not exist" in text
    assert "f_ok" not in text and len(found) == 3


# ------------------------------------------------------------------------------------------ check 3
def test_every_reader_of_a_list_must_read_the_one_the_model_names(tmp_path):
    constraints = {"c": {"column": "deployment_status", "canonicalSource": "app/status.py"}}
    sources = [
        {"column": "deployment_status", "sourceKind": "form-select", "sourceFile": "app/forms.py:9",
         "origin": "app/forms.py (written out in place)", "values": "planned,live"},
        {"column": "deployment_status", "sourceKind": "tool-enum", "sourceFile": "app/tool.py:4",
         "origin": "app/status.py", "values": ""},
        {"column": "other_column", "sourceKind": "form-select", "sourceFile": "app/other.py:1",
         "origin": "app/other.py (free text)", "values": ""},
    ]
    found = mc.check_one_source(constraints, sources, tmp_path)
    assert len(found) == 1 and "app/forms.py:9" in found[0] and "planned,live" in found[0]


# ------------------------------------------------------------------------------------------ check 4
def _req(i, parent, **kw):
    base = {"id": i, "text": "The product shall do one thing.", "parent": parent, "ruleLine": "4", "job": "1", "ownerRole": "x",
            "rationale": "Because.", "asked": "", "method": "test"}
    base.update(kw)
    return base


def test_a_requirement_needs_a_part_a_test_that_exists_and_a_parent_that_resolves(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_present():\n    pass\n")
    reqs = {
        "n": _req("NEED-X", ""),
        "r": _req("RR-4", "NEED-X"),
        "ok": _req("REQ-OK", "RR-4"),
        "nopart": _req("REQ-NOPART", "RR-4"),
        "notest": _req("REQ-NOTEST", "RR-4"),
        "nofile": _req("REQ-NOFILE", "RR-4"),
        "noname": _req("REQ-NONAME", "RR-4"),
        "orphan": _req("REQ-ORPHAN", "RR-99"),
    }
    sat = [("ok", "p"), ("notest", "p"), ("nofile", "p"), ("noname", "p"), ("orphan", "p")]
    cases = {
        "c1": {"testFile": "test_a.py", "testName": "test_present", "verifies": ["ok", "nopart", "orphan"]},
        "c2": {"testFile": "gone.py", "testName": "test_x", "verifies": ["nofile"]},
        "c3": {"testFile": "test_a.py", "testName": "test_absent", "verifies": ["noname"]},
    }
    found, cover = mc.check_satisfied_and_verified(reqs, sat, cases, tmp_path)
    text = "\n".join(found)
    assert "REQ-NOPART: no part satisfies it" in text
    assert "REQ-NOTEST: no test verifies it" in text
    assert "REQ-NOFILE: its test file gone.py does not exist" in text
    assert "REQ-NONAME: test_a.py has no test named test_absent" in text
    assert "REQ-ORPHAN: its parent RR-99 is not in the model" in text
    assert "REQ-OK" not in text
    assert cover["without_requirement"] == []


def test_a_requirement_text_has_one_shall_and_no_vague_words(tmp_path):
    (tmp_path / "t.py").write_text("def test_x():\n    pass\n")
    reqs = {
        "n": _req("NEED-X", ""),
        "r": _req("RR-4", "NEED-X"),
        "none": _req("REQ-NONE", "RR-4", text="Saving works."),
        "two": _req("REQ-TWO", "RR-4", text="The product shall save and shall show."),
        "vague": _req("REQ-VAGUE", "RR-4", text="The product shall save forms, etc."),
        "good": _req("REQ-GOOD", "RR-4"),
    }
    sat = [(k, "p") for k in ("none", "two", "vague", "good")]
    cases = {"c": {"testFile": "t.py", "testName": "test_x", "verifies": ["none", "two", "vague", "good"]}}
    found, _ = mc.check_satisfied_and_verified(reqs, sat, cases, tmp_path)
    text = "\n".join(found)
    assert all(k in text for k in ("REQ-NONE", "REQ-TWO", "REQ-VAGUE")) and "REQ-GOOD" not in text


def test_a_release_rule_line_with_no_requirement_under_it_is_counted():
    reqs = {"n": _req("NEED-X", ""), "a": _req("RR-1", "NEED-X"), "b": _req("RR-2", "NEED-X"), "c": _req("REQ-A", "RR-1")}
    _, cover = mc.check_satisfied_and_verified(reqs, [], {}, Path("."))
    assert cover == {"rule_lines": 2, "without_requirement": ["RR-2"]}


# ------------------------------------------------------------------------------------- screen events
def _ifml(endpoint, route, dynamic=False):
    return {"viewContainers": [{"template": "t.html", "events": [{
        "component": "f", "line": 7, "action": {"endpoint": endpoint, "dynamic": dynamic, "route": route}}]}]}


def test_a_save_wired_to_a_second_route_is_reported_at_its_line():
    legacy = {"rule": "/applications/<int:id>/overview-update", "file": "app/legacy/legacy_save.py", "line": 197}
    found = mc.check_screen_events(_ifml("legacy.update_overview", legacy), [WANT])
    assert len(found) == 1 and found[0].startswith("t.html:7") and "unified.update_overview" in found[0]
    assert mc.check_screen_events(_ifml("unified.update_overview", legacy), [WANT]) == []


def test_an_event_to_no_route_is_reported_and_a_run_time_address_is_not():
    assert "not a route in the code" in mc.check_screen_events(_ifml("gone.thing", None), [WANT])[0]
    assert mc.check_screen_events(_ifml(None, None, dynamic=True), [WANT]) == []


# ------------------------------------------------------------------------------------- the generator
def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _inventory(root: Path, modules: dict[str, str], register: str, templates: dict[str, str] | None = None) -> dict:
    """The inventory of a small Flask app written under `root`: each module is imported from its own file, `register`
    runs against the app, and the routes and the template controls come from the inventory's own functions."""
    from flask import Flask

    app = Flask("tiny", static_folder=None)
    loaded = {}
    for i, (rel, body) in enumerate(modules.items()):
        _write(root, rel, body)
        spec = importlib.util.spec_from_file_location(f"tiny_{root.name}_{i}", root / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        loaded[Path(rel).stem] = mod
    exec(register, {"app": app, **loaded})
    routes, _ = ci.route_entries(app, {}, {}, {}, root=root)
    controls = []
    for rel, src in (templates or {}).items():
        controls += ci.controls_of_source(rel, src)
    return {"routes": routes, "controls": controls}


LEGACY = (
    "from flask import Blueprint\n"
    "legacy_bp = Blueprint('legacy', __name__)\n"
    "@legacy_bp.route('/applications/<int:id>/overview-update', methods=['POST'])\n"
    "def update_overview(id):\n    return ''\n"
)
UNIFIED = (
    "from flask import Blueprint\n"
    "unified_bp = Blueprint('unified', __name__)\n"
    "@unified_bp.route('/<int:id>/overview-update', methods=['POST'])\n"
    "def update_overview(id):\n    return ''\n"
)
SCOPE = {"slice": "demo", "routeTails": ["overview-update"], "screens": ["app/templates/screen.html"], "vocabularies": []}
WANT_UNIFIED = {"endpoint": "unified.update_overview", "rule": "/x/overview-update"}


def test_the_generator_finds_both_handlers_and_the_form_that_calls_one(tmp_path):
    screen = ("<form id=\"edit\" method=\"POST\" action=\"{{ url_for('legacy.update_overview', id=1) }}\"></form>\n"
              "<form method=\"POST\" action=\"{{ row.delete_url }}\"></form>\n")
    inv = _inventory(tmp_path, {"app/legacy/legacy_save.py": LEGACY, "app/unified/unified_save.py": UNIFIED},
                     "app.register_blueprint(legacy_save.legacy_bp, url_prefix='/l')\napp.register_blueprint(unified_save.unified_bp, url_prefix='/u')\n", {"app/templates/screen.html": screen})
    sysml, ifml = as_built.generate(tmp_path, SCOPE, inv)
    assert "port 'legacy.update_overview'" in sysml and "port 'unified.update_overview'" in sysml
    events = json.loads(ifml)["viewContainers"][0]["events"]
    assert events[0]["line"] == 1 and events[0]["action"]["endpoint"] == "legacy.update_overview"
    assert events[0]["action"]["route"]["file"] == "app/legacy/legacy_save.py" and events[0]["action"]["route"]["line"] == 4
    assert events[1]["action"]["dynamic"] is True and events[1]["line"] == 2
    found = mc.check_one_definition([WANT_UNIFIED], as_built.routes_of(inv))
    assert len(found) == 1 and "app/legacy/legacy_save.py:4" in found[0]


def test_the_generator_gives_the_same_files_every_time(tmp_path):
    inv = _inventory(tmp_path, {"app/legacy/legacy_save.py": LEGACY}, "app.register_blueprint(legacy_save.legacy_bp)\n",
                     {"app/templates/screen.html": "<form action=\"{{ url_for('legacy.update_overview', id=1) }}\"></form>"})
    assert as_built.generate(tmp_path, SCOPE, inv) == as_built.generate(tmp_path, SCOPE, inv)


def test_routes_added_by_add_url_rule_and_by_an_attribute_blueprint_reach_check_1(tmp_path):
    modules = {
        "app/plain.py": (
            "def plain_save(id):\n    return ''\n"
        ),
        "app/holder.py": (
            "from flask import Blueprint\n"
            "class Holder:\n    bp = Blueprint('held', __name__)\n"
            "h = Holder()\n"
            "@h.bp.route('/<int:id>/overview-update', methods=['POST'])\n"
            "def held_save(id):\n    return ''\n"
        ),
    }
    register = (
        "app.add_url_rule('/legacy/<int:id>/overview-update', 'plain_save', plain.plain_save, methods=['POST'])\n"
        "app.register_blueprint(holder.h.bp, url_prefix='/held')\n"
    )
    inv = _inventory(tmp_path, modules, register)
    routes = as_built.routes_of(inv)
    assert {r["endpoint"] for r in routes} == {"plain_save", "held.held_save"}
    assert {r["file"] for r in routes} == {"app/plain.py", "app/holder.py"}
    found = [f for f in mc.check_one_definition([{"endpoint": "unified.update_overview", "rule": "/u/overview-update"}], routes)
             if "also handles" in f]
    assert len(found) == 2 and any("app/plain.py:1" in f for f in found) and any("app/holder.py:6" in f for f in found)


def test_a_route_with_no_handler_file_is_listed_as_unresolved_never_as_clean(tmp_path):
    inv = _inventory(tmp_path, {"app/plain.py": "x = 1\n"},
                     "app.add_url_rule('/legacy/overview-update', 'gone', lambda: '', methods=['POST'])\n"
                     "app.add_url_rule('/legacy/other', 'builtin', len)\n")
    routes = as_built.routes_of(inv)
    named = {r["endpoint"]: r for r in routes}
    assert named["gone"]["file"] is None and named["builtin"]["file"] is None
    listed = mc.check_unresolved(routes)
    assert len(listed) == 2 and any(line.startswith("gone /legacy/overview-update") for line in listed)
    # the unresolved handler of the save route is still a second definition: it is named, without a file and line
    second = [f for f in mc.check_one_definition([{"endpoint": "unified.update_overview", "rule": "/u/overview-update"}], routes)
              if "also handles" in f]
    assert len(second) == 1 and "handler place not found" in second[0] and "gone also handles" in second[0]


def test_two_files_that_name_their_blueprint_variable_the_same_keep_their_own_blueprint_names(tmp_path):
    body = ("from flask import Blueprint\n"
            "shared_bp = Blueprint('{n}', __name__)\n"
            "@shared_bp.route('/save', methods=['POST'])\n"
            "def save():\n    return ''\n")
    inv = _inventory(tmp_path, {"app/a/routes_a.py": body.format(n="first"), "app/b/routes_b.py": body.format(n="second")},
                     "app.register_blueprint(routes_a.shared_bp)\napp.register_blueprint(routes_b.shared_bp)\n")
    assert sorted((r["endpoint"], r["file"]) for r in as_built.routes_of(inv)) == [
        ("first.save", "app/a/routes_a.py"), ("second.save", "app/b/routes_b.py")]


def test_a_button_formaction_is_an_event_like_a_form_action_and_is_named_when_it_calls_the_second_route(tmp_path):
    screen = ("<form id=\"edit\" method=\"POST\" action=\"{{ url_for('unified.update_overview', id=1) }}\">\n"
              "  <button type=\"submit\" formaction=\"{{ url_for('legacy.update_overview', id=1) }}\">Save</button>\n"
              "</form>\n")
    inv = _inventory(tmp_path, {"app/legacy/legacy_save.py": LEGACY, "app/unified/unified_save.py": UNIFIED},
                     "app.register_blueprint(legacy_save.legacy_bp, url_prefix='/l')\napp.register_blueprint(unified_save.unified_bp, url_prefix='/u')\n", {"app/templates/screen.html": screen})
    sysml, ifml = as_built.generate(tmp_path, SCOPE, inv)
    data = json.loads(ifml)
    ids = [e["id"] for e in data["viewContainers"][0]["events"]]
    assert ids == ["edit.submit", "edit.formaction-1"]
    found = mc.check_screen_events(data, [WANT_UNIFIED])
    assert len(found) == 1 and found[0].startswith("app/templates/screen.html:1") and "legacy.update_overview" in found[0]


# ---------------------------------------------------------------------------- where a list of values comes from
def _vocab_sources(root: Path, column: str = "deployment_status") -> list[dict]:
    """The readers as the model holds them after as-built, shaped the way check 3 takes them."""
    found = as_built.vocabulary_sources(root, {"vocabularies": [{"column": column}]})
    return [{"column": s["column"], "sourceKind": s["kind"], "sourceFile": f"{s['file']}:{s['line']}",
             "origin": s["origin"], "values": ",".join(s["values"]) if s["values"] else ""} for s in found]


CANON = "app/services/application_status.py"
CONSTRAINTS = {"c": {"column": "deployment_status", "canonicalSource": CANON}}


def _statuses(root: Path) -> None:
    _write(root, CANON, "STATUSES = (('a', 'A'), ('b', 'B'))\n\n\ndef status_options(current=None):\n    return list(STATUSES)\n")


def test_a_select_that_reads_the_one_list_is_clean_and_a_hand_written_options_list_is_named(tmp_path):
    _statuses(tmp_path)
    _write(tmp_path, "app/templates/good.html",
           "{% call native_select(id='s', name='deployment_status', options=[('', 'Not set')] + status_options(x)) %}{% endcall %}\n")
    assert mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path) == []
    _write(tmp_path, "app/templates/overview.html",
           "<p>x</p>\n{% call native_select(id='s', name='deployment_status', size='sm',\n"
           "   options=[('planned', 'Planned'), ('live', 'Live')]) %}{% endcall %}\n")
    found = mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path)
    assert len(found) == 1 and "app/templates/overview.html:2" in found[0] and "planned,live" in found[0]


def test_a_hand_written_option_loop_on_a_create_form_is_named_at_its_line(tmp_path):
    _statuses(tmp_path)
    _write(tmp_path, "app/templates/create.html",
           "<div>\n{% call native_select(id='deployment_status', name=('deployment_status'), size='sm') %}\n"
           "  {% for val in ['production', 'retired'] %}<option value=\"{{ val }}\">{{ val }}</option>{% endfor %}\n"
           "{% endcall %}\n</div>\n")
    found = mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path)
    assert len(found) == 1 and "app/templates/create.html:2" in found[0] and "production,retired" in found[0]


def test_a_further_form_with_its_own_choices_is_found_wherever_it_is_under_app(tmp_path):
    _statuses(tmp_path)
    _write(tmp_path, "app/forms_one.py",
           "from app.services.application_status import STATUSES\nclass F:\n    deployment_status = SelectField('S', choices=list(STATUSES))\n")
    assert mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path) == []
    _write(tmp_path, "app/elsewhere/deep/forms_two.py",
           "class G:\n    deployment_status = SelectField('S', choices=[('x', 'X'), ('y', 'Y')])\n"
           "class Model:\n    deployment_status = Column(String)\n")
    found = mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path)
    assert len(found) == 1 and "app/elsewhere/deep/forms_two.py:2" in found[0] and "x,y" in found[0]


def test_a_tool_enum_written_out_is_named_and_one_read_from_the_list_is_not(tmp_path):
    _statuses(tmp_path)
    tool = ("TOOLS = [{'route': 'ApplicationComponent.deployment_status', "
            "'parameters': {'properties': {'new_status': {'enum': %s}}}}]\n")
    _write(tmp_path, "app/tools_good.py", "from app.services.application_status import STATUSES\n" + tool % "list(STATUSES)")
    assert mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path) == []
    _write(tmp_path, "app/tools_bad.py", tool % "['design', 'live']")
    found = mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path)
    assert len(found) == 1 and "app/tools_bad.py" in found[0] and "design,live" in found[0]


def test_a_text_box_for_the_field_is_a_reader_that_does_not_take_the_list(tmp_path):
    _statuses(tmp_path)
    _write(tmp_path, "app/templates/box.html", "{{ edit_row('Status', 'deployment_status', 40, app) }}\n")
    found = mc.check_one_source(CONSTRAINTS, _vocab_sources(tmp_path), tmp_path)
    assert len(found) == 1 and "box.html:1" in found[0] and "free text" in found[0]


def test_the_scope_names_the_fields_modelled_and_does_not_limit_which_readers_are_read(tmp_path):
    _statuses(tmp_path)
    _write(tmp_path, "app/templates/never_listed.html",
           "{% call native_select(name='deployment_status', options=[('q', 'Q')]) %}{% endcall %}\n")
    scope = {"slice": "demo", "routeTails": [], "screens": ["app/templates/screen.html"],
             "vocabularies": [{"column": "deployment_status"}]}
    _write(tmp_path, "app/templates/screen.html", "<p>no form</p>\n")
    sysml, _ = as_built.generate(tmp_path, scope, {"routes": [], "controls": []})
    assert "app/templates/never_listed.html:1" in sysml


# ------------------------------------------------------------------------------------------ check 5
def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args], cwd=cwd, check=True,
                   capture_output=True)


def _brief(path: Path, branch: str, model_changes: str = "", builds_on: str = "") -> Path:
    lines = [f"# {path.stem}", "", f"SEAT NOTE: you work on branch {branch}, based on the line.", ""]
    if model_changes:
        lines.append(f"Model changes: {model_changes}")
    if builds_on:
        lines.append(f"Builds on: {builds_on}")
    path.write_text("\n".join(lines) + "\n")
    return path


def _repo_with_two_branches(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "app/services").mkdir(parents=True)
    (repo / "app/services/gap_register.py").write_text("RULE = 1\n")
    (repo / "app/services/other.py").write_text("X = 1\n")
    _git(repo, "init", "-q", "-b", "line")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    for name, f, body in (("ui/first", "gap_register.py", "RULE = 2\n"), ("ui/second", "gap_register.py", "RULE = 3\n"),
                          ("ui/third", "other.py", "X = 2\n")):
        _git(repo, "checkout", "-q", "-b", name, "line")
        (repo / "app/services" / f).write_text(body)
        _git(repo, "commit", "-q", "-am", name)
    _git(repo, "checkout", "-q", "line")
    return repo


def test_a_second_brief_that_changes_the_same_file_is_refused_and_names_it(tmp_path):
    repo = _repo_with_two_branches(tmp_path)
    first = mc.brief_elements(_brief(tmp_path / "first.md", "ui/first"), "line", repo)
    second = mc.brief_elements(_brief(tmp_path / "second.md", "ui/second"), "line", repo)
    third = mc.brief_elements(_brief(tmp_path / "third.md", "ui/third"), "line", repo)
    found = mc.check_collisions(second, [first])
    assert len(found) == 1 and "app/services/gap_register.py" in found[0]
    assert mc.check_collisions(third, [first, second]) == []


def test_two_briefs_that_declare_the_same_requirement_collide_unless_one_builds_on_the_other(tmp_path):
    repo = _repo_with_two_branches(tmp_path)
    a = mc.brief_elements(_brief(tmp_path / "a.md", "ui/none-a", model_changes="REQ-EDIT, REQ-X"), "line", repo)
    b = mc.brief_elements(_brief(tmp_path / "b.md", "ui/none-b", model_changes="REQ-EDIT"), "line", repo)
    c = mc.brief_elements(_brief(tmp_path / "c.md", "ui/none-c", model_changes="REQ-EDIT", builds_on="a"), "line", repo)
    assert "REQ-EDIT" in mc.check_collisions(b, [a])[0]
    assert mc.check_collisions(c, [a]) == []
    assert len(mc.check_collisions(c, [a, b])) == 1


def test_the_command_refuses_a_colliding_brief_and_lets_a_clean_one_through(tmp_path):
    repo = _repo_with_two_branches(tmp_path)
    first = _brief(tmp_path / "first.md", "ui/first")
    second = _brief(tmp_path / "second.md", "ui/second")
    third = _brief(tmp_path / "third.md", "ui/third")
    script = str(REPO_ROOT / "scripts/sysml/model_check.py")
    args = [sys.executable, script, "impact", "--repo", str(repo), "--base", "line", "--open", str(first)]
    refused = subprocess.run(args + ["--new", str(second)], capture_output=True, text=True)
    clean = subprocess.run(args + ["--new", str(third)], capture_output=True, text=True)
    assert refused.returncode == 1 and "REFUSED" in refused.stdout and "gap_register.py" in refused.stdout
    assert clean.returncode == 0 and "clean" in clean.stdout


def test_work_two_branches_inherit_from_a_shared_parent_is_not_a_collision_between_them(tmp_path):
    repo = _repo_with_two_branches(tmp_path)
    _git(repo, "checkout", "-q", "-b", "ui/parent", "line")
    (repo / "app/services/gap_register.py").write_text("RULE = 10\n")
    _git(repo, "commit", "-q", "-am", "parent")
    for name, body in (("ui/child-a", "X = 20\n"), ("ui/child-b", "X = 30\n"), ("ui/child-c", None)):
        _git(repo, "checkout", "-q", "-b", name, "ui/parent")
        if body:
            (repo / "app/services/other.py").write_text(body)
            _git(repo, "commit", "-q", "-am", name)
    _git(repo, "checkout", "-q", "line")
    a = mc.brief_elements(_brief(tmp_path / "a.md", "ui/child-a"), "line", repo)
    b = mc.brief_elements(_brief(tmp_path / "b.md", "ui/child-b"), "line", repo)
    c = mc.brief_elements(_brief(tmp_path / "c.md", "ui/child-c"), "line", repo)
    found = mc.check_collisions(b, [a])
    assert len(found) == 1 and "app/services/other.py" in found[0] and "gap_register" not in found[0]
    assert mc.check_collisions(c, [a]) == []


# ------------------------------------------------------------------- check 4: the rest of the requirement standard
def test_a_requirement_with_an_empty_or_missing_parent_is_reported(tmp_path):
    (tmp_path / "t.py").write_text("def test_x():\n    pass\n")
    reqs = {"n": _req("NEED-X", ""), "r": _req("RR-4", "NEED-X"), "empty": _req("REQ-EMPTY", ""), "ok": _req("REQ-OK", "RR-4")}
    sat = [("empty", "p"), ("ok", "p")]
    cases = {"c": {"testFile": "t.py", "testName": "test_x", "verifies": ["empty", "ok"]}}
    text = "\n".join(mc.check_satisfied_and_verified(reqs, sat, cases, tmp_path)[0])
    assert "REQ-EMPTY: has no parent" in text and "REQ-OK" not in text and "NEED-X" not in text


def test_a_requirement_is_one_sentence_and_tbd_is_refused(tmp_path):
    (tmp_path / "t.py").write_text("def test_x():\n    pass\n")
    reqs = {
        "n": _req("NEED-X", ""), "r": _req("RR-4", "NEED-X"),
        "three": _req("REQ-THREE", "RR-4", text="The product shall save. It is quick. It is safe."),
        "tbd": _req("REQ-TBD", "RR-4", text="The product shall answer within TBD seconds."),
        "eg": _req("REQ-EG", "RR-4", text="The product shall save forms, e.g. the Overview form, on one route."),
    }
    sat = [(k, "p") for k in ("three", "tbd", "eg")]
    cases = {"c": {"testFile": "t.py", "testName": "test_x", "verifies": ["three", "tbd", "eg"]}}
    text = "\n".join(mc.check_satisfied_and_verified(reqs, sat, cases, tmp_path)[0])
    assert "REQ-THREE: the text must be one sentence; it holds 3" in text
    assert "REQ-TBD: the text must hold exactly one 'shall'" in text
    assert "REQ-EG" not in text


def test_a_case_with_no_test_name_is_a_finding_even_when_the_file_has_tests(tmp_path):
    (tmp_path / "t.py").write_text("def test_x():\n    pass\n")
    reqs = {"n": _req("NEED-X", ""), "r": _req("RR-4", "NEED-X"), "a": _req("REQ-A", "RR-4"), "b": _req("REQ-B", "RR-4")}
    sat = [("a", "p"), ("b", "p")]
    for blank in ("", None):
        cases = {"c": {"testFile": "t.py", "testName": blank, "verifies": ["a"]},
                 "d": {"testFile": "t.py", "testName": "test_x", "verifies": ["b"]}}
        text = "\n".join(mc.check_satisfied_and_verified(reqs, sat, cases, tmp_path)[0])
        assert "REQ-A: its case in t.py names no test" in text and "REQ-B" not in text


# ------------------------------------------------------------------------ the reading path and the command's exit
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sysml_mini"


def test_the_parsed_model_is_read_back_with_the_counts_it_holds():
    model = sysml_model.load(FIXTURES / "mini.jsonl")
    reqs = model.requirements()
    assert sorted(r["id"] for r in reqs.values()) == ["NEED-M", "REQ-M-1", "REQ-M-2", "RR-M"]
    assert reqs["Mini::REQ_M1"]["parent"] == "RR-M" and reqs["Mini::REQ_M1"]["text"].startswith("The shop shall total")
    assert sorted(model.satisfies()) == [("Mini::REQ_M1", "Mini::tillModule"), ("Mini::REQ_M2", "Mini::tillModule")]
    cases = model.cases()
    assert {c["testName"]: c["verifies"] for c in cases.values()} == {
        "test_totals_every_sale": ["Mini::REQ_M1"], "test_refuses_a_sale_with_no_price": ["Mini::REQ_M2"]}
    assert len(model.parts()) == 1 and model.allocations() == [("Mini::totalSales", "Mini::tillModule")]
    assert list(model.functions()) == ["Mini::totalSales"]
    mc.require_readable(model)


def test_a_reader_that_returns_nothing_stops_with_a_plain_message_and_is_never_a_pass():
    with pytest.raises(SystemExit) as stop:
        mc.require_readable(sysml_model.Model())
    message = str(stop.value)
    assert "0 requirements" in message and "0 satisfy links" in message and "0 verification cases" in message
    assert "broken parse or reader" in message


def test_the_checks_over_the_fixture_model_find_what_it_holds(tmp_path):
    model = sysml_model.load(FIXTURES / "mini.jsonl")
    (tmp_path / "till.py").write_text("x = 1\n")
    _write(tmp_path, "tests/test_till.py", "def test_totals_every_sale():\n    pass\n")
    found, cover = mc.check_satisfied_and_verified(model.requirements(), model.satisfies(), model.cases(), tmp_path)
    assert found == ["REQ-M-2: tests/test_till.py has no test named test_refuses_a_sale_with_no_price"]
    assert cover == {"rule_lines": 1, "without_requirement": []}
    parts = {q: p for q, p in model.parts().items()}
    assert mc.check_allocation(list(model.functions()), model.allocations(), parts, tmp_path) == []


def test_a_parse_error_stops_the_run_instead_of_reading_what_was_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(sysml_model, "parse", lambda files, dump=None: (1, "line 3: nothing like this"))
    with pytest.raises(SystemExit) as stop:
        sysml_model.parse_model(tmp_path, tmp_path)
    assert "do not parse" in str(stop.value)


def _command_over(monkeypatch, tmp_path, findings, ratchet, report_only=False):
    (tmp_path / "ratchet.json").write_text(json.dumps({"slice": ratchet}))
    monkeypatch.setattr(mc, "MODEL", tmp_path)
    model = sysml_model.load(FIXTURES / "mini.jsonl")
    monkeypatch.setattr(mc, "run_checks", lambda root, inventory=None: (findings, {"rule_lines": 1, "without_requirement": []}, "parsed", model))

    class A:
        root = str(tmp_path)
        inventory = None

    A.report_only = report_only
    return mc.cmd_check(A)


def test_the_check_command_exits_non_zero_over_a_ratchet_and_zero_at_or_below_it(monkeypatch, tmp_path, capsys):
    keys = ("1", "2", "3", "4", "S", "U")
    ratchet = {k: 1 for k in keys}
    at = {k: ["one"] for k in keys}
    over = {**at, "3": ["one", "two"]}
    assert _command_over(monkeypatch, tmp_path, at, ratchet) == 0
    assert _command_over(monkeypatch, tmp_path, {**at, "4": []}, ratchet) == 0
    assert _command_over(monkeypatch, tmp_path, over, ratchet) == 1
    assert "check 3: findings=2 ratchet=1 OVER THE RATCHET" in capsys.readouterr().out
    assert _command_over(monkeypatch, tmp_path, over, ratchet, report_only=True) == 0


# ----------------------------------------------------------------------------------- check 5, the rest
def test_a_brief_names_its_own_branch_from_its_seat_note_else_from_its_id_and_never_from_a_mention(tmp_path):
    seat = "# x\n\nSEAT NOTE: you work on branch ui/sysmodel02, based on ui/sysmodel01.\nSee branch `ui/other` too.\n"
    assert mc.own_branch(seat, "sysmodel02") == "ui/sysmodel02"
    assert mc.own_branch("SEAT NOTE: you work on branch ui/sysmodel02.\n", "x") == "ui/sysmodel02"
    assert mc.own_branch("you work on branch `ui/fix-1.2` today", "x") == "ui/fix-1.2"
    mention = "# T\n\nThe slice on branch `ui/sysmodel01` already holds the rule.\n"
    assert mc.own_branch(mention, "T-sysmodel-01-dispatch-impact-check") == "ui/T-sysmodel-01"
    assert mc.brief_id("T-sysmodel-05b-interaction-model-and-checks") == "T-sysmodel-05b"
    assert mc.brief_id("odd") == "odd"


def test_the_real_dispatch_brief_prints_its_own_branch_and_not_the_one_it_mentions(tmp_path):
    repo = _repo_with_two_branches(tmp_path)
    brief = tmp_path / "T-sysmodel-01-dispatch-impact-check.md"
    brief.write_text("# T\n\n- The slice on branch `ui/first` already holds the rule.\n")
    script = str(REPO_ROOT / "scripts/sysml/model_check.py")
    run = subprocess.run([sys.executable, script, "impact", "--repo", str(repo), "--base", "line", "--new", str(brief)],
                         capture_output=True, text=True)
    assert run.returncode == 0
    assert "own branch ui/T-sysmodel-01 (not created yet)" in run.stdout and "ui/first" not in run.stdout


def test_two_branches_that_change_one_file_under_migrations_or_tests_collide(tmp_path):
    repo = tmp_path / "repo"
    for rel in ("migrations/versions/m1.py", "tests/test_a.py", "app/x.py"):
        _write(repo, rel, "v = 0\n")
    _git(repo, "init", "-q", "-b", "line")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    for name, n in (("ui/p", 1), ("ui/q", 2)):
        _git(repo, "checkout", "-q", "-b", name, "line")
        for rel in ("migrations/versions/m1.py", "tests/test_a.py"):
            _write(repo, rel, f"v = {n}\n")
        _write(repo, "docs/component-inventory.json", f"{{\"n\": {n}}}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
    _git(repo, "checkout", "-q", "line")
    p = mc.brief_elements(_brief(tmp_path / "p.md", "ui/p"), "line", repo)
    q = mc.brief_elements(_brief(tmp_path / "q.md", "ui/q"), "line", repo)
    found = mc.check_collisions(q, [p])
    assert len(found) == 1 and "migrations/versions/m1.py" in found[0] and "tests/test_a.py" in found[0]
    assert "component-inventory" not in found[0]


# ----------------------------------------------------------------------------------- the installer and its words
def test_the_installer_names_the_real_licence_and_refuses_a_download_that_differs_from_the_pinned_checksum(tmp_path):
    script = REPO_ROOT / "scripts" / "sysml" / "install_parser.sh"
    text = script.read_text(encoding="utf-8")
    assert "Eclipse Public License 2.0" in text and "LGPL" not in text
    assert text.count("_SHA256=\"") == 2
    fake = tmp_path / "bin"
    fake.mkdir()
    curl = fake / "curl"
    curl.write_text("#!/bin/sh\nwhile [ \"$1\" != \"-o\" ]; do shift; done\nprintf 'not the release' > \"$2\"\n")
    curl.chmod(0o755)
    home = tmp_path / "home"
    run = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "SYSML_HOME": str(home)})
    assert run.returncode == 1 and "REFUSED: jdk.tgz has SHA-256" in run.stderr
    assert not list(home.glob("jdk*")) and not (home / "jdk.tgz").exists()


def test_every_file_the_checks_cite_exists():
    text = (REPO_ROOT / "scripts" / "sysml" / "model_check.py").read_text(encoding="utf-8")
    cited = {m for m in re.findall(r"\b((?:docs|model|scripts|tests)/[\w./-]+\.(?:md|json|py|sysml))", text)}
    assert "model/README.md" in cited
    assert [c for c in sorted(cited) if not (REPO_ROOT / c).exists()] == []
