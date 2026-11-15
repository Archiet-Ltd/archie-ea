"""Demonstration and sample history is dated when it is written, and the audit chain is never sealed again.

The change history is the hash-chained audit log: its purpose is to show that a recorded change was not
altered afterwards. A demonstration or sample company is built in one sitting, so its records are given
their dates as they are written. Nothing in the application rewrites a row that was already sealed.
"""
import ast
import datetime as dt
import os
import pathlib
import re

import pytest
from sqlalchemy import event

from app import db

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"
MODEL_TABLES = ("archimate_elements", "archimate_relationships")
# All tables the demo seed creates that are audited and should have spread dates.
SEED_AUDITED_TABLES = (
    "archimate_elements",
    "archimate_relationships",
    "application_component",
    "application_owners",
    "application_ownership",
    "business_capability",
    "solution",
    "user",
)


class _AuditUpdates:
    """Every statement that updates the audit log while it is attached."""

    def __init__(self):
        self.statements = []

    def _spy(self, conn, cursor, statement, parameters, context, executemany):
        if re.match(r'\s*UPDATE\s+"?soc2_audit_log"?\b', statement, re.I):
            self.statements.append(statement)

    def __enter__(self):
        event.listen(db.engine, "before_cursor_execute", self._spy)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, "before_cursor_execute", self._spy)


def _rows(org_id):
    """The organisation's audit rows, in the order they were sealed."""
    return [
        tuple(row) for row in db.session.execute(
            db.text(
                "SELECT id, prev_hash, row_hash, created_at FROM soc2_audit_log "
                "WHERE organization_id = :o ORDER BY id"
            ),
            {"o": org_id},
        )
    ]


def _days_the_model_was_added(org_id, tables=MODEL_TABLES):
    return db.session.execute(
        db.text(
            "SELECT count(DISTINCT created_at::date), min(created_at) FROM soc2_audit_log "
            "WHERE organization_id = :o AND action = 'create' AND table_name IN :tables"
        ).bindparams(db.bindparam("tables", expanding=True)),
        {"o": org_id, "tables": list(tables)},
    ).one()


def _assert_chain_whole(org_id):
    from app.models.audit_log import AuditLog

    result = AuditLog.verify_chain(org_id)
    rows = _rows(org_id)
    assert result["status"] == "intact", result
    assert result["unsealed"] == 0, result
    assert result["checked"] == len(rows) > 0, (result, len(rows))
    previous = None
    for _id, prev_hash, row_hash, _created in rows:
        assert prev_hash == previous
        previous = row_hash


def _architect(org, email):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=email, first_name="Sam", last_name="Founder", organization_id=org.id,
        enterprise_role="enterprise_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    return user


def _history_of_a_neighbour(org):
    """Something already recorded in another organisation, to prove a load leaves it alone."""
    from app.models.application_portfolio import ApplicationComponent

    db.session.add(ApplicationComponent(name="Neighbour ledger", organization_id=org.id, lifecycle_status="2.2 tactical"))
    db.session.commit()
    return _rows(org.id)


def test_loading_the_sample_dates_its_history_as_it_is_written_and_updates_no_sealed_row(client, login_as, make_org):
    org, neighbour = make_org("sample"), make_org("neighbour")
    user = _architect(org, "sample-owner@example.com")
    before = _history_of_a_neighbour(neighbour)
    login_as(client, user)
    with _AuditUpdates() as updates:
        assert client.post("/architecture/sample-company/load").status_code == 302
    assert updates.statements == [], updates.statements
    _assert_chain_whole(org.id)
    days, first = _days_the_model_was_added(org.id)
    assert days >= 8, "the sample's records were added on %s days" % days
    assert first <= dt.datetime.utcnow() - dt.timedelta(days=60)
    # the other organisation's rows are exactly as they were sealed, and its chain still verifies
    assert _rows(neighbour.id) == before
    _assert_chain_whole(neighbour.id)


def test_the_demonstration_seed_dates_its_history_as_it_is_written_and_updates_no_sealed_row(app, db_session, make_org):
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization

    neighbour = make_org("neighbour")
    neighbour_id = neighbour.id
    before = _history_of_a_neighbour(neighbour)
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        with _AuditUpdates() as updates:
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    assert updates.statements == [], updates.statements
    demo_id = Organization.query.filter_by(slug="lantern-quay").one().id
    _assert_chain_whole(demo_id)
    days, first = _days_the_model_was_added(demo_id, tables=SEED_AUDITED_TABLES)
    assert days >= 8, "the demonstration's records were added on %s days" % days
    assert first <= dt.datetime.utcnow() - dt.timedelta(days=60)
    assert _rows(neighbour_id) == before
    _assert_chain_whole(neighbour_id)


def test_each_seed_table_has_spread_dates(app, db_session, make_org):
    """Every table the demo seed creates should have its creates spread over multiple days."""
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    demo_id = Organization.query.filter_by(slug="lantern-quay").one().id

    for table in SEED_AUDITED_TABLES:
        result = db.session.execute(
            db.text(
                "SELECT count(DISTINCT created_at::date) FROM soc2_audit_log "
                "WHERE organization_id = :o AND action = 'create' AND table_name = :t"
            ),
            {"o": demo_id, "t": table},
        ).scalar()
        assert result >= 3, f"{table} has creates on only {result} day(s), expected at least 3"


