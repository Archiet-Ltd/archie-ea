"""
flask reconcile-schema — bring an existing database's columns in line with the
ORM models.

`db.create_all()` (run by `flask init-db`) creates missing *tables* but never
adds *columns* to tables that already exist. When a model gains a column in a
later release, a long-lived database drifts: the ORM SELECTs a column Postgres
doesn't have, the request 500s, and one bad column can blank a whole page.

This command diffs every mapped model's columns against the live table and runs
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for each missing one. It is:
  - SAFE: adds columns only — never drops, retypes, or reorders. Added columns
    are always nullable, so an existing row can never violate them.
  - IDEMPOTENT: `IF NOT EXISTS` means re-running is a no-op.

A column that declares a `server_default` keeps it, so existing rows are
populated as the column is added rather than left NULL. This matters for any
column the ORM must read back — an optimistic-lock version, a status the code
treats as non-optional — where an all-NULL backfill is not a neutral starting
state but a broken one. On PostgreSQL 11+ `ADD COLUMN ... DEFAULT` is a
metadata-only operation, so this stays cheap on a large table.

It also creates the four canonical Transformation Programme tables when they are
absent. Other missing tables remain the responsibility of `flask init-db`
(`create_all`).

Deploy order (scripts/database/deploy-schema.sh):
    flask init-db && flask schema-upgrade && flask reconcile-schema

This command is the drift detector in that sequence, not the authority. Any
change it cannot make — relaxing or tightening NOT NULL, retyping or widening a
column, a constraint added after a backfill — is an Alembic revision applied by
`flask schema-upgrade` (app/commands/schema_migrations.py). On a database the
first two steps brought up to date it adds only the nullable columns models
gained since the last deploy, each listed in its output, which is the deploy
log's record of them.

Usage:
    flask --app manage reconcile-schema            # apply
    flask --app manage reconcile-schema --dry-run  # report drift, change nothing
"""
import click
from flask.cli import with_appcontext

from app import db


_TRANSFORMATION_TABLES = (
    "programme_workstreams",
    "programme_role_assignments",
    "programme_outcome_commitments",
    "measure_definitions",
    "command_idempotency_records",
    "command_materialisations",
    "operation_results",
    "transformation_outbox_events",
    "event_log",
    "transformation_candidates",
    "candidate_overlap_dispositions",
    "candidate_signals",
    "evidence_records",
    "evidence_claim_heads",
    "evidence_head_events",
    "evidence_requests",
    "transformation_options",
    "transformation_option_versions",
    "decision_briefs",
    "decision_brief_versions",
    "decision_brief_option_citations",
    "decision_brief_evidence_citations",
    "decision_events",
    "arb_subject_evidence_snapshots",
    # arb_review_cycles carries a RESTRICT FK to this table, so it must exist
    # first. Without it reconcile-schema can never converge on a database that
    # predates the submission-evidence feature: arb_review_cycles fails with
    # UndefinedTable on every pass.
    "arb_submission_evidence_snapshots",
    # Declared alongside the snapshot table in app/models/arb_submission_evidence.py
    # and equally required by the typed evidence path; omitting it is the same
    # class of miss as the tables above.
    "workbench_artifact_evidence",
    "arb_review_cycles",
    "arb_submission_events",
    "arb_decision_events",
    "arb_canonical_conditions",
    "arb_waiver_expiry_checkpoints",
    "arb_condition_evidence_records",
    "arb_condition_events",
    "delivery_export_attempts",
    "outcome_measurements",
)

_TRANSFORMATION_FOREIGN_KEYS = (
    (
        "fk_evidence_requests_submitted_evidence",
        "evidence_requests",
        "submitted_evidence_id",
        "evidence_records",
        "id",
        "RESTRICT",
    ),
    (
        "fk_evidence_requests_accepted_evidence",
        "evidence_requests",
        "accepted_evidence_id",
        "evidence_records",
        "id",
        "RESTRICT",
    ),
    (
        "fk_work_packages_strategic_initiative",
        "work_packages",
        "strategic_initiative_id",
        "strategic_initiatives",
        "id",
        "RESTRICT",
    ),
    (
        "fk_work_packages_programme_workstream",
        "work_packages",
        "programme_workstream_id",
        "programme_workstreams",
        "id",
        "RESTRICT",
    ),
    (
        "fk_work_packages_decision_brief_version",
        "work_packages",
        "decision_brief_version_id",
        "decision_brief_versions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_strategic_roadmap_items_initiative",
        "strategic_roadmap_items",
        "initiative_id",
        "strategic_initiatives",
        "id",
        "RESTRICT",
    ),
    (
        # CASCADE, not RESTRICT: organization_id comes from TenantMixin, which
        # declares ON DELETE CASCADE for every tenant table, so this entry was
        # the one place contradicting the ORM. It did two kinds of harm, found
        # independently by two lanes:
        #   - _ensure_transformation_foreign_keys DROPs the live constraint
        #     before re-adding it, so RESTRICT here actively replaced a correct
        #     CASCADE and made this the single table blocking organization
        #     deletion.
        #   - schema-drift could therefore never go green: the detector compared
        #     the models (CASCADE) against a database this command had just
        #     forced to RESTRICT, on every pass.
        # The models are the source of truth; this list has to follow them.
        "fk_strategic_roadmap_items_organization",
        "strategic_roadmap_items",
        "organization_id",
        "organizations",
        "id",
        "CASCADE",
    ),
    (
        "fk_strategic_roadmap_items_programme_workstream",
        "strategic_roadmap_items",
        "programme_workstream_id",
        "programme_workstreams",
        "id",
        "RESTRICT",
    ),
    (
        "fk_strategic_roadmap_items_work_package",
        "strategic_roadmap_items",
        "work_package_id",
        "work_packages",
        "id",
        "RESTRICT",
    ),
    (
        "fk_strategic_roadmap_items_decision_brief_version",
        "strategic_roadmap_items",
        "decision_brief_version_id",
        "decision_brief_versions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_benefits_strategic_initiative",
        "benefits",
        "strategic_initiative_id",
        "strategic_initiatives",
        "id",
        "RESTRICT",
    ),
    (
        "fk_benefits_programme_workstream",
        "benefits",
        "programme_workstream_id",
        "programme_workstreams",
        "id",
        "RESTRICT",
    ),
    (
        "fk_benefits_outcome_commitment",
        "benefits",
        "outcome_commitment_id",
        "programme_outcome_commitments",
        "id",
        "RESTRICT",
    ),
    (
        "fk_benefits_decision_brief_version",
        "benefits",
        "decision_brief_version_id",
        "decision_brief_versions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_solutions_strategic_initiative",
        "solutions",
        "initiative_id",
        "strategic_initiatives",
        "id",
        "RESTRICT",
    ),
    (
        "fk_solutions_programme_workstream",
        "solutions",
        "workstream_id",
        "programme_workstreams",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_subject_snapshot_architecture_model",
        "arb_subject_evidence_snapshots",
        "architecture_model_id",
        "architecture_models",
        "id",
        "RESTRICT",
    ),
    (
        # E2E-H: repointed from architecture_decision_records (0 rows in
        # production) to architecture_decisions -- this list is a separate
        # copy of the same spec _ARB_FK_SPECS
        # (app/models/architecture_review_board.py) declares, and reconcile-
        # schema's generic "add if missing" here collided with what
        # ensure_arb_cycle_constraints had already correctly created,
        # because this copy still named the old target.
        "fk_arb_subject_snapshot_adr",
        "arb_subject_evidence_snapshots",
        "adr_id",
        "architecture_decisions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_subject_snapshot_captured_by",
        "arb_subject_evidence_snapshots",
        "captured_by_id",
        "users",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_decision_brief",
        "arb_review_cycles",
        "decision_brief_id",
        "decision_briefs",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_solution",
        "arb_review_cycles",
        "solution_id",
        "solutions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_architecture_model",
        "arb_review_cycles",
        "architecture_model_id",
        "architecture_models",
        "id",
        "RESTRICT",
    ),
    (
        # E2E-H: same duplicate-spec collision as fk_arb_subject_snapshot_adr
        # above -- repointed to match _ARB_FK_SPECS.
        "fk_arb_review_cycle_adr",
        "arb_review_cycles",
        "adr_id",
        "architecture_decisions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_decision_brief_version",
        "arb_review_cycles",
        "decision_brief_version_id",
        "decision_brief_versions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_solution_snapshot",
        "arb_review_cycles",
        "solution_evidence_snapshot_id",
        "arb_submission_evidence_snapshots",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_subject_snapshot",
        "arb_review_cycles",
        "subject_evidence_snapshot_id",
        "arb_subject_evidence_snapshots",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_cycle_predecessor",
        "arb_review_cycles",
        "predecessor_cycle_id",
        "arb_review_cycles",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_item_decision_brief",
        "arb_review_items",
        "decision_brief_id",
        "decision_briefs",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_item_decision_brief_version",
        "arb_review_items",
        "decision_brief_version_id",
        "decision_brief_versions",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_item_solution_snapshot",
        "arb_review_items",
        "solution_evidence_snapshot_id",
        "arb_submission_evidence_snapshots",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_item_subject_snapshot",
        "arb_review_items",
        "subject_evidence_snapshot_id",
        "arb_subject_evidence_snapshots",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_review_item_cycle",
        "arb_review_items",
        "review_cycle_id",
        "arb_review_cycles",
        "id",
        "RESTRICT",
    ),
    # arb_canonical_conditions and arb_condition_evidence_records reference each
    # other, so these three FKs are declared use_alter=True and SQLAlchemy emits
    # them only from metadata.create_all()'s final ALTER pass. _ensure_transformation_tables
    # calls Table.create() per table, which never emits a use_alter constraint,
    # so on a long-lived database these three would otherwise never exist and the
    # condition evidence RESTRICT invariant would be silently unenforced.
    # Pinned by test_reconcile_schema_covers_typed_arb_surface.
    (
        "fk_arb_condition_fulfilment_evidence",
        "arb_canonical_conditions",
        "fulfilment_evidence_id",
        "arb_condition_evidence_records",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_condition_submitted_evidence",
        "arb_canonical_conditions",
        "submitted_evidence_id",
        "arb_condition_evidence_records",
        "id",
        "RESTRICT",
    ),
    (
        "fk_arb_condition_event_evidence",
        "arb_condition_events",
        "submitted_evidence_id",
        "arb_condition_evidence_records",
        "id",
        "RESTRICT",
    ),
)

