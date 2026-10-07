"""R1-B88: the access review revision is idempotent, reversible, and the one head."""

import importlib.util
import pathlib
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

ROOT = pathlib.Path(__file__).resolve().parent.parent
REVISION_FILE = ROOT / "migrations" / "versions" / "20261008_access_review.py"


def _load():
    spec = importlib.util.spec_from_file_location("access_review_revision", REVISION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config():
    from alembic.config import Config

    config = Config(str(ROOT / "migrations" / "alembic.ini")) if (ROOT / "migrations" / "alembic.ini").exists() else Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_the_revision_is_the_single_head_and_chains_onto_the_previous_head():
    module = _load()
    script = ScriptDirectory.from_config(_alembic_config())

    assert script.get_heads() == [module.revision]
    assert module.down_revision == "20261007_public_visitor_events"


def test_upgrade_twice_downgrade_twice_and_upgrade_again(app):
    from app import db
    from app.models.access_review import AccessReviewCycle, AccessReviewItem

    module = _load()
    schema = "mig_b88_%s" % uuid.uuid4().hex[:8]
    with app.app_context():
        connection = db.engine.connect()
        outer = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA %s" % schema))
            # Tables land in the scratch schema; the foreign keys resolve to
            # organizations and users in public. Rolled back at the end.
            connection.execute(text("SET LOCAL search_path TO %s, public" % schema))
            context = MigrationContext.configure(connection)

            def tables():
                return set(inspect(connection).get_table_names(schema=schema))

            with Operations.context(context):
                module.upgrade()
                module.upgrade()  # a second run is a no-op
                assert {"access_review_cycles", "access_review_items"} <= tables()

                module.downgrade()
                module.downgrade()  # and so is a second downgrade
                assert tables() == set()

                module.upgrade()

            inspector = inspect(connection)
            for model in (AccessReviewCycle, AccessReviewItem):
                table = model.__table__
                actual = {c["name"]: c for c in inspector.get_columns(table.name, schema=schema)}
                assert set(actual) == {c.name for c in table.columns}, table.name
                for column in table.columns:
                    assert actual[column.name]["nullable"] == column.nullable, (table.name, column.name)
            indexes = {i["name"]: i for i in inspector.get_indexes("access_review_cycles", schema=schema)}
            assert indexes["uq_access_review_one_open"]["unique"]
            uniques = {
                u["name"] for u in inspector.get_unique_constraints("access_review_items", schema=schema)
            }
            assert "uq_access_review_item_user" in uniques
        finally:
            outer.rollback()
            connection.close()
