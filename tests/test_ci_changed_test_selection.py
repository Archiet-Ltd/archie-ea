"""The fast lane runs the tests a pull request changes and the tests that import
a module it changes, and nothing from the browser journeys."""

from pathlib import Path

import pytest

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


@pytest.mark.parametrize("path", [
    "tests/conftest.py", "pytest.ini", "requirements.txt", "requirements-test.txt",
    "migrations/versions/abc_add_table.py", "app/__init__.py", "app/_bootstrap/context_processors.py",
    "config.py", ".github/workflows/ci.yml", "scripts/ci/test_sharding.py",
])
def test_shared_wiring_forces_the_full_suite(path):
    assert sel.escalation_reason([path, "app/x.py"], ["tests/test_x.py"])


def test_an_ordinary_change_stays_in_the_fast_lane():
    assert sel.escalation_reason(["app/services/pricing.py", "tests/test_pricing.py"], ["tests/test_pricing.py"]) == ""


def test_a_wide_change_that_matched_few_tests_forces_the_full_suite():
    changed = [f"app/area/mod{i}.py" for i in range(40)]
    assert "40 source files" in sel.escalation_reason(changed, ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"])


def test_a_wide_change_that_matched_many_tests_may_stay_fast():
    changed = [f"app/area/mod{i}.py" for i in range(40)]
    selected = [f"tests/test_{i}.py" for i in range(sel.WIDE_CHANGE_MIN_TESTS)]
    assert sel.escalation_reason(changed, selected) == ""


def test_a_model_class_import_reaches_the_lower_case_model_file(tmp_path):
    root = _tree(tmp_path, {
        "app/models/work_package.py": "class WorkPackage: pass\n",
        "app/models/foo.py": "class Foo: pass\n",
        "tests/test_wp.py": "from app.models import WorkPackage\n",
        "tests/test_foo.py": "from app.models import Foo\n",
        "tests/test_other.py": "from app.models import Bar\n",
    })
    assert sel.select_tests(["app/models/foo.py"], root) == ["tests/test_foo.py"]
    assert sel.select_tests(["app/models/work_package.py"], root) == ["tests/test_wp.py"]


def test_a_change_inside_a_module_selects_its_tests_and_anything_importing_it(tmp_path):
    root = _tree(tmp_path, {
        "app/modules/billing/service.py": "",
        "app/modules/billing/tests/test_local.py": "def test(): pass\n",
        "app/modules/other/tests/test_other.py": "def test(): pass\n",
        "tests/test_uses_billing.py": "from app.modules.billing.views import x\n",
        "tests/test_unrelated.py": "import app.modules.other\n",
    })
    assert sel.select_tests(["app/modules/billing/service.py"], root) == [
        "app/modules/billing/tests/test_local.py",
        "tests/test_uses_billing.py",
    ]


def test_the_command_can_report_an_escalation(capsys):
    assert sel.main(["--files", "tests/conftest.py", "--escalate"]) == 0
    assert "shared wiring changed" in capsys.readouterr().out


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
