"""The audit trail is complete, exportable at any size, and tamper-evident.

* ``/admin/audit-log?export=csv`` streams every matching entry of the
  caller's organisation, with no row cap, and states the row count and the
  organisation.
* Every entry is sealed into a per-organisation hash chain; altering or
  removing an entry in SQL is reported by the verify action at that entry.
* ARB, ArchiMate composer and application-rationalisation audit entries are
  copied into the one audit store with their provenance, and their earlier
  history is copied by ``flask backfill-audit-trail``, one organisation at a
  time, never into shared scope.

Uses the shared fixtures in tests/conftest.py.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #

def _user(db_session, org, *, admin=False, enterprise_role="solution_architect"):
    from app.models.user import Role, User

    user = User(
        email=f"audit-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Audit",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    role_name = "Administrator" if admin else "User"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


def _log(org, user=None, **kw):
    from app.models.audit_log import AuditLog

    kw.setdefault("action", "update")
    kw.setdefault("table_name", "application_component")
    return AuditLog.log(organization_id=org.id, user_id=getattr(user, "id", None), **kw)


def _export(client, login_as, user, query=""):
    login_as(client, user)
    resp = client.get(f"/admin/audit-log?export=csv{query}")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    rows = list(csv.DictReader(io.StringIO(body)))
    return resp, rows


# --------------------------------------------------------------------------- #
#  Export                                                                     #
# --------------------------------------------------------------------------- #

def test_export_contains_only_the_callers_organisation(db_session, make_org, client, login_as):
    org_a, org_b = make_org("audit-a"), make_org("audit-b")
    admin_a = _user(db_session, org_a, admin=True)
    admin_b = _user(db_session, org_b, admin=True)
    for i in range(3):
        _log(org_a, admin_a, record_id=i)
    for i in range(2):
        _log(org_b, admin_b, record_id=100 + i)

    resp, rows = _export(client, login_as, admin_a)
    assert resp.headers["X-Audit-Row-Count"] == "3"
    assert len(rows) == 3
    assert {r["organisation"] for r in rows} == {org_a.name}
    assert {r["entity_id"] for r in rows} == {"0", "1", "2"}

    resp, rows = _export(client, login_as, admin_b)
    assert resp.headers["X-Audit-Row-Count"] == "2"
    assert {r["organisation"] for r in rows} == {org_b.name}


def test_export_streams_every_row_beyond_ten_thousand(db_session, make_org, client, login_as):
    """25,000 entries export completely; the old export stopped at 10,000."""
    from app.models.audit_log import AuditLog

    org = make_org("audit-big")
    admin = _user(db_session, org, admin=True)
    start = datetime(2026, 1, 1)
    db_session.execute(
        AuditLog.__table__.insert(),
        [
            {
                "organization_id": org.id,
                "user_id": admin.id,
                "action": "update",
                "table_name": "application_component",
                "record_id": i,
                "created_at": start + timedelta(seconds=i),
            }
            for i in range(25_000)
        ],
    )
    db_session.flush()

    resp, rows = _export(client, login_as, admin)
    assert resp.headers["X-Audit-Row-Count"] == "25000"
    assert len(rows) == 25_000
    assert len({r["id"] for r in rows}) == 25_000
    assert {int(r["entity_id"]) for r in rows} == set(range(25_000))


def test_export_date_filter_matches_the_screen_count(db_session, make_org, client, login_as):
    org = make_org("audit-dates")
    admin = _user(db_session, org, admin=True)
    for day in (1, 2, 3):
        _log(org, admin, record_id=day, created_at=datetime(2026, 3, day, 12))

    login_as(client, admin)
    screen = client.get(
        "/admin/audit-log?date_from=2026-03-02&date_to=2026-03-03",
        headers={"Accept": "application/json"},
    ).get_json()
    assert screen["total"] == 2

    resp, rows = _export(client, login_as, admin, "&date_from=2026-03-02&date_to=2026-03-03")
    assert resp.headers["X-Audit-Row-Count"] == "2"
    assert sorted(r["entity_id"] for r in rows) == ["2", "3"]


def test_security_architect_reads_the_audit_log_and_other_personas_do_not(
    db_session, make_org, client, login_as
):
    org = make_org("audit-access")
    security = _user(db_session, org, enterprise_role="security_architect")
    solution = _user(db_session, org, enterprise_role="solution_architect")

    login_as(client, security)
    assert client.get("/admin/audit-log").status_code == 200
    login_as(client, solution)
    assert client.get("/admin/audit-log").status_code == 403


# --------------------------------------------------------------------------- #
#  Integrity chain                                                            #
# --------------------------------------------------------------------------- #

def test_chain_is_per_organisation(db_session, make_org):
    from app.models.audit_log import AuditLog

    org_a, org_b = make_org("chain-a"), make_org("chain-b")
    a1 = _log(org_a, record_id=1)
    b1 = _log(org_b, record_id=1)
    a2 = _log(org_a, record_id=2)
    b2 = _log(org_b, record_id=2)

    assert a2.prev_hash == a1.row_hash
    assert b2.prev_hash == b1.row_hash
    assert AuditLog.verify_chain(org_a.id)["status"] == "intact"
    assert AuditLog.verify_chain(org_a.id)["checked"] == 2
    assert AuditLog.verify_chain(org_b.id)["checked"] == 2


def test_every_writer_is_sealed(db_session, make_org, tenant_ctx):
    """ORM add, AuditLog.log and the mapper-event writer all join the chain."""
    from app.extensions import db
    from app.models.audit_log import AuditLog, chain_insert

    org = make_org("chain-writers")
    _log(org, record_id=1)
    db_session.add(AuditLog(organization_id=org.id, action="create", table_name="x", record_id=2))
    db_session.flush()
    chain_insert(db.session.connection(), organization_id=org.id, action="delete", table_name="x", record_id=3)

    result = AuditLog.verify_chain(org.id)
    assert result["status"] == "intact", result
    assert result["checked"] == 3


def test_row_altered_in_sql_fails_verification_at_that_row(db_session, make_org):
    from app.extensions import db
    from app.models.audit_log import AuditLog

    org_a, org_b = make_org("tamper-a"), make_org("tamper-b")
    rows = [_log(org_a, record_id=i, new_value={"n": i}) for i in range(5)]
    _log(org_b, record_id=1)
    target = rows[2]

    db_session.execute(
        db.text("UPDATE soc2_audit_log SET new_value = '{\"n\": 99}' WHERE id = :id"),
        {"id": target.id},
    )
    db_session.expire_all()

    result = AuditLog.verify_chain(org_a.id)
    assert result["status"] == "broken"
    assert result["first_broken_id"] == target.id
    assert "altered" in result["reason"]
    # Another organisation's chain is untouched by it.
    assert AuditLog.verify_chain(org_b.id)["status"] == "intact"


def _reseal_with_id_in_seal(db_session, org):
    """Re-seal ``org``'s chain the way entries were sealed while the id was covered."""
    from app.extensions import db
    from app.models.audit_log import (
        _CHAIN_INFO_KEY,
        LEGACY_CHAINED_COLUMNS,
        AuditLog,
        chain_digest,
    )

    table = AuditLog.__table__
    rows = db_session.execute(
        db.select(table).where(table.c.organization_id == org.id).order_by(table.c.id)
    ).mappings().all()
    prev = None
    for row in rows:
        digest = chain_digest(prev, row, LEGACY_CHAINED_COLUMNS)
        db_session.execute(
            db.text("UPDATE soc2_audit_log SET prev_hash = :p, row_hash = :h WHERE id = :id"),
            {"p": prev, "h": digest, "id": row["id"]},
        )
        prev = digest
    # Those entries were sealed in earlier transactions; drop this
    # transaction's cached chain tail, as a new transaction starts without it.
    db_session.connection().info.pop(_CHAIN_INFO_KEY, None)
    db_session.expire_all()
    return rows


