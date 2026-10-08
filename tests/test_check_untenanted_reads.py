"""The untenanted-reads gate finds a read of an unfenced model, and a new unfenced table.

Each test builds a tiny fake ``app/`` tree in a temp directory, so the scanner is
proven against the exact shapes of the two real leaks it exists for (a
``db.select(User)`` with no organisation predicate, and a read of a model that has no
tenant column at all), not just against the repository as it happens to be today.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_untenanted_reads.py"
_spec = importlib.util.spec_from_file_location("check_untenanted_reads", SCRIPT)
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_untenanted_reads"] = gate
_spec.loader.exec_module(gate)

MODELS = """
from app import db
from app.models.mixins import TenantMixin


class Fenced(TenantMixin, db.Model):
    __tablename__ = "fenced_things"
    id = db.Column(db.Integer, primary_key=True)


class ColumnOnly(db.Model):
    __tablename__ = "column_only_things"
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer)


class NoColumn(db.Model):
    __tablename__ = "no_column_things"
    id = db.Column(db.Integer, primary_key=True)


class ChildOfFenced(Fenced):
    pass
"""


def _tree(tmp_path, code, models=MODELS):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "things.py").write_text(models)
    (app / "routes.py").write_text(textwrap.dedent(code))
    return app


def _scan(tmp_path, code):
    app = _tree(tmp_path, code)
    return gate.scan(app=app, repo=tmp_path)


def _shapes(hits):
    return sorted((h["model"], h["shape"]) for h in hits)


def test_models_are_classified_by_fence_and_column(tmp_path):
    app = _tree(tmp_path, "x = 1\n")
    classes = gate.discover_models(app, tmp_path)
    models = gate.unfenced_models(classes)

    assert set(models) == {"ColumnOnly", "NoColumn"}
    assert models["ColumnOnly"]["has_col"] is True
    assert models["NoColumn"]["has_col"] is False


def test_a_select_of_an_unfenced_model_with_no_predicate_is_flagged(tmp_path):
    """The programme-lens user read, in miniature."""
    _, hits = _scan(tmp_path, """
        def owners(ids):
            return db.session.execute(db.select(ColumnOnly).where(ColumnOnly.id.in_(ids))).scalars()
    """)

    assert _shapes(hits) == [("ColumnOnly", "select")]
    assert hits[0]["has_org_col"] is True


def test_a_model_with_no_tenant_column_is_flagged_too(tmp_path):
    """The ownership-unit read, in miniature: no column, so no gate saw it."""
    _, hits = _scan(tmp_path, """
        def unit(unit_id):
            return NoColumn.query.filter_by(id=unit_id).first()
    """)

    assert _shapes(hits) == [("NoColumn", "Model.query")]
    assert hits[0]["has_org_col"] is False


def test_every_read_shape_is_seen(tmp_path):
    _, hits = _scan(tmp_path, """
        def a(i): return db.session.query(NoColumn).filter(NoColumn.id == i).first()
        def b(i): return db.session.get(NoColumn, i)
        def c(i): return NoColumn.query.get(i)
        def d(i): return db.select(NoColumn)
    """)

    assert _shapes(hits) == [
        ("NoColumn", "Model.query"), ("NoColumn", "select"),
        ("NoColumn", "session.get"), ("NoColumn", "session.query"),
    ]


def test_an_organisation_predicate_in_the_statement_clears_the_read(tmp_path):
    _, hits = _scan(tmp_path, """
        def owners(ids, org_id):
            return db.session.execute(
                db.select(ColumnOnly).where(ColumnOnly.id.in_(ids)).where(ColumnOnly.organization_id == org_id)
            ).scalars()
    """)

    assert hits == []


def test_a_written_reason_clears_the_read(tmp_path):
    _, hits = _scan(tmp_path, """
        def by_email(email):
            # tenant-scoping-ok: login lookup, e-mail is globally unique
            return ColumnOnly.query.filter_by(id=email).first()
    """)

    assert hits == []


def test_a_bare_marker_with_no_reason_does_not_clear_the_read(tmp_path):
    _, hits = _scan(tmp_path, """
        def by_email(email):
            # tenant-scoping-ok
            return ColumnOnly.query.filter_by(id=email).first()
    """)

    assert [h["model"] for h in hits] == ["ColumnOnly"]


def test_the_org_word_inside_another_name_does_not_clear_the_read(tmp_path):
    _, hits = _scan(tmp_path, """
        def by_id(row_id, other_org_id, tenant_label):
            return ColumnOnly.query.filter_by(id=row_id).first() or other_org_id or tenant_label
    """)

    assert [h["model"] for h in hits] == ["ColumnOnly"]


def test_the_org_word_in_a_comment_does_not_clear_the_read(tmp_path):
    _, hits = _scan(tmp_path, """
        def by_id(row_id):
            # organization_id is checked by the caller
            return db.session.execute(db.select(ColumnOnly).where(ColumnOnly.id == row_id)).scalars()
    """)

    assert [h["model"] for h in hits] == ["ColumnOnly"]


def test_a_keyword_org_filter_clears_the_read(tmp_path):
    _, hits = _scan(tmp_path, """
        def rows(org_id):
            return ColumnOnly.query.filter_by(organization_id=org_id).all()
    """)

    assert hits == []


def test_a_model_defined_twice_with_different_fencing_is_reported_not_silent(tmp_path):
    gate.AMBIGUOUS.clear()
    models = MODELS + """