_MATERIALISATION_INDEXES = (
    ("uq_work_package_materialisation", "work_packages"),
    ("uq_roadmap_item_materialisation", "strategic_roadmap_items"),
    ("uq_benefit_materialisation", "benefits"),
    ("uq_decision_brief_workstream_scope", "decision_briefs"),
    ("uq_decision_brief_candidate_scope", "decision_briefs"),
    ("uq_plateau_initiative_scope", "plateaus"),
)

_DECISION_BRIEF_SCOPE_INDEXES = {
    "uq_decision_brief_workstream_scope": (
        "organization_id, workstream_id",
        "candidate_id IS NULL",
    ),
    "uq_decision_brief_candidate_scope": (
        "organization_id, workstream_id, candidate_id",
        "candidate_id IS NOT NULL",
    ),
}

_MEMBERSHIP_TABLES = (
    "programme_workstreams",
    "programme_role_assignments",
    "programme_outcome_commitments",
    "measure_definitions",
    "work_packages",
    "strategic_roadmap_items",
    "benefits",
    "solutions",
)

_EVIDENCE_WAIVER_CHECK = (
    "waiver_id IS NULL OR (waiver_authority_id IS NOT NULL AND "
    "waiver_reason IS NOT NULL AND waiver_expires_at IS NOT NULL AND "
    "interim_accountable_id IS NOT NULL AND waived_at IS NOT NULL)"
)


def _create_transformation_tables(*, dry_run, existing_tables, added, failed):
    """Create only the new canonical tables when init-db has not run yet."""
    # These modules are not guaranteed to be reached by every CLI bootstrap;
    # import them before consulting metadata so fresh deployments cannot omit
    # the typed ARB tables.
    from app.models.architecture_review_board import ARBReviewCycle  # noqa: F401
    from app.models.arb_submission_evidence import (  # noqa: F401
        ARBSubmissionEvidenceSnapshot,
    )
    from app.models.transformation_decision import (  # noqa: F401
        ARBSubjectEvidenceSnapshot,
    )
    from app.models.arb_submission_event import ARBSubmissionEvent  # noqa: F401
    from app.models.arb_decision_event import (  # noqa: F401
        ARBCondition,
        ARBDecisionEvent,
        ARBWaiverExpiryCheckpoint,
    )
    from app.models.arb_condition_evidence import ARBConditionEvidenceRecord  # noqa: F401
    from app.models.arb_condition_event import ARBConditionEvent  # noqa: F401

    for table_name in _TRANSFORMATION_TABLES:
        if table_name in existing_tables:
            continue
        table = db.metadata.tables.get(table_name)
        if table is None:
            failed.append(f"{table_name}: model is not registered")
            continue
        label = f"table.{table_name}"
        if dry_run:
            added.append(f"{label} :: CREATE TABLE")
            continue
        try:
            # ``existing_tables`` was read from the active/default schema.
            # PostgreSQL's unqualified checkfirst lookup follows search_path
            # and can mistake a same-named public fallback table for this
            # schema's table, silently skipping the required CREATE.
            table.create(bind=db.engine, checkfirst=False)
            existing_tables.add(table_name)
            added.append(f"{label} :: CREATE TABLE")
        except Exception as exc:  # noqa: BLE001 — aggregate every reconciliation failure
            failed.append(f"{label}: {str(exc)[:120]}")


