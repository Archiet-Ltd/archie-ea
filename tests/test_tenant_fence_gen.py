"""The tenant-fence generator writes a compiling, well-formed batch migration.

Runs scripts/tenant_fence_gen.py in a scratch tree: a model gets the tenant mixin and names its parent,
the migration is emitted with the table in BACKFILL and TENANT_TABLES, and the table leaves the unfenced
list and its baseline.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tenant_fence_gen.py"

MODEL = '''from app import db


class Widget(db.Model):
    """A widget."""
    __tablename__ = "widgets"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"))
'''


def _tree(tmp_path):
    (tmp_path / "app" / "models").mkdir(parents=True)
    (tmp_path / "app" / "models" / "widget.py").write_text(MODEL)
    (tmp_path / "migrations" / "versions").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    for name in ("unfenced_tables.txt", "unfenced_tables_baseline.txt"):
        (tmp_path / "scripts" / name).write_text("# header\nwidgets\nother\n")
    (tmp_path / "scripts" / "shared_reference_tables.txt").write_text("# header\n")
    spec = {
        "revision": "20261099_fence_widgets", "down": "20261098_prev", "title": "Widgets", "date": "2026-10-27",
        "tables": [{"table": "widgets", "model": "Widget", "file": "app/models/widget.py",
                    "sources": [["solutions", "solution_id"]]}],
    }
    (tmp_path / "spec.json").write_text(json.dumps(spec))


def test_it_fences_a_model_and_writes_a_compiling_migration(tmp_path):
    _tree(tmp_path)
    done = subprocess.run([sys.executable, str(SCRIPT), "spec.json"], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    model = (tmp_path / "app" / "models" / "widget.py").read_text()
    assert "class Widget(TenantMixin, db.Model):" in model
    assert "from app.models.mixins import TenantMixin" in model
    assert "__tenant_parent__ = (('solution_id', 'solutions'),)" in model
    source = (tmp_path / "migrations" / "versions" / "20261099_fence_widgets.py").read_text()
    tree = ast.parse(source)
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert {"revision", "down_revision", "BACKFILL", "TENANT_TABLES", "HYBRID_TABLES"} <= names
    assert 'down_revision = "20261098_prev"' in source
    assert "'widgets'" in source and "'solutions', 'solution_id'" in source
    for name in ("unfenced_tables.txt", "unfenced_tables_baseline.txt"):
        assert (tmp_path / "scripts" / name).read_text() == "# header\nother\n"


def test_running_it_twice_changes_nothing_more(tmp_path):
    _tree(tmp_path)
    for _ in range(2):
        assert subprocess.run([sys.executable, str(SCRIPT), "spec.json"], cwd=tmp_path, capture_output=True).returncode == 0
    model = (tmp_path / "app" / "models" / "widget.py").read_text()
    assert model.count("TenantMixin, db.Model") == 1 and model.count("__tenant_parent__") == 1
