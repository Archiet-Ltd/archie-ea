"""The one-record-per-process-element migration merges existing duplicates before it adds the constraint.

The whole run happens inside one transaction that is rolled back, so the shared test database is left as it was.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import text

MIGRATION_FILE = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261102_business_process_one_record.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_20261102_bp_one_record", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert(conn, sql, **values):
    return conn.execute(text(sql + " RETURNING id"), values).scalar_one()


def test_existing_duplicates_keep_the_oldest_record_and_repoint_what_pointed_at_the_others(app):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    module = _load()
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            conn.execute(text("ALTER TABLE business_processes DROP CONSTRAINT IF EXISTS uq_business_processes_org_element"))
            orgs = [_insert(conn, "INSERT INTO organizations (name, slug) VALUES (:n, :s)", n="Org %s %s" % (i, suffix), s="o%s-%s" % (i, suffix))
                    for i in (1, 2)]
            elements = [_insert(conn, "INSERT INTO archimate_elements (name, type, layer, organization_id) VALUES (:n, 'BusinessProcess', 'business', :o)",
                                n="Handle returns %s" % suffix, o=o) for o in orgs]
            oldest = _insert(conn, "INSERT INTO business_processes (name, organization_id, archimate_element_id) VALUES ('Handle returns', :o, :e)",
                             o=orgs[0], e=elements[0])
            newer = _insert(conn, "INSERT INTO business_processes (name, organization_id, archimate_element_id, bpmn_flow) "
                                  "VALUES ('Handle returns', :o, :e, CAST(:f AS json))", o=orgs[0], e=elements[0], f='{"nodes": [{"id": "n1"}]}')
            child = _insert(conn, "INSERT INTO business_processes (name, organization_id, parent_process_id) VALUES ('Child', :o, :p)",
                            o=orgs[0], p=newer)
            other = _insert(conn, "INSERT INTO business_processes (name, organization_id, archimate_element_id) VALUES ('Handle returns', :o, :e)",
                            o=orgs[1], e=elements[1])

            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()

            ids = [r[0] for r in conn.execute(text("SELECT id FROM business_processes WHERE organization_id = :o AND archimate_element_id = :e"),
                                              {"o": orgs[0], "e": elements[0]})]
            assert ids == [oldest]
            assert conn.execute(text("SELECT parent_process_id FROM business_processes WHERE id = :c"), {"c": child}).scalar_one() == oldest
            assert conn.execute(text("SELECT bpmn_flow->'nodes'->0->>'id' FROM business_processes WHERE id = :k"), {"k": oldest}).scalar_one() == "n1"
            assert conn.execute(text("SELECT count(*) FROM business_processes WHERE id = :o"), {"o": other}).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM pg_constraint WHERE conname = 'uq_business_processes_org_element'")).scalar_one() == 1
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        finally:
            trans.rollback()
            conn.close()
