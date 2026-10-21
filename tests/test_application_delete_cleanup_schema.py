"""Application clean-up skips tables and columns absent from the live schema."""
import pytest
from sqlalchemy import event, text

from app.modules.applications.routes import _helpers


@pytest.fixture
def cleanup(app, db_session):
    _helpers._reset_schema_cache()
    yield db_session
    _helpers._reset_schema_cache()


def _capture(db_session):
    seen = []
    engine = db_session.get_bind().engine

    def _on(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _on)
    return seen, lambda: event.remove(engine, "before_cursor_execute", _on)


def _drop(db_session, table):
    db_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))


def test_missing_tables_and_column_are_never_queried(cleanup):
    for t in ("data_flows", "application_interface", "application_cost_records",
              "capability_governance_records", "architecture_session",
              "consolidation_list_item"):
        _drop(cleanup, t)
    # Present table, missing column.
    cleanup.execute(text("DROP TABLE IF EXISTS application_interfaces CASCADE"))
    cleanup.execute(text("CREATE TABLE application_interfaces (id integer)"))
    seen, stop = _capture(cleanup)
    try:
        _helpers._cascade_delete_application(987654)
    finally:
        stop()
    joined = "\n".join(seen)
    assert "FROM data_flows" not in joined
    assert "FROM application_interface WHERE" not in joined
    assert "FROM application_cost_records" not in joined
    assert "FROM capability_governance_records" not in joined
    assert "FROM architecture_session WHERE" not in joined
    assert "FROM consolidation_list_item " not in joined
    assert "FROM application_interfaces" not in joined
    assert "SAVEPOINT" in joined  # present tables are still cleaned


def test_present_tables_still_cleaned(cleanup):
    cleanup.execute(text("DROP TABLE IF EXISTS data_flows CASCADE"))
    cleanup.execute(text(
        "CREATE TABLE data_flows (id serial primary key, source_app_id integer, target_app_id integer)"))
    cleanup.execute(text(
        "INSERT INTO data_flows (source_app_id, target_app_id) VALUES (987654, 1), (2, 987654), (2, 3)"))
    _helpers._cascade_delete_application(987654)
    left = cleanup.execute(text("SELECT count(*) FROM data_flows")).scalar()
    assert left == 1
