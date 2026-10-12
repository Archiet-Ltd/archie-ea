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