def _ensure_transformation_foreign_keys(*, dry_run, existing_tables, added, failed):
    """Install FKs that ADD COLUMN cannot carry on a long-lived schema."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    for name, table, column, target, target_column, ondelete in _TRANSFORMATION_FOREIGN_KEYS:
        if table not in existing_tables or target not in existing_tables:
            continue
        live_columns = {item["name"] for item in inspector.get_columns(table)}
        if column not in live_columns:
            continue
        existing = inspector.get_foreign_keys(table)
        matching = [
            fk
            for fk in existing
            if fk.get("constrained_columns") == [column]
            and fk.get("referred_table") == target
        ]
        if any(
            fk.get("constrained_columns") == [column]
            and fk.get("referred_table") == target
            and (fk.get("options") or {}).get("ondelete", "").upper() == ondelete
            for fk in matching
        ):
            continue
        label = f"constraint.{name}"
        if dry_run:
            added.append(f"{label} :: FOREIGN KEY")
            continue
        ddl = (
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("{column}") REFERENCES "{target}" ("{target_column}") '
            f"ON DELETE {ondelete}"
        )
        try:
            for fk in matching:
                old_name = fk.get("name")
                if old_name:
                    db.session.execute(
                        text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{old_name}"')
                    )
            db.session.execute(text(ddl))
            db.session.commit()
            added.append(f"{label} :: FOREIGN KEY")
            inspector = inspect(db.engine)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            failed.append(f"{label}: {str(exc)[:120]}")


def _ensure_connector_config_organization_fk_and_index(
    *, dry_run, existing_tables, added, failed
):
    """Install the FK and index on connector_configs.organization_id.

    ADD COLUMN IF NOT EXISTS carries the column but not its constraint or
    index on a long-lived schema, the same gap _ensure_transformation_foreign_keys
    exists to close for other tables.
    """
    from sqlalchemy import inspect, text

    table = "connector_configs"
    if table not in existing_tables:
        return
    inspector = inspect(db.engine)
    live_columns = {item["name"] for item in inspector.get_columns(table)}
    if "organization_id" not in live_columns:
        return

    existing_fks = inspector.get_foreign_keys(table)
    has_fk = any(
        fk.get("constrained_columns") == ["organization_id"]
        and fk.get("referred_table") == "organizations"
        for fk in existing_fks
    )
    if not has_fk:
        label = "constraint.fk_connector_configs_organization_id"
        if dry_run:
            added.append(f"{label} :: FOREIGN KEY")
        else:
            try:
                db.session.execute(text(
                    'ALTER TABLE "connector_configs" '
                    'ADD CONSTRAINT "fk_connector_configs_organization_id" '
                    'FOREIGN KEY ("organization_id") REFERENCES "organizations" ("id") '
                    "ON DELETE CASCADE"
                ))
                db.session.commit()
                added.append(f"{label} :: FOREIGN KEY")
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                failed.append(f"{label}: {str(exc)[:120]}")

    existing_indexes = {idx["name"] for idx in inspector.get_indexes(table)}
    if "ix_connector_configs_organization_id" not in existing_indexes:
        label = "index.ix_connector_configs_organization_id"
        if dry_run:
            added.append(f"{label} :: CREATE INDEX")
        else:
            try:
                db.session.execute(text(
                    'CREATE INDEX IF NOT EXISTS "ix_connector_configs_organization_id" '
                    'ON "connector_configs" ("organization_id")'
                ))
                db.session.commit()
                added.append(f"{label} :: CREATE INDEX")
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                failed.append(f"{label}: {str(exc)[:120]}")


def _ensure_evidence_waiver_constraint(
    *, dry_run, existing_tables, added, failed
):
    """Install and validate the Task 6 waiver invariant on upgraded databases."""
    from sqlalchemy import inspect, text

    table_name = "evidence_requests"
    constraint_name = "ck_evidence_request_waiver_complete"
    if table_name not in existing_tables:
        return

    required_columns = {
        "waiver_id",
        "waiver_authority_id",
        "waiver_reason",
        "waiver_expires_at",
        "interim_accountable_id",
        "waived_at",
    }
    live_columns = {
        item["name"] for item in inspect(db.engine).get_columns(table_name)
    }
    if not dry_run and not required_columns <= live_columns:
        failed.append(
            f"constraint.{constraint_name}: required waiver columns are missing"
        )
        return

    row = db.session.execute(
        text(
            """
            SELECT c.convalidated
            FROM pg_constraint AS c
            JOIN pg_class AS t ON t.oid = c.conrelid
            JOIN pg_namespace AS n ON n.oid = t.relnamespace
            WHERE n.nspname = current_schema()
              AND t.relname = :table_name
              AND c.conname = :constraint_name
              AND c.contype = 'c'
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    ).mappings().one_or_none()
    if row is not None and row["convalidated"]:
        return

    label = f"constraint.{constraint_name}"
    action = "CHECK NOT VALID THEN VALIDATE"
    if dry_run:
        added.append(f"{label} :: {action}")
        return

    try:
        if row is None:
            db.session.execute(
                text(
                    f'ALTER TABLE "{table_name}" ADD CONSTRAINT '
                    f'"{constraint_name}" CHECK ({_EVIDENCE_WAIVER_CHECK}) NOT VALID'
                )
            )
        db.session.execute(
            text(
                f'ALTER TABLE "{table_name}" VALIDATE CONSTRAINT '
                f'"{constraint_name}"'
            )
        )
        db.session.commit()
        added.append(f"{label} :: {action}")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        failed.append(f"{label}: {str(exc)[:120]}")


def _ensure_benefit_legacy_fk(*, dry_run, existing_tables, added, failed):
    """Replace the historic CASCADE FK without deleting any Benefit rows."""
    from sqlalchemy import inspect, text

    if not {"benefits", "enterprise_initiatives"} <= existing_tables:
        return
    inspector = inspect(db.engine)
    matching = [
        fk
        for fk in inspector.get_foreign_keys("benefits")
        if fk.get("constrained_columns") == ["initiative_id"]
        and fk.get("referred_table") == "enterprise_initiatives"
    ]
    if len(matching) == 1:
        fk = matching[0]
        if (
            fk.get("name") == "fk_benefits_legacy_enterprise_initiative"
            and (fk.get("options") or {}).get("ondelete", "").upper() == "SET NULL"
        ):
            return

    label = "constraint.fk_benefits_legacy_enterprise_initiative"
    if dry_run:
        added.append(f"{label} :: REPLACE WITH ON DELETE SET NULL")
        return
    try:
        for fk in matching:
            name = fk.get("name")
            if name:
                db.session.execute(
                    text(f'ALTER TABLE "benefits" DROP CONSTRAINT "{name}"')
                )
        db.session.execute(
            text(
                """
                ALTER TABLE "benefits"
                ADD CONSTRAINT "fk_benefits_legacy_enterprise_initiative"
                FOREIGN KEY ("initiative_id") REFERENCES "enterprise_initiatives" ("id")
                ON DELETE SET NULL
                """
            )
        )
        db.session.commit()
        added.append(f"{label} :: REPLACE WITH ON DELETE SET NULL")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        failed.append(f"{label}: {str(exc)[:120]}")


def _ensure_materialisation_indexes(*, dry_run, existing_tables, added, failed):
    """Install canonical partial uniqueness on upgraded transformation tables."""
    from sqlalchemy import text

    for index_name, table_name in _MATERIALISATION_INDEXES:
        if table_name not in existing_tables:
            continue
        if db.session.scalar(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND tablename = :table_name AND indexname = :index_name"
            ),
            {"table_name": table_name, "index_name": index_name},
        ):
            continue
        table = db.metadata.tables[table_name]
        index = next((item for item in table.indexes if item.name == index_name), None)
        if index is None:
            failed.append(f"index.{index_name}: model index is not registered")
            continue
        label = f"index.{index_name}"
        if dry_run:
            added.append(f"{label} :: CREATE UNIQUE INDEX")
            continue
        try:
            if index_name in _DECISION_BRIEF_SCOPE_INDEXES:
                columns, predicate = _DECISION_BRIEF_SCOPE_INDEXES[index_name]
                schema_name = db.session.scalar(text("SELECT current_schema()"))
                quote = db.engine.dialect.identifier_preparer.quote
                db.session.execute(
                    text(
                        f"CREATE UNIQUE INDEX {quote(index_name)} "
                        f"ON {quote(schema_name)}.{quote(table_name)} ({columns}) "
                        f"WHERE {predicate}"
                    )
                )
                db.session.commit()
            else:
                # The catalog query above already checked the active schema.
                index.create(bind=db.engine, checkfirst=False)
            added.append(f"{label} :: CREATE UNIQUE INDEX")
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            failed.append(f"{label}: {str(exc)[:120]}")


