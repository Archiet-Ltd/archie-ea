"""reconcile-schema must report nothing for an index that already exists.

The NULL-organisation capability embedding index was reported as "added" on
every run, so a database that already matched the models still showed one
drifted item and the schema-drift check could never pass.
"""

from sqlalchemy import inspect


def test_existing_null_organisation_index_is_not_reported_again(app):
    from app import db
    from app.commands.reconcile_schema import _ensure_embedding_null_org_unique_indexes

    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        first = []
        _ensure_embedding_null_org_unique_indexes(
            dry_run=False, existing_tables=tables, added=first, failed=[]
        )
        again = []
        _ensure_embedding_null_org_unique_indexes(
            dry_run=False, existing_tables=tables, added=again, failed=[]
        )
        dry = []
        _ensure_embedding_null_org_unique_indexes(
            dry_run=True, existing_tables=tables, added=dry, failed=[]
        )

    assert again == []
    assert dry == []
