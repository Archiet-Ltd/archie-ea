"""A database built from migrations has the table ADM actions are audited in.

The baseline builds the tables of the models the app imports at boot, and the
audit log model is not one of them, so a new environment, a restore drill or a
test database had nowhere to write an ADM audit row. These build a database
from migrations alone (the deploy's ``schema-upgrade``) and act on it.
"""
from __future__ import annotations

import json
import subprocess
import sys

from sqlalchemy import create_engine, text

from tests.test_schema_migrations import (
    REPO_ROOT,
    _flask,
    _snapshot,
    _tables,
    scratch_databases,  # noqa: F401  (fixture)
)

REVISION = "20261031_adm_audit_logs"
PARENT = "20261031_fw_adopt_source"
RETIRED = ("adm_audit_summaries", "adm_data_retention_policies")

# An ADM action through the audit service, in a fresh process against `url`.
_ACT = r"""
import json, sys, uuid
from manage import app
from app import db
from app.models.organization import Organization
from app.models.user import User
from app.models.adm_audit_log import ADMAuditAction, ADMAuditLog
from app.models.adm_phase_approval import ADMPhaseApproval  # noqa: F401
from app.services.adm_audit_service import adm_audit_service

with app.app_context():
    tag = uuid.uuid4().hex[:8]
    org = Organization(name="Audit " + tag, slug="audit-" + tag)
    db.session.add(org)
    db.session.flush()
    user = User(first_name="Ada", last_name="Audit", email=tag + "@example.test",
                password="test-password-123", confirmed=True, organization_id=org.id)
    db.session.add(user)
    db.session.flush()
    entry = adm_audit_service.log_event(
        action=ADMAuditAction.CARD_CREATED, entity_type="card", entity_id=7,
        actor_id=user.id, entity_reference="Target architecture", organization_id=org.id,
    )
    db.session.commit()
    found = ADMAuditLog.query.filter_by(audit_id=entry.audit_id).one()
    print("@@ROW@@" + json.dumps([found.action, found.entity_reference, found.actor_email, user.email]))
"""


