"""The content-only detector decides which CI jobs a pull request skips, so it must fail safe."""

import subprocess

import pytest

from scripts.ci import detect_content_only as d


@pytest.mark.parametrize(
    "path,expected",
    [
        ("content/pages/pricing.md", True),
        ("docs/guide.md", True),
        ("app/templates/public/home.html", True),
        ("tests/test_public_content_pages.py", True),
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
    assert d.classify(["content/a.md", "docs/b.md"]) == "true"


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
