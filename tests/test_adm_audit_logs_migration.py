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