# Map from audit table_label to the actual database table name for row counts.
_AUDIT_LABEL_TO_TABLE = {
    "application_component": "application_components",
    "business_capability": "business_capability",
    "application_owners": "application_owners",
    "application_ownership": "application_ownership",
    "archimate_elements": "archimate_elements",
    "archimate_relationships": "archimate_relationships",
    "solution": "solutions",
    "user": "users",
}


def test_every_create_in_the_audit_log_counts_a_seeded_row(app, db_session, make_org):
    """After the seed, the number of 'create' rows per table equals the number of rows in that table."""
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    demo_id = Organization.query.filter_by(slug="lantern-quay").one().id

    for table in SEED_AUDITED_TABLES:
        create_count = db.session.execute(
            db.text(
                "SELECT count(*) FROM soc2_audit_log "
                "WHERE organization_id = :o AND action = 'create' AND table_name = :t"
            ),
            {"o": demo_id, "t": table},
        ).scalar()
        db_table = _AUDIT_LABEL_TO_TABLE.get(table, table)
        row_count = db.session.execute(
            db.text(f"SELECT count(*) FROM {db_table} WHERE organization_id = :o"),
            {"o": demo_id},
        ).scalar()
        assert create_count == row_count, (
            f"{table}: {create_count} creates in history vs {row_count} rows"
        )


def test_the_value_stream_the_seed_writes_has_its_dated_create_entry(app, db_session, make_org):
    """The strategy-layer mirror of a value stream is written outside a flush, and still has its first version."""
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    demo_id = Organization.query.filter_by(slug="lantern-quay").one().id
    element_id = db.session.execute(
        db.text(
            "SELECT id FROM archimate_elements WHERE organization_id = :o AND type = 'ValueStream' "
            "AND name = 'Calibration Request to Certificate'"
        ),
        {"o": demo_id},
    ).scalar_one()
    entries = db.session.execute(
        db.text(
            "SELECT count(*) FROM soc2_audit_log WHERE organization_id = :o AND table_name = 'archimate_elements' "
            "AND action = 'create' AND record_id = :r"
        ),
        {"o": demo_id, "r": element_id},
    ).scalar()
    assert entries == 1, "the value stream has %d create entries" % entries
    _assert_chain_whole(demo_id)


def test_a_dated_block_dates_what_is_written_inside_it_and_nothing_outside(app, db_session, make_org):
    from app.models import ArchiMateElement
    from app.services.history_spread import dated_history

    org, other = make_org("dated"), make_org("other")
    now = dt.datetime(2026, 10, 20, 12, 0, 0)
    with dated_history(days_ago=(30, 10), now=now, rest=("manual", "Entered by the team")):
        old = ArchiMateElement(name="Billing", type="ApplicationComponent", layer="application", organization_id=org.id)
        newer = ArchiMateElement(name="Ledger", type="ApplicationComponent", layer="application", organization_id=org.id)
        db_session.add_all([old, newer])
        db_session.flush()
    after = ArchiMateElement(name="Portal", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(after)
    db_session.flush()
    stamped = dict(db_session.execute(
        db.text(
            "SELECT record_id, created_at FROM soc2_audit_log WHERE organization_id = :o "
            "AND table_name = 'archimate_elements' AND action = 'create'"
        ), {"o": org.id},
    ).all())
    assert stamped[old.id] == now - dt.timedelta(days=30)
    assert stamped[newer.id] == now - dt.timedelta(days=10, hours=1)
    assert stamped[after.id] > dt.datetime.utcnow() - dt.timedelta(minutes=5)
    # each record in the block says where it came from, in its own first version and on the record
    sources = dict(db_session.execute(
        db.text(
            "SELECT record_id, new_value->>'source_label' FROM soc2_audit_log WHERE organization_id = :o "
            "AND table_name = 'archimate_elements' AND action = 'create'"
        ), {"o": org.id},
    ).all())
    assert sources[old.id] == "GitHub" and sources[newer.id] == "Entered by the team"
    assert sources[after.id] is None
    assert db_session.get(ArchiMateElement, old.id).source_label == "GitHub"
    _assert_chain_whole(org.id)
    assert _rows(other.id) == []


def test_no_application_code_seals_or_rewrites_an_audit_row_except_the_audit_writer():
    """The only code that sets a seal is the audit writer, on insert. A re-seal would defeat tamper evidence."""
    writer = APP_DIR / "models" / "audit_log.py"
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path == writer:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            sets_a_seal = (
                (isinstance(node, ast.keyword) and node.arg in ("row_hash", "prev_hash"))
                or (isinstance(node, ast.Attribute) and node.attr in ("row_hash", "prev_hash")
                    and isinstance(node.ctx, ast.Store))
            )
            if sets_a_seal:
                offenders.append("%s:%s" % (path.relative_to(APP_DIR.parent), node.lineno))
    assert offenders == []
    from app.services import history_spread

    assert not hasattr(history_spread, "_reseal")
    assert not hasattr(history_spread, "chain_digest")
