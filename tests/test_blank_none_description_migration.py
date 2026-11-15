"""The repair for work packages saved with the description "None": exact match only, safe to run twice."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = REPO_ROOT / "migrations" / "versions" / "20261114_blank_none_description.py"


def _upgrade(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("migration_blank_none_description", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


def test_none_descriptions_are_emptied_and_real_ones_kept(app, db_session, make_org):
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, org_b = make_org(), make_org()
    tag = uuid.uuid4().hex[:8]
    rows = {}
    for org, label, text in (
        (org_a, "stored_none", "None"),
        (org_b, "other_org_none", "None"),
        (org_a, "real", "Retire the ledger"),
        (org_a, "mentions_none", "None of the above"),
    ):
        row = UnifiedWorkPackage(name="%s %s" % (label, tag), description=text, organization_id=org.id)
        db_session.add(row)
        rows[label] = row
    db_session.flush()
    ids = {k: v.id for k, v in rows.items()}

    connection = db_session.connection()
    _upgrade(connection)
    _upgrade(connection)

    def _description(label):
        return connection.exec_driver_sql(
            "SELECT description FROM unified_work_packages WHERE id = %s", (ids[label],)
        ).scalar()

    assert _description("stored_none") is None
    assert _description("other_org_none") is None
    assert _description("real") == "Retire the ledger"
    assert _description("mentions_none") == "None of the above"
