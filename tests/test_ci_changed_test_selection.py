"""The fast lane runs the tests a pull request changes and the tests that import
a module it changes, and nothing from the browser journeys."""

from pathlib import Path

from scripts.ci import changed_test_selection as sel


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_changed_test_file_is_selected(tmp_path):
    root = _tree(tmp_path, {"tests/test_a.py": "def test_a(): pass\n"})
    assert sel.select_tests(["tests/test_a.py"], root) == ["tests/test_a.py"]


def test_a_deleted_or_missing_test_file_is_not_selected(tmp_path):
    root = _tree(tmp_path, {})
    assert sel.select_tests(["tests/test_gone.py"], root) == []


def test_tests_importing_a_changed_module_are_selected(tmp_path):
    root = _tree(tmp_path, {
        "app/services/pricing.py": "X = 1\n",
        "tests/test_pricing.py": "from app.services.pricing import X\n",
        "tests/test_via_package.py": "from app.services import pricing\n",
        "tests/test_plain_import.py": "import app.services.pricing as p\n",
        "tests/test_unrelated.py": "from app.services.other import Y\n",
    })
    assert sel.select_tests(["app/services/pricing.py"], root) == [
        "tests/test_plain_import.py",
        "tests/test_pricing.py",
        "tests/test_via_package.py",
    ]


def test_a_module_local_test_directory_is_searched(tmp_path):
    root = _tree(tmp_path, {
        "app/modules/intelligence/service.py": "X = 1\n",
        "app/modules/intelligence/tests/test_service.py":
            "from app.modules.intelligence.service import X\n",
    })
    assert sel.select_tests(["app/modules/intelligence/service.py"], root) == [
        "app/modules/intelligence/tests/test_service.py"
    ]


def test_relative_imports_resolve_against_the_importing_package(tmp_path):
    root = _tree(tmp_path, {
        "tests/helpers.py": "H = 1\n",
        "tests/test_rel.py": "from .helpers import H\n",
        "tests/sub/test_deep.py": "from ..helpers import H\n",
    })
    assert sel.select_tests(["tests/helpers.py"], root) == [
        "tests/sub/test_deep.py",
        "tests/test_rel.py",
    ]


def test_package_init_changes_match_imports_of_the_package(tmp_path):
    root = _tree(tmp_path, {
        "app/core/__init__.py": "",
        "tests/test_core.py": "import app.core\n",
    })
    assert sel.select_tests(["app/core/__init__.py"], root) == ["tests/test_core.py"]


def test_browser_journeys_are_never_selected(tmp_path):
    root = _tree(tmp_path, {
        "app/x.py": "",
        "tests/smoke/test_journey.py": "import app.x\n",
        "tests/test_x.py": "import app.x\n",
    })
    selected = sel.select_tests(["app/x.py", "tests/smoke/test_journey.py"], root)
    assert selected == ["tests/test_x.py"]


def test_a_change_nothing_imports_selects_nothing(tmp_path):
    root = _tree(tmp_path, {"app/x.py": "", "tests/test_y.py": "import app.y\n"})
    assert sel.select_tests(["app/x.py", "README.md"], root) == []


def test_an_unparseable_test_file_does_not_stop_selection(tmp_path):
    root = _tree(tmp_path, {
        "app/x.py": "",
        "tests/test_broken.py": "def (:\n",
        "tests/test_ok.py": "import app.x\n",
    })
    assert sel.select_tests(["app/x.py"], root) == ["tests/test_ok.py"]


def test_a_change_to_shared_test_configuration_is_flagged():
    assert sel.global_impact(["tests/conftest.py", "app/x.py", "requirements.txt"]) == [
        "requirements.txt",
        "tests/conftest.py",
    ]


def test_extra_browsers_are_installed_only_when_a_selected_file_names_them(tmp_path):
    root = _tree(tmp_path, {
        "tests/test_a.py": "p.firefox.launch()\n",
        "tests/test_b.py": "def test(): pass\n",
        "tests/test_c.py": "browser = 'webkit'\n",
    })
    assert sel.browsers_needed(["tests/test_b.py"], root) == []
    assert sel.browsers_needed(["tests/test_a.py"], root) == ["firefox"]
    assert sel.browsers_needed(["tests/test_a.py", "tests/test_c.py"], root) == ["firefox", "webkit"]


def test_the_command_prints_one_path_per_line(tmp_path, capsys, monkeypatch):
    root = _tree(tmp_path, {"app/x.py": "", "tests/test_x.py": "import app.x\n"})
    monkeypatch.chdir(root)
    assert sel.main(["--files", "app/x.py"]) == 0
    assert capsys.readouterr().out.split() == ["tests/test_x.py"]
