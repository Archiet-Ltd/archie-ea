"""Tests for scripts/check_asvs_register.py.

These build a tiny synthetic register (docs/security/asvs-l2.yml) plus a few
fake test files under a tmp directory and point the check at it with --root, so
the test is fast and self-contained. The real register is exercised when the
gate runs against the repository root.

The register is a flat list of rows under a top-level ``rows:`` key. Each row
carries ``id``, ``family``, ``applies``, ``evidence`` (a block list) and
``status``. The script must parse this with PyYAML when it is importable and
with a built-in fallback loader when it is not, so the gate runs the same way
with or without PyYAML on the path. One of the tests below runs the script with
PyYAML forced off (PYTHONPATH scrubbed of yaml) to prove the fallback works.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_asvs_register.py")

BRIEF_TOKEN = "T-qa-13"


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _run(root: str, *args: str, drop_yaml: bool = False):
    env = dict(os.environ)
    if drop_yaml:
        # Force the fallback loader: remove any path that could carry PyYAML.
        env["PYTHONPATH"] = ""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--root", root, *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return result


def _build_tree(tmp_path, register_text, test_files=None):
    root = str(tmp_path)
    _write(os.path.join(root, "docs", "security", "asvs-l2.yml"), register_text)
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    for rel, text in (test_files or {}).items():
        _write(os.path.join(root, rel), text)
    return root


EXISTING_TEST = (
    "import pytest\n\n\n"
    "class TestAccess:\n"
    "    def test_method_boundary(self):\n"
    "        assert True\n\n\n"
    "def test_org_isolation(two_orgs):\n"
    "    assert True\n"
)


def test_evidence_naming_existing_node_passes(tmp_path):
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_org_isolation\n"
        "  - id: V4.1.3\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::TestAccess::test_method_boundary\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_evidence_naming_missing_node_fails(tmp_path):
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_does_not_exist\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("test_does_not_exist" in line for line in lines[:-1])


def test_fail_row_without_brief_id_is_reported_malformed(tmp_path):
    register = (
        "rows:\n"
        "  - id: V13.1.1\n"
        "    family: webhook\n"
        "    applies: true\n"
        "    status: fail\n"
        "    evidence:\n"
        "      - no brief named here\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("V13.1.1" in line for line in lines[:-1])


def test_fail_row_naming_brief_id_passes(tmp_path):
    register = (
        "rows:\n"
        "  - id: V13.1.1\n"
        "    family: webhook\n"
        "    applies: true\n"
        "    status: fail\n"
        "    evidence:\n"
        "      - no evidence yet; tracked by " + BRIEF_TOKEN + "\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_applies_false_row_needs_no_evidence(tmp_path):
    register = (
        "rows:\n"
        "  - id: V9.1.1\n"
        "    family: account\n"
        "    applies: false\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - not applicable: TLS is terminated at the load balancer\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_control_description_with_existing_file_passes(tmp_path):
    # A file named with a path (slash or extension) resolves. A bare top-level
    # file must be cited with its path (./Dockerfile), not as a bare word.
    register = (
        "rows:\n"
        "  - id: V14.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - build pipeline recorded in ./Dockerfile\n"
    )
    root = _build_tree(tmp_path, register, {})
    _write(os.path.join(root, "Dockerfile"), "FROM python:3.11\n")
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_control_description_with_missing_file_fails(tmp_path):
    register = (
        "rows:\n"
        "  - id: V14.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - control recorded in docs/security/no_such_file.md\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("no_such_file" in line for line in lines[:-1])


def test_control_description_with_existing_directory_passes(tmp_path):
    # A directory counts only when the row also names a FILE within it. The
    # directory is named for context, and app/auth/__init__.py is the concrete
    # control file that resolves the evidence.
    register = (
        "rows:\n"
        "  - id: V3.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - session handling recorded in app/auth app/auth/__init__.py\n"
    )
    root = _build_tree(tmp_path, register, {})
    os.makedirs(os.path.join(root, "app", "auth"), exist_ok=True)
    _write(os.path.join(root, "app", "auth", "__init__.py"), "")
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_parametrised_node_id_resolves_to_base_test(tmp_path):
    # A parametrised node id resolves to the base test collected from the AST.
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_org_isolation[org-b-case]\n"
        "  - id: V4.1.3\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::TestAccess::test_method_boundary[case-1]\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_mistyped_node_id_fails(tmp_path):
    # A base name that is not among the file's collected tests fails, even with
    # a parametrisation suffix that makes it look shaped like a real node id.
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_org_isolatoin[org-b-case]\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("test_org_isolatoin" in line for line in lines[:-1])


def test_bare_directory_without_named_file_fails(tmp_path):
    # A directory alone no longer resolves; a file within it must be named too.
    register = (
        "rows:\n"
        "  - id: V3.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - session handling recorded in app/auth\n"
    )
    root = _build_tree(tmp_path, register, {})
    os.makedirs(os.path.join(root, "app", "auth"), exist_ok=True)
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("app/auth" in line for line in lines[:-1])


def test_directory_with_named_file_passes(tmp_path):
    # Naming a file within the directory resolves the evidence.
    register = (
        "rows:\n"
        "  - id: V3.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - session handling recorded in app/auth/session.py\n"
    )
    root = _build_tree(tmp_path, register, {})
    _write(os.path.join(root, "app", "auth", "session.py"), "# control\n")
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_bare_word_never_resolves(tmp_path):
    # A bare word must never resolve, even when a same-named file exists on disk.
    register = (
        "rows:\n"
        "  - id: V3.4.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - control recorded in Dockerfile\n"
    )
    root = _build_tree(tmp_path, register, {})
    _write(os.path.join(root, "Dockerfile"), "FROM python:3.11\n")
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("V3.4.1" in line for line in lines[:-1])


def test_evidence_resolving_to_nothing_fails(tmp_path):
    # Evidence that is neither a test node, an existing file, nor an existing
    # directory must be reported with the row id and the unresolved text.
    register = (
        "rows:\n"
        "  - id: V3.2.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - control recorded in app/nowhere\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("V3.2.1" in line for line in lines[:-1])
    assert any("app/nowhere" in line for line in lines[:-1])


def test_free_prose_without_any_path_fails(tmp_path):
    # Free prose that resolves to no node, file or directory fails: a control
    # description with no recognisable path must not pass silently.
    register = (
        "rows:\n"
        "  - id: V3.3.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - the control is handled carefully by the team\n"
    )
    root = _build_tree(tmp_path, register, {})
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "1"
    assert any("V3.3.1" in line for line in lines[:-1])


def _write_inventory(root, routes):
    import json

    _write(
        os.path.join(root, "docs", "component-inventory.json"),
        json.dumps({"routes": routes}),
    )


BILLING_TWO_ORG_TEST = (
    "def test_billing_two_org_isolation(org_a, org_b, client):\n"
    "    client.get('/admin/billing/', org=org_a)\n"
    "    resp = client.get('/admin/billing/', org=org_b)\n"
    "    assert resp.status_code == 404\n"
)


def test_startup_family_with_v4_two_org_row_passes(tmp_path):
    # A startup family (billing -> admin billing) with a V4 row citing a genuine
    # two-organisation test that drives /admin/billing/ passes.
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: billing\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_billing_iso.py::test_billing_two_org_isolation\n"
    )
    root = _build_tree(
        tmp_path, register, {"tests/test_billing_iso.py": BILLING_TWO_ORG_TEST}
    )
    _write_inventory(
        root,
        [{"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"}],
    )
    result = _run(root, "--count")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_startup_family_missing_v4_two_org_row_fails(tmp_path):
    # The startup family is present but its V4 row cites an ordinary single-org
    # test that does not build a second organisation, so the family has no
    # genuine two-organisation V4 row and the gate must fail.
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: billing\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_billing_single.py::test_billing_page_renders\n"
    )
    single_org_test = (
        "def test_billing_page_renders(client):\n"
        "    resp = client.get('/admin/billing/')\n"
        "    assert resp.status_code == 200\n"
    )
    root = _build_tree(
        tmp_path, register, {"tests/test_billing_single.py": single_org_test}
    )
    _write_inventory(
        root,
        [{"blueprint": "billing", "login_required": True, "rule": "/admin/billing/"}],
    )
    result = _run(root)
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert any("admin billing" in line for line in lines[:-1])


def test_count_prints_trailing_integer(tmp_path):
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_missing_one\n"
        "  - id: V4.1.2\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_missing_two\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count")
    last = result.stdout.strip().splitlines()[-1]
    assert last.isdigit()
    assert last == "2"


def test_fallback_loader_runs_without_pyyaml(tmp_path):
    # Same as the passing case, but with PyYAML forced off the path. The gate
    # must still parse the register and report 0 problems.
    register = (
        "rows:\n"
        "  - id: V4.1.1\n"
        "    family: account\n"
        "    applies: true\n"
        "    status: pass\n"
        "    evidence:\n"
        "      - tests/test_access.py::test_org_isolation\n"
    )
    root = _build_tree(tmp_path, register, {"tests/test_access.py": EXISTING_TEST})
    result = _run(root, "--count", drop_yaml=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"
