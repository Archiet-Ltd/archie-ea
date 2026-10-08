#!/usr/bin/env python
import os
import subprocess
import sys
import logging

# Force UTF-8 on stdout/stderr before anything prints.
#
# Several CLI commands below print non-ASCII progress markers (check marks, warning
# signs). On Windows the default console encoding is cp1252, which cannot encode
# them, so `flask --app manage init-db` died mid-run with:
#
#     UnicodeEncodeError: 'charmap' codec can't encode character '✓'
#
# That aborted the documented setup path from README.md partway through — after some
# tables were created but before the final commit. Reconfiguring here fixes the whole
# class of failure rather than the ~30 individual call sites, and covers future ones.
# errors="replace" guarantees output can never again crash a command.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # Not a TextIOWrapper (redirected/wrapped stream) — nothing to reconfigure.
        pass

# Load .env file if present — allows API keys to be set without restarting
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=False)
except ImportError:
    pass

import click
from flask_migrate import Migrate

# Optional Redis/RQ imports
try:
    from redis import Redis
    from rq import Connection, Queue, Worker

    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

# Import app components without creating app yet
from app import db
from app.models import Role, User
from config import Config

# ===== CLI COMMAND DEFINITIONS =====

# We'll define commands using a different approach since app isn't created yet
# These will be registered after app creation


