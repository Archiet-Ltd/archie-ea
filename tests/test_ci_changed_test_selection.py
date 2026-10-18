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


def test_an_empty_selection_prints_nothing_at_all(tmp_path, capsys, monkeypatch):
    # A single newline would leave a one-byte file that looks non-empty to the workflow.
    monkeypatch.chdir(_tree(tmp_path, {"docs/note.md": "x\n"}))
    assert sel.main(["--files", "docs/note.md"]) == 0
    assert capsys.readouterr().out == ""


def test_the_workflow_tests_for_real_content_not_file_size():
    text = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "if ! grep -q . fast-tests.txt" in text
    assert "[ ! -s fast-tests.txt ]" not in text


def test_non_python_source_files_count_toward_a_wide_change(tmp_path):
    root = _tree(tmp_path, {"app/modules/m/tests/test_m.py": "def test(): pass\n"})
    changed = [f"app/modules/m/templates/page{i}.html" for i in range(8)] + [
        f"app/modules/m/static/a{i}.css" for i in range(8)
    ]
    assert len(sel.source_files(changed)) == 16
    selected = sel.select_tests(changed, root)
    assert "16 source files" in sel.escalation_reason(changed, selected, root)


def test_non_python_application_files_never_pass_with_nothing_selected():
    reason = sel.escalation_reason(["app/templates/page.html"], [])
    assert "no test is tied to" in reason
    # An unrelated selected test must not mask a template no test is tied to.
    assert "no test is tied to" in sel.escalation_reason(["app/templates/page.html"], ["tests/test_page.py"])


def test_a_module_template_selects_the_modules_tests(tmp_path):
    root = _tree(tmp_path, {
        "app/modules/billing/templates/invoice.html": "<p></p>\n",
        "app/modules/billing/tests/test_invoice.py": "def test(): pass\n",
    })
    assert sel.select_tests(["app/modules/billing/templates/invoice.html"], root) == [
        "app/modules/billing/tests/test_invoice.py"
    ]


def test_documentation_changes_need_no_tests():
    assert sel.escalation_reason(["docs/guide.md", "README.md"], []) == ""


@pytest.mark.parametrize("path", [
    "app/config/base.py", "app/extensions/db.py", "tests/_helpers.py", "tests/fixtures/org.json",
    "tests/schema_baseline.json", "tests/shard_durations.json", ".coveragerc", "ruff.toml",
    ".github/workflows/deploy.yml", "scripts/deploy_workflow.py", "scripts/route_verification_audit.py",
    "package.json", "Dockerfile", "docker/Dockerfile.worker",
])
def test_more_shared_wiring_forces_the_full_suite(path):
    assert sel.escalation_reason([path], ["tests/test_x.py"])


def test_a_script_that_tests_import_forces_the_full_suite(tmp_path):
    root = _tree(tmp_path, {
        "scripts/helper.py": "",
        "scripts/lonely.py": "",
        "tests/test_uses_helper.py": "from scripts import helper\n",
    })
    assert "scripts that tests import" in sel.escalation_reason(["scripts/helper.py"], [], root)
    assert sel.escalation_reason(["scripts/lonely.py"], [], root) == ""


def test_deleted_and_renamed_paths_are_reported(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "old name.py").write_text("x = 1\n" * 20, encoding="utf-8")
    (tmp_path / "app" / "gone.py").write_text("y = 1\n" * 20, encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "topic")
    git("mv", "app/old name.py", "app/new.py")
    git("rm", "-q", "app/gone.py")
    git("commit", "-q", "-am", "change")
    cwd = Path.cwd()
    import os

    os.chdir(tmp_path)
    try:
        assert sorted(sel.changed_files("main")) == ["app/gone.py", "app/new.py", "app/old name.py"]
    finally:
        os.chdir(cwd)


@pytest.mark.parametrize("path", [
    "tests/unit/conftest.py", "tests/smoke/conftest.py", "app/utils/conftest.py", "conftest.py",
    "tests/factories.py", "tests/helpers/builders.py",
])
def test_every_conftest_outside_a_module_and_the_shared_helpers_force_the_full_suite(path):
    assert sel.escalation_reason([path], ["tests/test_x.py"])


def test_a_conftest_inside_a_module_uses_the_module_mapping():
    assert sel.escalation_reason(["app/modules/billing/conftest.py"], ["tests/test_x.py"]) == ""


@pytest.mark.parametrize("path", [
    ".github/actions/setup/action.yml", ".github/dependabot.yml", "requirements/base.txt",
    "compose.yml", "compose.yaml", "Makefile", "alembic.ini", "gunicorn.conf.py", "wsgi.py", "runtime.txt",
])
def test_build_and_runtime_files_force_the_full_suite(path):
    assert sel.escalation_reason([path], ["tests/test_x.py"])


def test_a_template_is_judged_on_its_own_mapping_not_the_global_selection(tmp_path):
    root = _tree(tmp_path, {
        "app/modules/billing/templates/invoice.html": "x\n",
        "app/modules/billing/tests/test_invoice.py": "def test(): pass\n",
        "app/templates/base.html": "x\n",
    })
    # The module template is tied to its module's tests; the shared template is tied to nothing,
    # even though another file in the same diff selected a test.
    changed = ["app/modules/billing/templates/invoice.html", "app/templates/base.html"]
    selected = sel.select_tests(changed, root)
    assert selected == ["app/modules/billing/tests/test_invoice.py"]
    assert sel.untested_application_files(changed, root) == ["app/templates/base.html"]
    assert "app/templates/base.html" in sel.escalation_reason(changed, selected, root)


def test_file_names_are_cleaned_before_they_are_printed():
    reason = sel.escalation_reason(["tests/conftest.py\n::error::forged", "tests/unit/conftest.py\x1b[31m"], [])
    assert "\n" not in reason and "\x1b" not in reason
