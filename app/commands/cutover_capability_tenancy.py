"""Controlled maintenance cutover for hybrid UnifiedCapability ownership.

The command is deliberately explicit: dry-run is read-only, apply requires a
recorded backup, ownership comes only from audited relationships/provenance,
and every discovered foreign-key table is measured before and after merges.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Literal, Sequence

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db


ADVISORY_LOCK_ID = 1_684_220_026

# `classify_capability` accepts audited tenant provenance on a row with no
# relationships as evidence of tenant ownership. `flask project-capabilities`
# reads this flag to decide whether projecting before the cutover would leave
# every projected row 'ambiguous' and block `run_cutover` outright.
CLASSIFIES_PROVENANCE_ONLY_TENANT = True


class CutoverBlocked(RuntimeError):
    """Raised before an unsafe or unverifiable cutover can proceed."""


class CutoverReportPending(RuntimeError):
    """The database committed, but the completed audit report is not published."""

    def __init__(self, message: str, marker_path: Path):
        super().__init__(message)
        self.marker_path = marker_path


@dataclass(frozen=True)
class CapabilityClassification:
    capability_id: int
    scope: Literal["reference", "tenant", "ambiguous"]
    organization_id: int | None
    evidence: Sequence[str]


@dataclass(frozen=True)
class CapabilityProvenance:
    source_table: str | None
    source_id: str | None
    source_org_id: int | None
    source_checksum: str | None
    citations: tuple[str, ...]

    @property
    def is_seeded_reference(self) -> bool:
        source = (self.source_table or "").casefold()
        return bool(
            self.source_checksum
            and self.source_id
            and self.source_org_id is None
            and (source.startswith("seed") or source.startswith("reference"))
        )

    def supports_tenant(self, organization_id: int) -> bool:
        return bool(
            self.source_checksum
            and self.source_id
            and self.source_org_id == organization_id
        )


def _table_columns(connection, table_name: str) -> frozenset[str]:
    relation_id = connection.execute(
        text("SELECT to_regclass(:table_name)::oid"), {"table_name": table_name}
    ).scalar_one_or_none()
    if relation_id is None:
        return frozenset()
    rows = connection.execute(
        text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = :relation_id AND attnum > 0 AND NOT attisdropped"
        ),
        {"relation_id": relation_id},
    )
    return frozenset(row[0] for row in rows)


def _quoted(identifier: str) -> str:
    """Quote a database identifier discovered from PostgreSQL metadata."""

    return '"' + identifier.replace('"', '""') + '"'


def load_capability_provenance(connection, capability_id: int) -> CapabilityProvenance:
    row = connection.execute(
        text(
            "SELECT source_table, source_id, source_org_id, source_checksum "
            "FROM unified_capabilities WHERE id = :id"
        ),
        {"id": capability_id},
    ).mappings().one()
    citations = tuple(
        f"{field}={row[field]}"
        for field in ("source_table", "source_id", "source_org_id", "source_checksum")
        if row[field] is not None
    )
    return CapabilityProvenance(
        source_table=row["source_table"],
        source_id=row["source_id"],
        source_org_id=row["source_org_id"],
        source_checksum=row["source_checksum"],
        citations=citations,
    )


def _relationship_rows(connection, capability_id: int) -> list[tuple[int, str]]:
    """Return audited organization IDs and human-readable source citations."""

    found: list[tuple[int, str]] = []

    for mapping_table in (
        "unified_application_capability_mapping",
        "unified_capability_application_mappings",
    ):
        columns = _table_columns(connection, mapping_table)
        if not {"id", "unified_capability_id", "application_component_id"} <= columns:
            continue
        active = "AND m.is_active IS NOT FALSE" if "is_active" in columns else ""
        rows = connection.execute(
            text(
                f"SELECT a.id, a.organization_id FROM {_quoted(mapping_table)} AS m "  # nosec B608 -- identifier comes from a fixed tuple and is quoted
                "JOIN application_components AS a ON a.id = m.application_component_id "
                "WHERE m.unified_capability_id = :capability_id "
                f"AND a.organization_id IS NOT NULL {active}"
            ),
            {"capability_id": capability_id},
        )
        found.extend(
            (int(row.organization_id), f"application_components:{row.id}:organization_id={row.organization_id}")
            for row in rows
        )

    direct_sources = (
        ("benefits", "capability_id", "status", {"cancelled"}),
        ("work_packages", "capability_id", "status", {"cancelled"}),
        ("unified_work_packages", "capability_id", "status", {"cancelled"}),
    )
    for table_name, capability_column, status_column, inactive_statuses in direct_sources:
        columns = _table_columns(connection, table_name)
        if not {"id", capability_column, "organization_id"} <= columns:
            continue
        status_filter = ""
        parameters: dict[str, object] = {"capability_id": capability_id}
        if status_column in columns:
            status_filter = f"AND COALESCE({_quoted(status_column)}, '') <> ALL(:inactive_statuses)"
            parameters["inactive_statuses"] = list(inactive_statuses)
        rows = connection.execute(
            text(
                f"SELECT id, organization_id FROM {_quoted(table_name)} "  # nosec B608 -- identifiers come from a fixed tuple and are quoted
                f"WHERE {_quoted(capability_column)} = :capability_id "
                f"AND organization_id IS NOT NULL {status_filter}"
            ),
            parameters,
        )
        found.extend(
            (
                int(row.organization_id),
                f"{table_name}:{row.id}:organization_id={row.organization_id}",
            )
            for row in rows
        )

    columns = _table_columns(connection, "work_package_capabilities")
    if {"work_package_id", "capability_id"} <= columns and _table_columns(
        connection, "work_packages"
    ):
        rows = connection.execute(
            text(
                "SELECT w.id, w.organization_id FROM work_package_capabilities AS m "
                "JOIN work_packages AS w ON w.id = m.work_package_id "
                "WHERE m.capability_id = :capability_id AND w.organization_id IS NOT NULL "
                "AND COALESCE(w.status, '') <> 'cancelled'"
            ),
            {"capability_id": capability_id},
        )
        found.extend(
            (int(row.organization_id), f"work_packages:{row.id}:organization_id={row.organization_id}")
            for row in rows
        )

    return sorted(set(found), key=lambda item: (item[0], item[1]))


def load_relationship_organization_ids(connection, capability_id: int) -> tuple[int, ...]:
    """Load the organization union reached by current audited relationships."""

    return tuple(sorted({organization_id for organization_id, _ in _relationship_rows(
        connection, capability_id
    )}))


def classify_capability(connection, capability_id: int) -> CapabilityClassification:
    row = connection.execute(
        text("SELECT id FROM unified_capabilities WHERE id = :id FOR SHARE"),
        {"id": capability_id},
    ).mappings().one()
    relationship_rows = _relationship_rows(connection, row["id"])
    owners = tuple(sorted({organization_id for organization_id, _ in relationship_rows}))
    provenance = load_capability_provenance(connection, row["id"])
    evidence = tuple(provenance.citations) + tuple(citation for _, citation in relationship_rows)
    if provenance.is_seeded_reference and not owners:
        return CapabilityClassification(row["id"], "reference", None, evidence)
    if len(owners) == 1 and provenance.supports_tenant(owners[0]):
        return CapabilityClassification(row["id"], "tenant", owners[0], evidence)
    # A row projected from a tenant-owned source has audited provenance but no
    # downstream links yet. Its owner comes from a NOT NULL organization_id on
    # the source row, which is STRONGER evidence than the branch above: that one
    # infers ownership from relationships and then merely checks provenance
    # agrees. What is dropped here is not evidence but corroboration -- and
    # corroboration that cannot exist for a row nothing has linked to yet.
    # Accepted only when nothing contradicts it: no relationship owner at all.
    if (
        not owners
        and provenance.source_org_id is not None
        and provenance.source_checksum
        and provenance.source_id
    ):
        return CapabilityClassification(
            row["id"], "tenant", provenance.source_org_id, evidence
        )
    return CapabilityClassification(row["id"], "ambiguous", None, evidence)


def _foreign_keys(connection) -> list[dict[str, str]]:
    rows = connection.execute(
        text(
            """
            SELECT c.conrelid::regclass::text AS table_name,
                   a.attname AS column_name,
                   c.conname AS constraint_name
            FROM pg_constraint AS c
            JOIN unnest(c.conkey) WITH ORDINALITY AS key(attnum, ordinality) ON true
            JOIN pg_attribute AS a
              ON a.attrelid = c.conrelid AND a.attnum = key.attnum
            WHERE c.contype = 'f'
              AND c.confrelid = to_regclass('unified_capabilities')
            ORDER BY table_name, column_name, constraint_name
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _lock_cutover_tables(connection, foreign_keys: Sequence[dict[str, str]]) -> None:
    """Freeze classification evidence and every discovered inbound FK surface.

    ``SHARE ROW EXCLUSIVE`` permits ordinary reads but conflicts with the
    ``ROW EXCLUSIVE`` lock taken by INSERT/UPDATE/DELETE.  The locks live only
    for the cutover transaction, so relationship ownership cannot change
    between classification and final verification.
    """

    candidates = {
        "unified_capabilities",
        "application_components",
        "benefits",
        "work_packages",
        "unified_work_packages",
        "work_package_capabilities",
        "unified_application_capability_mapping",
        "unified_capability_application_mappings",
    }
    candidates.update(item["table_name"] for item in foreign_keys)
    existing = sorted(name for name in candidates if _table_columns(connection, name))
    if existing:
        connection.execute(
            text(
                "LOCK TABLE "
                + ", ".join(_quoted(name) for name in existing)
                + " IN SHARE ROW EXCLUSIVE MODE"
            )
        )