def register_cli_commands(app):
    """Register all CLI commands with the Flask app."""

    @app.shell_context_processor
    def make_shell_context():
        return dict(app=app, db=db, User=User, Role=Role)

    @app.cli.command()
    def test():
        """Run the unit tests."""
        import unittest

        tests = unittest.TestLoader().discover("tests")
        unittest.TextTestRunner(verbosity=2).run(tests)

    @app.cli.command()
    def init_db():
        """
        Create all database tables if they do not exist. Safe and non-destructive:
        existing tables and data are preserved. Use this when tables are missing
        (e.g. after cloning the repo or switching to a fresh database).
        """
        # Schema coherence for fresh installs: a few tables are mapped by two model
        # classes (legacy duplicates). SQLAlchemy merges them via extend_existing,
        # which can leave the SAME index defined twice on a table. A from-scratch
        # create_all() then emits a duplicate "CREATE INDEX" and fails on an empty
        # database. (Production never hit this because its schema was built
        # incrementally.) Drop duplicate same-named indexes per table before creating.
        from app.commands.schema_migrations import dedupe_metadata_indexes
        dedupe_metadata_indexes(db.metadata)
        db.create_all()
        # LEGACY WORKAROUNDS: These ALTER TABLE statements add columns that predate
        # alembic tracking. They are idempotent (IF NOT EXISTS) and remain here to
        # support fresh installs until proper migrations are written for each column.
        # DO NOT add new columns here — use `flask db migrate` instead.
        from sqlalchemy import text
        db.session.execute(text(
            "ALTER TABLE kanban_cards "
            "ADD COLUMN IF NOT EXISTS workflow_instance_id INTEGER "
            "REFERENCES ea_workflow_instances(id) ON DELETE SET NULL"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_kanban_cards_workflow_instance_id "
            "ON kanban_cards(workflow_instance_id)"
        ))
        # Add impacted_element_ids to architecture_review_boards (ARB ArchiMate linkage)
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "ALTER TABLE architecture_review_boards "
                "ADD COLUMN IF NOT EXISTS impacted_element_ids JSONB"
            ))
            # Add missing columns to unified_application_capability_mapping (QA-CAP-001)
            db.session.execute(text(
                "ALTER TABLE unified_application_capability_mapping "
                "ADD COLUMN IF NOT EXISTS maturity_level INTEGER"
            ))
            db.session.execute(text(
                "ALTER TABLE unified_application_capability_mapping "
                "ADD COLUMN IF NOT EXISTS is_strategic BOOLEAN DEFAULT FALSE"
            ))
            db.session.execute(text(
                "ALTER TABLE unified_application_capability_mapping "
                "ADD COLUMN IF NOT EXISTS notes TEXT"
            ))
        # VA-005 principles columns and the RAT-114 created_at index. No model
        # declares them, so the schema baseline creates them from the same list.
        from app.commands.schema_migrations import ensure_undeclared_schema
        ensure_undeclared_schema(db.session.connection())

        # SA-003: Add Phase C lifecycle planning columns to application_components
        for col_ddl in [
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS arch_pattern VARCHAR(30) DEFAULT 'unknown'",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS current_lifecycle_state VARCHAR(20) DEFAULT 'active'",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS target_disposition VARCHAR(20) DEFAULT 'tbd'",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS transition_wave INTEGER",
            # Application layer / version control columns (avoid UndefinedColumn on SELECT)
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS version VARCHAR(50)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS version_control_url TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS repository_type VARCHAR(50)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS main_branch TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS cloud_provider TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS deployment_region TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS container_image TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS kubernetes_namespace TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS response_time_target_ms INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS throughput_target_tps INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS current_response_time_ms INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS current_throughput_tps INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS scalability_model VARCHAR(50)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS max_instances INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS min_instances INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS sla_availability_percentage NUMERIC(5,2)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS current_uptime_percentage NUMERIC(5,2)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS disaster_recovery_enabled BOOLEAN DEFAULT FALSE",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS rpo_hours INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS rto_hours INTEGER",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS backup_frequency VARCHAR(50)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS last_backup_date TIMESTAMP",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS authentication_method TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS authorization_model VARCHAR(50)",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS encryption_at_rest BOOLEAN DEFAULT FALSE",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS encryption_in_transit BOOLEAN DEFAULT TRUE",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS pii_data_processed BOOLEAN DEFAULT FALSE",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS gdpr_compliant BOOLEAN DEFAULT FALSE",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS compliance_tags TEXT",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS last_security_audit_date DATE",
            "ALTER TABLE application_components ADD COLUMN IF NOT EXISTS last_penetration_test_date DATE",
        ]:
            db.session.execute(text(col_ddl))
        # Index for application_components.version (model has index=True)
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_application_components_version "
                "ON application_components(version)"
            ))

        # OA-002: scoring columns for gap_solution_options
        for col_ddl in [
            "ALTER TABLE gap_solution_options ADD COLUMN IF NOT EXISTS prioritisation_score FLOAT",
            "ALTER TABLE gap_solution_options ADD COLUMN IF NOT EXISTS feasibility_score FLOAT",
            "ALTER TABLE gap_solution_options ADD COLUMN IF NOT EXISTS implementation_cost_estimate VARCHAR(50)",
            "ALTER TABLE gap_solution_options ADD COLUMN IF NOT EXISTS time_to_implement_weeks INTEGER",
            "ALTER TABLE gap_solution_options ADD COLUMN IF NOT EXISTS strategic_alignment_score FLOAT",
        ]:
            db.session.execute(text(col_ddl))

        # RAT-103: Canonical disposition taxonomy columns on application_rationalization_scores
        # disposition_action: 7R action (retain/rehost/replatform/refactor/replace/retire/consolidate)
        # disposition_confidence: evidence quality (high/medium/low)
        for col_ddl in [
            "ALTER TABLE application_rationalization_scores ADD COLUMN IF NOT EXISTS disposition_action VARCHAR(50)",
            "ALTER TABLE application_rationalization_scores ADD COLUMN IF NOT EXISTS disposition_confidence VARCHAR(20)",
        ]:
            db.session.execute(text(col_ddl))

        # RAT-105: confidence_reasons — JSON list of uncertainty reasons for sub-high confidence scores
        db.session.execute(text(
            "ALTER TABLE application_rationalization_scores ADD COLUMN IF NOT EXISTS confidence_reasons JSON"
        ))
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_application_rationalization_scores_disposition_action "
                "ON application_rationalization_scores(disposition_action)"
            ))

        # RAT-106: Scoped policy overlays — create rationalization_policies table
        # and add policy_id / policy_name columns to application_rationalization_scores.
        # The CREATE TABLE is idempotent (checkfirst=True via SQLAlchemy model table).
        from app.models.application_rationalization import RationalizationPolicy
        RationalizationPolicy.__table__.create(db.engine, checkfirst=True)
        for col_ddl in [
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS policy_id INTEGER "
            "REFERENCES rationalization_policies(id) ON DELETE SET NULL",
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS policy_name VARCHAR(100)",
        ]:
            db.session.execute(text(col_ddl))
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_application_rationalization_scores_policy_id "
                "ON application_rationalization_scores(policy_id)"
            ))

        # RAT-108: Replacement planning table
        # CREATE TABLE is idempotent via checkfirst=True.
        from app.models.application_rationalization import ReplacementPlan
        ReplacementPlan.__table__.create(db.engine, checkfirst=True)

        # RAT-111: Review workflow state machine columns on application_rationalization_scores.
        # review_status: current state (draft/reviewed/approved/rejected/exception_approved)
        # reviewed_by / reviewed_at: who performed the "reviewed" transition and when
        # review_notes: optional note attached to any transition
        # approved_by / approved_at: who performed the "approved" or "exception_approved" transition
        for col_ddl in [
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS review_status VARCHAR(30) NOT NULL DEFAULT 'draft'",
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(100)",
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS review_notes TEXT",
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS approved_by VARCHAR(100)",
            "ALTER TABLE application_rationalization_scores "
            "ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP",
        ]:
            db.session.execute(text(col_ddl))
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_application_rationalization_scores_review_status "
                "ON application_rationalization_scores(review_status)"
            ))

        # RAT-113: Override mechanism columns on application_rationalization_scores.
        # override_active: whether an active override is in effect
        # override_disposition: the manually chosen disposition
        # override_rationale: required justification (min 20 chars enforced at API layer)
        # override_actor: display name of who set the override
        # override_created_at / override_expiry: time-bounded — expiry enforced in effective_disposition
        # override_original_disposition: the system recommendation being overridden (audit trail)
        for col_def in [
            ("override_active", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("override_disposition", "VARCHAR(50)"),
            ("override_rationale", "TEXT"),
            ("override_actor", "VARCHAR(100)"),
            ("override_created_at", "TIMESTAMP"),
            ("override_expiry", "TIMESTAMP"),
            ("override_original_disposition", "VARCHAR(50)"),
        ]:
            col_name, col_type = col_def
            try:
                db.session.execute(db.text(
                    f"ALTER TABLE application_rationalization_scores "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                db.session.commit()
                print(f"  ✓ RAT-113: Added {col_name}")
            except Exception:
                db.session.rollback()

        # RAT-112: ARB governance columns on application_rationalization_scores.
        # arb_required: whether this disposition needs ARB sign-off
        # arb_submission_id: conceptual link to ARBReviewItem
        # arb_submission_status: pending, submitted, approved, rejected, deferred
        # arb_submitted_at / arb_submitted_by: who submitted and when
        # arb_decision: approved, approved_with_conditions, rejected, deferred
        # arb_decision_at / arb_decision_notes: ARB decision timestamp and rationale
        for col_def in [
            ("arb_required", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("arb_submission_id", "INTEGER"),
            ("arb_submission_status", "VARCHAR(30)"),
            ("arb_submitted_at", "TIMESTAMP"),
            ("arb_submitted_by", "VARCHAR(100)"),
            ("arb_decision", "VARCHAR(50)"),
            ("arb_decision_at", "TIMESTAMP"),
            ("arb_decision_notes", "TEXT"),
        ]:
            col_name, col_type = col_def
            try:
                db.session.execute(db.text(
                    f"ALTER TABLE application_rationalization_scores "
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                db.session.commit()
                print(f"  ✓ RAT-112: Added {col_name}")
            except Exception:
                db.session.rollback()

        # RAT-114: Audit trail table — records every state change, approval, override,
        # evidence update, and scoring event with actor, action, before/after state, and timestamp.
        try:
            db.session.execute(db.text("""
                CREATE TABLE IF NOT EXISTS rationalization_audit_entries (
                    id SERIAL PRIMARY KEY,
                    application_id INTEGER NOT NULL REFERENCES application_components(id),
                    score_id INTEGER REFERENCES application_rationalization_scores(id),
                    action VARCHAR(50) NOT NULL,
                    actor VARCHAR(100) NOT NULL,
                    actor_type VARCHAR(20) NOT NULL DEFAULT 'user',
                    before_state JSON,
                    after_state JSON,
                    details TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(db.text(
                "CREATE INDEX IF NOT EXISTS idx_rat_audit_app_action "
                "ON rationalization_audit_entries(application_id, action)"
            ))
            db.session.commit()
            print("  \u2713 RAT-114: Created rationalization_audit_entries table")
        except Exception:
            db.session.rollback()

        # RAT-116: Decommission plan table — CREATE TABLE is idempotent via checkfirst=True.
        from app.models.application_rationalization import DecommissionPlan
        DecommissionPlan.__table__.create(db.engine, checkfirst=True)
        print("  \u2713 RAT-116: decommission_plans table ensured")

        # RAT-117: Benefits tracking table — CREATE TABLE is idempotent via checkfirst=True.
        from app.models.application_rationalization import RationalizationBenefitsTracker
        RationalizationBenefitsTracker.__table__.create(db.engine, checkfirst=True)
        print("  \u2713 RAT-117: rationalization_benefits table ensured")

        # Ensure workflow artifact tables exist (Alembic may skip these)
        from app.models.workflow_artifacts import GapRemediationReport, WorkflowCompletionSummary
        GapRemediationReport.__table__.create(db.engine, checkfirst=True)
        WorkflowCompletionSummary.__table__.create(db.engine, checkfirst=True)

        # PLT-015: Add webhook_type column to webhook_subscriptions (Teams/Slack support)
        db.session.execute(text(
            "ALTER TABLE webhook_subscriptions "
            "ADD COLUMN IF NOT EXISTS webhook_type VARCHAR(20) NOT NULL DEFAULT 'generic'"
        ))
        print("  \u2713 PLT-015: webhook_subscriptions.webhook_type column ensured")

        # PLT-017: Add notification_preferences JSON column to users
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_preferences JSONB"
            ))
        else:
            try:
                db.session.execute(text(
                    "ALTER TABLE users ADD COLUMN notification_preferences JSON"
                ))
            except Exception:  # column already exists in SQLite
                db.session.rollback()
        print("  \u2713 PLT-017: users.notification_preferences column ensured")

        # Add encryption key versioning columns for org credential store
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text(
                "ALTER TABLE organization_encryption_keys "
                "ADD COLUMN IF NOT EXISTS previous_encrypted_key BYTEA"
            ))
            db.session.execute(text(
                "ALTER TABLE org_connector_credentials "
                "ADD COLUMN IF NOT EXISTS key_version INTEGER NOT NULL DEFAULT 1"
            ))

        db.session.commit()
        print("Database tables created (or already exist).")

    @app.cli.command()
    @click.option(
        "--force",
        is_flag=True,
        help="Required: confirms you understand this DESTROYS all data. Never run without explicit human approval.",
    )
    def recreate_db(force):
        """
        DESTRUCTIVE: Drops all tables and recreates them. All data is permanently lost.
        Requires --force. Use only when you explicitly need a clean slate.
        Prefer 'flask init-db' to create missing tables without destroying data.
        """
        if not force:
            print("ERROR: recreate-db is DESTRUCTIVE and will delete all data.")
            print("Run with --force only if you have explicit human approval.")
            raise SystemExit(1)
        db.drop_all()
        db.create_all()
        db.session.commit()
        print("Database recreated. All previous data was removed.")

    # Register AI Semantic Discovery Commands
    try:
        from app.commands.semantic_discovery_commands import register_commands

        register_commands(app.cli)
        print("AI Semantic Discovery commands registered")
    except ImportError as e:
        print(f"Warning: Could not register semantic discovery commands: {e}")

    # Register Vendor Population Commands
    try:
        from app.commands.vendor_population_commands import register_commands

        register_commands(app.cli)
        print("Vendor Population commands registered")
    except ImportError as e:
        print(f"Warning: Could not register vendor population commands: {e}")

    # Register Advanced TCO Commands
    try:
        from app.commands.advanced_tco_commands import register_commands

        register_commands(app.cli)
        print("Advanced TCO commands registered")
    except ImportError as e:
        print(f"Warning: Could not register advanced TCO commands: {e}")

    # Register ACM (Application Capability Model) Commands
    try:
        from app.commands.acm_commands import register_commands as register_acm_commands

        register_acm_commands(app)
        print("ACM Technical Capability commands registered")
    except ImportError as e:
        print(f"Warning: Could not register ACM commands: {e}")

    # Register ArchiMate Commands
    try:
        from app.commands.archimate_commands import register_archimate_commands

        register_archimate_commands(app)
        print("ArchiMate enterprise architecture commands registered")
    except ImportError as e:
        print(f"Warning: Could not register ArchiMate commands: {e}")

    # Register Credential Management Commands
    @app.cli.command()
    @click.option("--dry-run", is_flag=True, help="Show counts without making changes")
    def migrate_connector_credentials(dry_run):
        """Migrate credentials from retired stores into ``OrgConnectorCredential``.

        Copies every row from ``OrgConnectorConfig`` and
        ``LucidchartConnectorConfig`` into the new per-organisation store,
        under the same discrete ``credential_type`` keys the live readers
        use -- not a combined JSON blob, which those readers never look for:

        * ServiceNow (``OrgConnectorConfig``): ``client_secret``, read by
          ``ServiceNowConnectorService._get_token``.
        * Lucidchart (``LucidchartConnectorConfig``): ``client_secret``,
          ``access_token`` and ``refresh_token``, read by
          ``LucidchartConnectorService.get_access_token`` /
          ``get_refresh_token`` / ``_require_client_credentials``.

        ``DevOpsConnectorConfig`` has no live writer or vault-based reader
        yet (see app/services/devops_push_service.py), so its token is still
        copied as a JSON blob under credential_type="credentials" -- nothing
        reads it that way today, but this preserves it for whenever a reader
        is added, rather than dropping it.

        Reports counts before and after; never logs a credential value.
        Idempotent: a (organisation, connector_type, credential_type) already
        present in the vault is skipped, so re-running after a partial
        migration only fills in what is still missing.

        Use ``--dry-run`` to preview what would be migrated.
        """
        from app.models.connector_config import (
            OrgConnectorConfig, DevOpsConnectorConfig,
            LucidchartConnectorConfig, OrgConnectorCredential,
        )
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        import json

        vault = OrgCredentialVault()
        migrations = []
        total_before = 0

        # Scan OrgConnectorConfig -- one discrete client_secret entry per row.
        for row in OrgConnectorConfig.query.all():
            secret = row.client_secret
            if secret:
                migrations.append({
                    "source_table": "org_connector_configs",
                    "source_id": row.id,
                    "org_id": row.organization_id,
                    "connector_type": row.connector_type,
                    "credential_type": "client_secret",
                    "value": secret,
                })
            total_before += 1

        # Scan DevOpsConnectorConfig -- no live reader yet; kept as a single
        # blob entry under credential_type="credentials" (unchanged shape).
        for row in DevOpsConnectorConfig.query.all():
            token = row.access_token
            if token:
                migrations.append({
                    "source_table": "devops_connector_configs",
                    "source_id": row.id,
                    "org_id": row.organization_id,
                    "connector_type": "devops",
                    "credential_type": "credentials",
                    "value": json.dumps({"access_token": token, "provider": row.provider}),
                })
            total_before += 1

        # Scan LucidchartConnectorConfig -- one discrete entry per secret
        # field actually stored on the row.
        for row in LucidchartConnectorConfig.query.all():
            for credential_type, value in (
                ("client_secret", row.client_secret),
                ("access_token", row.access_token),
                ("refresh_token", row.refresh_token),
            ):
                if value:
                    migrations.append({
                        "source_table": "lucidchart_connector_configs",
                        "source_id": row.id,
                        "org_id": row.organization_id,
                        "connector_type": "lucidchart",
                        "credential_type": credential_type,
                        "value": value,
                    })
            total_before += 1

        print(f"Found {total_before} rows across retired stores, "
              f"{len(migrations)} credential value(s) to migrate.")

        if dry_run:
            for m in migrations:
                print(f"  would migrate: {m['source_table']}[{m['source_id']}] "
                      f"org={m['org_id']} type={m['connector_type']}/{m['credential_type']}")
            print(f"Dry run: {len(migrations)} credentials would be migrated.")
            return

        migrated = 0
        skipped = 0
        errors = 0
        for m in migrations:
            try:
                # Check if already migrated
                existing = OrgConnectorCredential.query.filter_by(
                    organization_id=m["org_id"],
                    connector_type=m["connector_type"],
                    credential_type=m["credential_type"],
                ).first()
                if existing:
                    skipped += 1
                    continue
                vault.store(m["org_id"], m["connector_type"], m["credential_type"], m["value"])
                migrated += 1
            except Exception as exc:
                logger = logging.getLogger(__name__)
                logger.error("Failed to migrate %s[%s] credential_type=%s: %s",
                             m["source_table"], m["source_id"], m["credential_type"], exc)
                errors += 1

        from app.extensions import db
        db.session.commit()

        total_after = OrgConnectorCredential.query.count()
        print(f"Migrated: {migrated}, skipped (already present): {skipped}, "
              f"errors: {errors}")
        print(f"OrgConnectorCredential rows before: {total_before} source rows | "
              f"after: {total_after}")

    @app.cli.command()
    @click.option("--org", "org_id", type=int, required=True, help="Organisation id")
    def rotate_credential_key(org_id):
        """Rotate the encryption key for one organisation.

        Generates a new Fernet key, stores it encrypted under the master key,
        and re-encrypts every credential row. All operations are atomic.
        """
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        vault = OrgCredentialVault()
        try:
            new_version = vault.rotate_all_credentials(org_id)
            print(f"Key rotated for org {org_id}: new version {new_version}")
        except Exception as exc:
            print(f"Error rotating key for org {org_id}: {exc}")
            raise

    @app.cli.command()
    @click.option("--dry-run", is_flag=True, help="Show what would change without making changes")
    def rewrap_org_keys(dry_run):
        """Re-wrap every organisation's Fernet key under the current master key.

        Idempotent: organisations already encrypted with the current master key
        are unchanged. Use after ``ORG_ENCRYPTION_MASTER_KEY`` is changed.
        """
        from app.models.connector_config import OrganizationEncryptionKey
        from app.modules.codegen.services.credential_encryption import _get_master_fernet
        from app.extensions import db

        master = _get_master_fernet()
        rows = OrganizationEncryptionKey.query.all()
        rewrapped = 0

        for row in rows:
            if dry_run:
                print(f"  would rewrap org {row.organization_id} key version {row.key_version}")
                continue
            # Decrypt with old master, re-encrypt with current master
            try:
                raw_key = master.decrypt(row.encrypted_key)
                row.encrypted_key = master.encrypt(raw_key)
                rewrapped += 1
            except Exception as exc:
                logger = logging.getLogger(__name__)
                logger.warning(
                    "Could not rewrap key for org %s: %s. "
                    "The key may already use the current master key.",
                    row.organization_id, exc,
                )

        db.session.commit()
        if dry_run:
            print(f"Dry run: {len(rows)} org keys would be checked.")
        else:
            print(f"Rewrapped: {rewrapped} of {len(rows)} org keys.")

    @app.cli.command()
    def setup_dev():
        """Runs the set-up needed for local development."""
        setup_general()

    @app.cli.command()
    def reset_admin_password():
        """Reset the admin user's password to match ADMIN_PASSWORD in .env.
        Use when the admin was created before ADMIN_PASSWORD was set (random password was used).
        """
        admin = User.query.filter_by(email=Config.ADMIN_EMAIL).first()
        if admin is None:
            print(f"No admin user found with email {Config.ADMIN_EMAIL}. Run 'flask setup-dev' first.")
            return
        admin.password = Config.ADMIN_PASSWORD
        db.session.add(admin)
        db.session.commit()
        print(f"Admin password updated for {Config.ADMIN_EMAIL}. You can now log in with ADMIN_PASSWORD from .env.")

    @app.cli.command()
    def setup_prod():
        """Runs the set-up needed for production."""
        setup_general()

    @app.cli.command()
    def setup_test():
        """Runs the set-up needed for test database (PostgreSQL required).

        This command sets up a dedicated test database with:
        - All required tables
        - Role definitions
        - Admin user with credentials from Config

        Usage:
            FLASK_CONFIG=testing python manage.py setup_test

        Environment:
            TEST_DATABASE_URL - PostgreSQL connection string (required)
            Example: postgresql://postgres:postgres@localhost:5432/flask_test
        """
        # Validate PostgreSQL is being used (not SQLite)
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if "sqlite" in db_uri.lower():
            print("ERROR: Tests MUST use PostgreSQL, not SQLite.")
            print("Set TEST_DATABASE_URL environment variable to a PostgreSQL connection string.")
            print(
                "Example: export TEST_DATABASE_URL='postgresql://user:pass@localhost:5432/flask_test'"  # secrets-safety-ok
            )
            return

        print(f"Setting up test database: {db_uri[:50]}...")

        # Create all tables
        db.create_all()
        print("Database tables created.")

        # Insert roles
        Role.insert_roles()
        print("Roles inserted.")

        # Create test admin from Config (single source of truth)
        admin_email = Config.ADMIN_EMAIL
        admin_password = Config.ADMIN_PASSWORD

        if User.query.filter_by(email=admin_email).first() is None:
            admin_role = Role.query.filter_by(name="Administrator").first()
            admin = User(
                first_name="Test",
                last_name="Admin",
                email=admin_email,
                password=admin_password,
                confirmed=True,
                role=admin_role,
            )
            db.session.add(admin)
            db.session.commit()
            print(f"Test admin created: {admin_email}")
        else:
            print(f"Test admin already exists: {admin_email}")

        print("\n" + "=" * 60)
        print("TEST DATABASE SETUP COMPLETE")
        print("=" * 60)
        print(f"Admin Email:    {admin_email}")
        print(f"Admin Password: {admin_password}")
        print("Login Route:    /account/login")
        print("=" * 60)

    @app.cli.command()
    def run_worker():
        """Initializes a slim rq task queue."""
        if not HAS_REDIS:
            print("Error: redis and rq packages are required for background workers.")
            print("Install with: pip install redis rq")
            return

        listen = ["default"]
        conn = Redis(
            host=app.config["RQ_DEFAULT_HOST"],
            port=app.config["RQ_DEFAULT_PORT"],
            db=0,
            password=app.config["RQ_DEFAULT_PASSWORD"],
        )

        with Connection(conn):
            worker = Worker(map(Queue, listen))
            worker.work()

    @app.cli.command()
    def format():
        """Runs the yapf and isort formatters over the project."""
        import glob

        # The shell used to expand *.py; glob does it without one.
        top_level = sorted(glob.glob("*.py"))
        isort = ["isort", "-rc", *top_level, "app/"]
        yapf = ["yapf", "-r", "-i", *top_level, "app/"]

        print("Running {}".format(" ".join(isort)))
        subprocess.call(isort)

        print("Running {}".format(" ".join(yapf)))
        subprocess.call(yapf)

    # ===== FEATURE FLAGS COMMANDS =====

    @app.cli.command()
    @click.option(
        "--filter-infra/--no-filter",
        default=True,
        help="Filter out infrastructure routes (health, metrics, static)",
    )
    @click.option("--dry-run", is_flag=True, help="Show what would be created without creating")
    def discover_features(filter_infra, dry_run):
        """Auto-discover features from Flask routes and create feature flags.
        
        This command scans all registered routes and creates hierarchical feature flags:
        - Level 1: Blueprints (e.g., 'admin', 'applications')
        - Level 2: Route sections (e.g., '/admin/users')
        - Level 3: Individual routes (e.g., '/admin/users/<id>/edit')
        
        Use --no-filter to include infrastructure routes (not recommended).
        Use --dry-run to preview what would be created.
        
        Examples:
            flask discover-features              # Create all features
            flask discover-features --dry-run    # Preview only
            flask discover-features --no-filter  # Include health/metrics
        """
        from app.admin.views import _auto_discover_features
        
        print("Discovering features from Flask routes...")
        print(f"   Filter infrastructure: {filter_infra}")
        print(f"   Dry run: {dry_run}")
        print()
        
        if dry_run:
            print("DRY RUN MODE - No database changes will be made")
            print()
        
        try:
            if dry_run:
                # TODO: Implement dry-run logic to show what would be created
                print("Dry run mode not yet implemented. Remove --dry-run to create features.")
            else:
                created_count = _auto_discover_features(app)
                
                print()
                print(f"Successfully discovered {created_count} features")
                print()
                print("Next steps:")
                print("  1. Visit /admin/feature-flags to view all features")
                print("  2. Disable unwanted features")
                print("  3. Add @require_feature() decorator to routes")
                print()
                print("Example usage:")
                print("  from app.decorators import require_feature")
                print()
                print("  @require_feature('user_management')")
                print("  @admin.route('/admin/users')")
                print("  def users_list():")
                print("      ...")
        except Exception as e:
            print(f"ERROR: {e}")
                
        except Exception as e:
            print(f"❌ Error during feature discovery: {e}")
            import traceback
            traceback.print_exc()
            return 1

    # ===== SERVER MANAGEMENT COMMANDS =====

    @app.cli.command()
    @click.option("--port", "-p", default=5000, help="Port number to check (default: 5000)")
    @click.option("--json", is_flag=True, help="Output JSON format")
    def server_check(port, json):
        """Check server status and process information."""
        try:
            # Import server manager
            import sys

            sys.path.append(os.path.join(os.path.dirname(__file__), "scripts", "enforcement"))
            from server_manager import ServerManager

            manager = ServerManager(port)
            result = manager.check_server_status()

            if json:
                import json as json_module

                print(json_module.dumps(result, indent=2))
            else:
                print(f"Server Status (Port {port}):")
                if result.get("running"):
                    print("✅ Server is running")
                    flask_processes = result.get("flask_processes", [])
                    if flask_processes:
                        print(f"   Flask processes: {len(flask_processes)}")
                        for process in flask_processes:
                            print(f"   - PID {process['pid']}: {process['name']}")
                    else:
                        print("   ⚠️  No Flask processes detected")
                else:
                    print("❌ No server running")

        except ImportError as e:
            print(f"Error: Server management module not found: {e}")
            print("Please ensure scripts/enforcement/server_manager.py exists")
        except Exception as e:
            print(f"Error checking server: {e}")

    @app.cli.command()
    @click.option("--port", "-p", default=5000, help="Port number to enforce (default: 5000)")
    @click.option("--json", is_flag=True, help="Output JSON format")
    def server_enforce(port, json):
        """Enforce single server instance (kill duplicates)."""
        try:
            import sys

            sys.path.append(os.path.join(os.path.dirname(__file__), "scripts", "enforcement"))
            from server_manager import ServerManager

            manager = ServerManager(port)
            result = manager.enforce_single_instance()

            if json:
                import json as json_module

                print(json_module.dumps(result, indent=2))
            else:
                print(f"Server Enforcement (Port {port}):")
                if result.get("compliant"):
                    print(f"✅ {result.get('message', 'Compliant')}")
                else:
                    print(f"❌ {result.get('message', 'Not compliant')}")
                    killed = result.get("killed_processes", [])
                    if killed:
                        print(f"   Killed processes: {', '.join(killed)}")

        except ImportError as e:
            print(f"Error: Server management module not found: {e}")
            print("Please ensure scripts/enforcement/server_manager.py exists")
        except Exception as e:
            print(f"Error enforcing server: {e}")

    @app.cli.command()
    @click.option("--port", "-p", default=5000, help="Port number to use (default: 5000)")
    @click.option(
        "--hot-reload", is_flag=True, default=True, help="Enable hot reload when possible"
    )
    @click.option("--force-restart", is_flag=True, help="Force restart even if server running")
    @click.option("--json", is_flag=True, help="Output JSON format")
    def server_start(port, hot_reload, force_restart, json):
        """Start server with proper conflict prevention and hot reload."""
        try:
            import sys

            sys.path.append(os.path.join(os.path.dirname(__file__), "scripts", "enforcement"))
            from server_manager import ServerManager

            manager = ServerManager(port)
            result = manager.start_server(hot_reload=hot_reload, force_restart=force_restart)

            if json:
                import json as json_module

                print(json_module.dumps(result, indent=2))
            else:
                print(f"Server Start (Port {port}):")
                if result.get("success"):
                    action = result.get("action", "unknown")
                    message = result.get("message", "Started")

                    if action == "hot_reload":
                        print(f"🔄 {message}")
                    elif action == "started":
                        pid = result.get("pid")
                        print(f"🚀 {message} (PID: {pid})")
                    else:
                        print(f"✅ {message}")
                else:
                    print(f"❌ Failed to start: {result.get('message', 'Unknown error')}")

        except ImportError as e:
            print(f"Error: Server management module not found: {e}")
            print("Please ensure scripts/enforcement/server_manager.py exists")
        except Exception as e:
            print(f"Error starting server: {e}")

    @app.cli.command()
    @click.option("--port", "-p", default=5000, help="Port number to check (default: 5000)")
    def server_kill(port):
        """Kill all processes using the specified port."""
        try:
            import sys

            sys.path.append(os.path.join(os.path.dirname(__file__), "scripts", "enforcement"))
            from server_manager import ServerManager

            manager = ServerManager(port)
            success, killed = manager.kill_existing_server(force=True)

            if success:
                print(f"✅ Successfully killed {len(killed)} processes")
                if killed:
                    print(f"   Killed: {', '.join(killed)}")
            else:
                print("❌ Failed to kill some processes")
                if killed:
                    print(f"   Killed: {', '.join(killed)}")

        except ImportError as e:
            print(f"Error: Server management module not found: {e}")
            print("Please ensure scripts/enforcement/server_manager.py exists")
        except Exception as e:
            print(f"Error killing server: {e}")

    @app.cli.command()
    @click.option("--port", "-p", default=5000, help="Port number to use (default: 5000)")
    @click.option(
        "--hot-reload", is_flag=True, default=True, help="Enable hot reload when possible"
    )
    @click.option("--json", is_flag=True, help="Output JSON format")
    def runserver_safe(port, hot_reload, json):
        """Safe server runner with automatic conflict prevention."""
        print(f"🔍 Starting safe server on port {port}...")

        # Step 1: Check existing server
        print("Step 1: Checking existing server...")
        try:
            import sys

            sys.path.append(os.path.join(os.path.dirname(__file__), "scripts", "enforcement"))
            from server_manager import ServerManager

            manager = ServerManager(port)
            status = manager.check_server_status()

            if status.get("running") and not hot_reload:
                print("⚠️  Server already running. Use --hot-reload to use existing server.")
                return

            # Step 2: Enforce single instance
            print("Step 2: Enforcing single instance...")
            enforce_result = manager.enforce_single_instance()

            if not enforce_result.get("compliant"):
                print(f"⚠️  {enforce_result.get('message', 'Enforcement issues detected')}")

            # Step 3: Start server
            print("Step 3: Starting server...")
            start_result = manager.start_server(hot_reload=hot_reload)

            if json:
                import json as json_module

                print(json_module.dumps(start_result, indent=2))
            else:
                if start_result.get("success"):
                    action = start_result.get("action", "unknown")
                    message = start_result.get("message", "Started")

                    if action == "hot_reload":
                        print(f"🔄 {message}")
                    elif action == "started":
                        pid = start_result.get("pid")
                        print(f"🚀 {message} (PID: {pid})")
                    else:
                        print(f"✅ {message}")
                else:
                    print(f"❌ Failed to start: {start_result.get('message', 'Unknown error')}")

        except ImportError as e:
            print(f"Error: Server management module not found: {e}")
            print("Please ensure scripts/enforcement/server_manager.py exists")
        except Exception as e:
            print(f"Error in safe server startup: {e}")

    # ===== APQC AND VENDOR SEEDING COMMANDS =====

    @app.cli.command()
    @click.option("--apqc-only", is_flag=True, help="Seed only APQC processes")
    @click.option("--vendors-only", is_flag=True, help="Seed only vendors")
    @click.option("--mappings-only", is_flag=True, help="Seed only vendor-APQC mappings")
    def seed_vendor_apqc(apqc_only, vendors_only, mappings_only):
        """
        Seed APQC PCF processes, vendors, and vendor-APQC mappings.

        By default, seeds all three. Use flags to seed specific components.

        Examples:
            flask seed-vendor-apqc           # Seed all
            flask seed-vendor-apqc --apqc-only    # Seed only APQC processes
            flask seed-vendor-apqc --vendors-only # Seed only vendors
        """
        from app.services.vendor_process_mapping_service import VendorProcessMappingService

        service = VendorProcessMappingService()

        if apqc_only:
            print("Seeding APQC processes...")
            result = service.seed_apqc_processes()
            print(
                f"APQC: {result.get('seeded', 0)} seeded, {result.get('skipped', 0)} skipped, {result.get('errors', 0)} errors"
            )
        elif vendors_only:
            print("Seeding vendors from catalogue...")
            result = service.seed_vendors_from_catalogue()
            print(
                f"Vendors: {result.get('orgs_seeded', 0)} orgs, {result.get('products_seeded', 0)} products seeded"
            )
        elif mappings_only:
            print("Seeding vendor-APQC mappings...")
            result = service.seed_vendor_apqc_mappings()
            print(
                f"Mappings: {result.get('seeded', 0)} seeded, {result.get('skipped', 0)} skipped, {result.get('errors', 0)} errors"
            )
        else:
            print("Running full vendor-APQC seeding...")
            results = service.run_full_seed()

            print("\n=== Seeding Results ===")
            apqc = results.get("apqc", {})
            print(
                f"APQC Processes: {apqc.get('seeded', 0)} seeded, {apqc.get('skipped', 0)} skipped"
            )

            vendors = results.get("vendors", {})
            print(
                f"Vendors: {vendors.get('orgs_seeded', 0)} orgs, {vendors.get('products_seeded', 0)} products"
            )

            mappings = results.get("mappings", {})
            print(
                f"Mappings: {mappings.get('seeded', 0)} seeded, {mappings.get('skipped', 0)} skipped"
            )

            print("\nDone!")

    # ===== ARCHIMATE MOTIVATION VOCABULARY SEEDING =====

    @app.cli.command()
    def seed_motivation():
        """Seed enterprise-standard ArchiMate Driver, Stakeholder, and Constraint vocabulary records. Safe to run multiple times."""
        from app.commands.seed_motivation_elements import seed_motivation_elements
        seed_motivation_elements()

    # ===== REGULATORY FRAMEWORK CATALOGUE SEEDING =====

    @app.cli.command()
    def seed_framework_catalogue():
        """Seed the shared regulatory framework catalogue with ISO 27001, SOC 2 and DORA. Safe to run multiple times."""
        from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService
        seeded = RegulatoryFrameworkService.seed_manufacturing_frameworks()
        print(f"Framework catalogue: {seeded} frameworks seeded")

    # ===== BUSINESS CAPABILITY SEEDING =====

    @app.cli.command()
    @click.option(
        "--dry-run", is_flag=True, help="Show what would be seeded without making changes"
    )
    def seed_capabilities(dry_run):
        """
        Seed business capabilities and domains for AI auto-mapping.

        Creates the 9 standard business domains and common L1/L2 capabilities.
        """
        from app.models.unified_capability import BusinessDomain, UnifiedCapability

        # Define the 9 business domains
        BUSINESS_DOMAINS = [
            {
                "code": "CUST",
                "name": "Customer Management",
                "domain_type": "primary",
                "description": "Customer acquisition, retention, service, and relationship management",
            },
            {
                "code": "PROD",
                "name": "Product Management",
                "domain_type": "primary",
                "description": "Product development, lifecycle management, and portfolio management",
            },
            {
                "code": "OPER",
                "name": "Operations Management",
                "domain_type": "primary",
                "description": "Supply chain, manufacturing, logistics, and operational excellence",
            },
            {
                "code": "FIN",
                "name": "Financial Management",
                "domain_type": "supporting",
                "description": "Accounting, treasury, financial planning, and reporting",
            },
            {
                "code": "RISK",
                "name": "Risk & Compliance",
                "domain_type": "supporting",
                "description": "Risk management, regulatory compliance, audit, and governance",
            },
            {
                "code": "DATA",
                "name": "Data & Analytics",
                "domain_type": "enabling",
                "description": "Business intelligence, analytics, data management, and reporting",
            },
            {
                "code": "PART",
                "name": "Partner & Supplier Management",
                "domain_type": "supporting",
                "description": "Vendor management, procurement, and partner ecosystem",
            },
            {
                "code": "WORK",
                "name": "Workforce Management",
                "domain_type": "supporting",
                "description": "Human resources, talent management, and employee experience",
            },
            {
                "code": "TECH",
                "name": "Technology Enablement",
                "domain_type": "enabling",
                "description": "IT infrastructure, application management, and digital enablement",
            },
        ]

        # Define capabilities per domain (L1 and L2)
        CAPABILITIES = {
            "CUST": [
                # L1 Capabilities
                {
                    "name": "Customer Relationship Management",
                    "code": "CUST-CRM",
                    "level": 1,
                    "description": "Managing customer interactions and relationships throughout the lifecycle",
                },
                {
                    "name": "Customer Service",
                    "code": "CUST-SVC",
                    "level": 1,
                    "description": "Providing support and service to customers",
                },
                {
                    "name": "Sales Management",
                    "code": "CUST-SAL",
                    "level": 1,
                    "description": "Managing sales processes, pipeline, and revenue generation",
                },
                {
                    "name": "Marketing Management",
                    "code": "CUST-MKT",
                    "level": 1,
                    "description": "Marketing campaigns, brand management, and demand generation",
                },
                # L2 Capabilities
                {
                    "name": "Customer Acquisition",
                    "code": "CUST-CRM-ACQ",
                    "level": 2,
                    "parent_code": "CUST-CRM",
                    "description": "Acquiring new customers through various channels",
                },
                {
                    "name": "Customer Retention",
                    "code": "CUST-CRM-RET",
                    "level": 2,
                    "parent_code": "CUST-CRM",
                    "description": "Retaining existing customers and reducing churn",
                },
                {
                    "name": "Contact Center Operations",
                    "code": "CUST-SVC-CC",
                    "level": 2,
                    "parent_code": "CUST-SVC",
                    "description": "Managing contact center operations and customer inquiries",
                },
                {
                    "name": "Field Service Management",
                    "code": "CUST-SVC-FSM",
                    "level": 2,
                    "parent_code": "CUST-SVC",
                    "description": "Managing field service technicians and on-site support",
                },
            ],
            "PROD": [
                {
                    "name": "Product Development",
                    "code": "PROD-DEV",
                    "level": 1,
                    "description": "Developing new products and services",
                },
                {
                    "name": "Product Lifecycle Management",
                    "code": "PROD-PLM",
                    "level": 1,
                    "description": "Managing products through their entire lifecycle",
                },
                {
                    "name": "Portfolio Management",
                    "code": "PROD-PORT",
                    "level": 1,
                    "description": "Managing the product portfolio and roadmap",
                },
                {
                    "name": "Innovation Management",
                    "code": "PROD-INN",
                    "level": 1,
                    "description": "Managing innovation processes and ideation",
                },
                {
                    "name": "Requirements Management",
                    "code": "PROD-DEV-REQ",
                    "level": 2,
                    "parent_code": "PROD-DEV",
                    "description": "Capturing and managing product requirements",
                },
                {
                    "name": "Engineering Design",
                    "code": "PROD-DEV-ENG",
                    "level": 2,
                    "parent_code": "PROD-DEV",
                    "description": "Engineering and technical design of products",
                },
            ],
            "OPER": [
                {
                    "name": "Supply Chain Management",
                    "code": "OPER-SCM",
                    "level": 1,
                    "description": "End-to-end supply chain planning and execution",
                },
                {
                    "name": "Manufacturing Operations",
                    "code": "OPER-MFG",
                    "level": 1,
                    "description": "Manufacturing and production operations",
                },
                {
                    "name": "Logistics Management",
                    "code": "OPER-LOG",
                    "level": 1,
                    "description": "Warehousing, transportation, and distribution",
                },
                {
                    "name": "Quality Management",
                    "code": "OPER-QUA",
                    "level": 1,
                    "description": "Quality assurance and control processes",
                },
                {
                    "name": "Asset Management",
                    "code": "OPER-AST",
                    "level": 1,
                    "description": "Managing physical assets and maintenance",
                },
                {
                    "name": "Inventory Management",
                    "code": "OPER-SCM-INV",
                    "level": 2,
                    "parent_code": "OPER-SCM",
                    "description": "Managing inventory levels and stock",
                },
                {
                    "name": "Demand Planning",
                    "code": "OPER-SCM-DEM",
                    "level": 2,
                    "parent_code": "OPER-SCM",
                    "description": "Forecasting and planning demand",
                },
                {
                    "name": "Production Scheduling",
                    "code": "OPER-MFG-SCH",
                    "level": 2,
                    "parent_code": "OPER-MFG",
                    "description": "Scheduling and planning production",
                },
            ],
            "FIN": [
                {
                    "name": "Financial Accounting",
                    "code": "FIN-ACC",
                    "level": 1,
                    "description": "General ledger, accounts payable/receivable, and financial reporting",
                },
                {
                    "name": "Financial Planning & Analysis",
                    "code": "FIN-FPA",
                    "level": 1,
                    "description": "Budgeting, forecasting, and financial analysis",
                },
                {
                    "name": "Treasury Management",
                    "code": "FIN-TRE",
                    "level": 1,
                    "description": "Cash management, banking, and investments",
                },
                {
                    "name": "Tax Management",
                    "code": "FIN-TAX",
                    "level": 1,
                    "description": "Tax planning, compliance, and reporting",
                },
                {
                    "name": "Accounts Payable",
                    "code": "FIN-ACC-AP",
                    "level": 2,
                    "parent_code": "FIN-ACC",
                    "description": "Managing vendor invoices and payments",
                },
                {
                    "name": "Accounts Receivable",
                    "code": "FIN-ACC-AR",
                    "level": 2,
                    "parent_code": "FIN-ACC",
                    "description": "Managing customer invoices and collections",
                },
                {
                    "name": "Cost Accounting",
                    "code": "FIN-ACC-COST",
                    "level": 2,
                    "parent_code": "FIN-ACC",
                    "description": "Cost allocation and product costing",
                },
            ],
            "RISK": [
                {
                    "name": "Risk Management",
                    "code": "RISK-MGT",
                    "level": 1,
                    "description": "Identifying, assessing, and mitigating business risks",
                },
                {
                    "name": "Compliance Management",
                    "code": "RISK-CMP",
                    "level": 1,
                    "description": "Ensuring regulatory and policy compliance",
                },
                {
                    "name": "Audit Management",
                    "code": "RISK-AUD",
                    "level": 1,
                    "description": "Internal and external audit management",
                },
                {
                    "name": "Information Security",
                    "code": "RISK-SEC",
                    "level": 1,
                    "description": "Cybersecurity and information protection",
                },
                {
                    "name": "Business Continuity",
                    "code": "RISK-BCM",
                    "level": 1,
                    "description": "Business continuity and disaster recovery",
                },
            ],
            "DATA": [
                {
                    "name": "Business Intelligence",
                    "code": "DATA-BI",
                    "level": 1,
                    "description": "Reporting, dashboards, and business analytics",
                },
                {
                    "name": "Data Management",
                    "code": "DATA-MGT",
                    "level": 1,
                    "description": "Data governance, quality, and master data management",
                },
                {
                    "name": "Advanced Analytics",
                    "code": "DATA-ADV",
                    "level": 1,
                    "description": "Predictive analytics, machine learning, and AI",
                },
                {
                    "name": "Data Integration",
                    "code": "DATA-INT",
                    "level": 1,
                    "description": "ETL, data pipelines, and integration",
                },
                {
                    "name": "Master Data Management",
                    "code": "DATA-MGT-MDM",
                    "level": 2,
                    "parent_code": "DATA-MGT",
                    "description": "Managing master data across the enterprise",
                },
                {
                    "name": "Data Quality Management",
                    "code": "DATA-MGT-DQM",
                    "level": 2,
                    "parent_code": "DATA-MGT",
                    "description": "Ensuring data quality and accuracy",
                },
            ],
            "PART": [
                {
                    "name": "Procurement Management",
                    "code": "PART-PRO",
                    "level": 1,
                    "description": "Sourcing, purchasing, and procurement processes",
                },
                {
                    "name": "Vendor Management",
                    "code": "PART-VEN",
                    "level": 1,
                    "description": "Managing vendor relationships and performance",
                },
                {
                    "name": "Contract Management",
                    "code": "PART-CON",
                    "level": 1,
                    "description": "Managing contracts and agreements",
                },
                {
                    "name": "Partner Ecosystem",
                    "code": "PART-ECO",
                    "level": 1,
                    "description": "Managing partner relationships and ecosystems",
                },
                {
                    "name": "Strategic Sourcing",
                    "code": "PART-PRO-STR",
                    "level": 2,
                    "parent_code": "PART-PRO",
                    "description": "Strategic sourcing and supplier selection",
                },
            ],
            "WORK": [
                {
                    "name": "Human Capital Management",
                    "code": "WORK-HCM",
                    "level": 1,
                    "description": "Core HR, employee records, and workforce administration",
                },
                {
                    "name": "Talent Management",
                    "code": "WORK-TAL",
                    "level": 1,
                    "description": "Recruiting, performance, learning, and succession",
                },
                {
                    "name": "Payroll Management",
                    "code": "WORK-PAY",
                    "level": 1,
                    "description": "Payroll processing and compensation management",
                },
                {
                    "name": "Workforce Planning",
                    "code": "WORK-PLN",
                    "level": 1,
                    "description": "Workforce planning and analytics",
                },
                {
                    "name": "Employee Experience",
                    "code": "WORK-EXP",
                    "level": 1,
                    "description": "Employee engagement and experience management",
                },
                {
                    "name": "Recruiting",
                    "code": "WORK-TAL-REC",
                    "level": 2,
                    "parent_code": "WORK-TAL",
                    "description": "Talent acquisition and recruiting",
                },
                {
                    "name": "Learning & Development",
                    "code": "WORK-TAL-LND",
                    "level": 2,
                    "parent_code": "WORK-TAL",
                    "description": "Employee training and development",
                },
                {
                    "name": "Performance Management",
                    "code": "WORK-TAL-PER",
                    "level": 2,
                    "parent_code": "WORK-TAL",
                    "description": "Employee performance management and reviews",
                },
            ],
            "TECH": [
                {
                    "name": "IT Service Management",
                    "code": "TECH-ITSM",
                    "level": 1,
                    "description": "IT service desk, incident, and change management",
                },
                {
                    "name": "Application Management",
                    "code": "TECH-APP",
                    "level": 1,
                    "description": "Application portfolio and lifecycle management",
                },
                {
                    "name": "Infrastructure Management",
                    "code": "TECH-INF",
                    "level": 1,
                    "description": "IT infrastructure and cloud management",
                },
                {
                    "name": "Enterprise Architecture",
                    "code": "TECH-EA",
                    "level": 1,
                    "description": "Enterprise architecture and technology strategy",
                },
                {
                    "name": "Integration Management",
                    "code": "TECH-INT",
                    "level": 1,
                    "description": "System integration and API management",
                },
                {
                    "name": "DevOps",
                    "code": "TECH-APP-DEV",
                    "level": 2,
                    "parent_code": "TECH-APP",
                    "description": "DevOps and continuous delivery",
                },
                {
                    "name": "Cloud Management",
                    "code": "TECH-INF-CLD",
                    "level": 2,
                    "parent_code": "TECH-INF",
                    "description": "Cloud infrastructure and services management",
                },
            ],
        }

        if dry_run:
            print("\n=== DRY RUN - No changes will be made ===\n")

        # Step 1: Seed Business Domains
        print("Seeding Business Domains...")
        domains_created = 0
        domains_skipped = 0
        domain_map = {}  # code -> domain object

        for domain_data in BUSINESS_DOMAINS:
            existing = BusinessDomain.query.filter_by(code=domain_data["code"]).first()
            if existing:
                domains_skipped += 1
                domain_map[domain_data["code"]] = existing
                print(f"  [SKIP] {domain_data['code']}: {domain_data['name']} (already exists)")
            else:
                if not dry_run:
                    domain = BusinessDomain(
                        code=domain_data["code"],
                        name=domain_data["name"],
                        description=domain_data["description"],
                        domain_type=domain_data["domain_type"],
                        investment_priority="high"
                        if domain_data["domain_type"] == "primary"
                        else "medium",
                    )
                    db.session.add(domain)
                    db.session.flush()
                    domain_map[domain_data["code"]] = domain
                domains_created += 1
                print(f"  [CREATE] {domain_data['code']}: {domain_data['name']}")

        print(f"\nDomains: {domains_created} created, {domains_skipped} skipped")

        # Step 2: Seed Capabilities
        print("\nSeeding Business Capabilities...")
        caps_created = 0
        caps_skipped = 0
        capability_map = {}  # code -> capability object

        # First pass: Create L1 capabilities
        for domain_code, capabilities in CAPABILITIES.items():
            domain = domain_map.get(domain_code)
            if not domain and not dry_run:
                print(f"  [ERROR] Domain {domain_code} not found, skipping capabilities")
                continue

            for cap_data in capabilities:
                if cap_data["level"] != 1:
                    continue

                existing = UnifiedCapability.query.filter_by(code=cap_data["code"]).first()
                if existing:
                    caps_skipped += 1
                    capability_map[cap_data["code"]] = existing
                    print(f"  [SKIP] {cap_data['code']}: {cap_data['name']}")
                else:
                    if not dry_run:
                        cap = UnifiedCapability(
                            name=cap_data["name"],
                            code=cap_data["code"],
                            description=cap_data["description"],
                            level=cap_data["level"],
                            domain_id=domain.id if domain else 1,
                            status="defined",
                            category="core"
                            if domain_code in ["CUST", "PROD", "OPER"]
                            else "supporting",
                            capability_type="strategic",
                            strategic_importance="high"
                            if domain_code in ["CUST", "PROD", "OPER"]
                            else "medium",
                        )
                        db.session.add(cap)
                        db.session.flush()
                        capability_map[cap_data["code"]] = cap
                    caps_created += 1
                    print(f"  [CREATE] {cap_data['code']}: {cap_data['name']}")

        # Second pass: Create L2 capabilities with parent links
        for domain_code, capabilities in CAPABILITIES.items():
            domain = domain_map.get(domain_code)
            if not domain and not dry_run:
                continue

            for cap_data in capabilities:
                if cap_data["level"] != 2:
                    continue

                existing = UnifiedCapability.query.filter_by(code=cap_data["code"]).first()
                if existing:
                    caps_skipped += 1
                    capability_map[cap_data["code"]] = existing
                else:
                    parent = capability_map.get(cap_data.get("parent_code"))
                    if not dry_run:
                        cap = UnifiedCapability(
                            name=cap_data["name"],
                            code=cap_data["code"],
                            description=cap_data["description"],
                            level=cap_data["level"],
                            domain_id=domain.id if domain else 1,
                            parent_capability_id=parent.id if parent else None,
                            status="defined",
                            category="core"
                            if domain_code in ["CUST", "PROD", "OPER"]
                            else "supporting",
                            capability_type="operational",
                            strategic_importance="medium",
                        )
                        db.session.add(cap)
                        db.session.flush()
                        capability_map[cap_data["code"]] = cap
                    caps_created += 1
                    print(f"  [CREATE] {cap_data['code']}: {cap_data['name']} (L2)")

        if not dry_run:
            db.session.commit()
            print(f"\nCapabilities: {caps_created} created, {caps_skipped} skipped")
            # Also seed the BUSINESS capability store, because that is the one
            # the product actually reads.
            #
            # This command shadows the richer `seed-capabilities` group in
            # app/commands/seed_capabilities.py -- same name, registered later --
            # and it only ever wrote UnifiedCapability + BusinessDomain. But
            # /capability-map counts BusinessCapability through
            # capability_count_service.count_business_capabilities(), so the
            # documented command reported "60 capabilities seeded" on a fresh
            # install and left the flagship screen showing zero. That is the
            # five-capability-stores problem costing an evaluator their first hour.
            from app.commands.seed_capabilities import seed_business_caps
            from app.jobs.tenant_safe_job import tenant_scope
            from app.models.organization import Organization

            # An explicit tenant, because TenantMixin fills organization_id
            # from g.current_org_id on flush and a CLI has no request. Without
            # this the seed died on a NOT NULL violation, which is why this
            # store was never populated by the documented command.
            _org = Organization.query.order_by(Organization.id).first()
            if _org is None:
                print(
                    "[skip] No organisation exists yet, so business "
                    "capabilities have no tenant to belong to. Create one "
                    "(python create_admin.py) and re-run."
                )
                _business = {"created": 0}
            else:
                _org_id, _org_name = _org.id, _org.name
                with tenant_scope(_org_id):
                    _business = seed_business_caps()
                print(f"    seeded into organisation {_org_id} ({_org_name})")

            print(
                "\n[OK] Seeding complete! Unified capabilities: "
                f"{UnifiedCapability.query.count()}, business capabilities: "
                f"{_business.get('created', 0)} created"
            )
        else:
            print(
                f"\nCapabilities: {caps_created} would be created, {caps_skipped} would be skipped"
            )
            print("\n[DRY RUN] No changes made. Run without --dry-run to seed.")

    @app.cli.command()
    @click.option(
        "--domain",
        "-d",
        help="Filter by architecture domain (Enterprise, Business, Application, Data, Integration, Technology)",
    )
    @click.option("--level", "-l", type=int, help="Filter by APQC level (1, 2, or 3)")
    def list_apqc_processes(domain, level):
        """List APQC processes with optional filtering."""
        from app.seed_data.vendor_catalogue import (
            APQC_PROCESSES,
            get_apqc_processes_by_architecture_domain,
            get_apqc_processes_by_level,
        )

        if level:
            processes = get_apqc_processes_by_level(level)
            print(f"\nAPQC Level {level} Processes ({len(processes)}):")
        elif domain:
            processes = get_apqc_processes_by_architecture_domain(domain)
            print(f"\nAPQC Processes for {domain} Architecture ({len(processes)}):")
        else:
            processes = [{"code": k, **v} for k, v in APQC_PROCESSES.items() if v.get("level") == 1]
            print(f"\nAPQC Categories (Level 1) ({len(processes)}):")

        for p in processes:
            code = p.get("code", "")
            name = p.get("name", "")
            domains = p.get("architecture_domains", [])
            domain_str = f" [{', '.join(domains)}]" if domains else ""
            print(f"  {code}: {name}{domain_str}")

    @app.cli.command()
    @click.argument("vendor_id")
    def show_vendor_apqc(vendor_id):
        """Show APQC processes for a specific vendor."""
        from app.seed_data.vendor_catalogue import (
            get_apqc_process_info,
            get_vendor_apqc_processes,
            get_vendor_architecture_domains,
            get_vendor_by_id,
        )

        vendor = get_vendor_by_id(vendor_id)
        if not vendor:
            print(f"Vendor '{vendor_id}' not found.")
            return

        print(f"\nVendor: {vendor.get('name')}")
        print(f"Category: {vendor.get('category')}")

        domains = get_vendor_architecture_domains(vendor_id)
        print(f"Architecture Domains: {', '.join(domains)}")

        apqc_codes = get_vendor_apqc_processes(vendor_id)
        print(f"\nAPQC Processes ({len(apqc_codes)}):")

        for code in apqc_codes:
            info = get_apqc_process_info(code)
            if info:
                print(f"  {code}: {info.get('name', 'Unknown')}")

    # ===== APPLICATION-VENDOR MAPPING COMMANDS =====

    @app.cli.command()
    @click.option("--limit", "-l", type=int, help="Limit number of applications to process")
    @click.option(
        "--dry-run", is_flag=True, help="Show what would be mapped without making changes"
    )
    @click.option("--verbose", "-v", is_flag=True, help="Show detailed output for each application")
    def map_applications_to_vendors(limit, dry_run, verbose):
        """
        Auto-map applications to vendor products based on vendor_name field.

        This command finds applications that have a vendor_name set but no
        vendor_product_id, and automatically maps them to the appropriate
        VendorProduct records using fuzzy matching.

        Examples:
            flask map-applications-to-vendors           # Map all unmapped applications
            flask map-applications-to-vendors -l 10    # Map first 10 unmapped
            flask map-applications-to-vendors --dry-run # Preview without changes
        """
        from app.services.application_vendor_mapping_service import (
            get_application_vendor_mapping_service,
        )

        service = get_application_vendor_mapping_service()

        if dry_run:
            print("DRY RUN - No changes will be made\n")
            apps = service.get_unmapped_applications()
            if limit:
                apps = apps[:limit]

            print(f"Found {len(apps)} unmapped applications:\n")
            for app in apps:
                vendor = service.find_vendor_by_name(app.vendor_name)
                if vendor:
                    product = service.find_best_product_match(
                        vendor, app.name, app.application_category
                    )
                    if product:
                        print(f"  {app.name}")
                        print(f"    vendor_name: {app.vendor_name}")
                        print(f"    -> Would map to: {vendor.name} - {product.name}")
                    else:
                        print(f"  {app.name}")
                        print(f"    vendor_name: {app.vendor_name}")
                        print(f"    -> Vendor found ({vendor.name}) but no products available")
                else:
                    print(f"  {app.name}")
                    print(f"    vendor_name: {app.vendor_name}")
                    print("    -> No matching vendor found")
            return

        print("Mapping applications to vendor products...\n")
        results = service.bulk_auto_map_applications(limit=limit)

        print("=== Mapping Results ===")
        print(f"Total processed: {results['total_processed']}")
        print(f"Successfully mapped: {results['mapped']}")
        print(f"Already mapped (skipped): {results['skipped']}")
        print(f"Failed: {results['failed']}")

        if verbose:
            print("\n=== Details ===")
            for detail in results["details"]:
                status = "OK" if detail["success"] else "FAIL"
                print(f"  [{status}] {detail['application_name']}: {detail['message']}")

    @app.cli.command()
    @click.argument("application_id", type=int)
    def show_application_vendors(application_id):
        """Show all vendor products mapped to an application."""
        from app.models.application_portfolio import ApplicationComponent
        from app.services.application_vendor_mapping_service import (
            get_application_vendor_mapping_service,
        )

        app_record = ApplicationComponent.query.get(application_id)
        if not app_record:
            print(f"Application with ID {application_id} not found")
            return

        service = get_application_vendor_mapping_service()
        products = service.get_application_vendor_products(application_id)

        print(f"\nApplication: {app_record.name} (ID: {application_id})")
        print(f"vendor_name field: {app_record.vendor_name or 'Not set'}")
        print(f"Primary vendor_product_id: {app_record.vendor_product_id or 'Not set'}")

        if app_record.primary_vendor_product:
            p = app_record.primary_vendor_product
            print(
                f"Primary vendor product: {p.vendor_organization.name if p.vendor_organization else 'Unknown'} - {p.name}"
            )

        print(f"\nAll mapped vendor products ({len(products)}):")
        for item in products:
            vp = item["vendor_product"]
            print(f"  - {vp['vendor_name']} - {vp['name']}")
            print(f"    Type: {item['relationship_type']}, Criticality: {item['criticality']}")

    @app.cli.command()
    @click.argument("vendor_product_id", type=int)
    def show_vendor_product_applications(vendor_product_id):
        """Show all applications using a vendor product."""
        from app.models.vendor.vendor_organization import VendorProduct
        from app.services.application_vendor_mapping_service import (
            get_application_vendor_mapping_service,
        )

        product = VendorProduct.query.get(vendor_product_id)
        if not product:
            print(f"Vendor product with ID {vendor_product_id} not found")
            return

        service = get_application_vendor_mapping_service()
        apps = service.get_vendor_product_applications(vendor_product_id)

        vendor_name = product.vendor_organization.name if product.vendor_organization else "Unknown"
        print(f"\nVendor Product: {vendor_name} - {product.name} (ID: {vendor_product_id})")
        print(f"\nApplications using this product ({len(apps)}):")

        for item in apps:
            app_info = item["application"]
            print(f"  - {app_info['name']} ({app_info['application_code'] or 'No code'})")
            print(f"    Status: {app_info['lifecycle_status']}, Type: {item['relationship_type']}")

    @app.cli.command()
    @click.option(
        "--dry-run", is_flag=True, help="Show what would be changed without making changes"
    )
    @click.option("--verbose", "-v", is_flag=True, help="Show detailed output")
    def standardize_vendor_domains(dry_run, verbose):
        """Standardize vendor product domains using the domain taxonomy.

        This command normalizes the product_family field in VendorProduct
        to use standardized domain keys (erp, crm, hcm, etc.).

        Example:
            flask standardize-vendor-domains --dry-run
            flask standardize-vendor-domains -v
        """
        from app.models.vendor.domain_choices import (
            get_domain_label,
            normalize_domain,
        )
        from app.models.vendor.vendor_organization import VendorProduct

        print("=" * 60)
        print("Vendor Product Domain Standardization")
        print("=" * 60)

        if dry_run:
            print("\n[DRY RUN MODE - No changes will be made]\n")

        # Get all products
        products = VendorProduct.query.all()
        print(f"\nTotal products: {len(products)}")

        # Track changes
        changes = []
        already_standard = 0
        needs_update = 0

        # Analyze each product
        for product in products:
            current = product.product_family.family_name if product.product_family else ""
            normalized = normalize_domain(current)

            # Check if already standardized
            if current.lower() == normalized:
                already_standard += 1
                if verbose:
                    print(f"  [OK] {product.name}: '{current}' (already standard)")
            else:
                needs_update += 1
                changes.append(
                    {
                        "product": product,
                        "old": current,
                        "new": normalized,
                        "new_label": get_domain_label(normalized),
                    }
                )
                if verbose:
                    print(
                        f"  [CHANGE] {product.name}: '{current}' -> '{normalized}' ({get_domain_label(normalized)})"
                    )

        # Summary
        print(f"\n{'=' * 60}")
        print("Summary:")
        print(f"  Already standardized: {already_standard}")
        print(f"  Needs update: {needs_update}")

        # Domain distribution
        print(f"\n{'=' * 60}")
        print("Domain Distribution After Standardization:")
        domain_counts = {}
        for product in products:
            current = product.product_family.family_name if product.product_family else ""
            normalized = normalize_domain(current)
            domain_counts[normalized] = domain_counts.get(normalized, 0) + 1

        for domain_key in sorted(
            domain_counts.keys(), key=lambda k: domain_counts[k], reverse=True
        ):
            label = get_domain_label(domain_key)
            count = domain_counts[domain_key]
            bar = "#" * min(count // 5, 30)
            print(f"  {label:15} {count:4}  {bar}")

        # Apply changes if not dry run
        if not dry_run and changes:
            print(f"\n{'=' * 60}")
            print(f"Applying {len(changes)} changes...")

            for change in changes:
                # Find or create the product family
                from app.models.vendor.vendor_product import VendorProductFamily

                family = VendorProductFamily.query.filter_by(family_name=change["new"]).first()
                if family:
                    change["product"].family_id = family.id
                else:
                    # Create new family if it doesn't exist
                    new_family = VendorProductFamily(family_name=change["new"])
                    db.session.add(new_family)
                    db.session.flush()
                    change["product"].family_id = new_family.id

            db.session.commit()
            print(f"Done! Updated {len(changes)} products.")
        elif dry_run and changes:
            print(f"\n[DRY RUN] Would update {len(changes)} products.")
            print("Run without --dry-run to apply changes.")


def _seed_requirement_templates():
    from app.models.requirement_template import RequirementTemplate, SYSTEM_TEMPLATES
    if RequirementTemplate.query.count() == 0:
        for tmpl in SYSTEM_TEMPLATES:
            db.session.add(RequirementTemplate(**tmpl))
        db.session.commit()
        print(f'[seed] Inserted {len(SYSTEM_TEMPLATES)} requirement templates')


def setup_general():
    """Runs the set-up needed for both local development and production.
    Also sets up first admin user."""
    Role.insert_roles()
    admin_query = Role.query.filter_by(name="Administrator")
    if admin_query.first() is not None:
        if User.query.filter_by(email=Config.ADMIN_EMAIL).first() is None:
            user = User(
                first_name="Admin",
                last_name="Account",
                password=Config.ADMIN_PASSWORD,
                confirmed=True,
                email=Config.ADMIN_EMAIL,
            )
            db.session.add(user)
            db.session.commit()
            print("Added administrator {}".format(user.full_name()))


# ===== APP CREATION AND COMMAND REGISTRATION =====

if __name__ == "__main__":
    import signal
    import socket

    # ── Single-instance enforcement ──────────────────────────────────────────
    # Kill any process already bound to port 5000 before starting.
    # This prevents duplicate server instances caused by repeated runserver calls.
    _PORT = int(os.environ.get("FLASK_RUN_PORT", 5000))

    def _kill_port(_port):
        """Kill all PIDs listening on _port (Windows + Unix)."""
        import platform
        killed = []
        if platform.system() == "Windows":
            import subprocess as _sp
            try:
                # Filtered here rather than piped through findstr, so no shell
                # is involved; findstr's match was this same substring test.
                out = _sp.check_output(["netstat", "-ano"], text=True, stderr=_sp.DEVNULL)
                for line in out.splitlines():
                    parts = line.split()
                    if parts and "LISTENING" in line and f":{_port}" in line:
                        pid = int(parts[-1])
                        if pid and pid != os.getpid():
                            try:
                                _sp.call(["taskkill", "/F", "/PID", str(pid)],
                                         stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                                killed.append(pid)
                            except Exception:
                                pass
            except Exception:
                pass
        else:
            try:
                import subprocess as _sp
                out = _sp.check_output(
                    ["lsof", "-ti", f":{_port}"], text=True, stderr=_sp.DEVNULL
                )
                for pid_str in out.split():
                    pid = int(pid_str.strip())
                    if pid and pid != os.getpid():
                        try:
                            os.kill(pid, signal.SIGKILL)
                            killed.append(pid)
                        except Exception:
                            pass
            except Exception:
                pass
        return killed

    # Check if port is in use
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.settimeout(0.5)
    _in_use = _sock.connect_ex(("127.0.0.1", _PORT)) == 0
    _sock.close()

    if _in_use:
        _killed = _kill_port(_PORT)
        if _killed:
            print(f"[manage.py] Killed existing server(s) on port {_PORT}: PIDs {_killed}")
            import time as _time
            _time.sleep(1)
        else:
            print(f"[manage.py] WARNING: port {_PORT} in use but could not kill process.")
    # ─────────────────────────────────────────────────────────────────────────

    # Create app and register commands
    from app import create_app

    app = create_app(os.getenv("FLASK_CONFIG") or "default")
    migrate = Migrate(app, db)

    # Register all CLI commands
    register_cli_commands(app)

    # Run the app — use_reloader=False avoids Python 3.13 threading crash
    # (SystemError: 'is_done' of '_thread._ThreadHandle' on reloader restart)
    app.run(use_reloader=False)
else:
    # When imported, create app and register commands for CLI usage
    from app import create_app

    app = create_app(os.getenv("FLASK_CONFIG") or "default")
    # Behind a TLS-terminating reverse proxy (Caddy/nginx), honour X-Forwarded-Proto/Host
    # so the app builds https:// URLs + form actions (no mixed-content warnings) and the
    # secure session cookie stays consistent. Opt-in via TRUST_PROXY to avoid trusting
    # forged headers when exposed directly.
    if os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes") and not getattr(app, "_proxyfix_applied", False):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
        app._proxyfix_applied = True
    migrate = Migrate(app, db)
    register_cli_commands(app)
