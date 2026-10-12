"""The content-only job runs a computed selection of tests, never a hand-picked list."""

from pathlib import Path

import yaml

from scripts.ci import content_test_selection as sel

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_MEMBERS = (
    "test_template_contracts.py",
    "test_template_render_contract.py",
    "test_template_auth_guards.py",
    "test_breadcrumb_root_label.py",
    "test_buy_keeps_the_plan.py",
    "test_analytics_consent.py",
    "test_proof_assets.py",
    "test_entelim_home_waitlist.py",
    "test_plain_language.py",
    "test_module_discoverability.py",
    "test_public_content_pages.py",
)


def test_the_selection_against_the_repository_includes_the_known_public_page_tests():
    selected = sel.select_content_tests(REPO_ROOT)
    missing = [name for name in REQUIRED_MEMBERS if f"tests/{name}" not in selected]
    assert not missing, missing


def test_the_selection_never_includes_the_smoke_tests():
    assert not [p for p in sel.select_content_tests(REPO_ROOT) if p.startswith("tests/smoke/")]


def test_the_selection_is_sorted_and_relative():
    selected = sel.select_content_tests(REPO_ROOT)
    assert selected == sorted(selected)
    assert all(p.startswith("tests/test_") and p.endswith(".py") for p in selected)


def test_selection_rules_on_a_synthetic_tree(tmp_path):
    tests = tmp_path / "tests"
    (tests / "smoke").mkdir(parents=True)
    (tests / "test_a.py").write_text("x = 'public_pages'\n")
    (tests / "test_b.py").write_text("x = 1\n")
    (tests / "test_template_contracts.py").write_text("x = 1\n")
    (tests / "test_c.py").write_text('client.get("/pricing")\n')
    (tests / "test_d.py").write_text("p = TEMPLATE_ROOT\n")
    (tests / "smoke" / "test_e.py").write_text("x = 'public_pages'\n")
    assert sel.select_content_tests(tmp_path) == [
        "tests/test_a.py", "tests/test_c.py", "tests/test_d.py", "tests/test_template_contracts.py",
    ]


def test_the_content_job_runs_the_computed_selection():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["content-page-tests"]["steps"]
    run = next(s["run"] for s in steps if "content_test_selection.py" in s.get("run", ""))
    assert "pytest" in run and "xargs" in run
