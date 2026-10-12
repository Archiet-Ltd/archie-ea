"""The content-only detector decides which CI jobs a pull request skips, so it must fail safe."""

import subprocess

import pytest

from scripts.ci import detect_content_only as d


@pytest.mark.parametrize(
    "path,expected",
    [
        ("content/pages/pricing.md", True),
        ("docs/guide.md", False),
        ("docs/background-mechanisms.yml", False),
        ("app/templates/public/home.html", True),
        ("app/templates/public/email/sales_inquiry.html", False),
        ("app/templates/public/email/sales_inquiry.txt", False),
        ("app/templates/public/pricing.html", True),
        ("app/templates/artefact_share/public.html", False),
        ("tests/test_public_content_pages.py", False),
        ("app/templates/layouts/public_base.html", False),
        ("app/templates/components/public_navbar.html", False),
        ("app/models/user.py", False),
        ("migrations/versions/abc.py", False),
        (".github/workflows/ci.yml", False),
        ("requirements.txt", False),
        ("content_extra/x.md", False),
    ],
)
def test_is_content_path(path, expected):
    assert d.is_content_path(path) is expected


def test_all_content_files_are_content_only():
    assert d.classify(["content/a.md", "app/templates/public/home.html"]) == "true"


def test_one_code_file_makes_the_change_not_content_only():
    assert d.classify(["content/a.md", "app/routes.py"]) == "false"


def test_email_template_diff_runs_everything():
    """An email template lives under a content prefix but is NOT content, so a
    diff that touches it alone must run the full suite."""
    assert d.classify(["app/templates/public/email/sales_inquiry.html"]) == "false"
    assert d.classify(["app/templates/public/email/sales_inquiry.txt"]) == "false"


def test_email_template_change_together_with_content_runs_everything():
    assert (
        d.classify(["content/a.md", "app/templates/public/email/sales_inquiry.html"])
        == "false"
    )


def test_empty_diff_is_not_content_only():
    assert d.classify([]) == "false"


def test_base_reference_uses_the_remote_tracking_branch(monkeypatch):
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    assert d.base_reference() == "origin/main"
    monkeypatch.delenv("GITHUB_BASE_REF")
    assert d.base_reference() == "origin/main"


def test_failure_to_diff_runs_everything(monkeypatch, capsys):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(d, "get_changed_files", boom)
    assert d.main() == 0
    assert "is_content_only=false" in capsys.readouterr().out


def test_a_rename_out_of_code_into_content_is_not_content_only(tmp_path, monkeypatch):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "app").mkdir()
    (tmp_path / "content").mkdir()
    (tmp_path / "app" / "svc.py").write_text("x = 1\n" * 40)
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-q", "-b", "pr")
    git("mv", "app/svc.py", "content/svc.md")
    git("commit", "-qm", "move")
    monkeypatch.chdir(tmp_path)
    changed = d.get_changed_files("main")
    assert "app/svc.py" in changed
    assert d.classify(changed) == "false"


def test_every_job_gated_on_the_detector_wraps_both_conditions_in_parentheses():
    import re
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    gated = {
        name: str(job["if"])
        for name, job in workflow["jobs"].items()
        if "needs.changes.outputs.is_content_only" in str(job.get("if", ""))
    }
    assert gated, "no job is gated on the detector"
    for name, expr in gated.items():
        assert re.search(r"&& \(github\.event_name != 'pull_request' \|\| github\.event\.action != 'labeled' \|\| contains\(.*?'ci-fast'\)\) && \(github\.event_name != 'pull_request' \|\| needs\.changes\.result != 'success'", expr), name


def test_content_page_tests_job_runs_only_when_is_content_only_is_true():
    """A content-only PR must run the public page tests as a dedicated light job
    (they would otherwise sit in the skipped shards), and the eight shards plus
    the browser jobs must skip when the detector says the diff is content-only."""
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    content_job = jobs["content-page-tests"]
    content_if = str(content_job["if"])
    assert "needs.changes.outputs.is_content_only == 'true'" in content_if, content_if
    assert "github.event_name == 'pull_request'" in content_if, (
        "the content page tests job must run only on pull requests -- on push "
        "to main the full suite already runs them"
    )
    assert "postgres" in content_job.get("services", {}), (
        "the content page tests job needs a Postgres service like tests-shard"
    )

    steps_text = "\n".join(step.get("run", "") for step in content_job["steps"])
    assert "scripts/ci/content_test_selection.py" in steps_text, (
        "the content page tests job must run the computed selection, not a hand-picked list"
    )
    assert "pytest" in steps_text and "xargs" in steps_text

    # The eight pytest shards, the tests combine job and the browser jobs all
    # skip when the detector says content-only.
    for job_id in (
        "tests-shard",
        "tests",
        "smoke",
        "smoke-main",
        "smoke-adversarial",
        "smoke-other",
        "browser-compatibility",
        "walkthrough",
    ):
        assert "is_content_only != 'true'" in str(jobs[job_id]["if"]), job_id