def _foreign_key_counts(connection, foreign_keys: Sequence[dict[str, str]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for foreign_key in foreign_keys:
        table_name = foreign_key["table_name"]
        column_name = foreign_key["column_name"]
        key = f"{table_name}.{column_name}"
        result[key] = int(
            connection.execute(
                text(
                    f"SELECT count(*) FROM {_quoted(table_name)} "  # nosec B608 -- catalog identifiers are quoted
                    f"WHERE {_quoted(column_name)} IS NOT NULL"
                )
            ).scalar_one()
        )
    return result


def _foreign_key_orphans(connection, foreign_keys: Sequence[dict[str, str]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for foreign_key in foreign_keys:
        table_name = foreign_key["table_name"]
        column_name = foreign_key["column_name"]
        key = f"{table_name}.{column_name}"
        result[key] = int(
            connection.execute(
                text(
                    f"SELECT count(*) FROM {_quoted(table_name)} AS source "  # nosec B608 -- catalog identifiers are quoted
                    "LEFT JOIN unified_capabilities AS target "
                    f"ON target.id = source.{_quoted(column_name)} "
                    f"WHERE source.{_quoted(column_name)} IS NOT NULL AND target.id IS NULL"
                )
            ).scalar_one()
        )
    return result


def _owner_expression(connection, table_name: str, alias: str = "source") -> str:
    columns = _table_columns(connection, table_name)
    if "organization_id" in columns:
        return f"{alias}.organization_id"
    if "application_component_id" in columns:
        return (
            f"(SELECT app.organization_id FROM application_components AS app "  # nosec B608 -- alias is fixed by internal callers
            f"WHERE app.id = {alias}.application_component_id)"
        )
    if "work_package_id" in columns and _table_columns(connection, "work_packages"):
        return (
            f"(SELECT package.organization_id FROM work_packages AS package "  # nosec B608 -- alias is fixed by internal callers
            f"WHERE package.id = {alias}.work_package_id)"
        )
    return "NULL::integer"


def _snapshot(connection) -> dict[str, object]:
    row = connection.execute(
        text(
            """
            SELECT count(*) AS row_count,
                   md5(COALESCE(string_agg(
                       concat_ws('|', id::text, COALESCE(code, ''),
                                 COALESCE(archimate_id, ''), COALESCE(scope, ''),
                                 COALESCE(organization_id::text, ''),
                                 COALESCE(retired_into_id::text, '')),
                       ',' ORDER BY id), '')) AS checksum
            FROM unified_capabilities
            """
        )
    ).mappings().one()
    return {"row_count": int(row["row_count"]), "checksum": row["checksum"]}


def _load_backup_manifest(path: str | Path | None) -> dict[str, object]:
    if path is None:
        raise CutoverBlocked("apply requires a backup manifest with a recorded backup path")
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CutoverBlocked(f"backup manifest is unreadable: {manifest_path}") from exc
    if not isinstance(payload, dict) or not payload.get("backup_path"):
        raise CutoverBlocked("backup manifest must record a non-empty backup_path")
    return payload


def _duplicate_plan(connection) -> list[dict[str, object]]:
    rows = connection.execute(
        text(
            """
            SELECT scope, organization_id, code, array_agg(id ORDER BY id) AS ids
            FROM unified_capabilities
            WHERE code IS NOT NULL AND retired_into_id IS NULL
              AND scope IN ('reference', 'tenant')
            GROUP BY scope, organization_id, code
            HAVING count(*) > 1
            ORDER BY scope, organization_id NULLS FIRST, code
            """
        )
    ).mappings()
    plan: list[dict[str, object]] = []
    for row in rows:
        target_id, *source_ids = [int(value) for value in row["ids"]]
        for source_id in source_ids:
            plan.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "scope": row["scope"],
                    "organization_id": row["organization_id"],
                    "code": row["code"],
                }
            )
    return plan


def _resolve_duplicate(
    connection,
    duplicate: dict[str, object],
    foreign_keys: Sequence[dict[str, str]],
) -> int:
    source_id = duplicate["source_id"]
    target_id = duplicate["target_id"]
    organization_id = duplicate["organization_id"]
    connection.execute(
        text(
            "SELECT id FROM unified_capabilities "
            "WHERE id IN (:source_id, :target_id) FOR UPDATE"
        ),
        {"source_id": source_id, "target_id": target_id},
    ).all()

    writes = 0
    before_counts = _foreign_key_counts(connection, foreign_keys)
    for foreign_key in foreign_keys:
        table_name = foreign_key["table_name"]
        column_name = foreign_key["column_name"]
        if table_name == "unified_capabilities" and column_name == "retired_into_id":
            continue
        owner_expression = _owner_expression(connection, table_name)
        table = _quoted(table_name)
        column = _quoted(column_name)
        connection.execute(
            text(
                f"SELECT 1 FROM {table} AS source "  # nosec B608 -- catalog identifiers are quoted; IDs are bound
                f"WHERE source.{column} IN (:source_id, :target_id) FOR UPDATE"
            ),
            {"source_id": source_id, "target_id": target_id},
        ).all()
        source_links = int(
            connection.execute(
                text(
                    f"SELECT count(*) FROM {table} AS source "  # nosec B608 -- catalog identifiers are quoted; ID is bound
                    f"WHERE source.{column} = :source_id"
                ),
                {"source_id": source_id},
            ).scalar_one()
        )
        if organization_id is None:
            ownership_predicate = f"({owner_expression}) IS NOT NULL"
        else:
            ownership_predicate = f"({owner_expression}) = :organization_id"
        eligible = int(
            connection.execute(
                text(
                    f"SELECT count(*) FROM {table} AS source "  # nosec B608 -- catalog identifiers are quoted; values are bound
                    f"WHERE source.{column} = :source_id "
                    f"AND {ownership_predicate}"
                ),
                {"source_id": source_id, "organization_id": organization_id},
            ).scalar_one()
        )
        if eligible != source_links:
            raise CutoverBlocked(
                "unknown or conflicting ownership for "
                f"{table_name}.{column_name} relationships to duplicate {source_id}"
            )
        target_links_before = int(
            connection.execute(
                text(
                    f"SELECT count(*) FROM {table} AS source "  # nosec B608 -- catalog identifiers are quoted; ID is bound
                    f"WHERE source.{column} = :target_id"
                ),
                {"target_id": target_id},
            ).scalar_one()
        )
        result = connection.execute(
            text(
                f"UPDATE {table} AS source SET {column} = :target_id "  # nosec B608 -- catalog identifiers are quoted; IDs are bound
                f"WHERE source.{column} = :source_id "
                f"AND {ownership_predicate}"
            ),
            {
                "source_id": source_id,
                "target_id": target_id,
                "organization_id": organization_id,
            },
        )
        if result.rowcount != eligible:
            raise CutoverBlocked(
                f"relationship count mismatch while repointing {table_name}.{column_name}"
            )
        writes += result.rowcount
        source_links_after, target_links_after = connection.execute(
            text(
                f"SELECT count(*) FILTER (WHERE source.{column} = :source_id), "  # nosec B608 -- catalog identifiers are quoted; IDs are bound
                f"count(*) FILTER (WHERE source.{column} = :target_id) "
                f"FROM {table} AS source"
            ),
            {"source_id": source_id, "target_id": target_id},
        ).one()
        if source_links_after != 0 or target_links_after != target_links_before + source_links:
            raise CutoverBlocked(
                f"relationship assignment mismatch after repointing {table_name}.{column_name}"
            )

    result = connection.execute(
        text(
            "UPDATE unified_capabilities SET code = NULL, archimate_id = NULL, "
            "retired_into_id = :target_id "
            "WHERE id = :source_id "
            "AND organization_id IS NOT DISTINCT FROM :organization_id"
        ),
        {
            "source_id": source_id,
            "target_id": target_id,
            "organization_id": organization_id,
        },
    )
    if result.rowcount != 1:
        raise CutoverBlocked(f"duplicate capability {source_id} changed during cutover")
    writes += 1

    after_counts = _foreign_key_counts(connection, foreign_keys)
    if before_counts != after_counts:
        raise CutoverBlocked(f"foreign-key row count mismatch after retiring {source_id}")
    return writes


def install_cutover_constraints(connection) -> None:
    """Swap global uniqueness for the four hybrid partial indexes in-transaction."""

    connection.execute(
        text(
            "ALTER TABLE unified_capabilities "
            "DROP CONSTRAINT IF EXISTS unified_capabilities_code_key, "
            "DROP CONSTRAINT IF EXISTS unified_capabilities_archimate_id_key"
        )
    )
    connection.execute(text("DROP INDEX IF EXISTS ix_unified_capabilities_code"))
    connection.execute(text("DROP INDEX IF EXISTS ix_unified_capabilities_archimate_id"))
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_capabilities_reference_code "
            "ON unified_capabilities (code) WHERE organization_id IS NULL"
        )
    )
    connection.execute(
        text(
            """
            DO $foreign_keys$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('unified_capabilities')
                  AND conname = 'fk_unified_capabilities_organization'
              ) THEN
                ALTER TABLE unified_capabilities
                  ADD CONSTRAINT fk_unified_capabilities_organization
                  FOREIGN KEY (organization_id) REFERENCES organizations(id)
                  ON DELETE CASCADE NOT VALID;
              END IF;
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('unified_capabilities')
                  AND conname = 'fk_unified_capabilities_source_org'
              ) THEN
                ALTER TABLE unified_capabilities
                  ADD CONSTRAINT fk_unified_capabilities_source_org
                  FOREIGN KEY (source_org_id) REFERENCES organizations(id)
                  ON DELETE SET NULL NOT VALID;
              END IF;
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('unified_capabilities')
                  AND conname = 'fk_unified_capabilities_reference'
              ) THEN
                ALTER TABLE unified_capabilities
                  ADD CONSTRAINT fk_unified_capabilities_reference
                  FOREIGN KEY (reference_capability_id) REFERENCES unified_capabilities(id)
                  ON DELETE SET NULL NOT VALID;
              END IF;
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = to_regclass('unified_capabilities')
                  AND conname = 'fk_unified_capabilities_retired_into'
              ) THEN
                ALTER TABLE unified_capabilities
                  ADD CONSTRAINT fk_unified_capabilities_retired_into
                  FOREIGN KEY (retired_into_id) REFERENCES unified_capabilities(id)
                  ON DELETE SET NULL NOT VALID;
              END IF;
            END
            $foreign_keys$
            """
        )
    )
    for constraint_name in (
        "fk_unified_capabilities_organization",
        "fk_unified_capabilities_source_org",
        "fk_unified_capabilities_reference",
        "fk_unified_capabilities_retired_into",
    ):
        connection.execute(
            text(f"ALTER TABLE unified_capabilities VALIDATE CONSTRAINT {constraint_name}")
        )
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION enforce_unified_capability_write_scope()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $write_guard$
            DECLARE
              actor_org_text text := current_setting('archie.organization_id', true);
              actor_org integer;
            BEGIN
              IF TG_OP <> 'DELETE' AND (
                NEW.scope IS NULL
                OR NEW.scope NOT IN ('reference', 'tenant')
                OR (NEW.scope = 'reference' AND NEW.organization_id IS NOT NULL)
                OR (NEW.scope = 'tenant' AND NEW.organization_id IS NULL)
              ) THEN
                RAISE EXCEPTION 'capability scope and ownership are invalid';
              END IF;
              IF TG_OP <> 'DELETE' AND NEW.reference_capability_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM unified_capabilities AS reference
                WHERE reference.id = NEW.reference_capability_id
                  AND reference.scope = 'reference'
                  AND reference.organization_id IS NULL
              ) THEN
                RAISE EXCEPTION
                  'reference_capability_id must identify a reference capability';
              END IF;
              IF actor_org_text IS NOT NULL AND actor_org_text <> '' THEN
                actor_org := actor_org_text::integer;
                IF TG_OP = 'DELETE' THEN
                  IF OLD.scope = 'reference' OR OLD.organization_id IS NULL THEN
                    RAISE EXCEPTION 'reference capabilities are read-only for tenant sessions';
                  END IF;
                  IF OLD.organization_id IS DISTINCT FROM actor_org THEN
                    RAISE EXCEPTION 'capability delete crosses tenant boundary';
                  END IF;
                  RETURN OLD;
                END IF;
                IF TG_OP = 'UPDATE' AND (
                  OLD.scope = 'reference' OR OLD.organization_id IS NULL
                ) THEN
                  RAISE EXCEPTION 'reference capabilities are read-only for tenant sessions';
                END IF;
                IF TG_OP = 'UPDATE' AND OLD.organization_id IS DISTINCT FROM actor_org THEN
                  RAISE EXCEPTION 'capability update crosses tenant boundary';
                END IF;
                IF NEW.organization_id IS DISTINCT FROM actor_org THEN
                  RAISE EXCEPTION 'capability write crosses tenant boundary';
                END IF;
              END IF;
              RETURN NEW;
            END
            $write_guard$
            """
        )
    )
    connection.execute(
        text("DROP TRIGGER IF EXISTS trg_unified_capability_write_scope ON unified_capabilities")
    )
    connection.execute(
        text(
            "CREATE TRIGGER trg_unified_capability_write_scope "
            "BEFORE INSERT OR UPDATE OR DELETE ON unified_capabilities "
            "FOR EACH ROW EXECUTE FUNCTION enforce_unified_capability_write_scope()"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_capabilities_tenant_code "
            "ON unified_capabilities (organization_id, code) WHERE organization_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_capabilities_reference_archimate_id "
            "ON unified_capabilities (archimate_id) "
            "WHERE organization_id IS NULL AND archimate_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_capabilities_tenant_archimate_id "
            "ON unified_capabilities (organization_id, archimate_id) "
            "WHERE organization_id IS NOT NULL AND archimate_id IS NOT NULL"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE unified_capabilities "
            "DROP CONSTRAINT IF EXISTS ck_unified_capabilities_scope_owner, "
            "ADD CONSTRAINT ck_unified_capabilities_scope_owner CHECK ("
            "scope IS NOT NULL AND ("
            "(scope = 'reference' AND organization_id IS NULL) OR "
            "(scope = 'tenant' AND organization_id IS NOT NULL)))"
        )
    )


def run_cutover(
    connection,
    *,
    apply: bool,
    backup_manifest: str | Path | None = None,
) -> dict[str, object]:
    """Classify and optionally apply the cutover on ``connection``'s schema."""

    manifest = _load_backup_manifest(backup_manifest) if apply else None
    locked = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID}
    ).scalar_one()
    if not locked:
        raise CutoverBlocked("another capability tenancy cutover holds the advisory lock")

    foreign_keys = _foreign_keys(connection)
    if apply:
        _lock_cutover_tables(connection, foreign_keys)
    before = _snapshot(connection)

    # A row an authoritative writer already classified at write time --
    # `project-capabilities` and `backfill-capability-catalogs` both set
    # `scope`/`organization_id` directly on INSERT, and so does the ordinary
    # tenant-scoped ORM create path (`_protect_reference_capability_writes`)
    # -- needs no reclassification here. Re-deriving one from relationships
    # and provenance alone is a *weaker* signal than the authoritative write
    # itself, and for a reference-scope row with no relationship owners (every
    # row `backfill-capability-catalogs` creates via its one positive
    # catalogue marker) it used to fall all the way through to 'ambiguous'
    # even though nothing about the row was ever actually ambiguous --
    # blocking the whole cutover on rows that were already correctly done.
    #
    # Trusting an already-set scope is only safe if it is self-consistent, so
    # that is checked explicitly rather than assumed: a row with
    # scope='reference' but a real organization_id (or the reverse) would
    # otherwise sail through unexamined and could surface as a tenant row
    # visible to every organisation, or a reference row no one can reach.
    malformed = connection.execute(
        text(
            "SELECT id, scope, organization_id FROM unified_capabilities "
            "WHERE retired_into_id IS NULL AND scope IS NOT NULL "
            "AND NOT ("
            "(scope = 'reference' AND organization_id IS NULL) OR "
            "(scope = 'tenant' AND organization_id IS NOT NULL)) "
            "ORDER BY id"
        )
    ).mappings().all()
    if apply and malformed:
        example = malformed[0]
        raise CutoverBlocked(
            f"{len(malformed)} capabilities already carry an inconsistent "
            f"scope/organization_id pairing (e.g. id={example['id']} "
            f"scope={example['scope']!r} organization_id={example['organization_id']!r}); "
            "fix the writer that produced them before the cutover can proceed"
        )

    capability_ids = [
        int(row[0])
        for row in connection.execute(
            text(
                "SELECT id FROM unified_capabilities "
                "WHERE retired_into_id IS NULL AND scope IS NULL ORDER BY id"
            )
        )
    ]
    already_classified = connection.execute(
        text(
            "SELECT count(*) FROM unified_capabilities "
            "WHERE retired_into_id IS NULL AND scope IS NOT NULL "
            "AND ((scope = 'reference' AND organization_id IS NULL) OR "
            "(scope = 'tenant' AND organization_id IS NOT NULL))"
        )
    ).scalar_one()
    classifications = [classify_capability(connection, capability_id) for capability_id in capability_ids]
    counts = {
        "classified": len(classifications),
        "reference": sum(item.scope == "reference" for item in classifications),
        "tenant": sum(item.scope == "tenant" for item in classifications),
        "ambiguous": sum(item.scope == "ambiguous" for item in classifications),
    }
    foreign_key_before = _foreign_key_counts(connection, foreign_keys)
    report: dict[str, object] = {
        "mode": "apply" if apply else "dry-run",
        "writes": 0,
        "counts": counts,
        # Rows an authoritative writer already scoped correctly, left alone
        # rather than reclassified from weaker relationship/provenance
        # evidence -- not counted in `counts["classified"]`, which is only
        # the rows this run actually had to classify.
        "already_classified": int(already_classified),
        "malformed_existing_scope": len(malformed),
        "before": before,
        "after": before.copy(),
        "classifications": [
            {
                "capability_id": item.capability_id,
                "scope": item.scope,
                "organization_id": item.organization_id,
                "evidence": list(item.evidence),
            }
            for item in classifications
        ],
        "duplicates": [],
        "foreign_keys": {
            "before": foreign_key_before,
            "after": foreign_key_before.copy(),
            "orphans": _foreign_key_orphans(connection, foreign_keys),
        },
        "backup_manifest": str(backup_manifest) if backup_manifest else None,
        "backup_path": manifest["backup_path"] if manifest else None,
        "constraint_swap": False,
    }
    if not apply:
        return report
    if counts["ambiguous"]:
        raise CutoverBlocked(
            f"{counts['ambiguous']} capabilities have ambiguous active links; "
            "constraint swap was not started"
        )

    writes = 0
    for item in classifications:
        existing = connection.execute(
            text(
                "SELECT scope, organization_id FROM unified_capabilities "
                "WHERE id = :capability_id FOR UPDATE"
            ),
            {"capability_id": item.capability_id},
        ).one()
        if tuple(existing) == (item.scope, item.organization_id):
            continue
        if existing.scope is not None:
            raise CutoverBlocked(
                f"capability {item.capability_id} has conflicting completed classification"
            )
        result = connection.execute(
            text(
                "UPDATE unified_capabilities SET scope = :scope, "
                "organization_id = :organization_id "
                "WHERE id = :capability_id "
                "AND organization_id IS NOT DISTINCT FROM :prior_organization_id "
                "AND scope IS NOT DISTINCT FROM :prior_scope"
            ),
            {
                "scope": item.scope,
                "organization_id": item.organization_id,
                "capability_id": item.capability_id,
                "prior_organization_id": existing.organization_id,
                "prior_scope": existing.scope,
            },
        )
        if result.rowcount != 1:
            raise CutoverBlocked(f"capability {item.capability_id} changed during classification")
        writes += result.rowcount

    duplicates = _duplicate_plan(connection)
    for duplicate in duplicates:
        writes += _resolve_duplicate(connection, duplicate, foreign_keys)
    install_cutover_constraints(connection)

    foreign_key_after = _foreign_key_counts(connection, foreign_keys)
    if foreign_key_before != foreign_key_after:
        raise CutoverBlocked("foreign-key row counts changed during capability cutover")
    orphans = _foreign_key_orphans(connection, foreign_keys)
    if any(orphans.values()):
        raise CutoverBlocked("foreign-key verification found orphan capability relationships")

    report["writes"] = writes
    report["duplicates"] = duplicates
    report["after"] = _snapshot(connection)
    report["foreign_keys"] = {
        "before": foreign_key_before,
        "after": foreign_key_after,
        "orphans": orphans,
    }
    report["constraint_swap"] = True
    return report


