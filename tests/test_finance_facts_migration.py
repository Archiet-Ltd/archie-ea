"""The finance_facts revision upgrades, downgrades and upgrades again, and fences the table by organisation."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

MIGRATION_FILE = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261114_finance_facts.py"


def _revision():
    spec = importlib.util.spec_from_file_location("migration_20261114_finance_facts", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(app, step):
    from app import db

    module = _revision()
    with app.app_context():
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                getattr(module, step)()


def _facts(app):
    """(table exists, row-level security enabled and forced, policy names, index exists)."""
    from app import db

    with app.app_context():
        with db.engine.connect() as c:
            exists = c.execute(text("SELECT to_regclass('public.finance_facts')")).scalar() is not None
            if not exists:
                return False, None, [], False
            flags = c.execute(
                text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'public.finance_facts'::regclass")
            ).one()
            policies = sorted(r[0] for r in c.execute(text("SELECT polname FROM pg_policy WHERE polrelid = 'public.finance_facts'::regclass")))
            index = c.execute(
                text("SELECT 1 FROM pg_indexes WHERE tablename = 'finance_facts' AND indexname = 'ix_finance_facts_org_measure_subject_as_of'")
            ).scalar() is not None
            return True, tuple(flags), policies, index


def test_upgrade_downgrade_upgrade_leaves_a_fenced_table(app):
    fenced = (True, (True, True), ["tenant_delete", "tenant_insert", "tenant_select", "tenant_update"], True)
    try:
        _run(app, "upgrade")
        assert _facts(app) == fenced
        _run(app, "downgrade")
        assert _facts(app)[0] is False
        _run(app, "upgrade")
        assert _facts(app) == fenced
        _run(app, "upgrade")  # idempotent
        assert _facts(app) == fenced
    finally:
        if not _facts(app)[0]:
            _run(app, "upgrade")
