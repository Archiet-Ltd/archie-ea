"""The cache-tenancy gate sees a cache in a module that never names an organisation.

The element typeahead cache lived in a module with no ``organization_id`` or
``current_org_id`` in it, keyed by an f-string of the request arguments, so the
gate skipped the whole file. A module that reads the database through the ORM is
a module that handles tenant data whether or not it says so.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "scripts", "check_cache_tenancy.py")

MODULE = '''
from app import db

_catalog_cache = {}


def typeahead(q, page):
    cache_key = f"typeahead:{q}:{page}"
    rows = db.session.query(Thing).filter(Thing.name == q).all()
    _catalog_cache[cache_key] = rows
    return rows
'''


def _count(tmp_path, source):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "routes.py").write_text(source, encoding="utf-8")
    out = subprocess.run(
        [sys.executable, GATE, "--root", str(tmp_path), "--count"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(out.splitlines()[-1])


def test_unkeyed_fstring_cache_in_an_orm_module_is_reported(tmp_path):
    assert _count(tmp_path, MODULE) == 1


def test_the_same_cache_keyed_by_organisation_is_clean(tmp_path):
    keyed = MODULE.replace('f"typeahead:{q}:{page}"', 'f"org{org_id}:typeahead:{q}:{page}"')
    assert _count(tmp_path, keyed) == 0