_MEMBERSHIP_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION archie_validate_transformation_membership()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'programme_workstreams' THEN
        IF NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.programme_id
              AND p.organization_id = NEW.organization_id
              AND p.record_kind = 'transformation_programme'
        ) THEN
            RAISE EXCEPTION 'workstream programme is outside its tenant or is not a transformation programme'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.lead_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = NEW.lead_id AND u.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'workstream lead is outside its tenant' USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'programme_role_assignments' THEN
        IF NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.programme_id AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'role programme is outside its tenant' USING ERRCODE = '23514';
        END IF;
        IF NEW.workstream_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_workstreams w
            WHERE w.id = NEW.workstream_id AND w.organization_id = NEW.organization_id
              AND w.programme_id = NEW.programme_id
        ) THEN
            RAISE EXCEPTION 'role workstream does not belong to its programme and tenant'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = NEW.user_id AND u.organization_id = NEW.organization_id
        ) OR NOT EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = NEW.assigned_by_id AND u.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'role user is outside its tenant' USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'programme_outcome_commitments' THEN
        IF NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.programme_id AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'outcome programme is outside its tenant' USING ERRCODE = '23514';
        END IF;
        IF NEW.workstream_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_workstreams w
            WHERE w.id = NEW.workstream_id AND w.organization_id = NEW.organization_id
              AND w.programme_id = NEW.programme_id
        ) THEN
            RAISE EXCEPTION 'outcome workstream does not belong to its programme and tenant'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM users u
            WHERE u.id = NEW.owner_id AND u.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'outcome owner is outside its tenant' USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'measure_definitions' THEN
        IF NOT EXISTS (
            SELECT 1 FROM programme_outcome_commitments o
            WHERE o.id = NEW.outcome_commitment_id
              AND o.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'measure outcome is outside its tenant' USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'work_packages' THEN
        IF NEW.strategic_initiative_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.strategic_initiative_id
              AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'work package programme is outside its tenant' USING ERRCODE = '23514';
        END IF;
        IF NEW.programme_workstream_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_workstreams w
            WHERE w.id = NEW.programme_workstream_id
              AND w.organization_id = NEW.organization_id
              AND w.programme_id = NEW.strategic_initiative_id
        ) THEN
            RAISE EXCEPTION 'work package programme and workstream disagree'
                USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'strategic_roadmap_items' THEN
        IF NEW.initiative_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.initiative_id AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'roadmap programme is outside its tenant' USING ERRCODE = '23514';
        END IF;
        IF NEW.programme_workstream_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_workstreams w
            WHERE w.id = NEW.programme_workstream_id
              AND w.organization_id = NEW.organization_id
              AND w.programme_id = NEW.initiative_id
        ) THEN
            RAISE EXCEPTION 'roadmap programme and workstream disagree' USING ERRCODE = '23514';
        END IF;
        IF NEW.work_package_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM work_packages wp
            WHERE wp.id = NEW.work_package_id AND wp.organization_id = NEW.organization_id
              AND (NEW.initiative_id IS NULL OR wp.strategic_initiative_id IS NULL
                   OR wp.strategic_initiative_id = NEW.initiative_id)
              AND (NEW.programme_workstream_id IS NULL OR wp.programme_workstream_id IS NULL
                   OR wp.programme_workstream_id = NEW.programme_workstream_id)
        ) THEN
            RAISE EXCEPTION 'roadmap work package is outside its delivery scope'
                USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'benefits' THEN
        IF NEW.initiative_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM enterprise_initiatives p
            WHERE p.id = NEW.initiative_id AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'legacy benefit initiative is outside its tenant'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.strategic_initiative_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.strategic_initiative_id
              AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'benefit programme is outside its tenant' USING ERRCODE = '23514';
        END IF;
        IF NEW.programme_workstream_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_workstreams w
            WHERE w.id = NEW.programme_workstream_id
              AND w.organization_id = NEW.organization_id
              AND w.programme_id = NEW.strategic_initiative_id
        ) THEN
            RAISE EXCEPTION 'benefit programme and workstream disagree' USING ERRCODE = '23514';
        END IF;
        IF NEW.outcome_commitment_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_outcome_commitments o
            WHERE o.id = NEW.outcome_commitment_id
              AND o.organization_id = NEW.organization_id
              AND o.programme_id = NEW.strategic_initiative_id
              AND (NEW.programme_workstream_id IS NULL OR o.workstream_id IS NULL
                   OR o.workstream_id = NEW.programme_workstream_id)
        ) THEN
            RAISE EXCEPTION 'benefit outcome is outside its programme scope'
                USING ERRCODE = '23514';
        END IF;

    ELSIF TG_TABLE_NAME = 'solutions' THEN
        IF NEW.initiative_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM strategic_initiatives p
            WHERE p.id = NEW.initiative_id AND p.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'solution programme is outside its tenant' USING ERRCODE = '23514';
        END IF;
        IF NEW.workstream_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM programme_workstreams w
            WHERE w.id = NEW.workstream_id AND w.organization_id = NEW.organization_id
              AND w.programme_id = NEW.initiative_id
        ) THEN
            RAISE EXCEPTION 'solution programme and workstream disagree' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""


def _ensure_membership_triggers(*, dry_run, existing_tables, added, failed):
    """Install deferrable database membership checks ordinary FKs cannot express."""
    from sqlalchemy import text

    present_tables = [table for table in _MEMBERSHIP_TABLES if table in existing_tables]
    missing_triggers = []
    for table in present_tables:
        # SQLAlchemy does not expose PostgreSQL triggers through Inspector.
        trigger_names = set(
            db.session.scalars(
                text(
                    """
                    SELECT tg.tgname
                    FROM pg_trigger tg
                    JOIN pg_class cls ON cls.oid = tg.tgrelid
                    JOIN pg_namespace namespace ON namespace.oid = cls.relnamespace
                    WHERE cls.relname = :table_name
                      AND namespace.nspname = current_schema()
                      AND NOT tg.tgisinternal
                    """
                ),
                {"table_name": table},
            )
        )
        if "trg_transformation_membership" not in trigger_names:
            missing_triggers.append(table)

    if dry_run:
        added.extend(
            f"trigger.{table}.trg_transformation_membership :: CREATE CONSTRAINT TRIGGER"
            for table in missing_triggers
        )
        return
    try:
        # Function bodies evolve independently of their trigger objects.  Refresh
        # the canonical body on every applying reconciliation and condition only
        # the trigger creation below.
        db.session.execute(text(_MEMBERSHIP_FUNCTION_SQL))
        for table in missing_triggers:
            db.session.execute(
                text(
                    f"""
                    CREATE CONSTRAINT TRIGGER trg_transformation_membership
                    AFTER INSERT OR UPDATE ON "{table}"
                    DEFERRABLE INITIALLY IMMEDIATE
                    FOR EACH ROW EXECUTE FUNCTION archie_validate_transformation_membership()
                    """
                )
            )
            added.append(
                f"trigger.{table}.trg_transformation_membership :: CREATE CONSTRAINT TRIGGER"
            )
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        failed.append(f"transformation_membership_triggers: {str(exc)[:120]}")


