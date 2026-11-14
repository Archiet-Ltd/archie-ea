"""The changed-files gates judge a branch against the line it merges into, and the macro
gate counts only scripts a browser runs."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _commit(repo, name):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def gate(monkeypatch):
    module = _load("check_smoke_coverage_on_change")
    monkeypatch.delenv("VERIFY_BASE_REF", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    return module


@pytest.fixture()
def repo(tmp_path, gate):
    """main is old; the line moved on from it; the branch is cut from the line."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "a")
    main_tip = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", main_tip)
    line_tip = _commit(tmp_path, "line_change")
    _git(tmp_path, "update-ref", "refs/remotes/anioko/ui/shadcn-rebuild", line_tip)
    _git(tmp_path, "checkout", "-q", "-b", "branch")
    _commit(tmp_path, "branch_change")
    gate.REPO_ROOT = tmp_path
    return {"path": tmp_path, "main": main_tip, "line": line_tip}


def test_explicit_base_wins(gate, repo, monkeypatch):
    monkeypatch.setenv("VERIFY_BASE_REF", "feedface")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    assert gate._base_ref() == "feedface"


def test_pull_request_base_is_used(gate, repo, monkeypatch):
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    assert gate._base_ref() == "origin/main"


def test_branch_on_the_line_is_judged_from_the_line_not_old_main(gate, repo):
    assert gate._base_ref() == repo["line"]
    changed = gate._changed_files(gate._base_ref())
    assert "branch_change" in changed
    assert "line_change" not in changed


def test_branch_without_the_line_falls_back_to_main(gate, repo):
    _git(repo["path"], "update-ref", "-d", "refs/remotes/anioko/ui/shadcn-rebuild")
    assert gate._base_ref() == repo["main"]


@pytest.fixture(scope="module")
def macros():
    return _load("check_macro_import_context")


def _counts(macros, body):
    tree = macros.Environment(extensions=[]).parse(body)
    return macros._has_script_in_macro(tree)


@pytest.mark.parametrize("tag", [
    '<script type="application/json" data-chart-spec>{}</script>',
    "<script type='application/ld+json'>{}</script>",
    '<script type="importmap">{}</script>',
])
def test_data_block_is_not_counted(macros, tag):
    assert _counts(macros, "{% macro m() %}" + tag + "{% endmacro %}") is False


@pytest.mark.parametrize("tag", [
    "<script>run()</script>",
    '<script nonce="x">run()</script>',
    '<script type="module">run()</script>',
    '<script type="text/javascript">run()</script>',
    '<script src="/a.js"></script>',
    '<script type="{{ kind }}">run()</script>',
])
def test_executable_script_is_counted(macros, tag):
    assert _counts(macros, "{% macro m() %}" + tag + "{% endmacro %}") is True


def test_json_block_beside_a_real_script_is_still_counted(macros):
    body = ('{% macro m() %}<script type="application/json">{}</script>'
            "<script>run()</script>{% endmacro %}")
    assert _counts(macros, body) is True


def test_line_head_has_no_context_less_import_of_a_running_script(macros):
    assert macros.scan() == []


def test_a_page_that_breaks_its_block_added_on_a_branch_of_the_line_still_fails(tmp_path):
    """A clone of this tree, a branch cut from the line, one new page with no block."""
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--no-hardlinks", "--shared", str(ROOT), str(clone))
    _git(clone, "checkout", "-q", "-b", "branch")
    # the line is the head the clone was cut from
    _git(clone, "update-ref", "refs/remotes/anioko/ui/shadcn-rebuild", "HEAD")
    for name in ("docs/design-map.json",):
        (clone / name).write_bytes((ROOT / name).read_bytes())
    page = clone / "app/templates/admin/zz_new_page.html"
    page.write_text('{% extends "layouts/admin_base.html" %}{% block content %}<p>x</p>{% endblock %}')
    _git(clone, "add", "-f", str(page.relative_to(clone)))
    _git(clone, "commit", "-q", "-m", "new page")
    run = subprocess.run([sys.executable, "scripts/check_design_map.py", "--changed"],
                         cwd=clone, capture_output=True, text=True)
    assert run.returncode != 0
    assert "zz_new_page.html" in run.stdout + run.stderr
    others = [ln for ln in (run.stdout + run.stderr).splitlines()
              if "app/templates/" in ln and "zz_new_page" not in ln]
    assert others == []
