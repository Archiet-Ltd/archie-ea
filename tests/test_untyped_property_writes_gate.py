"""The untyped property write gate must see direct bypasses and ignore the writer."""

from __future__ import annotations

import subprocess
import sys

from tests.test_gates_actually_fail import _run_checker, _write


def test_untyped_property_writes_gate_flags_a_direct_assignment(tmpdir):
    _write(
        tmpdir,
        "app/probe.py",
        "def f(element):\n"
        "    element.properties = '{}'\n",
    )

    assert _run_checker("check_untyped_property_writes.py", tmpdir) == 1


def test_untyped_property_writes_gate_allows_the_writer(tmpdir):
    _write(
        tmpdir,
        "app/probe.py",
        "from app.modules.architecture_assistant.property_service import PropertyService\n\n"
        "def f(element):\n"
        "    PropertyService().set_element_property(element, 'rate_limit', '1000 req/min')\n",
    )

    assert _run_checker("check_untyped_property_writes.py", tmpdir) == 0


def test_genome_patch_applier_still_has_untyped_write():
    """The genome patch applier's direct write must stay flagged until that
    work repoints it. This test fails when the genome module is
    repointed — that is the signal to remove this test."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "scripts", "check_untyped_property_writes.py")],
        capture_output=True, text=True, cwd=root,
    )
    output = proc.stdout
    assert "app/modules/genome/patch/applier.py" in output, (
        "genome/patch/applier.py must still be flagged for direct element property write; "
        "if the genome patch applier has been repointed, remove this test"
    )
