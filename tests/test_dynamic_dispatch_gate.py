"""Both tenancy gates see a model chosen at runtime, and share one accepted-sites list.

Each test builds a tiny fake ``app/`` tree so the detector is proven against the shapes
that let cross-organisation leaks past the name-matching gates: a registry subscript
(``REGISTRY[key].query``) and a class resolved by helper then handed to ``session.get``.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import dynamic_dispatch as dd  # noqa: E402

untenanted = _load("check_untenanted_reads")
scoping = _load("check_tenant_scoping")

MODELS = """
from app import db
class Plain(db.Model):
    __tablename__ = "plain_things"
    id = db.Column(db.Integer, primary_key=True)
"""

REGISTRY_SRC = """
REGISTRY = {}

def read(key, entity_id):
    return REGISTRY[key].query.filter_by(id=entity_id).first()
"""

GET_SRC = """
from app import db

def read(path, entity_id):
    cls = _import_model(path)
    return db.session.get(cls, entity_id)
"""

GETATTR_SRC = """
def read(mod, name):
    return getattr(mod, name).query.all()
"""


def _tree(tmp_path, src):
    app = tmp_path / "app"
    (app / "models").mkdir(parents=True, exist_ok=True)
    (app / "models" / "things.py").write_text(MODELS)
    (app / "routes").mkdir(exist_ok=True)
    (app / "routes" / "view.py").write_text(textwrap.dedent(src))
    return app


def _untenanted(tmp_path, src, monkeypatch, listed=()):
    monkeypatch.setattr(dd, "listed_sites", lambda path=None: set(listed))
    app = _tree(tmp_path, src)
    _, hits = untenanted.scan(app=app, repo=tmp_path)
    return [h for h in hits if h["shape"] == "dynamic-dispatch"]


def _scoping(tmp_path, src, monkeypatch, listed=()):
    monkeypatch.setattr(dd, "listed_sites", lambda path=None: set(listed))
    app = _tree(tmp_path, src)
    monkeypatch.setattr(scoping, "REPO_ROOT", str(tmp_path))
    return [f for f in scoping.scan_file(str(app / "routes" / "view.py"), set()) if f["model"] == "<unresolved>"]


def test_registry_subscript_query_is_flagged_by_both_gates(tmp_path, monkeypatch):
    assert len(_untenanted(tmp_path, REGISTRY_SRC, monkeypatch)) == 1
    assert len(_scoping(tmp_path, REGISTRY_SRC, monkeypatch)) == 1


def test_session_get_of_a_dynamically_resolved_class_is_flagged_by_both_gates(tmp_path, monkeypatch):
    hits = _untenanted(tmp_path, GET_SRC, monkeypatch)
    assert [h["line"] for h in hits] == [6]
    assert len(_scoping(tmp_path, GET_SRC, monkeypatch)) == 1


def test_getattr_with_a_non_literal_name_is_flagged(tmp_path, monkeypatch):
    assert len(_untenanted(tmp_path, GETATTR_SRC, monkeypatch)) == 1


def test_getattr_with_a_literal_name_is_not_flagged(tmp_path, monkeypatch):
    src = "def read(mod):\n    return getattr(mod, 'Plain').query.all()\n"
    assert _untenanted(tmp_path, src, monkeypatch) == []


def test_a_listed_site_is_accepted_by_both_gates(tmp_path, monkeypatch):
    listed = {"app/routes/view.py:5"}
    assert _untenanted(tmp_path, REGISTRY_SRC, monkeypatch, listed) == []
    assert _scoping(tmp_path, REGISTRY_SRC, monkeypatch, listed) == []


def test_a_reasoned_marker_exempts_and_a_bare_one_does_not(tmp_path, monkeypatch):
    ok = REGISTRY_SRC.replace(".first()", ".first()  # tenant-scoping-ok: registry holds fenced models only")
    bare = REGISTRY_SRC.replace(".first()", ".first()  # tenant-scoping-ok")
    assert _untenanted(tmp_path, ok, monkeypatch) == []
    assert len(_untenanted(tmp_path, bare, monkeypatch)) == 1


def test_a_literal_model_read_is_still_found_and_is_not_dynamic(tmp_path, monkeypatch):
    src = "from app import db\nfrom app.models.things import Plain\n\ndef read(i):\n    return db.session.get(Plain, i)\n"
    monkeypatch.setattr(dd, "listed_sites", lambda path=None: set())
    _, hits = untenanted.scan(app=_tree(tmp_path, src), repo=tmp_path)
    assert [(h["model"], h["shape"]) for h in hits] == [("Plain", "session.get")]


def test_the_committed_list_names_only_real_sites():
    """Every listed file:line is still a dynamic-dispatch site, so a moved site cannot hide."""
    repo = SCRIPTS.parent
    live = set()
    for path in (repo / "app").rglob("*.py"):
        import ast
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        rel = path.relative_to(repo).as_posix()
        live |= {f"{rel}:{s['line']}" for s in dd.find_dynamic_sites(tree, src.splitlines())}
    assert dd.listed_sites() <= live