def test_entries_sealed_with_their_id_still_verify_and_still_detect_tampering(db_session, make_org):
    from app.extensions import db
    from app.models.audit_log import AuditLog

    org = make_org("id-sealed")
    for i in range(3):
        _log(org, record_id=i, new_value={"n": i})
    rows = _reseal_with_id_in_seal(db_session, org)
    # Entries sealed since then follow on from the older ones.
    _log(org, record_id=3, new_value={"n": 3})

    result = AuditLog.verify_chain(org.id)
    assert result["status"] == "intact"
    assert result["checked"] == 4

    db_session.execute(
        db.text("UPDATE soc2_audit_log SET new_value = '{\"n\": 99}' WHERE id = :id"),
        {"id": rows[1]["id"]},
    )
    db_session.expire_all()
    result = AuditLog.verify_chain(org.id)
    assert result["status"] == "broken"
    assert result["first_broken_id"] == rows[1]["id"]


def test_row_deleted_in_sql_fails_verification_at_the_next_row(db_session, make_org):
    from app.extensions import db
    from app.models.audit_log import AuditLog

    org = make_org("tamper-del")
    rows = [_log(org, record_id=i) for i in range(4)]
    db_session.execute(db.text("DELETE FROM soc2_audit_log WHERE id = :id"), {"id": rows[1].id})

    result = AuditLog.verify_chain(org.id)
    assert result["status"] == "broken"
    assert result["first_broken_id"] == rows[2].id