def _column_clause(col, dialect):
    """Render `"name" TYPE DEFAULT ...` for one column, or None if it can't be.

    Hand-building the DEFAULT clause would be wrong: SQLAlchemy quotes a plain
    string server_default but emits a text() one raw, and getting that backwards
    produces either a syntax error or a literal that means something else. So
    let SQLAlchemy's own compiler render it.

    NOT NULL is then stripped deliberately. It is the one part of a column
    definition an existing row can fail, and reconcile-schema's contract is that
    it never rewrites or rejects existing data. The ORM still enforces the
    constraint on write; the database simply stays permissive about rows that
    predate the column.
    """
    import re

    from sqlalchemy.schema import CreateColumn

    try:
        rendered = str(CreateColumn(col).compile(dialect=dialect)).strip()
    except Exception:
        return None
    if not rendered:
        return None
    return re.sub(r"\s+NOT\s+NULL\b", "", rendered).strip()


def _backfill_sso_mapping_organizations(*, dry_run, existing_tables, added, failed):
    """Recover the tenant key for SSO group-role mappings that predate TenantMixin.

    Unlike webhook_subscriptions (user_id) or roadmap items (their programme),
    this table has NO provenance column at all -- nothing records which admin,
    from which org, created a given mapping. Guessing is not an option. The one
    case that is genuinely unambiguous: a single-tenant install (exactly one
    Organization) has only one possible owner, matching the same fallback
    `_default_org_id()` already uses for this exact situation (mixins/core.py).
    In a real multi-tenant deployment, existing rows are left NULL and reported;
    an admin must re-save each one via /admin/sso-settings to claim it for their
    org (that route's INSERT path sets organization_id automatically, same as
    any other TenantMixin create). Until then a NULL-org mapping matches no
    org's `organization_id = :id` filter and simply stops applying -- a real,
    visible operational consequence of closing this leak, not a silent one.
    """
    from sqlalchemy import inspect, text

    table = "sso_group_role_mappings"
    if table not in existing_tables or "organizations" not in existing_tables:
        return
    live_columns = {c["name"] for c in inspect(db.engine).get_columns(table)}
    if "organization_id" not in live_columns:
        return

    before = db.session.scalar(
        text(f"SELECT count(*) FROM {table} WHERE organization_id IS NULL")
    )
    if not before:
        return
    org_count = db.session.scalar(text("SELECT count(*) FROM organizations"))
    updated = 0
    if not dry_run and org_count == 1:
        result = db.session.execute(
            text(
                f"""
                UPDATE {table}
                SET organization_id = (SELECT id FROM organizations LIMIT 1)
                WHERE organization_id IS NULL
                """
            )
        )
        updated = result.rowcount
        db.session.commit()
    unresolved = before - updated
    added.append(
        f"backfill.{table}.organization_id :: before={before}, updated={updated}, "
        f"unresolved={unresolved} (single-tenant install: {org_count == 1})"
    )
    if unresolved:
        failed.append(
            f"backfill.{table}.organization_id: {unresolved} row(s) have no "
            "provenance column and this is a multi-tenant install -- they will "
            "stop applying at SSO login until an admin re-saves them via "
            "/admin/sso-settings to claim them for their org. Not a bug: "
            "guessing which org a pre-existing mapping belongs to is worse."
        )


def _ensure_sso_mapping_tenant_unique_constraint(*, dry_run, existing_tables, added, failed):
    """Replace the old global UNIQUE(sso_group_name) with a per-tenant one.

    The single-column constraint meant two different organisations could never
    both use a group named e.g. "Admins" -- a real functional bug riding along
    with the tenant leak this whole migration closes. Postgres treats NULL as
    distinct for uniqueness purposes, so pre-existing un-backfilled (NULL-org)
    rows sharing a name are unaffected by adding the composite constraint.
    """
    from sqlalchemy import inspect, text

    table = "sso_group_role_mappings"
    old_name = "sso_group_role_mappings_sso_group_name_key"
    new_name = "uq_sso_group_role_mappings_org_group"
    if table not in existing_tables:
        return
    live_columns = {c["name"] for c in inspect(db.engine).get_columns(table)}
    if "organization_id" not in live_columns:
        return

    existing_constraints = {
        c["name"] for c in inspect(db.engine).get_unique_constraints(table)
    }
    if new_name in existing_constraints:
        return  # already migrated

    if dry_run:
        added.append(
            f"constraint.{table}.{new_name} :: would replace {old_name} "
            "with a composite (organization_id, sso_group_name) UNIQUE constraint"
        )
        return

    if old_name in existing_constraints:
        db.session.execute(
            text(f'ALTER TABLE {table} DROP CONSTRAINT "{old_name}"')
        )
    db.session.execute(
        text(
            f'ALTER TABLE {table} ADD CONSTRAINT "{new_name}" '
            "UNIQUE (organization_id, sso_group_name)"
        )
    )
    db.session.commit()
    added.append(f"constraint.{table}.{new_name} :: added, replacing {old_name}")
# Tenant-owned tables created with a platform-wide UNIQUE on a business key
# each organisation chooses for itself. Each organisation overrides a
# system-default governance gate by name and numbers its own contracts, so the
# routes' tenant-filtered duplicate checks passed and the INSERT then hit the
# global rule: once one organisation used a name, every other organisation got
# an error for it (for contracts, also learning that another tenant holds that
# number). The models declare the per-organisation rule; this brings an
# existing database into line. A platform-wide unique CONSTRAINT is dropped; a
# platform-wide unique INDEX is replaced by a plain index of the same name so
# lookups by the key stay indexed.
# (table, key column, per-organisation constraint, platform-wide rule, rule kind)
_TENANT_SCOPED_UNIQUE_KEYS = (
    ("governance_gates", "gate_name", "uq_governance_gates_org_gate_name",
     "governance_gates_gate_name_key", "constraint"),
    ("vendor_contracts", "contract_number", "uq_vendor_contracts_org_contract_number",
     "ix_vendor_contracts_contract_number", "index"),
)


def _ensure_tenant_scoped_unique_keys(*, dry_run, existing_tables, added, failed):
    """Make organisation-chosen business keys unique per organisation."""
    from sqlalchemy import inspect, text

    for table, column, new_name, old_name, old_kind in _TENANT_SCOPED_UNIQUE_KEYS:
        if table not in existing_tables:
            continue
        try:
            conn = db.session.connection()
            insp = inspect(conn)
            uniques = {u.get("name") for u in insp.get_unique_constraints(table)}
            add_new = new_name not in uniques
            if old_kind == "constraint":
                remove_old = old_name in uniques
            else:
                remove_old = any(ix.get("name") == old_name and ix.get("unique")
                                 for ix in insp.get_indexes(table))
            if not (add_new or remove_old):
                continue
            label = f"constraint.{table}.{new_name}"
            if dry_run:
                added.append(f"{label} :: would make {column} unique per organisation, "
                             f"replacing platform-wide {old_name}")
                continue
            if add_new:
                conn.execute(text(
                    f'ALTER TABLE "{table}" ADD CONSTRAINT "{new_name}" '
                    f'UNIQUE (organization_id, "{column}")'
                ))
            if remove_old and old_kind == "constraint":
                conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{old_name}"'))
            elif remove_old:
                conn.execute(text(f'DROP INDEX IF EXISTS "{old_name}"'))
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{old_name}" ON "{table}" ("{column}")'))
            db.session.commit()
            added.append(f"{label} :: {column} unique per organisation, replacing platform-wide {old_name}")
        except Exception as exc:  # noqa: BLE001 — keep going, report at end
            db.session.rollback()
            failed.append(f"constraint.{table}.{new_name}: {str(exc)[:120]}")