_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF_SECONDS = 0.05


def _replace_with_retry(temporary: Path, destination: Path) -> None:
    """os.replace, retried briefly because Windows lets a third party veto it.

    The replace itself is atomic on every platform we run on. What is not
    guaranteed on Windows is that it can *start*: a real-time virus scanner or
    a search indexer opens a newly written file to inspect it, and while it
    holds that handle the rename onto it fails with WinError 5 (access
    denied). It is transient and it is not our bug, but it lands on the report
    write, which is the one step whose whole purpose is to leave durable
    evidence that the cutover happened.

    Measured on Windows 11 with Defender active: writing this exact report /
    marker / report sequence, roughly a third of the replaces failed, in no
    stable pattern -- some rounds clean, some failing twice. On Linux the call
    succeeds first time and this loop costs one comparison, because POSIX
    permits renaming over an open file.

    Deliberately NOT swallowing the error: after the last attempt it raises,
    so a genuine permission problem still fails loudly instead of being
    retried into silence.
    """

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, destination)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def _write_report_atomic(report_path: str | Path, payload: dict[str, object]) -> None:
    """Durably replace the report without ever exposing partial JSON."""

    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _audit_payload(
    payload: dict[str, object],
    *,
    state: Literal["pending_commit", "committed_report_pending", "completed"],
    database_commit_confirmed: bool,
    **details: object,
) -> dict[str, object]:
    audited = dict(payload)
    audited.update(
        {
            "audit_state": state,
            "database_commit_confirmed": database_commit_confirmed,
            **details,
        }
    )
    return audited


