"""The system model's consistency checks, each shown to catch the failure it exists for.

These tests need no Java: they feed the checks plain data, a small throw-away code tree, and a throw-away git
repository. The command that parses the real model with the SysML v2 parser is in model/README.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "sysml"))

import as_built  # noqa: E402
import model_check as mc  # noqa: E402

BUILT_OLD = {"endpoint": "legacy.update_overview", "rule": "/applications/<int:id>/overview-update", "methods": "POST",
             "file": "app/legacy/save.py", "line": "197"}
BUILT_NEW = {"endpoint": "unified.update_overview", "rule": "/<int:id>/overview-update", "methods": "POST",
             "file": "app/unified/save.py", "line": "31"}
WANT = {"endpoint": "unified.update_overview", "rule": "/applications/<int:id>/overview-update"}


# ------------------------------------------------------------------------------------------ check 1
def test_two_handlers_for_one_save_route_are_reported_with_both_places():
    found = mc.check_one_definition([WANT], [BUILT_OLD, BUILT_NEW])
    assert len(found) == 1
    assert "app/legacy/save.py:197" in found[0] and "unified.update_overview" in found[0]


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
    legacy = {"rule": "/applications/<int:id>/overview-update", "file": "app/legacy/save.py", "line": 197}
    found = mc.check_screen_events(_ifml("legacy.update_overview", legacy), [WANT])
    assert len(found) == 1 and found[0].startswith("t.html:7") and "unified.update_overview" in found[0]
    assert mc.check_screen_events(_ifml("unified.update_overview", legacy), [WANT]) == []


def test_an_event_to_no_route_is_reported_and_a_run_time_address_is_not():
    assert "not a route in the code" in mc.check_screen_events(_ifml("gone.thing", None), [WANT])[0]
    assert mc.check_screen_events(_ifml(None, None, dynamic=True), [WANT]) == []


# ------------------------------------------------------------------------------------- the generator
def _tree(root: Path, handler_files: dict[str, str], template: str) -> None:
    for rel, body in handler_files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    t = root / "app/templates/screen.html"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(template)


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


def test_the_generator_finds_both_handlers_and_the_form_that_calls_one(tmp_path):
    _tree(tmp_path, {"app/legacy/save.py": LEGACY, "app/unified/save.py": UNIFIED},
          "<form id=\"edit\" method=\"POST\" action=\"{{ url_for('legacy.update_overview', id=1) }}\"></form>\n"
          "<form method=\"POST\" action=\"{{ row.delete_url }}\"></form>\n")
    sysml, ifml = as_built.generate(tmp_path, SCOPE)
    assert "port 'legacy.update_overview'" in sysml and "port 'unified.update_overview'" in sysml
    data = json.loads(ifml)
    events = data["viewContainers"][0]["events"]
    assert events[0]["action"]["endpoint"] == "legacy.update_overview"
    assert events[0]["action"]["route"]["file"] == "app/legacy/save.py"
    assert events[1]["action"]["dynamic"] is True
    routes = as_built.route_index(tmp_path)
    found = mc.check_one_definition([{"endpoint": "unified.update_overview", "rule": "/x/overview-update"}],
                                    [{**r, "line": r["line"]} for r in routes])
    assert len(found) == 1 and "app/legacy/save.py" in found[0]


def test_the_generator_gives_the_same_files_every_time(tmp_path):
    _tree(tmp_path, {"app/legacy/save.py": LEGACY, "app/unified/save.py": UNIFIED}, "<form action=\"{{ url_for('unified.update_overview', id=1) }}\"></form>")
    assert as_built.generate(tmp_path, SCOPE) == as_built.generate(tmp_path, SCOPE)


def test_the_generator_reads_where_a_list_of_values_comes_from(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/status.py").write_text("STATUSES = (('a', 'A'), ('b', 'B'))\n")
    (tmp_path / "app/forms_one.py").write_text(
        "from app.status import STATUSES\nclass F:\n    s = SelectField('S', choices=list(STATUSES))\n")
    (tmp_path / "app/forms_two.py").write_text(
        "class F:\n    s = SelectField('S', choices=[('x', 'X'), ('y', 'Y')])\n")
    one = as_built.source_form_select(tmp_path, {"file": "app/forms_one.py", "class": "F", "field": "s"})
    two = as_built.source_form_select(tmp_path, {"file": "app/forms_two.py", "class": "F", "field": "s"})
    assert one["origin"] == "app/status.py" and one["values"] is None
    assert two["values"] == ["x", "y"] and "written out in place" in two["origin"]


# ------------------------------------------------------------------------------------------ check 5
def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args], cwd=cwd, check=True,
                   capture_output=True)


def _brief(path: Path, branch: str, model_changes: str = "", builds_on: str = "") -> Path:
    lines = [f"# {path.stem}", "", f"Branch `{branch}` off the line.", ""]
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


def test_two_files_that_name_their_blueprint_the_same_keep_their_own_names(tmp_path):
    for rel, bp in (("app/a/routes.py", "first"), ("app/b/routes.py", "second")):
        p = tmp_path / rel
        p.parent.mkdir(parents=True)
        p.write_text(
            "from flask import Blueprint\n"
            f"shared_bp = Blueprint('{bp}', __name__)\n"
            "@shared_bp.route('/save', methods=['POST'])\n"
            "def save():\n    return ''\n"
        )
    endpoints = sorted(r["endpoint"] for r in as_built.route_index(tmp_path))
    assert endpoints == ["first.save", "second.save"]
