"""framework_adoption_harmonisation_regulatory_change

Revision ID: 20261003_fw_adopt
Revises: 20261001_approval_nullable
Create Date: 2026-10-03 00:34:30.660416

Idempotent: every CREATE / ADD is guarded by IF NOT EXISTS so that CI
(which builds the schema from the models before running the migration chain)
does not fail with DuplicateTable or DuplicateColumn.  Every DROP in the
downgrade is guarded by IF EXISTS for the same reason.  Matches the pattern
established by 20260930_capability_backlinks.py and 20261001_risk_score_fields.py.
"""
from alembic import op
from sqlalchemy import text

revision = "20261003_fw_adopt"
down_revision = "20261002_data_domain_org_unique"
branch_labels = None
depends_on = None

# ── explicit constraint names ──────────────────────────────────────────

_FK_HARMONIZED_CONTROL = "fk_compliance_controls_harmonized_control_id"

_FK_ADOPTION_ADOPTED_BY = "fk_framework_adoptions_adopted_by_id"
_FK_ADOPTION_FRAMEWORK = "fk_framework_adoptions_framework_id"
_FK_ADOPTION_ORG = "fk_framework_adoptions_organization_id"
_FK_ADOPTION_REF = "fk_framework_adoptions_reference_adoption_id"

_FK_CHANGE_FRAMEWORK = "fk_regulatory_changes_framework_id"
_FK_CHANGE_ORG = "fk_regulatory_changes_organization_id"
_FK_CHANGE_RECORDED_BY = "fk_regulatory_changes_recorded_by_id"

_FK_IMPACT_CHANGE = "fk_regulatory_change_impacts_change_id"
_FK_IMPACT_ORG = "fk_regulatory_change_impacts_organization_id"
_FK_IMPACT_OWNER = "fk_regulatory_change_impacts_owner_id"

_IX_HARMONIZED_CONTROL = "ix_compliance_controls_harmonized_control_id"
_IX_ADOPTION_FRAMEWORK = "ix_framework_adoptions_framework_id"
_IX_ADOPTION_ORG = "ix_framework_adoptions_organization_id"
_IX_ADOPTION_SCOPE = "ix_framework_adoptions_scope"
_IX_CHANGE_FRAMEWORK = "ix_regulatory_changes_framework_id"
_IX_CHANGE_ORG = "ix_regulatory_changes_organization_id"
_IX_IMPACT_CHANGE = "ix_regulatory_change_impacts_change_id"
_IX_IMPACT_ORG = "ix_regulatory_change_impacts_organization_id"

_UQ_ORG_ADOPTION = "uq_org_framework_adoption"


def _add_fk_if_not_exists(bind, table, constraint_name, columns, ref_table, ref_columns, ondelete=None):
    """Add a foreign key if it does not already exist."""
    bind.execute(text(f"""
        DO $do_fk$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = to_regclass('{table}')
              AND conname = '{constraint_name}'
          ) THEN
            ALTER TABLE "{table}"
              ADD CONSTRAINT {constraint_name}
              FOREIGN KEY ({', '.join(columns)})
              REFERENCES {ref_table} ({', '.join(ref_columns)})
              {'ON DELETE ' + ondelete if ondelete else ''};
          END IF;
        END
        $do_fk$
    """))


def _add_unique_if_not_exists(bind, table, constraint_name, columns):
    """Add a UNIQUE constraint if it does not already exist."""
    bind.execute(text(f"""
        DO $do_uq$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = to_regclass('{table}')
              AND conname = '{constraint_name}'
          ) THEN
            ALTER TABLE "{table}"
              ADD CONSTRAINT {constraint_name}
              UNIQUE ({', '.join(columns)});
          END IF;
        END
        $do_uq$
    """))