def _committed_report_pending_path(report_path: str | Path) -> Path:
    return Path(f"{report_path}.committed-report-pending.json")


def execute_cutover_with_report(
    connection,
    *,
    report_path: str | Path,
    apply: bool,
    backup_manifest: str | Path | None = None,
) -> dict[str, object]:
    """Run inside an open transaction and persist explicit pending evidence."""

    payload = run_cutover(
        connection,
        apply=apply,
        backup_manifest=backup_manifest,
    )
    _write_report_atomic(
        report_path,
        _audit_payload(
            payload,
            state="pending_commit",
            database_commit_confirmed=False,
        ),
    )
    return payload


def execute_cutover_with_audit(
    engine,
    *,
    report_path: str | Path,
    apply: bool,
    backup_manifest: str | Path | None = None,
) -> dict[str, object]:
    """Commit the cutover, then publish only evidence whose state is known.

    The requested report path first contains a durable ``pending_commit``
    artifact.  A completed report is never written until ``commit()`` returns.
    After commit, a separate recovery marker is made durable before completed
    publication is attempted, so a later filesystem failure has an explicit
    committed-but-report-pending result.
    """

    connection = engine.connect()
    transaction = connection.begin()
    try:
        payload = execute_cutover_with_report(
            connection,
            report_path=report_path,
            apply=apply,
            backup_manifest=backup_manifest,
        )
        transaction.commit()
    except BaseException:
        if transaction.is_active:
            transaction.rollback()
        raise
    finally:
        connection.close()

    destination = Path(report_path)
    marker_path = _committed_report_pending_path(destination)
    marker = _audit_payload(
        payload,
        state="committed_report_pending",
        database_commit_confirmed=True,
        report_path=str(destination),
        superseded_when_completed_report_is_present=True,
    )
    try:
        _write_report_atomic(marker_path, marker)
    except BaseException as marker_error:
        # The database is already committed.  Preserve the strongest possible
        # state at the original durable path without implying rollback.
        try:
            _write_report_atomic(destination, marker)
        except BaseException:
            pass
        raise CutoverReportPending(
            "database committed; committed report marker publication pending",
            marker_path,
        ) from marker_error

    completed = _audit_payload(
        payload,
        state="completed",
        database_commit_confirmed=True,
        report_path=str(destination),
    )
    try:
        _write_report_atomic(destination, completed)
    except BaseException as publication_error:
        marker["publication_error"] = (
            f"{type(publication_error).__name__}: {publication_error}"
        )
        _write_report_atomic(marker_path, marker)
        raise CutoverReportPending(
            f"database committed; report publication pending at {marker_path}",
            marker_path,
        ) from publication_error

    try:
        marker_path.unlink(missing_ok=True)
    except OSError:
        # The completed report is authoritative; the marker declares that a
        # completed report supersedes it if cleanup is interrupted.
        pass
    return completed


@click.command("cutover-capability-tenancy")
@click.option("--dry-run", is_flag=True, help="Measure and classify without writing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Apply the classified cutover.")
@click.option(
    "--report",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="JSON report destination.",
)
@click.option(
    "--backup-manifest",
    type=click.Path(path_type=Path, dir_okay=False),
    help="JSON manifest containing the recorded backup_path (required for --apply).",
)
@with_appcontext
def cutover_capability_tenancy(dry_run, apply_changes, report, backup_manifest):
    """Classify and cut over UnifiedCapability hybrid tenancy."""

    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")
    try:
        payload = execute_cutover_with_audit(
            db.engine,
            report_path=report,
            apply=apply_changes,
            backup_manifest=backup_manifest,
        )
    except CutoverBlocked as exc:
        raise click.ClickException(str(exc)) from exc
    except CutoverReportPending as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"{payload['mode']}: {payload['counts']['classified']} classified, "
        f"{payload['counts']['ambiguous']} ambiguous, {payload['writes']} writes"
    )
    click.echo(f"report: {report}")


def init_app(app):
    app.cli.add_command(cutover_capability_tenancy)