def test_email_template_diff_runs_the_full_suite_header_contract():
    """A diff touching an email template must run everything -- the detector
    must report false, and the content page tests job must not run."""
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    content_if = str(workflow["jobs"]["content-page-tests"]["if"])
    assert "needs.changes.outputs.is_content_only == 'true'" in content_if
    assert d.classify(["app/templates/public/email/sales_inquiry.html"]) == "false"


def _git_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "content").mkdir()
    (tmp_path / "content" / "a.md").write_text("a\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-q", "-b", "pr")
    return git


def _blob(tmp_path, text):
    return subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=tmp_path, input=text,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_a_symlink_under_a_content_path_is_not_content_only(tmp_path, monkeypatch):
    git = _git_repo(tmp_path)
    git("update-index", "--add", "--cacheinfo", f"120000,{_blob(tmp_path, '../app/svc.py')},content/link.md")
    git("commit", "-qm", "link")
    monkeypatch.chdir(tmp_path)
    changed = d.get_changed_files("main")
    assert changed == ["content/link.md"]
    assert d.get_non_regular_files("main") == frozenset({"content/link.md"})
    assert d.classify(changed, d.get_non_regular_files("main")) == "false"


def test_a_submodule_under_a_content_path_is_not_content_only(tmp_path, monkeypatch):
    git = _git_repo(tmp_path)
    git("update-index", "--add", "--cacheinfo",
        "160000,1111111111111111111111111111111111111111,content/sub")
    git("commit", "-qm", "sub")
    monkeypatch.chdir(tmp_path)
    non_regular = d.get_non_regular_files("main")
    assert non_regular == frozenset({"content/sub"})
    assert d.classify(d.get_changed_files("main"), non_regular) == "false"


def test_replacing_a_regular_file_with_a_symlink_is_not_content_only(tmp_path, monkeypatch):
    git = _git_repo(tmp_path)
    git("update-index", "--cacheinfo", f"120000,{_blob(tmp_path, '../x')},content/a.md")
    git("commit", "-qm", "swap")
    monkeypatch.chdir(tmp_path)
    non_regular = d.get_non_regular_files("main")
    assert non_regular == frozenset({"content/a.md"})
    assert d.classify(d.get_changed_files("main"), non_regular) == "false"


def test_a_regular_content_edit_is_still_content_only(tmp_path, monkeypatch):
    git = _git_repo(tmp_path)
    (tmp_path / "content" / "a.md").write_text("changed\n")
    git("commit", "-qam", "edit")
    monkeypatch.chdir(tmp_path)
    assert d.get_non_regular_files("main") == frozenset()
    assert d.classify(d.get_changed_files("main"), d.get_non_regular_files("main")) == "true"


def test_main_treats_a_symlink_as_not_content_only(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(d, "get_changed_files", lambda base: ["content/link.md"])
    monkeypatch.setattr(d, "get_non_regular_files", lambda base: frozenset({"content/link.md"}))
    assert d.main() == 0
    assert "is_content_only=false" in capsys.readouterr().out


def _ci_workflow():
    from pathlib import Path

    import yaml

    return yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_changes_job_has_the_same_labeled_event_guard_as_every_other_job():
    guard = (
        "github.event_name != 'pull_request' || github.event.action != 'labeled' "
        "|| contains(github.event.pull_request.labels.*.name, 'ci-fast')"
    )
    expr = str(_ci_workflow()["jobs"]["changes"]["if"]).strip()
    assert expr == "${{ " + guard + " }}"
    assert expr == str(_ci_workflow()["jobs"]["secret-scan"]["if"]).strip()


def test_changes_job_runs_the_detector_from_the_base_branch_copy():
    steps = _ci_workflow()["jobs"]["changes"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "detect")
    assert 'git show "origin/${GITHUB_BASE_REF:-main}:scripts/ci/detect_content_only.py" > /tmp/detect_content_only.py' in run
    assert "python /tmp/detect_content_only.py" in run
    assert "python scripts/ci/detect_content_only.py" in run  # fallback for the first introduction
    assert run.index("/tmp/detect_content_only.py") < run.index("python scripts/ci/detect_content_only.py")


def test_detector_docstring_says_it_runs_and_outputs_false_on_a_push():
    doc = d.__doc__
    assert "outputs is_content_only=false" in doc
    assert "is skipped" not in doc