def upgrade():
    bind = op.get_bind()

    # ── Harmonisation columns on compliance_controls ────────────────────
    bind.execute(text(
        "ALTER TABLE compliance_controls "
        "ADD COLUMN IF NOT EXISTS harmonized_control_id INTEGER"
    ))
    bind.execute(text(
        "ALTER TABLE compliance_controls "
        "ADD COLUMN IF NOT EXISTS harmonization_status VARCHAR(20)"
    ))
    bind.execute(text(
        "ALTER TABLE compliance_controls "
        "ADD COLUMN IF NOT EXISTS harmonization_notes TEXT"
    ))
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_HARMONIZED_CONTROL} "
        "ON compliance_controls (harmonized_control_id)"
    ))
    _add_fk_if_not_exists(
        bind, "compliance_controls", _FK_HARMONIZED_CONTROL,
        ["harmonized_control_id"], "compliance_controls", ["id"],
    )

    # ── Framework adoptions ─────────────────────────────────────────────
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS framework_adoptions (
            id SERIAL NOT NULL,
            organization_id INTEGER,
            scope VARCHAR(16),
            framework_id INTEGER NOT NULL,
            reference_adoption_id INTEGER,
            adopted_by_id INTEGER,
            adopted_at TIMESTAMP WITHOUT TIME ZONE,
            status VARCHAR(20),
            tailoring_notes TEXT,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (id)
        )
    """))
    _add_fk_if_not_exists(
        bind, "framework_adoptions", _FK_ADOPTION_ADOPTED_BY,
        ["adopted_by_id"], "users", ["id"],
    )
    _add_fk_if_not_exists(
        bind, "framework_adoptions", _FK_ADOPTION_FRAMEWORK,
        ["framework_id"], "regulatory_frameworks", ["id"],
    )
    _add_fk_if_not_exists(
        bind, "framework_adoptions", _FK_ADOPTION_ORG,
        ["organization_id"], "organizations", ["id"], ondelete="CASCADE",
    )
    _add_fk_if_not_exists(
        bind, "framework_adoptions", _FK_ADOPTION_REF,
        ["reference_adoption_id"], "framework_adoptions", ["id"], ondelete="SET NULL",
    )
    _add_unique_if_not_exists(
        bind, "framework_adoptions", _UQ_ORG_ADOPTION,
        ["organization_id", "framework_id"],
    )
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_ADOPTION_FRAMEWORK} "
        "ON framework_adoptions (framework_id)"
    ))
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_ADOPTION_ORG} "
        "ON framework_adoptions (organization_id)"
    ))
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_ADOPTION_SCOPE} "
        "ON framework_adoptions (scope)"
    ))

    # ── Adoption link on application_compliance_controls ─────────────────
    bind.execute(text("""
        DO $app_id_nullable$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'application_compliance_controls'
              AND column_name = 'application_id'
              AND is_nullable = 'NO'
          ) THEN
            ALTER TABLE application_compliance_controls
              ALTER COLUMN application_id DROP NOT NULL;
          END IF;
        END
        $app_id_nullable$
    """))
    bind.execute(text(
        "ALTER TABLE application_compliance_controls "
        "ADD COLUMN IF NOT EXISTS adoption_id INTEGER"
    ))
    _add_fk_if_not_exists(
        bind, "application_compliance_controls", "fk_app_compliance_adoption",
        ["adoption_id"], "framework_adoptions", ["id"], ondelete="SET NULL",
    )
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_app_compliance_adoption_id "
        "ON application_compliance_controls (adoption_id)"
    ))

    # ── Regulatory changes ──────────────────────────────────────────────
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS regulatory_changes (
            id SERIAL NOT NULL,
            organization_id INTEGER NOT NULL,
            framework_id INTEGER NOT NULL,
            change_type VARCHAR(30) NOT NULL,
            title VARCHAR(500) NOT NULL,
            description TEXT,
            effective_date DATE,
            source_url VARCHAR(500),
            recorded_by_id INTEGER,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (id)
        )
    """))
    _add_fk_if_not_exists(
        bind, "regulatory_changes", _FK_CHANGE_FRAMEWORK,
        ["framework_id"], "regulatory_frameworks", ["id"],
    )
    _add_fk_if_not_exists(
        bind, "regulatory_changes", _FK_CHANGE_ORG,
        ["organization_id"], "organizations", ["id"], ondelete="CASCADE",
    )
    _add_fk_if_not_exists(
        bind, "regulatory_changes", _FK_CHANGE_RECORDED_BY,
        ["recorded_by_id"], "users", ["id"],
    )
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_CHANGE_FRAMEWORK} "
        "ON regulatory_changes (framework_id)"
    ))
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_CHANGE_ORG} "
        "ON regulatory_changes (organization_id)"
    ))

    # ── Regulatory change impacts ───────────────────────────────────────
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS regulatory_change_impacts (
            id SERIAL NOT NULL,
            organization_id INTEGER NOT NULL,
            change_id INTEGER NOT NULL,
            element_type VARCHAR(50) NOT NULL,
            element_id INTEGER NOT NULL,
            element_name VARCHAR(500),
            impact_assessment TEXT,
            owner_id INTEGER,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            PRIMARY KEY (id)
        )
    """))
    _add_fk_if_not_exists(
        bind, "regulatory_change_impacts", _FK_IMPACT_CHANGE,
        ["change_id"], "regulatory_changes", ["id"],
    )
    _add_fk_if_not_exists(
        bind, "regulatory_change_impacts", _FK_IMPACT_ORG,
        ["organization_id"], "organizations", ["id"], ondelete="CASCADE",
    )
    _add_fk_if_not_exists(
        bind, "regulatory_change_impacts", _FK_IMPACT_OWNER,
        ["owner_id"], "users", ["id"],
    )
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_IMPACT_CHANGE} "
        "ON regulatory_change_impacts (change_id)"
    ))
    bind.execute(text(
        f"CREATE INDEX IF NOT EXISTS {_IX_IMPACT_ORG} "
        "ON regulatory_change_impacts (organization_id)"
    ))


def downgrade():
    bind = op.get_bind()

    bind.execute(text("DROP TABLE IF EXISTS regulatory_change_impacts CASCADE"))
    bind.execute(text("DROP TABLE IF EXISTS regulatory_changes CASCADE"))

    bind.execute(text("DROP INDEX IF EXISTS ix_app_compliance_adoption_id"))
    bind.execute(text(
        "ALTER TABLE application_compliance_controls "
        "DROP CONSTRAINT IF EXISTS fk_app_compliance_adoption"
    ))
    bind.execute(text(
        "ALTER TABLE application_compliance_controls "
        "DROP COLUMN IF EXISTS adoption_id"
    ))
    bind.execute(text(
        "ALTER TABLE application_compliance_controls "
        "ALTER COLUMN application_id SET NOT NULL"
    ))

    bind.execute(text("DROP TABLE IF EXISTS framework_adoptions CASCADE"))

    bind.execute(text(
        f"ALTER TABLE compliance_controls "
        f"DROP CONSTRAINT IF EXISTS {_FK_HARMONIZED_CONTROL}"
    ))
    bind.execute(text(
        f"DROP INDEX IF EXISTS {_IX_HARMONIZED_CONTROL}"
    ))
    bind.execute(text(
        "ALTER TABLE compliance_controls "
        "DROP COLUMN IF EXISTS harmonization_notes"
    ))
    bind.execute(text(
        "ALTER TABLE compliance_controls "
        "DROP COLUMN IF EXISTS harmonization_status"
    ))
    bind.execute(text(
        "ALTER TABLE compliance_controls "
        "DROP COLUMN IF EXISTS harmonized_control_id"
    ))