def test_entries_recorded_before_sealing_are_counted_not_vouched_for(db_session, make_org):
    from app.models.audit_log import AuditLog

    org = make_org("legacy")
    db_session.execute(
        AuditLog.__table__.insert(),
        [{"organization_id": org.id, "action": "update", "table_name": "x"} for _ in range(2)],
    )
    _log(org, record_id=1)
    result = AuditLog.verify_chain(org.id)
    assert result["status"] == "intact"
    assert result["unsealed"] == 2
    assert result["checked"] == 1


def test_verify_action_is_recorded_and_shown_only_to_its_organisation(
    db_session, make_org, client, login_as
):
    from app.models.audit_log import AuditLog

    org_a, org_b = make_org("verify-a"), make_org("verify-b")
    security_a = _user(db_session, org_a, enterprise_role="security_architect")
    admin_b = _user(db_session, org_b, admin=True)
    _log(org_a, security_a, record_id=1)
    _log(org_b, admin_b, record_id=1)

    login_as(client, security_a)
    resp = client.post("/admin/audit-log")
    assert resp.status_code == 302

    recorded = AuditLog.latest_verification(org_a.id)
    assert recorded is not None
    assert recorded.user_id == security_a.id
    assert recorded.new_value["status"] == "intact"
    assert AuditLog.latest_verification(org_b.id) is None

    login_as(client, security_a)
    page = client.get("/admin/audit-log").get_data(as_text=True)
    assert "Intact" in page and "Last verified" in page

    login_as(client, admin_b)
    page = client.get("/admin/audit-log").get_data(as_text=True)
    assert "Not verified yet" in page


# --------------------------------------------------------------------------- #
#  Copies from the other audit stores                                         #
# --------------------------------------------------------------------------- #

def test_archimate_and_rationalisation_entries_are_copied_with_provenance(
    db_session, make_org, tenant_ctx
):
    from app.models.application import ApplicationComponent
    from app.models.application_rationalization import RationalizationAuditEntry
    from app.models.archimate_viewpoint import ArchimateAuditLog
    from app.models.audit_log import AuditLog

    org = make_org("copy")
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        app_row = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
        db_session.add(app_row)
        db_session.flush()

        composer = ArchimateAuditLog(user_id=user.id, action="remove_element", entity_type="element", entity_id=5)
        rational = RationalizationAuditEntry(
            application_id=app_row.id, action="score_updated", actor=str(user.id),
            before_state={"score": 1}, after_state={"score": 2},
        )
        db_session.add_all([composer, rational])
        db_session.flush()

    for source, table in ((composer, "archimate_audit_logs"), (rational, "rationalization_audit_entries")):
        copy = AuditLog.query.filter_by(source_table=table, source_id=source.id).one()
        assert copy.organization_id == org.id
        assert source.retired_into_id == copy.id
    assert AuditLog.verify_chain(org.id)["status"] == "intact"


