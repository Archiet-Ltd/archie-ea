"""The list of unprotected tables can only shrink, and shared reference data needs a reason.

Each case builds a tiny app tree and tiny list files in a temp directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_untenanted_reads.py"
_spec = importlib.util.spec_from_file_location("check_untenanted_reads_iso", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_untenanted_reads_iso"] = gate
_spec.loader.exec_module(gate)

MODELS = (
    "from app import db\nfrom app.models.mixins import TenantMixin\n\n\n"
    "class Fenced(TenantMixin, db.Model):\n    __tablename__ = 'fenced_things'\n\n\n"
    "class Old(db.Model):\n    __tablename__ = 'old_things'\n"
).replace("\n", "\n")


def _setup(tmp_path, extra="", listed="old_things\n", baseline="old_things\n", shared=""):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "things.py").write_text(MODELS + extra)
    lf, bf, sf = tmp_path / "list.txt", tmp_path / "base.txt", tmp_path / "shared.txt"
    lf.write_text(listed)
    bf.write_text(baseline)
    sf.write_text(shared)
    classes = gate.discover_models(app, tmp_path)
    tables = gate.unfenced_tables(classes, gate.unfenced_models(classes))
    return gate.table_problems(
        tables, listed=gate.listed_tables(lf), baseline=gate.listed_tables(bf),
        shared=gate.shared_reference_tables(sf),
    )


NEW_MODEL = "\n\nclass Fresh(db.Model):\n    __tablename__ = 'fresh_things'\n"


def test_the_baseline_case_passes(tmp_path):
    assert _setup(tmp_path) == []


def test_adding_a_line_to_the_unfenced_list_fails(tmp_path):
    problems = _setup(tmp_path, NEW_MODEL, listed="old_things\nfresh_things\n")
    assert problems == ["  added to the unfenced list: fresh_things"]


def test_a_model_without_the_mixin_and_without_a_listing_fails(tmp_path):
    problems = _setup(tmp_path, NEW_MODEL)
    assert problems == ["  new unfenced table: fresh_things"]


def test_a_shared_reference_line_with_a_reason_passes(tmp_path):
    assert _setup(tmp_path, NEW_MODEL, shared="fresh_things  # seeded by a command, same for everyone\n") == []


def test_a_shared_reference_line_without_a_reason_fails(tmp_path):
    problems = _setup(tmp_path, NEW_MODEL, shared="fresh_things\n")
    assert problems == ["  shared reference table with no reason: fresh_things"]
    problems = _setup(tmp_path, NEW_MODEL, shared="fresh_things  #   \n")
    assert problems == ["  shared reference table with no reason: fresh_things"]


def test_removing_a_name_from_the_list_passes(tmp_path):
    assert _setup(tmp_path, listed="", baseline="old_things\nretired_things\n", shared="old_things # x\n") == []


def test_the_committed_list_has_no_name_beyond_the_committed_baseline():
    assert gate.listed_tables() - gate.listed_tables(gate.TABLE_BASELINE) == set()


def test_the_committed_shared_reference_file_has_a_reason_on_every_line():
    assert gate.shared_reference_tables()[1] == []


def test_the_script_passes_on_the_repository(capfd):
    import subprocess
    out = subprocess.run([sys.executable, str(SCRIPT), "--new-tables"], capture_output=True, text=True)
    assert out.stdout.strip().splitlines()[-1] == "0", out.stdout


def _git(repo, *args):
    import subprocess
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(repo)})


def _repo_with_main(tmp_path):
    repo = tmp_path / "r"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "unfenced_tables.txt").write_text("# h\nold_things\n")
    (repo / "scripts" / "unfenced_tables_baseline.txt").write_text("old_things\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    return repo


def test_a_branch_that_adds_a_name_to_both_files_fails_against_main(tmp_path):
    repo = _repo_with_main(tmp_path)
    (repo / "scripts" / "unfenced_tables.txt").write_text("# h\nold_things\nfresh_things\n")
    (repo / "scripts" / "unfenced_tables_baseline.txt").write_text("old_things\nfresh_things\n")
    base, source, error = gate.base_table_list(repo=repo, env={})
    assert source.startswith("origin/main") and error is None
    listed = gate.listed_tables(repo / "scripts" / "unfenced_tables.txt")
    problems = gate.table_problems([], listed=listed, baseline=base, shared=(set(), []))
    assert problems == ["  added to the unfenced list: fresh_things"]
    # the committed snapshot alone would have passed it
    snapshot = gate.listed_tables(repo / "scripts" / "unfenced_tables_baseline.txt")
    assert gate.table_problems([], listed=listed, baseline=snapshot, shared=(set(), [])) == []


def test_with_no_main_ref_the_snapshot_is_the_fallback(tmp_path, capsys):
    repo = _repo_with_main(tmp_path)
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    base, source, error = gate.base_table_list(repo=repo, env={})
    assert source == "snapshot" and error is None
    assert "no main ref" in capsys.readouterr().err


def test_a_pull_request_run_that_cannot_read_the_base_list_fails_closed(tmp_path):
    repo = _repo_with_main(tmp_path)
    base, source, error = gate.base_table_list(repo=repo, env={"GITHUB_BASE_REF": "nonexistent"})
    assert base is None and "cannot read" in error


def test_a_pull_request_run_reads_the_base_branch(tmp_path):
    repo = _repo_with_main(tmp_path)
    base, source, error = gate.base_table_list(repo=repo, env={"GITHUB_BASE_REF": "main"})
    assert base == {"old_things"} and source.startswith("origin/main") and error is None


def test_a_table_on_both_the_unfenced_list_and_the_shared_list_fails(tmp_path):
    problems = _setup(tmp_path, listed="old_things\n", baseline="old_things\n",
                      shared="old_things  # reference data\n")
    assert problems == ["  table is on both the unfenced list and the shared reference list: old_things"]


def _route_tree(tmp_path, code):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "things.py").write_text(MODELS)
    (app / "routes.py").write_text(code)
    (app / "helpers.py").write_text(code)
    return app


READ = "from app import db\nfrom app.models.things import Old\n\n\ndef view(org):\n    return db.session.execute(db.select(Old).where(Old.organization_id == org)).all()\n"


def test_a_changed_route_reading_an_unfenced_table_fails_even_with_a_predicate(tmp_path):
    app = _route_tree(tmp_path, READ)
    problems = gate.changed_route_problems(["app/routes.py"], listed={"old_things"}, shared=(set(), []),
                                           app=app, repo=tmp_path, pending={})
    assert len(problems) == 1
    assert "app/routes.py" in problems[0] and "Old (old_things)" in problems[0]


def test_a_changed_route_is_clear_when_the_table_is_shared_fenced_or_the_file_is_not_a_route(tmp_path):
    app = _route_tree(tmp_path, READ)
    assert gate.changed_route_problems(["app/routes.py"], listed={"old_things"},
                                       shared=({"old_things"}, []), app=app, repo=tmp_path, pending={}) == []
    assert gate.changed_route_problems(["app/routes.py"], listed=set(), shared=(set(), []),
                                       app=app, repo=tmp_path, pending={}) == []
    assert gate.changed_route_problems(["app/helpers.py"], listed={"old_things"}, shared=(set(), []),
                                       app=app, repo=tmp_path, pending={}) == []


def test_an_unchanged_route_is_not_reported(tmp_path):
    app = _route_tree(tmp_path, READ)
    assert gate.changed_route_problems([], listed={"old_things"}, shared=(set(), []),
                                       app=app, repo=tmp_path, pending={}) == []


def test_a_written_reason_on_the_statement_clears_a_changed_route_read(tmp_path):
    code = READ.replace("    return db.session", "    # tenant-scoping-ok: the caller already checked the organisation\n    return db.session")
    app = _route_tree(tmp_path, code)
    assert gate.changed_route_problems(["app/routes.py"], listed={"old_things"}, shared=(set(), []),
                                       app=app, repo=tmp_path, pending={}) == []


def test_a_listed_pending_read_passes_and_an_unlisted_one_fails(tmp_path):
    app = _route_tree(tmp_path, READ)
    listed = {"old_things"}
    clear = gate.changed_route_problems(["app/routes.py"], listed=listed, shared=(set(), []), app=app,
                                        repo=tmp_path, pending={("app/routes.py", "old_things"): "b9"})
    assert clear == []
    failing = gate.changed_route_problems(["app/routes.py"], listed=listed, shared=(set(), []), app=app,
                                          repo=tmp_path, pending={})
    assert len(failing) == 1 and "tenant_gate_pending.txt" in failing[0]


def test_a_pending_line_for_a_table_that_is_now_fenced_or_shared_must_be_removed(tmp_path):
    app = _route_tree(tmp_path, READ)
    stale = gate.changed_route_problems(["app/routes.py"], listed=set(), shared=(set(), []), app=app,
                                        repo=tmp_path, pending={("app/routes.py", "old_things"): "b9"})
    assert len(stale) == 1 and "no longer needed" in stale[0]
    shared = gate.changed_route_problems(["app/routes.py"], listed={"old_things"}, shared=({"old_things"}, []),
                                         app=app, repo=tmp_path, pending={("app/routes.py", "old_things"): "b9"})
    assert len(shared) == 1 and "no longer needed" in shared[0]


def test_a_pending_line_for_a_changed_route_that_no_longer_reads_the_table_must_be_removed(tmp_path):
    app = _route_tree(tmp_path, "def view():\n    return 1\n")
    stale = gate.changed_route_problems(["app/routes.py"], listed={"old_things"}, shared=(set(), []), app=app,
                                        repo=tmp_path, pending={("app/routes.py", "old_things"): "b9"})
    assert len(stale) == 1 and "does not read it" in stale[0]


def test_the_pending_list_cannot_grow_but_the_first_population_is_allowed():
    current = {("a.py", "t1"): "b3", ("a.py", "t2"): "b3", ("b.py", "t3"): "b4"}
    assert gate.pending_growth_problems(current, None) == []
    assert gate.pending_growth_problems(current, "a.py:t1:b3\na.py:t2:b3\nb.py:t3:b4\n") == []
    assert gate.pending_growth_problems(current, "a.py:t1:b3\n# note\nb.py:t3:b4\n") != []
    assert gate.pending_growth_problems({}, "a.py:t1:b3\n") == []


def test_the_committed_pending_list_names_only_unfenced_unshared_tables():
    listed, shared = gate.listed_tables(), gate.shared_reference_tables()[0]
    stale = [k for k in gate.pending_entries() if k[1] not in listed or k[1] in shared]
    assert stale == []
