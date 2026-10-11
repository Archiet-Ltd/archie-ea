"""Generate and run migrations on DEPLOYED SOLUTION databases.

IMPORTANT: This service runs Alembic/SQL on the deployed solution's database,
NOT on Entelim's database. Entelim's migration freeze (CLAUDE.md) does not apply
to generated solution databases, which are independent PostgreSQL instances
provisioned by Coolify.
"""
import logging

import sqlalchemy as sa

from app import db
from app.modules.codegen.models import SolutionInstance
from app.modules.codegen.services.credential_encryption import decrypt_credential

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "string": "VARCHAR(255)",
    "integer": "INTEGER",
    "decimal": "NUMERIC(12,2)",
    "date": "DATE",
    "boolean": "BOOLEAN",
    "enum": "VARCHAR(50)",
    "text": "TEXT",
}


class LiveMigrationService:
    """Generate SQL migrations for deployed solution databases."""

    def _sql_type(self, field_type: str) -> str:
        """Map abstract field type to SQL column type."""
        return _TYPE_MAP.get(field_type, "VARCHAR(255)")

    def generate_add_column_sql(
        self,
        table_name: str,
        field_name: str,
        field_type: str,
        default_value: str | None = None,
        enum_values: list[str] | None = None,
    ) -> str:
        """Generate ALTER TABLE ADD COLUMN SQL."""
        sql_type = self._sql_type(field_type)
        parts = [f"ALTER TABLE {table_name} ADD COLUMN {field_name} {sql_type}"]

        if default_value is not None:
            if field_type in ("integer", "decimal", "boolean"):
                parts.append(f"DEFAULT {default_value}")
            else:
                safe_default = default_value.replace("'", "''")
                parts.append(f"DEFAULT '{safe_default}'")

        return " ".join(parts) + ";"

    def generate_backfill_sql(
        self,
        table_name: str,
        field_name: str,
        default_value: str,
    ) -> str:
        """Generate UPDATE SQL to backfill existing rows."""
        safe_value = default_value.replace("'", "''")
        return f"UPDATE {table_name} SET {field_name} = '{safe_value}' WHERE {field_name} IS NULL;"

    def generate_model_code_patch(
        self,
        entity_name: str,
        field_name: str,
        field_type: str,
        default_value: str | None = None,
    ) -> str:
        """Generate Python code to add to the SQLAlchemy model."""
        sa_type_map = {
            "string": "db.String(255)",
            "integer": "db.Integer",
            "decimal": "db.Numeric(12, 2)",
            "date": "db.Date",
            "boolean": "db.Boolean",
            "enum": "db.String(50)",
            "text": "db.Text",
        }
        sa_type = sa_type_map.get(field_type, "db.String(255)")
        default_part = ""
        if default_value is not None:
            if field_type in ("integer",):
                default_part = f", default={default_value}"
            elif field_type == "boolean":
                default_part = f", default={default_value}"
            else:
                # default_value is architect-supplied with no character
                # restriction; a literal '"' would break out of this
                # generated Python string literal and corrupt the patch.
                safe_default = default_value.replace('"', '\\"')
                default_part = f', default="{safe_default}"'  # raw-html-ok: safe_default has its quotes escaped above; also generated Python source, not HTML
        return f"    {field_name} = db.Column({sa_type}{default_part})"

    def generate_schema_code_patch(
        self,
        field_name: str,
        field_type: str,
    ) -> str:
        """Generate Pydantic schema field code."""
        py_type_map = {
            "string": "Optional[str]",
            "integer": "Optional[int]",
            "decimal": "Optional[float]",
            "date": "Optional[date]",
            "boolean": "Optional[bool]",
            "enum": "Optional[str]",
            "text": "Optional[str]",
        }
        py_type = py_type_map.get(field_type, "Optional[str]")
        return f"    {field_name}: {py_type} = None"

    # ─── High-level orchestrator ──────────────────────────────────────

    def add_field(
        self,
        instance_id: int,
        entity: str,
        field_name: str,
        field_type: str,
        default=None,
    ) -> dict:
        """Add a field to a deployed solution's database.

        1. Generate ALTER TABLE SQL
        2. Run migration on deployed DB
        3. Update generated code (model + schema + route)
        4. Redeploy via DeploymentOrchestrator

        Returns: {success: bool, migration_sql: str, files_updated: int, error: str|None}
        """
        table_name = entity.lower() + "s"  # simple pluralisation
        result = {
            "success": False,
            "migration_sql": "",
            "files_updated": 0,
            "error": None,
        }

        # 1. Generate migration SQL
        migration_sql = self.generate_add_column_sql(
            table_name=table_name,
            field_name=field_name,
            field_type=field_type,
            default_value=str(default) if default is not None else None,
        )
        result["migration_sql"] = migration_sql

        # 2. Look up the deployed instance and its database URL
        instance = db.session.get(SolutionInstance, instance_id)
        if not instance:
            result["error"] = f"SolutionInstance {instance_id} not found"
            return result

        db_url = decrypt_credential(instance.database_url_encrypted)
        if not db_url:
            result["error"] = "No database URL configured for this instance"
            return result

        # 3. Execute migration on the deployed solution's database
        try:
            engine = sa.create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(sa.text(migration_sql))
                # Backfill if a default was provided
                if default is not None:
                    backfill_sql = self.generate_backfill_sql(
                        table_name=table_name,
                        field_name=field_name,
                        default_value=str(default),
                    )
                    conn.execute(sa.text(backfill_sql))
                conn.commit()
            engine.dispose()
        except Exception as exc:
            result["error"] = f"Migration failed: {exc}"
            logger.exception("Live migration failed for instance %s", instance_id)
            return result

        # 4. Regenerate code patches
        model_patch = self.generate_model_code_patch(
            entity_name=entity,
            field_name=field_name,
            field_type=field_type,
            default_value=str(default) if default is not None else None,
        )
        schema_patch = self.generate_schema_code_patch(
            field_name=field_name,
            field_type=field_type,
        )

        code_files = {
            f"models/{entity.lower()}.py": model_patch,
            f"schemas/{entity.lower()}.py": schema_patch,
        }
        result["files_updated"] = len(code_files)

        # 5. Redeploy via DeploymentOrchestrator
        try:
            from app.modules.codegen.services.deployment_orchestrator import (
                DeploymentOrchestrator,
            )

            orchestrator = DeploymentOrchestrator()
            orchestrator.redeploy(instance_id, code_files)
        except Exception as exc:
            result["error"] = f"Redeploy failed: {exc}"
            logger.exception("Redeploy failed for instance %s", instance_id)
            return result

        result["success"] = True
        return result