def test_backfill_copies_history_per_organisation_and_quarantines_the_unattributable(
    app, db_session, make_org
):
    from app.extensions import db
    from app.models.audit_log import AuditLog

    org_a, org_b = make_org("bf-a"), make_org("bf-b")
    user_a = _user(db_session, org_a)
    user_b = _user(db_session, org_b)

    def _raw_arb(org_id, user_id, action="update", ts=None):
        # Raw insert: history written before the copy existed.
        return db_session.execute(
            db.text(
                "INSERT INTO arb_audit_logs (organization_id, entity_type, entity_id, action, user_id, timestamp) "
                "VALUES (:o, 'review_item', 1, :a, :u, :t) RETURNING id"
            ),
            {"o": org_id, "a": action, "u": user_id, "t": ts or datetime.utcnow()},
        ).scalar()

    a_rows = [_raw_arb(org_a.id, user_a.id) for _ in range(3)]
    b_rows = [_raw_arb(org_b.id, user_b.id) for _ in range(2)]
    orphan = _raw_arb(None, None)
    composer_a = db_session.execute(
        db.text(
            "INSERT INTO archimate_audit_logs (user_id, action, created_at) "
            "VALUES (:u, 'save', now()) RETURNING id"
        ),
        {"u": user_a.id},
    ).scalar()
    # A decision the earlier decision-only mirror already copied: merged, not duplicated.
    decided_at = datetime.utcnow()
    decision = _raw_arb(org_b.id, user_b.id, action="decision", ts=decided_at)
    earlier = AuditLog.log(
        organization_id=org_b.id, user_id=user_b.id, action="decision",
        table_name="arb:review_item", record_id=1, created_at=decided_at,
    ).id

    a_id, b_id = org_a.id, org_b.id  # the command ends its sessions per organisation
    runner = app.test_cli_runner()
    result = runner.invoke(args=["backfill-audit-trail"])
    assert result.exit_code == 0, result.output
    assert "Reconciled" in result.output
    assert f"arb_audit_logs #{orphan}" in result.output  # listed in quarantine
    assert f"merged arb_audit_logs #{decision} into soc2_audit_log #{earlier}" in result.output

    def _copies(source, ids):
        return AuditLog.query.filter(
            AuditLog.source_table == source, AuditLog.source_id.in_(ids)
        ).all()

    assert {c.organization_id for c in _copies("arb_audit_logs", a_rows)} == {a_id}
    assert len(_copies("arb_audit_logs", a_rows)) == 3
    assert {c.organization_id for c in _copies("arb_audit_logs", b_rows)} == {b_id}
    assert _copies("arb_audit_logs", [orphan]) == []
    assert _copies("arb_audit_logs", [decision]) == []
    assert [c.organization_id for c in _copies("archimate_audit_logs", [composer_a])] == [a_id]
    retired = db_session.execute(
        db.text("SELECT retired_into_id FROM arb_audit_logs WHERE id = :i"), {"i": decision}
    ).scalar()
    assert retired == earlier
    assert AuditLog.verify_chain(a_id)["status"] == "intact"
    assert AuditLog.verify_chain(b_id)["status"] == "intact"

    # Idempotent: nothing is copied twice.
    before = AuditLog.query.filter(AuditLog.organization_id.in_([a_id, b_id])).count()
    again = runner.invoke(args=["backfill-audit-trail"])
    assert again.exit_code == 0, again.output
    after = AuditLog.query.filter(AuditLog.organization_id.in_([a_id, b_id])).count()
    assert after == before