# Columns that were NOT NULL DEFAULT 0 although "not recorded" is a real state:
# a zero stored for an unknown figure is indistinguishable from a measured
# zero on every screen that reads it. The models now declare them nullable;
# this relaxes the constraint on an existing database. Existing rows keep
# their values - a stored 0 cannot be told apart from a real one after the
# fact, so none is rewritten.
_UNRECORDED_ALLOWED = (
    ("license_entitlements", "quantity_deployed"),
    ("license_entitlements", "quantity_used"),
)


def _relax_not_null_for_unrecorded_values(*, dry_run, existing_tables, added, failed):
    """Allow NULL (not recorded) where a column wrongly forced a zero."""
    from sqlalchemy import inspect, text

    for table, column in _UNRECORDED_ALLOWED:
        if table not in existing_tables:
            continue
        try:
            conn = db.session.connection()
            live = {c["name"]: c for c in inspect(conn).get_columns(table)}
            if column not in live or live[column].get("nullable", True):
                continue
            label = f"nullable.{table}.{column}"
            if dry_run:
                added.append(f"{label} :: would allow NULL (not recorded)")
                continue
            conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP NOT NULL'))
            conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" DROP DEFAULT'))
            db.session.commit()
            added.append(f"{label} :: NULL now means not recorded")
        except Exception as exc:  # noqa: BLE001 — keep going, report at end
            db.session.rollback()
            failed.append(f"nullable.{table}.{column}: {str(exc)[:120]}")


def _backfill_webhook_organizations(*, dry_run, existing_tables, added, failed):
    """Recover the tenant key for webhook rows that predate TenantMixin.

    `webhook_subscriptions.user_id` / `webhook_events.user_id` are varchar
    columns storing `str(User.id)` (see webhook_service.py's own comment on
    that cast), so `users.organization_id` -- itself a plain column, not
    TenantMixin, but the only provenance these rows ever had -- is the
    trustworthy source via a cast-and-join. A user_id that isn't a plain
    integer string, or that names no live user, is left NULL and reported,
    not guessed. `webhook_deliveries` has no user_id at all; its provenance
    is its own subscription (via subscription_id), falling back to its event
    (via event_id) only when the subscription itself has no organization_id
    -- both already backfilled by the two updates above it in this function,
    so ordering matters here.
    """
    from sqlalchemy import inspect, text

    if "webhook_subscriptions" not in existing_tables or "users" not in existing_tables:
        return

    def _backfill_by_user(table: str):
        live_columns = {c["name"] for c in inspect(db.engine).get_columns(table)}
        if "organization_id" not in live_columns or "user_id" not in live_columns:
            return
        before = db.session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE organization_id IS NULL")
        )
        eligible = db.session.scalar(
            text(
                f"""
                SELECT count(*)
                FROM {table} t
                JOIN users u ON u.id = CAST(t.user_id AS INTEGER)
                WHERE t.organization_id IS NULL
                  AND t.user_id ~ '^[0-9]+$'
                  AND u.organization_id IS NOT NULL
                """
            )
        )
        updated = eligible
        if not dry_run and eligible:
            result = db.session.execute(
                text(
                    f"""
                    UPDATE {table} AS t
                    SET organization_id = u.organization_id
                    FROM users AS u
                    WHERE u.id = CAST(t.user_id AS INTEGER)
                      AND t.organization_id IS NULL
                      AND t.user_id ~ '^[0-9]+$'
                      AND u.organization_id IS NOT NULL
                    """
                )
            )
            updated = result.rowcount
            db.session.commit()
        # A NULL user_id (system/external events) is an expected, not a
        # failure, case -- only count rows that HAD a user_id we could not
        # resolve (unknown user, or the rare non-numeric legacy value).
        unresolved = db.session.scalar(
            text(
                f"""
                SELECT count(*) FROM {table} t
                WHERE t.organization_id IS NULL
                  AND t.user_id IS NOT NULL
                  AND NOT (
                    t.user_id ~ '^[0-9]+$'
                    AND EXISTS (
                        SELECT 1 FROM users u
                        WHERE u.id = CAST(t.user_id AS INTEGER)
                          AND u.organization_id IS NOT NULL
                    )
                  )
                """
            )
        )
        if before:
            added.append(
                f"backfill.{table}.organization_id :: before={before}, "
                f"updated={updated}, unresolved={unresolved}"
            )
        if unresolved:
            failed.append(
                f"backfill.{table}.organization_id: {unresolved} row(s) with a "
                "user_id that names no live user with a known organization"
            )

    _backfill_by_user("webhook_subscriptions")
    if "webhook_events" in existing_tables:
        _backfill_by_user("webhook_events")

    if "webhook_deliveries" not in existing_tables:
        return
    live_columns = {c["name"] for c in inspect(db.engine).get_columns("webhook_deliveries")}
    if "organization_id" not in live_columns:
        return
    before = db.session.scalar(
        text("SELECT count(*) FROM webhook_deliveries WHERE organization_id IS NULL")
    )
    updated = 0
    if not dry_run and before:
        result = db.session.execute(
            # tenancy-ok: one-time backfill, retirement 2026-12-31
            text(
                """
                UPDATE webhook_deliveries AS d
                SET organization_id = s.organization_id
                FROM webhook_subscriptions AS s
                WHERE s.id = d.subscription_id
                  AND d.organization_id IS NULL
                  AND s.organization_id IS NOT NULL
                """
            )
        )
        updated += result.rowcount
        db.session.commit()
        if "webhook_events" in existing_tables:
            result = db.session.execute(
                # tenancy-ok: one-time backfill, retirement 2026-12-31
                text(
                    """
                    UPDATE webhook_deliveries AS d
                    SET organization_id = e.organization_id
                    FROM webhook_events AS e
                    WHERE e.id = d.event_id
                      AND d.organization_id IS NULL
                      AND e.organization_id IS NOT NULL
                    """
                )
            )
            updated += result.rowcount
            db.session.commit()
    unresolved = db.session.scalar(
        text("SELECT count(*) FROM webhook_deliveries WHERE organization_id IS NULL")
    )
    if before:
        added.append(
            f"backfill.webhook_deliveries.organization_id :: before={before}, "
            f"updated={updated}, unresolved={unresolved}"
        )
    if unresolved:
        failed.append(
            f"backfill.webhook_deliveries.organization_id: {unresolved} row(s) "
            "whose subscription and event are both missing or org-less"
        )


