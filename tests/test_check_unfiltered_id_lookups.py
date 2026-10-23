"""The unfiltered-id-lookups gate counts get-by-id calls on organisation-scoped models."""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_unfiltered_id_lookups.py"
sys.path.insert(0, str(SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("check_unfiltered_id_lookups", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_unfiltered_id_lookups"] = gate
_spec.loader.exec_module(gate)

MODELS = """
from app import db
from app.models.mixins import TenantMixin


class Fenced(TenantMixin, db.Model):
    __tablename__ = "fenced_things"
    id = db.Column(db.Integer, primary_key=True)


class Loose(db.Model):
    __tablename__ = "loose_things"
    id = db.Column(db.Integer, primary_key=True)
"""

ONE = """
from app import db
from app.models.fenced import Fenced


def load(record_id):
    return Fenced.query.get(record_id)
"""


def _count(tmp_path, code):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "fenced.py").write_text(MODELS)
    (app / "svc.py").write_text(textwrap.dedent(code))
    return gate.scan(app=app, repo=tmp_path)


def test_query_get_on_a_fenced_model_is_counted(tmp_path):
    assert len(_count(tmp_path, ONE)) == 1


def test_session_get_on_a_fenced_model_is_counted(tmp_path):
    code = "from app import db\nfrom app.models.fenced import Fenced\n\ndef f(i):\n    return db.session.get(Fenced, i)\n"
    assert len(_count(tmp_path, code)) == 1


def test_a_second_lookup_raises_the_count_by_one(tmp_path):
    two = ONE + "\n\ndef load_two(record_id):\n    return Fenced.query.get(record_id)\n"
    assert len(_count(tmp_path, two)) == len(_count(tmp_path, ONE)) + 1


def test_the_helper_an_unfenced_model_and_filtered_queries_are_not_counted(tmp_path):
    code = (
        "from app import db\nfrom app.models.fenced import Fenced, Loose\n\n"
        "def f(i):\n    a = Fenced.get_in_org(i)\n    b = Loose.query.get(i)\n"
        "    c = Fenced.query.filter_by(id=i).first()\n    return a, b, c\n"
    )
    assert _count(tmp_path, code) == []


def test_a_reasoned_marker_exempts_and_a_bare_one_does_not(tmp_path):
    ok = ONE.replace("    return Fenced", "    # unfiltered-id-lookup-ok: id came from this organisation's own row\n    return Fenced")
    bare = ONE.replace("    return Fenced", "    # unfiltered-id-lookup-ok:\n    return Fenced")
    assert _count(tmp_path, ok) == []
    assert len(_count(tmp_path, bare)) == 1


def test_the_gate_passes_at_the_baseline_and_fails_at_plus_one():
    root = SCRIPT.parent.parent
    baseline = json.loads((root / "verification_baseline.json").read_text())["ratchets"]["unfiltered_id_lookups"]
    import verify

    count = len(gate.scan())
    assert count <= baseline
    assert verify.gate_unfiltered_id_lookups(count).status == verify.PASS
    assert verify.gate_unfiltered_id_lookups(count - 1).status == verify.FAIL
