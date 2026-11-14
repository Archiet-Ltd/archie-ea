"""The backfill turns a stored 0 into "not entered", except where the audit log shows a
person changed progress on that package. Two organisations, rolled back."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import bindparam, text

from app.models.audit_log import AuditLog
from app.services import work_package_service as service

pytestmark = pytest.mark.usefixtures("db_session")

REVISION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261114_progress_not_entered.py"


def _run_upgrade(db_session):
    spec = importlib.util.spec_from_file_location("progress_not_entered", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conn = db_session.connection()
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade()


def test_backfill_clears_stored_zeros_but_keeps_a_zero_a_person_recorded(db_session, make_org, tenant_ctx):
    ids = {}
    for tag in ("migA", "migB"):
        org = make_org(tag)
        with tenant_ctx(org.id):
            untouched = service.create_work_package(organization_id=org.id, name="Never entered", status="planned")
            entered = service.create_work_package(organization_id=org.id, name="Entered zero", status="in_progress")
            real = service.create_work_package(
                organization_id=org.id, name="Forty", status="in_progress", progress_percentage=40)
            db_session.flush()
            db_session.add(AuditLog(
                organization_id=org.id, action="UPDATE", table_name="unified_work_packages", record_id=entered.id,
                old_value={"progress_percentage": 30}, new_value={"progress_percentage": 0}))
            db_session.flush()
            ids[tag] = (untouched.id, entered.id, real.id)
    every = [i for trio in ids.values() for i in trio]
    db_session.execute(text(
        "UPDATE unified_work_packages SET progress_percentage = 0 "
        "WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)), {"ids": every[:2] + every[3:5]})
    db_session.flush()
    _run_upgrade(db_session)
    rows = dict(db_session.execute(text(
        "SELECT id, progress_percentage FROM unified_work_packages WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True)), {"ids": every}).all())
    for untouched, entered, real in ids.values():
        assert rows[untouched] is None
        assert rows[entered] == 0
        assert rows[real] == 40