def _backfill_document_chunk_organizations(*, dry_run, existing_tables, added, failed):
    """Recover the tenant key for DocumentChunkEmbedding rows that predate
    TenantMixin, via document_id -> ai_chat_document_uploads (already scoped).

    document_id carries no FK constraint (see the model's own comment), so a
    chunk whose document was since deleted, or whose id never matched a real
    upload, is left NULL and reported -- not guessed.
    """
    from sqlalchemy import inspect, text

    required = {"document_chunk_embeddings", "ai_chat_document_uploads"}
    if not required <= existing_tables:
        return
    live_columns = {
        c["name"] for c in inspect(db.engine).get_columns("document_chunk_embeddings")
    }
    if "organization_id" not in live_columns:
        return

    before = db.session.scalar(
        text("SELECT count(*) FROM document_chunk_embeddings WHERE organization_id IS NULL")
    )
    if not before:
        return
    eligible = db.session.scalar(
        text(
            """
            SELECT count(*)
            FROM document_chunk_embeddings c
            JOIN ai_chat_document_uploads d ON d.id = c.document_id
            WHERE c.organization_id IS NULL
              AND d.organization_id IS NOT NULL
            """
        )
    )
    updated = eligible
    if not dry_run and eligible:
        result = db.session.execute(
            # tenancy-ok: one-time backfill, retirement 2026-12-31
            text(
                """
                UPDATE document_chunk_embeddings AS c
                SET organization_id = d.organization_id
                FROM ai_chat_document_uploads AS d
                WHERE d.id = c.document_id
                  AND c.organization_id IS NULL
                  AND d.organization_id IS NOT NULL
                """
            )
        )
        updated = result.rowcount
        db.session.commit()
    unresolved = before - updated
    added.append(
        f"backfill.document_chunk_embeddings.organization_id :: before={before}, "
        f"updated={updated}, unresolved={unresolved}"
    )
    if unresolved:
        failed.append(
            f"backfill.document_chunk_embeddings.organization_id: {unresolved} "
            "row(s) whose document_id names no live ai_chat_document_uploads row"
        )


def _ensure_condition_evidence_canonical_document(
    *, dry_run, existing_tables, added, failed
):
    """Make the hash preimage mandatory without inventing it for legacy rows."""
    from sqlalchemy import inspect, text

    table_name = "arb_condition_evidence_records"
    if table_name not in existing_tables:
        return
    columns = {column["name"]: column for column in inspect(db.engine).get_columns(table_name)}
    column = columns.get("canonical_document")
    if column is None or not column.get("nullable", True):
        return
    label = f"{table_name}.canonical_document :: SET NOT NULL"
    try:
        gaps = db.session.execute(
            text(
                "SELECT count(*) FROM arb_condition_evidence_records "
                "WHERE canonical_document IS NULL "
                "/* tenancy-ok: reconcile measures all tenants without exposing rows */"
            )
        ).scalar_one()
        if gaps:
            raise RuntimeError(
                f"{gaps} historical condition-evidence row(s) lack their exact hash preimage"
            )
        if dry_run:
            added.append(label)
            return
        db.session.execute(
            text(
                "ALTER TABLE arb_condition_evidence_records "
                "ALTER COLUMN canonical_document SET NOT NULL"
            )
        )
        db.session.commit()
        added.append(label)
    except Exception as exc:  # noqa: BLE001 — explicit blocking schema gap
        db.session.rollback()
        failed.append(f"{label}: {str(exc)[:120]}")


def _ensure_enum_members(*, dry_run, existing_tables, added, failed, blocking):
    """Add missing labels to native PostgreSQL enum types (ADD VALUE only).

    Two models may share one PG type name with different Python members —
    `batch_jobs.status` and `batch_import_job.status` both use `batchjobstatus`,
    and whichever model's `create_all` ran first fixed the label set. Measured in
    production on 5 Sep 2026: the type lacked RUNNING and RECOVERING, so
    `BatchProcessingService` could never mark a job running. ADD COLUMN cannot
    fix a label set; this is the enum counterpart — additive, idempotent, never
    renames or removes. Runs on an autocommit connection because ADD VALUE is
    refused inside a transaction block on older servers.
    """
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import ENUM as PGEnum

    if db.engine.dialect.name != "postgresql":
        return
    wanted: dict[str, list[str]] = {}
    for table in db.metadata.tables.values():
        if table.name not in existing_tables:
            continue
        for col in table.columns:
            typ = col.type
            if not isinstance(typ, (PGEnum, db.Enum)) or not getattr(typ, "name", None):
                continue
            labels = wanted.setdefault(typ.name, [])
            for label in typ.enums:
                if label not in labels:
                    labels.append(label)
    if not wanted:
        return
    try:
        rows = db.session.execute(text(
            "SELECT t.typname, e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid WHERE t.typname = ANY(:names)"
        ), {"names": list(wanted)}).all()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        failed.append(f"enum_members_inspection: {str(exc)[:120]}")
        return
    live: dict[str, set[str]] = {}
    for typname, label in rows:
        live.setdefault(typname, set()).add(label)
    for typname, labels in wanted.items():
        if typname not in live:
            continue  # type not created yet; create_all owns that
        for label in labels:
            if label in live[typname]:
                continue
            item = f"enum {typname} += {label}"
            if dry_run:
                added.append(item)
                continue
            try:
                with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text(
                        f'ALTER TYPE "{typname}" ADD VALUE IF NOT EXISTS ' + "'" + label.replace("'", "''") + "'"
                    ))
                added.append(item)
            except Exception as exc:  # noqa: BLE001
                # Measured in production 6 Sep 2026: the deploy role does not
                # own every enum type any more than it owns every table (see
                # the table-ownership gap this same command already tolerates
                # via `blocking`) - ALTER TYPE then raises InsufficientPrivilege
                # on a type this role never created. That is an infrastructure
                # permissions gap to fix by granting ownership, not a schema
                # defect, and a missing enum label degrades one write path
                # rather than blocking every INSERT the way a NOT NULL column
                # gap does - it must not fail the whole deploy.
                message = str(exc)
                if "InsufficientPrivilege" in message or "must be owner of type" in message:
                    blocking.append(
                        f"{item}: insufficient privilege - grant ownership of \"{typname}\" "
                        "to the deploy role"
                    )
                else:
                    failed.append(f"{item}: {message[:120]}")


