"""The list of unprotected tables can only shrink, and shared reference data needs a reason.

Each case builds a tiny app tree and tiny list files in a temp directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_untenanted_reads.py"
_spec = importlib.util.spec_from_file_location("check_untenanted_reads_iso", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_untenanted_reads_iso"] = gate
_spec.loader.exec_module(gate)

MODELS = (
    "from app import db\nfrom app.models.mixins import TenantMixin\n\n\n"
    "class Fenced(TenantMixin, db.Model):\n    __tablename__ = 'fenced_things'\n\n\n"
    "class Old(db.Model):\n    __tablename__ = 'old_things'\n"
).replace("\n", "\n")


def _setup(tmp_path, extra="", listed="old_things\n", baseline="old_things\n", shared=""):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "things.py").write_text(MODELS + extra)
    lf, bf, sf = tmp_path / "list.txt", tmp_path / "base.txt", tmp_path / "shared.txt"
    lf.write_text(listed)
    bf.write_text(baseline)
    sf.write_text(shared)
    classes = gate.discover_models(app, tmp_path)
    tables = gate.unfenced_tables(classes, gate.unfenced_models(classes))
    return gate.table_problems(
        tables, listed=gate.listed_tables(lf), baseline=gate.listed_tables(bf),
        shared=gate.shared_reference_tables(sf),
    )


NEW_MODEL = "\n\nclass Fresh(db.Model):\n    __tablename__ = 'fresh_things'\n"


def test_the_baseline_case_passes(tmp_path):
    assert _setup(tmp_path) == []


def test_adding_a_line_to_the_unfenced_list_fails(tmp_path):
    problems = _setup(tmp_path, NEW_MODEL, listed="old_things\nfresh_things\n")
    assert problems == ["  added to the unfenced list: fresh_things"]


def test_a_model_without_the_mixin_and_without_a_listing_fails(tmp_path):
    problems = _setup(tmp_path, NEW_MODEL)
    assert problems == ["  new unfenced table: fresh_things"]


def test_a_shared_reference_line_with_a_reason_passes(tmp_path):
    assert _setup(tmp_path, NEW_MODEL, shared="fresh_things  # seeded by a command, same for everyone\n") == []


def test_a_shared_reference_line_without_a_reason_fails(tmp_path):
    problems = _setup(tmp_path, NEW_MODEL, shared="fresh_things\n")
    assert problems == ["  shared reference table with no reason: fresh_things"]
    problems = _setup(tmp_path, NEW_MODEL, shared="fresh_things  #   \n")
    assert problems == ["  shared reference table with no reason: fresh_things"]


def test_removing_a_name_from_the_list_passes(tmp_path):
    assert _setup(tmp_path, listed="", baseline="old_things\nretired_things\n", shared="old_things # x\n") == []


def test_the_committed_list_has_no_name_beyond_the_committed_baseline():
    assert gate.listed_tables() - gate.listed_tables(gate.TABLE_BASELINE) == set()


def test_the_committed_shared_reference_file_has_a_reason_on_every_line():
    assert gate.shared_reference_tables()[1] == []


def test_the_script_passes_on_the_repository(capfd):
    import subprocess
    out = subprocess.run([sys.executable, str(SCRIPT), "--new-tables"], capture_output=True, text=True)
    assert out.stdout.strip().splitlines()[-1] == "0", out.stdout
