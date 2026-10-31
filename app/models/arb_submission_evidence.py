"""Immutable evidence captured when a solution enters ARB review."""

from datetime import datetime
import hashlib
import json
import re

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import db
from app.models.mixins import TenantMixin


class ARBSubmissionEvidenceSnapshot(TenantMixin, db.Model):
    """Append-only copy of the evidence used for an ARB submission decision."""

    __tablename__ = "arb_submission_evidence_snapshots"
    __table_args__ = (
        db.UniqueConstraint("review_item_id", name="uq_arb_submission_snapshot_review"),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True
    )
    review_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "arb_review_items.id",
            name="fk_arb_submission_snapshot_review_item",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
        index=True,
    )
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=True, index=True)
    workspace_id = db.Column(
        db.Integer, db.ForeignKey("solution_analysis_sessions.id"), nullable=True, index=True
    )
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    schema_version = db.Column(db.Integer, nullable=True)
    workflow_type = db.Column(db.String(30), nullable=True)
    captured_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    checks = db.Column(db.JSON, nullable=True)
    artifacts = db.Column(db.JSON, nullable=True)
    governance_result = db.Column(db.JSON, nullable=True)
    request_assertions = db.Column(db.JSON, nullable=True)
    content_hash = db.Column(db.String(64), nullable=True, index=True)

    review_item = db.relationship("ARBReviewItem", foreign_keys=[review_item_id])

    def canonical_content(self):
        return {
            "schema_version": self.schema_version,
            "organization_id": self.organization_id,
            "review_item_id": self.review_item_id,
            "solution_id": self.solution_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "workflow_type": self.workflow_type,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "checks": self.checks or {},
            "artifacts": self.artifacts or {},
            "governance_result": self.governance_result or {},
            "request_assertions": self.request_assertions or {},
        }

    def recompute_content_hash(self):
        return hashlib.sha256(
            json.dumps(
                self.canonical_content(), sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()


class WorkbenchArtifactEvidence(TenantMixin, db.Model):
    """Append-only persisted evidence for one named workbench artifact version."""

    __tablename__ = "workbench_artifact_evidence"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "solution_id",
            "name",
            "version",
            name="uq_workbench_artifact_evidence_version",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=True, index=True
    )
    workspace_id = db.Column(
        db.Integer, db.ForeignKey("solution_analysis_sessions.id"), nullable=True, index=True
    )
    solution_id = db.Column(db.Integer, db.ForeignKey("solutions.id"), nullable=True, index=True)
    name = db.Column(db.String(80), nullable=True, index=True)
    state = db.Column(db.String(30), nullable=True)
    payload = db.Column(db.JSON, nullable=True)
    content_hash = db.Column(db.String(64), nullable=True, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    captured_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    version = db.Column(db.Integer, nullable=True)

    @classmethod
    def capture(cls, **values):
        captured_at = values.pop("captured_at", None) or datetime.utcnow()
        version = values.pop("version", None)
        if version is None:
            version = (
                db.session.execute(
                    db.select(db.func.max(cls.version)).where(
                        cls.organization_id == values["organization_id"],
                        cls.workspace_id == values["workspace_id"],
                        cls.solution_id == values["solution_id"],
                        cls.name == values["name"],
                    )
                ).scalar()
                or 0
            ) + 1
        canonical = {
            **values,
            "captured_at": captured_at.isoformat(),
            "version": version,
        }
        row = cls(
            **values,
            captured_at=captured_at,
            version=version,
            content_hash=hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode()
            ).hexdigest(),
        )
        db.session.add(row)
        return row


def ensure_evidence_immutability_triggers(connection):
    """Install PostgreSQL append-only triggers for fresh and existing schemas."""
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(
        "SELECT pg_advisory_xact_lock(hashtext('archie_evidence_immutability_triggers'))"
    )
    from app.models.transformation_db_guards import _ORG_DELETION_ADMITS_SQL

    connection.exec_driver_sql(_ORG_DELETION_ADMITS_SQL)
    connection.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION reject_archie_evidence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF archie_org_deletion_admits(OLD.organization_id) THEN
                    RETURN OLD;
                END IF;
            END IF;
            RAISE EXCEPTION 'ARB and workbench evidence records are append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    _ensure_snapshot_review_fk(connection)
    _ensure_evidence_triggers(connection)


def _ensure_snapshot_review_fk(connection):
    """Repair the cyclic Solution snapshot FK on upgraded PostgreSQL schemas."""
    tables_ready = connection.exec_driver_sql(
        "SELECT to_regclass(current_schema() || '.arb_submission_evidence_snapshots') "
        "IS NOT NULL AND to_regclass(current_schema() || '.arb_review_items') IS NOT NULL"
    ).scalar()
    if not tables_ready:
        return
    row = connection.exec_driver_sql(
        """
        SELECT c.conname, c.condeferrable, c.condeferred,
               target.relname AS target_table
        FROM pg_constraint AS c
        JOIN pg_class AS source ON source.oid = c.conrelid
        JOIN pg_namespace AS n ON n.oid = source.relnamespace
        JOIN pg_class AS target ON target.oid = c.confrelid
        WHERE n.nspname = current_schema()
          AND source.relname = 'arb_submission_evidence_snapshots'
          AND c.contype = 'f'
          AND c.conkey = ARRAY[
              (SELECT attnum FROM pg_attribute
               WHERE attrelid = source.oid AND attname = 'review_item_id')
          ]::smallint[]
        """
    ).mappings().one_or_none()
    if (
        row is not None
        and row["conname"] == "fk_arb_submission_snapshot_review_item"
        and row["target_table"] == "arb_review_items"
        and row["condeferrable"]
        and row["condeferred"]
    ):
        return
    if row is not None:
        preparer = connection.dialect.identifier_preparer
        constraint_name = preparer.quote(row["conname"])
        connection.exec_driver_sql(
            "ALTER TABLE arb_submission_evidence_snapshots "
            f"DROP CONSTRAINT {constraint_name}"
        )
    connection.exec_driver_sql(
        "ALTER TABLE arb_submission_evidence_snapshots "
        "ADD CONSTRAINT fk_arb_submission_snapshot_review_item "
        "FOREIGN KEY (review_item_id) REFERENCES arb_review_items(id) "
        "DEFERRABLE INITIALLY DEFERRED"
    )


def _ensure_evidence_triggers(connection):
    connection.exec_driver_sql(
        """
        DO $$
        DECLARE table_name text;
        BEGIN
            FOREACH table_name IN ARRAY ARRAY[
                'arb_submission_evidence_snapshots',
                'workbench_artifact_evidence'
            ] LOOP
                IF to_regclass('public.' || table_name) IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM pg_trigger
                       WHERE tgname = 'trg_reject_evidence_mutation'
                         AND tgrelid = to_regclass('public.' || table_name)
                   ) THEN
                    EXECUTE format(
                        'CREATE TRIGGER trg_reject_evidence_mutation '
                        'BEFORE UPDATE OR DELETE ON %%I '
                        'FOR EACH ROW EXECUTE FUNCTION reject_archie_evidence_mutation()',
                        table_name
                    );
                END IF;
            END LOOP;
        END;
        $$
        """
    )


def evidence_immutability_is_installed(connection):
    """Verify the PostgreSQL function and enabled triggers without schema writes."""
    if connection.dialect.name != "postgresql":
        return False
    return bool(
        connection.exec_driver_sql(
            """
            SELECT
                to_regprocedure('reject_archie_evidence_mutation()') IS NOT NULL
                AND (
                    SELECT count(*) = 2
                    FROM pg_trigger
                    WHERE tgname = 'trg_reject_evidence_mutation'
                      AND tgenabled <> 'D'
                      AND tgrelid IN (
                          to_regclass('public.arb_submission_evidence_snapshots'),
                          to_regclass('public.workbench_artifact_evidence')
                      )
                )
            """
        ).scalar()
    )


def _install_evidence_guards_after_metadata_create(_target, connection, **_kwargs):
    """Install cross-table guards only after every mapped table exists."""
    ensure_evidence_immutability_triggers(connection)


event.listen(db.metadata, "after_create", _install_evidence_guards_after_metadata_create)


def _org_deletion_in_progress(connection):
    """True while an organisation delete's transaction-local marker is set.

    The database trigger (``reject_archie_evidence_mutation``) still makes the precise,
    per-row check that the row's own organisation matches the marker; this Python-level
    guard only needs to know whether to let a DELETE reach the database at all.
    """
    marker = connection.scalar(db.text("SELECT current_setting('archie.deleting_organisation', TRUE)"))
    return bool(marker)


def _reject_snapshot_mutation(mapper, connection, target):
    if _org_deletion_in_progress(connection):
        return
    raise ValueError("ARB submission evidence snapshots are append-only")


event.listen(ARBSubmissionEvidenceSnapshot, "before_update", _reject_snapshot_mutation)
event.listen(ARBSubmissionEvidenceSnapshot, "before_delete", _reject_snapshot_mutation)
event.listen(WorkbenchArtifactEvidence, "before_update", _reject_snapshot_mutation)
event.listen(WorkbenchArtifactEvidence, "before_delete", _reject_snapshot_mutation)


_IMMUTABLE_UPDATE_PATTERN = re.compile(
    r"^\s*UPDATE\s+[\"']?(?:arb_submission_evidence_snapshots|workbench_artifact_evidence)[\"']?\b",
    re.IGNORECASE,
)
_IMMUTABLE_DELETE_PATTERN = re.compile(
    r"^\s*DELETE\s+FROM\s+[\"']?(?:arb_submission_evidence_snapshots|workbench_artifact_evidence)[\"']?\b",
    re.IGNORECASE,
)


@event.listens_for(Engine, "before_cursor_execute", retval=True)
def _guard_append_only_sql(conn, _cursor, statement, parameters, _context, _executemany):
    if _IMMUTABLE_UPDATE_PATTERN.match(statement):
        raise ValueError("ARB and workbench evidence records are append-only")
    if _IMMUTABLE_DELETE_PATTERN.match(statement) and not _org_deletion_in_progress(conn):
        raise ValueError("ARB and workbench evidence records are append-only")
    return statement, parameters