def _act(url):
    import os

    env = dict(os.environ, DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    proc = subprocess.run(
        [sys.executable, "-c", _ACT], cwd=REPO_ROOT, env=env, capture_output=True,
        timeout=1800, encoding="utf-8", errors="replace",
    )
    assert "@@ROW@@" in proc.stdout, (proc.stdout + proc.stderr)[-4000:]
    return json.loads(proc.stdout.rsplit("@@ROW@@", 1)[1].splitlines()[0])


def _count(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_an_adm_action_is_audited_on_a_database_built_from_migrations(scratch_databases):  # noqa: F811
    url = scratch_databases("admaudit")
    _flask(url, ["schema-upgrade"])

    tables = _tables(_snapshot(url))
    assert "adm_audit_logs" in tables
    assert not set(RETIRED) & tables

    action, reference, recorded_email, user_email = _act(url)
    assert (action, reference) == ("card_created", "Target architecture")
    assert recorded_email == user_email
    assert _count(url, "SELECT count(*) FROM adm_audit_logs") == 1

    _, output = _flask(url, ["reconcile-schema", "--dry-run"])
    # Other drift belongs to the later steps of the deploy; none of it may name these tables.
    assert "table(s) absent" not in output, output
    for name in ("adm_audit_logs", *RETIRED):
        assert name not in output, output


def test_upgrading_a_database_that_already_has_the_audit_table_changes_nothing(scratch_databases):  # noqa: F811
    url = scratch_databases("admaudit_prod")
    _flask(url, ["schema-upgrade"])
    _act(url)
    before = _snapshot(url)

    # Production's shape: the table and a row exist, the revision is not yet recorded.
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": PARENT})
    engine.dispose()

    code, output = _flask(url, ["schema-upgrade"], check=False)
    assert code == 0, output
    assert _snapshot(url) == before
    assert _count(url, "SELECT count(*) FROM adm_audit_logs") == 1
    assert _count(url, "SELECT version_num FROM alembic_version") == REVISION


# What production's table does when the thing an audit row points at is removed.
_RULES = {
    "actor_id": "c",
    "board_id": "n",
    "card_id": "n",
    "approval_id": "n",
    "phase_id": "n",
    "source_phase_id": "n",
    "target_phase_id": "n",
}

# Builds two organisations' worth of audit rows, removes what they point at, and reports.
_REMOVE = r"""
import json, uuid
from manage import app
from app import db
from sqlalchemy import text
from app.models.organization import Organization
from app.models.user import User
from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
from app.models.adm_phase_approval import ADMPhaseApproval
from app.models.adm_audit_log import ADMAuditAction, ADMAuditLog
from app.services.adm_audit_service import adm_audit_service


def make_org(tag):
    org = Organization(name="Audit " + tag, slug="audit-" + tag)
    db.session.add(org)
    db.session.flush()
    user = User(first_name="Ada", last_name="Audit", email=tag + "@example.test",
                password="test-password-123", confirmed=True, organization_id=org.id)
    db.session.add(user)
    db.session.flush()
    return org, user


with app.app_context():
    tag = uuid.uuid4().hex[:6]
    org_a, ada = make_org("a" + tag)
    org_b, bob = make_org("b" + tag)
    keeper = User(first_name="Kay", last_name="Keeper", email="k" + tag + "@example.test",
                  password="test-password-123", confirmed=True, organization_id=org_a.id)
    db.session.add(keeper)
    db.session.flush()

    def phase(n):
        p = ADMPhase(name="Phase " + n + tag, code=n, order=1)
        db.session.add(p)
        db.session.flush()
        return p

    def board(name):
        b = KanbanBoard(name=name, created_by_id=keeper.id, organization_id=org_a.id)
        db.session.add(b)
        db.session.flush()
        return b

    anchor = phase("A" + tag[:2])
    phase_pointed, source, target = phase("P" + tag[:2]), phase("S" + tag[:2]), phase("T" + tag[:2])
    board_pointed, board_home = board("Pointed " + tag), board("Home " + tag)
    card_pointed = KanbanCard(title="Pointed", adm_phase_id=anchor.id, board_id=board_home.id,
                              card_type="design", created_by_id=keeper.id, organization_id=org_a.id)
    card_home = KanbanCard(title="Home", adm_phase_id=anchor.id, board_id=board_home.id,
                           card_type="design", created_by_id=keeper.id, organization_id=org_a.id)
    db.session.add_all([card_pointed, card_home])
    db.session.flush()
    approval = ADMPhaseApproval(approval_number="AP-" + tag, card_id=card_home.id, board_id=board_home.id,
                                source_phase_id=anchor.id, target_phase_id=anchor.id,
                                requested_by_id=keeper.id, organization_id=org_a.id)
    db.session.add(approval)
    db.session.flush()

    def row(user, **keys):
        entry = adm_audit_service.log_event(
            action=ADMAuditAction.CARD_CREATED, entity_type="card", entity_id=1,
            actor_id=user.id, organization_id=user.organization_id, **keys)
        return entry.audit_id

    ids = {
        "board_id": row(keeper, board_id=board_pointed.id),
        "card_id": row(keeper, card_id=card_pointed.id),
        "approval_id": row(keeper, approval_id=approval.id),
        "phase_id": row(keeper, phase_id=phase_pointed.id),
        "source_phase_id": row(keeper, source_phase_id=source.id),
        "target_phase_id": row(keeper, target_phase_id=target.id),
    }
    ada_row, bob_row = row(ada), row(bob)
    db.session.commit()

    def run(sql, **p):
        db.session.execute(text(sql), p)
        db.session.commit()

    run("DELETE FROM users WHERE id = :i", i=ada.id)
    run("DELETE FROM kanban_boards WHERE id = :i", i=board_pointed.id)
    run("DELETE FROM kanban_cards WHERE id = :i", i=card_pointed.id)
    run("DELETE FROM adm_phase_approvals WHERE id = :i", i=approval.id)
    for p in (phase_pointed, source, target):
        run("DELETE FROM adm_phases WHERE id = :i", i=p.id)

    def fetch(audit_id):
        return db.session.execute(
            text("SELECT board_id, card_id, approval_id, phase_id, source_phase_id, target_phase_id "
                 "FROM adm_audit_logs WHERE audit_id = :a"), {"a": audit_id}).fetchone()

    def exists(audit_id):
        return db.session.execute(
            text("SELECT count(*) FROM adm_audit_logs WHERE audit_id = :a"), {"a": audit_id}).scalar()

    out = {
        "ada_rows": exists(ada_row),
        "bob_rows": exists(bob_row),
        "nulled": {k: (None if fetch(v) is None else fetch(v)._mapping[k]) for k, v in ids.items()},
        "kept": {k: exists(v) for k, v in ids.items()},
    }
    print("@@OUT@@" + json.dumps(out))
"""


def test_removing_what_an_audit_row_points_at_behaves_as_on_production(scratch_databases):  # noqa: F811
    import os

    url = scratch_databases("admaudit_rules")
    _flask(url, ["schema-upgrade"])

    engine = create_engine(url)
    with engine.connect() as conn:
        rules = dict(conn.execute(text(
            "SELECT a.attname, c.confdeltype FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.conrelid = 'adm_audit_logs'::regclass AND c.contype = 'f'"
        )).fetchall())
    engine.dispose()
    assert {k: v for k, v in rules.items()} == {
        k: {"c": "c", "n": "n"}[v] for k, v in _RULES.items()
    }, rules

    env = dict(os.environ, DATABASE_URL=url, TEST_DATABASE_URL=url, DEV_DATABASE_URL=url)
    env.setdefault("FLASK_CONFIG", "testing")
    proc = subprocess.run(
        [sys.executable, "-c", _REMOVE], cwd=REPO_ROOT, env=env, capture_output=True,
        timeout=1800, encoding="utf-8", errors="replace",
    )
    assert "@@OUT@@" in proc.stdout, (proc.stdout + proc.stderr)[-4000:]
    out = json.loads(proc.stdout.rsplit("@@OUT@@", 1)[1].splitlines()[0])

    # Removing a user removes their audit rows; another organisation's user keeps theirs.
    assert out["ada_rows"] == 0
    assert out["bob_rows"] == 1
    # Removing a board, card, approval or phase keeps the row and empties that key.
    assert out["kept"] == {k: 1 for k in out["kept"]}
    assert out["nulled"] == {k: None for k in out["nulled"]}
