"""The content-only job runs a computed selection of tests, never a hand-picked list."""

import re
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
    assert all(p.startswith("tests/") and p.endswith(".py") for p in selected)
    assert all(Path(p).name.startswith("test_") for p in selected)


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


def test_selection_accepts_either_quote_fstrings_slugs_txt_xml_routes_and_subdirectories(tmp_path):
    tests = tmp_path / "tests"
    (tests / "sub").mkdir(parents=True)
    cases = {
        "test_single.py": "client.get('/pricing')\n",
        "test_fstr.py": "client.get(f'/vs/{slug}')\n",
        "test_dq_fstr.py": 'client.get(f"/use-cases/{slug}")\n',
        "test_slug.py": 'client.get("/about")\n',
        "test_txt.py": "client.get('/llms.txt')\n",
        "test_xml.py": 'client.get("/sitemap.xml")\n',
        "test_rg.py": "list(root.rglob('*.html'))\n",
        "test_none.py": 'client.get("/dashboard")\n',
        "test_prefix.py": 'client.get("/pricingx")\n',
        "sub/test_deep.py": 'client.get("/terms")\n',
    }
    for name, text in cases.items():
        (tests / name).write_text(text)
    expected = sorted(f"tests/{n}" for n in cases if n not in ("test_none.py", "test_prefix.py"))
    assert sel.select_content_tests(tmp_path) == expected


# Independent, deliberately loose detector: any public route fetch in a test
# file. Every such file must be in the selection (ratchet: a quoting style or
# route the selector misses fails here instead of silently dropping a test
# from the content job).
_LOOSE = re.compile(
    r"""\.get\(\s*f?['"]/(pricing|vs|use-cases|modules|vision|legal|about|features|connectors|architecture-health-check|team-annual-onboarding|"""
    r"""contact|docs|privacy|security|terms|llms|sitemap|robots|data-processing-agreement|"""
    r"""cookie-policy|refund-policy|commercial-licence|how-archiet-runs-on-entelim|roles)\b"""
)


def test_ratchet_every_public_route_fetching_test_under_tests_is_selected():
    selected = set(sel.select_content_tests(REPO_ROOT))
    unselected = []
    for path in (REPO_ROOT / "tests").rglob("test_*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("tests/smoke/"):
            continue
        if _LOOSE.search(path.read_text(encoding="utf-8", errors="replace")) and rel not in selected:
            unselected.append(rel)
    assert not unselected, unselected


def test_the_content_job_installs_chromium_and_records_junit():
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["content-page-tests"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "python -m playwright install --with-deps chromium" in runs
    assert "--maxfail" not in runs
    assert "--junitxml=content-tests.xml" in runs
    assert "xargs -r" in runs
    uploads = [s for s in steps if "upload-artifact" in s.get("uses", "")]
    assert uploads and "content-tests.xml" in str(uploads[0]["with"]["path"])


def test_an_empty_selection_is_reported_and_xargs_r_runs_nothing(tmp_path, monkeypatch, capsys):
    import shutil
    import subprocess

    (tmp_path / "tests").mkdir()
    monkeypatch.chdir(tmp_path)
    assert sel.select_content_tests(".") == []
    assert sel.main() == 1
    assert capsys.readouterr().out == ""
    xargs = shutil.which("xargs")
    if xargs:
        out = subprocess.run([xargs, "-r", "echo", "ran"], input="", capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout == ""


def test_selector_and_its_tests_contain_no_control_characters():
    for rel in ("scripts/ci/content_test_selection.py", "tests/test_content_test_selection.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        bad = sorted({hex(ord(c)) for c in text if ord(c) < 32 and c not in "\n\t\r"})
        assert not bad, (rel, bad)


def test_plain_language_is_always_selected():
    assert "tests/test_plain_language.py" in sel.ALWAYS_INCLUDED