def _reconcile(dry_run=False):
    """Return (added, failed, missing_tables, blocking) lists of "table.column".

    `blocking` is the REVERSE direction: columns the live database has that the
    models do not declare, restricted to NOT NULL columns with no server default.
    Those are the ones that break writes — the ORM omits the column from its INSERT
    and Postgres rejects the row.

    This direction was previously invisible. `value_streams.organization_id` was
    NOT NULL in production while the ValueStream model did not declare the column
    at all, so every attempt to create a value stream failed with NotNullViolation,
    and this command still reported "0 column(s) would add" because it only ever
    compared model -> database.
    """
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    active_schema = db.session.scalar(text("SELECT current_schema()"))
    existing_tables = set(insp.get_table_names(schema=active_schema))
    dialect = db.engine.dialect
    added, failed, missing_tables, blocking = [], [], [], []

    for table in db.metadata.tables.values():
        if table.name not in existing_tables:
            continue
        model_cols = {c.name for c in table.columns}
        for live in insp.get_columns(table.name):
            if live["name"] in model_cols:
                continue
            if live.get("nullable", True):
                continue  # extra but harmless: the ORM simply never writes it
            if live.get("default") is not None or live.get("autoincrement"):
                continue  # the database fills it
            blocking.append(f"{table.name}.{live['name']} :: NOT NULL, no default")

    # .tables.values() (not sorted_tables) so FK-cycle tables are still checked.
    for table in db.metadata.tables.values():
        if table.name not in existing_tables:
            continue
        live_cols = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in live_cols:
                continue
            try:
                coltype = col.type.compile(dialect=dialect)
            except Exception:
                coltype = "TEXT"
            coldef = _column_clause(col, dialect) or f'"{col.name}" {coltype}'
            label = f"{table.name}.{col.name}"
            if dry_run:
                added.append(f"{label} :: {coltype}")
                continue
            ddl = (
                f'ALTER TABLE "{table.name}" '
                f'ADD COLUMN IF NOT EXISTS {coldef}'
            )
            try:
                db.session.execute(text(ddl))
                db.session.commit()
                added.append(f"{label} :: {coltype}")
            except Exception as exc:  # noqa: BLE001 — keep going, report at end
                db.session.rollback()
                failed.append(f"{label}: {str(exc)[:120]}")

    # Upgrade existing transformation tables before creating new ones.  Each
    # guarded table installs the complete transformation guard set in its
    # after_create hook; creating a new table while an older peer still lacks a
    # newly introduced column makes that hook fail and rolls back the upgrade.
    _create_transformation_tables(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    # Table creation changes the catalog; do not keep using a stale inspector.
    insp = inspect(db.engine)
    existing_tables = set(insp.get_table_names(schema=active_schema))
    missing_tables.extend(
        table.name
        for table in db.metadata.tables.values()
        if table.name not in existing_tables
    )

    _ensure_enum_members(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
        blocking=blocking,
    )
    _backfill_sso_mapping_organizations(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_sso_mapping_tenant_unique_constraint(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_tenant_scoped_unique_keys(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _relax_not_null_for_unrecorded_values(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _backfill_document_chunk_organizations(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _backfill_webhook_organizations(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_evidence_waiver_constraint(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_transformation_foreign_keys(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_connector_config_organization_fk_and_index(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_benefit_legacy_fk(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_materialisation_indexes(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_membership_triggers(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )
    _ensure_condition_evidence_canonical_document(
        dry_run=dry_run,
        existing_tables=existing_tables,
        added=added,
        failed=failed,
    )

    if dry_run:
        try:
            from app.models.transformation_db_guards import (
                inspect_transformation_db_guards,
            )

            guard_drift = inspect_transformation_db_guards(db.session.connection())
            failed.extend(
                f"transformation_db_guards:{item}" for item in guard_drift
            )
        except Exception as exc:  # noqa: BLE001 — report inspection failure
            failed.append(f"transformation_db_guards_inspection: {str(exc)[:120]}")
    else:
        try:
            from app.models.transformation_db_guards import (
                ensure_transformation_db_guards,
            )

            ensure_transformation_db_guards(db.session.connection())
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 — report alongside column failures
            db.session.rollback()
            failed.append(f"transformation_db_guards: {str(exc)[:120]}")

    execution_tables = {"delivery_export_attempts", "outcome_measurements"}
    if execution_tables <= existing_tables:
        try:
            from app.models.transformation_execution import (
                ensure_execution_history_immutability,
                inspect_execution_history_immutability,
            )

            execution_drift = inspect_execution_history_immutability(
                db.session.connection()
            )
            if dry_run:
                failed.extend(
                    f"execution_history_guards:{item}" for item in execution_drift
                )
            elif execution_drift:
                ensure_execution_history_immutability(db.session.connection())
                db.session.commit()
                added.extend(
                    f"execution_history_guards:{item}" for item in execution_drift
                )
        except Exception as exc:  # noqa: BLE001 — report inspection/repair failure
            db.session.rollback()
            failed.append(f"execution_history_guards: {str(exc)[:120]}")

    if not dry_run:
        try:
            from app.models.arb_submission_evidence import (
                ensure_evidence_immutability_triggers,
            )

            ensure_evidence_immutability_triggers(db.session.connection())
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 — report alongside column failures
            db.session.rollback()
            failed.append(f"evidence_immutability_triggers: {str(exc)[:120]}")

    if dry_run:
        # When either typed table is absent, dry-run already reports its CREATE
        # action above.  Its functions/triggers cannot exist yet, so describing
        # those dependent objects as failures would turn a repairable pre-feature
        # schema into a false red gate.
        required_arb_tables = {
            "arb_subject_evidence_snapshots",
            "arb_review_cycles",
            "arb_review_items",
        }
        if required_arb_tables <= existing_tables:
            try:
                from app.models.architecture_review_board import (
                    inspect_arb_cycle_constraints,
                )

                arb_drift = inspect_arb_cycle_constraints(db.session.connection())
                failed.extend(f"typed_arb_constraints:{item}" for item in arb_drift)
            except Exception as exc:  # noqa: BLE001 — report inspection failure
                failed.append(
                    f"typed_arb_constraints_inspection: {str(exc)[:120]}"
                )
    else:
        try:
            from app.models.architecture_review_board import (
                ensure_arb_cycle_constraints,
            )
            from app.models.transformation_decision import (
                ensure_arb_subject_snapshot_immutability,
            )
            from app.models.arb_submission_event import (
                ensure_arb_submission_event_guards,
            )
            from app.models.arb_decision_event import ensure_arb_decision_guards
            from app.models.arb_condition_evidence import ensure_arb_condition_evidence_guards
            from app.models.arb_condition_event import ensure_arb_condition_event_guards

            connection = db.session.connection()
            ensure_arb_subject_snapshot_immutability(connection)
            ensure_arb_cycle_constraints(connection)
            ensure_arb_submission_event_guards(connection)
            ensure_arb_decision_guards(connection)
            ensure_arb_condition_evidence_guards(connection)
            ensure_arb_condition_event_guards(connection)
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 — report alongside schema failures
            db.session.rollback()
            failed.append(f"typed_arb_constraints: {str(exc)[:120]}")

    return added, failed, missing_tables, blocking


@click.command("reconcile-schema")
@click.option("--dry-run", is_flag=True, help="Report drift without altering anything.")
@with_appcontext
def reconcile_schema(dry_run):
    """Add columns the models declare but existing tables lack (safe, idempotent)."""
    added, failed, missing_tables, blocking = _reconcile(dry_run=dry_run)

    verb = "would add" if dry_run else "added"
    click.echo(f"reconcile-schema: {len(added)} column(s) {verb}.")
    for a in added:
        click.echo(f"  + {a}")
    if missing_tables:
        click.echo(
            f"\n{len(missing_tables)} table(s) absent — run 'flask init-db' to "
            f"create them: {', '.join(sorted(missing_tables)[:10])}"
            + (" ..." if len(missing_tables) > 10 else "")
        )
    if blocking:
        click.echo(
            f"\n{len(blocking)} known, non-fatal drift item(s) - a NOT NULL column "
            "the models don't declare (INSERTs into that table will fail) or an "
            "enum label the deploy role lacks ownership to add (that value can't "
            "be written until ownership is granted):"
        )
        for b in blocking:
            click.echo(f"  ! {b}")

    if failed:
        click.echo(f"\n{len(failed)} column(s) FAILED:")
        for f in failed:
            click.echo(f"  ! {f}")
        raise SystemExit(1)
    if not added and not dry_run:
        click.echo("Schema already matches the models. Nothing to do.")


def init_app(app):
    """Register the reconcile-schema and schema-upgrade CLI commands."""
    from app.commands.schema_migrations import init_app as init_schema_migrations

    app.cli.add_command(reconcile_schema)
    init_schema_migrations(app)