class Twice(TenantMixin, db.Model):
    __tablename__ = "twice_things"


class Twice(db.Model):
    __tablename__ = "twice_things"
"""
    app = _tree(tmp_path, "def f():\n    return 1\n", models=models)
    gate.scan(app=app, repo=tmp_path)

    assert "Twice" in gate.AMBIGUOUS


def test_a_lookup_by_id_is_flagged_even_with_a_predicate_nearby(tmp_path):
    """session.get has nowhere to put a predicate, so it always needs a reason."""
    _, hits = _scan(tmp_path, """
        def get(row_id, org_id):
            return db.session.get(NoColumn, row_id)
    """)

    assert _shapes(hits) == [("NoColumn", "session.get")]


def test_fenced_models_and_their_subclasses_are_not_flagged(tmp_path):
    _, hits = _scan(tmp_path, """
        def a(i): return Fenced.query.get(i)
        def b(i): return db.select(ChildOfFenced)
    """)

    assert hits == []


def test_adding_a_read_raises_the_count_by_one(tmp_path):
    _, before = _scan(tmp_path, "def a(i):\n    return NoColumn.query.get(i)\n")
    _, after = _scan(tmp_path, "def a(i):\n    return NoColumn.query.get(i)\n\ndef b(i):\n    return NoColumn.query.get(i)\n")

    assert len(after) == len(before) + 1


def test_a_new_unfenced_table_is_reported_by_name(tmp_path, monkeypatch):
    app = _tree(tmp_path, "x = 1\n")
    listed = tmp_path / "unfenced_tables.txt"
    listed.write_text("# header\ncolumn_only_things\nno_column_things  # global reference\n")
    classes = gate.discover_models(app, tmp_path)
    tables = gate.unfenced_tables(classes, gate.unfenced_models(classes))

    assert tables == ["column_only_things", "no_column_things"]
    assert [t for t in tables if t not in gate.listed_tables(listed)] == []

    (app / "models" / "more.py").write_text(
        "from app import db\n\n\nclass Fresh(db.Model):\n    __tablename__ = \"fresh_table\"\n"
    )
    classes = gate.discover_models(app, tmp_path)
    tables = gate.unfenced_tables(classes, gate.unfenced_models(classes))

    assert [t for t in tables if t not in gate.listed_tables(listed)] == ["fresh_table"]


def test_the_committed_table_list_is_current():
    """The list in the repository matches the models today, so a table that was
    removed or gained the mixin does not leave a stale entry hiding a new one."""
    classes = gate.discover_models()
    actual = set(gate.unfenced_tables(classes, gate.unfenced_models(classes)))

    assert actual - gate.listed_tables() == set()

def test_a_model_listed_as_global_with_a_reason_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setitem(gate.GLOBAL_MODELS, "NoColumn", "shared reference data (test)")
    _, hits = _scan(tmp_path, """
        def read():
            return NoColumn.query.all()
    """)

    assert hits == []


def test_the_vendor_catalogue_is_listed_as_global_with_its_decision():
    assert "ADR-0003" in gate.GLOBAL_MODELS["VendorOrganization"]


def test_every_global_model_carries_a_written_reason():
    assert gate.GLOBAL_MODELS
    for name, reason in gate.GLOBAL_MODELS.items():
        assert len(reason) > 40, "%s needs a real reason" % name
