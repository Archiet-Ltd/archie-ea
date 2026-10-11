"""Backfill the four remaining superseded capability stores into unified_capabilities.

ADR 0008 names `unified_capabilities` the one system of record for capabilities.
`flask project-capabilities` already projects the first and largest superseded
store, `business_capability`. This command retires the other four the same
ADR names for retirement (`docs/adr/0008-one-system-of-record.md`):
`capabilities` (the `Capability` model), `enterprise_capabilities`,
`archimate_capabilities` and `technical_capabilities`.

Design, in order of preference per row:

1. **A row that already stands for a projected `business_capability`.**
   `enterprise_capabilities` and `archimate_capabilities` both carry a
   `business_capability_id` column documented as "link to the single source of
   truth" (see the docstrings in app/models/capabilities.py). When it is set
   and the referenced `business_capability` has already been projected (it
   runs first in scripts/database/deploy-schema.sh), this row is not a new
   capability at all -- it is retired straight into that projection's own
   `unified_capabilities` row. No new row is ever created for it.
2. **A row identified by a real identifier.** `capabilities.archimate_id`,
   `archimate_capabilities.archimate_id` and `technical_capabilities.code`
   are each unique on their own table already. If an existing
   `unified_capabilities` row in the same scope (the same organisation for a
   tenant-owned row, the shared reference set otherwise) already carries that
   identifier, this row is retired into it (keep-oldest: whichever row was
   already there wins). Otherwise this row becomes the new canonical
   `unified_capabilities` row for that identifier.
3. **A row with no identifier and no resolvable link.** Matched by normalised
   name within the same scope, same keep-oldest rule. Otherwise it becomes
   canonical.

Organisation ownership, never guessed (settled design rule 3) -- fail closed:

- `capabilities` (`Capability`) carries `TenantMixin` -- every row already has
  a real, NOT NULL `organization_id`. Projected as `scope='tenant'`.
- `enterprise_capabilities` / `archimate_capabilities` have no organisation
  column at all. A row linked to a projected `business_capability` inherits
  that capability's ownership by construction (step 1 above -- it is retired
  into that exact row, tenant or reference). A row with **no** such link is
  quarantined in `ErrorEvent` (reusing the platform-wide, admin-visible
  surface `backfill_review_queue_approvals.py` already established for
  exactly this job, at `/admin/errors`) -- never guessed into
  `scope='reference'`. Neither model carries any field distinguishing a
  genuinely shared framework definition from a row that happens to describe
  one tenant's confidential capability (its docstring calling the table a
  "legacy ... layer" is not evidence about any individual row), so there is
  no marker here to trust; per the lead's ruling, the absence of one means
  quarantine, not a shared-by-default fallback.
- `technical_capabilities` has no organisation column and no per-row link to
  a business capability at all (only many-to-many mapping tables). It has one
  real, pre-existing marker: `acm_domain`, constrained by the model's own
  `ACMDomain.ALL_DOMAINS` (app/models/technical_capability.py) to one of
  exactly seven fixed values describing the ACM taxonomy framework itself,
  not any tenant's data -- a known catalogue allow-list that already existed
  in the code for an unrelated purpose before this command. A row whose
  `acm_domain` is one of those seven values is `scope='reference'`; any other
  value (corruption, or a future domain the allow-list has not caught up
  with) is quarantined rather than assumed safe.

Idempotent: only rows with `retired_into_id IS NULL` on their own legacy table
are read. A merged or newly-canonical row gets that column set and is never
revisited. A quarantined row's `retired_into_id` stays NULL on purpose -- it
is still unresolved -- so it is re-evaluated (and its `ErrorEvent` row
deduplicated by fingerprint, occurrence count incremented) on every run until
either the link it needed resolves or an operator attributes it an
organisation by hand. The command exits non-zero while any row remains
neither merged nor quarantined (this is a stricter bar than it sounds: a
genuinely quarantined row IS accounted for, but still keeps the exit non-zero
on purpose, matching `backfill_review_queue_approvals.py`'s established
precedent, so the command cannot be mistaken for "fully resolved" while a
human still needs to look at /admin/errors).

    flask backfill-capability-catalogs --dry-run
    flask backfill-capability-catalogs --apply
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db
from app.utils.duplicate_guard import normalize_name

# Neighbours of project-capabilities' 1_684_220_027 and the cutover's
# 1_684_220_026 (app/commands/cutover_capability_tenancy.py) -- serialises
# this command against itself and refuses to interleave with either, since
# all three read and write unified_capabilities' scope/ownership columns.
ADVISORY_LOCK_ID = 1_684_220_028


class BackfillBlocked(RuntimeError):
    """Raised before this backfill can proceed safely (matches ProjectionBlocked
    and CutoverBlocked's role in the sibling commands)."""


@dataclass(frozen=True)
class _SourceRow:
    table: str
    id: int
    name: str
    description: Optional[str]
    level: int
    category: Optional[str]
    identifier: Optional[str]
    identifier_column: str  # "archimate_id" or "code" -- which unified column it matches
    business_capability_id: Optional[int]
    organization_id: Optional[int]  # only ever set for `capabilities`
    created_at: Optional[datetime]
    current_maturity_level: Optional[int] = None
    target_maturity_level: Optional[int] = None
    status: Optional[str] = None
    code_prefix: str = field(default="CAP")
    # True only when a real, pre-existing, code-defined marker positively
    # identifies this row as shared catalogue data (currently: a
    # technical_capabilities row whose acm_domain is one of the seven values
    # ACMDomain.ALL_DOMAINS already declares). Never defaulted to True by the
    # absence of an organisation column -- see the module docstring.
    is_known_catalogue: bool = False


def _fetch_capabilities(connection) -> list[_SourceRow]:
    rows = connection.execute(
        text(
            "SELECT id, name, description, level, archimate_id, "
            "organization_id, created_at "
            "FROM capabilities WHERE retired_into_id IS NULL ORDER BY created_at NULLS LAST, id"
        )
    ).mappings().all()
    return [
        _SourceRow(
            table="capabilities",
            id=row["id"],
            name=row["name"],
            description=row["description"],
            level=min(max(row["level"] or 1, 1), 3),
            category=None,
            identifier=row["archimate_id"],
            identifier_column="archimate_id",
            business_capability_id=None,
            organization_id=row["organization_id"],
            created_at=row["created_at"],
            code_prefix="CAP",
        )
        for row in rows
    ]


def _fetch_enterprise_capabilities(connection) -> list[_SourceRow]:
    rows = connection.execute(
        text(
            "SELECT id, name, description, category, maturity_level, status, "
            "business_capability_id, created_at "
            "FROM enterprise_capabilities WHERE retired_into_id IS NULL "
            "ORDER BY created_at NULLS LAST, id"
        )
    ).mappings().all()
    return [
        _SourceRow(
            table="enterprise_capabilities",
            id=row["id"],
            name=row["name"],
            description=row["description"],
            level=1,
            category=row["category"],
            identifier=None,
            identifier_column="archimate_id",
            business_capability_id=row["business_capability_id"],
            organization_id=None,
            created_at=row["created_at"],
            current_maturity_level=row["maturity_level"],
            status=row["status"],
            code_prefix="ENT",
        )
        for row in rows
    ]


def _fetch_archimate_capabilities(connection) -> list[_SourceRow]:
    rows = connection.execute(
        text(
            "SELECT id, name, description, archimate_id, current_maturity, "
            "target_maturity, business_capability_id "
            "FROM archimate_capabilities WHERE retired_into_id IS NULL ORDER BY id"
        )
    ).mappings().all()
    return [
        _SourceRow(
            table="archimate_capabilities",
            id=row["id"],
            name=row["name"],
            description=row["description"],
            level=1,
            category=None,
            identifier=row["archimate_id"],
            identifier_column="archimate_id",
            business_capability_id=row["business_capability_id"],
            organization_id=None,
            # This table has no created_at/updated_at column at all (verified
            # against app/models/capabilities.py); id order is the only
            # available "oldest first" proxy.
            created_at=None,
            current_maturity_level=row["current_maturity"],
            target_maturity_level=row["target_maturity"],
            code_prefix="ARC",
        )
        for row in rows
    ]


def _fetch_technical_capabilities(connection) -> list[_SourceRow]:
    from app.models.technical_capability import ACMDomain

    rows = connection.execute(
        text(
            "SELECT id, name, description, code, acm_domain, created_at "
            "FROM technical_capabilities WHERE retired_into_id IS NULL "
            "ORDER BY created_at NULLS LAST, id"
        )
    ).mappings().all()
    return [
        _SourceRow(
            table="technical_capabilities",
            id=row["id"],
            name=row["name"],
            description=row["description"],
            level=1,
            category=None,
            identifier=row["code"],
            identifier_column="code",
            business_capability_id=None,
            organization_id=None,
            created_at=row["created_at"],
            code_prefix="TECH",
            # The one real marker on this table: a fixed, pre-existing
            # allow-list of ACM domain codes declared for an unrelated
            # purpose (the ACM taxonomy itself), not invented to justify a
            # reference fallback.
            is_known_catalogue=row["acm_domain"] in ACMDomain.ALL_DOMAINS,
        )
        for row in rows
    ]


# Fixed processing order across tables: `capabilities` mirrors
# `business_capability` most directly (its own docstring calls it "the
# canonical ArchiMate strategy capability registry entry" and it is the only
# other tenant-owned store of the four), so it is treated as the
# next-most-authoritative after business_capability, which is always already
# projected. The remaining three are catalogue/framework tables with no
# organisation of their own; their relative order does not change which
# organisation anything belongs to, only which of two same-named *reference*
# rows would survive, which none of the production counts in ADR 0008 show
# ever colliding.
_FETCHERS = (
    _fetch_capabilities,
    _fetch_enterprise_capabilities,
    _fetch_archimate_capabilities,
    _fetch_technical_capabilities,
)

_RETIRE_SQL = {
    "capabilities": "UPDATE capabilities SET retired_into_id = :target WHERE id = :id",
    "enterprise_capabilities": (
        "UPDATE enterprise_capabilities SET retired_into_id = :target WHERE id = :id"
    ),
    "archimate_capabilities": (
        "UPDATE archimate_capabilities SET retired_into_id = :target WHERE id = :id"
    ),
    "technical_capabilities": (
        "UPDATE technical_capabilities SET retired_into_id = :target WHERE id = :id"
    ),
}


def _find_business_capability_projection(connection, business_capability_id: int):
    return connection.execute(
        text(
            "SELECT id FROM unified_capabilities "
            "WHERE source_table = 'business_capability' AND source_id = :source_id"
        ),
        {"source_id": str(business_capability_id)},
    ).scalar_one_or_none()


def _find_by_identifier(connection, *, column: str, value: str, organization_id):
    # tenancy-ok: the organisation predicate is explicit and part of the
    # match itself, not a scoping omission -- a tenant row and a reference
    # row are never allowed to satisfy the same lookup.
    if organization_id is not None:
        query = text(
            f"SELECT id FROM unified_capabilities "  # nosec B608 -- column is one of two module literals, never request input
            f"WHERE organization_id = :organization_id AND {column} = :value"
        )
        params = {"organization_id": organization_id, "value": value}
    else:
        query = text(
            f"SELECT id FROM unified_capabilities "  # nosec B608 -- column is one of two module literals, never request input
            f"WHERE organization_id IS NULL AND scope = 'reference' AND {column} = :value"
        )
        params = {"value": value}
    return connection.execute(query, params).scalar_one_or_none()


def _find_by_name(connection, *, normalised_name: str, organization_id):
    if organization_id is not None:
        query = text(
            "SELECT id, name FROM unified_capabilities "
            "WHERE organization_id = :organization_id"
        )
        params = {"organization_id": organization_id}
    else:
        query = text(
            "SELECT id, name FROM unified_capabilities "
            "WHERE organization_id IS NULL AND scope = 'reference'"
        )
        params = {}
    for candidate_id, candidate_name in connection.execute(query, params).all():
        if normalize_name(candidate_name) == normalised_name:
            return candidate_id
    return None


def _insert_canonical(connection, row: _SourceRow, *, scope: str, organization_id) -> int:
    code = row.identifier if row.identifier_column == "code" and row.identifier else None
    archimate_id = row.identifier if row.identifier_column == "archimate_id" and row.identifier else None
    if code is None and archimate_id is None:
        code = f"{row.code_prefix}-{row.id}"
    new_id = connection.execute(
        text(
            "INSERT INTO unified_capabilities ("
            "name, description, code, level, scope, organization_id, "
            "source_table, source_id, source_org_id, source_checksum, "
            "specialization_type, category, current_maturity_level, "
            "target_maturity_level, status, discovery_source, archimate_id, "
            "created_at, updated_at"
            ") VALUES ("
            ":name, :description, :code, :level, :scope, :organization_id, "
            ":source_table, :source_id, :source_org_id, "
            # PostgreSQL's own md5(), not Python's hashlib: a drift fingerprint,
            # not a security control, computed the same way
            # project_capabilities.py's _CHECKSUM_SQL already does -- avoids a
            # second checksum convention and the weak-hash finding Python's
            # hashlib.md5 raises for exactly this non-security use. Each
            # argument is its own, separately-named, explicitly text-cast bind
            # parameter: concat_ws's VARIADIC "any" gives Postgres no type to
            # infer, and reusing :source_table/:source_id (bound elsewhere as
            # plain columns) left the driver unable to settle on one type for
            # the shared parameter.
            "md5(concat_ws('|', CAST(:chk_source_table AS text), "
            "CAST(:chk_source_id AS text), CAST(:chk_name AS text), "
            "COALESCE(CAST(:chk_description AS text), ''), CAST(:chk_level AS text), "
            "COALESCE(CAST(:chk_category AS text), ''), "
            "COALESCE(CAST(:chk_identifier AS text), ''))), "
            "'BUSINESS', :category, :current_maturity_level, "
            ":target_maturity_level, :status, :discovery_source, :archimate_id, "
            "COALESCE(:created_at, now()), now()"
            ") RETURNING id"
        ),
        {
            "name": row.name,
            "description": row.description,
            "code": code,
            "level": row.level,
            "scope": scope,
            "organization_id": organization_id,
            "source_table": row.table,
            "source_id": str(row.id),
            "source_org_id": organization_id,
            "chk_source_table": row.table,
            "chk_source_id": str(row.id),
            "chk_name": row.name,
            "chk_description": row.description,
            "chk_level": row.level,
            "chk_category": row.category,
            "chk_identifier": row.identifier,
            "category": row.category,
            "current_maturity_level": row.current_maturity_level,
            "target_maturity_level": row.target_maturity_level,
            "status": row.status or "defined",
            "discovery_source": f"projection:{row.table}",
            "archimate_id": archimate_id,
            "created_at": row.created_at,
        },
    ).scalar_one()
    return new_id


_QUARANTINE_MESSAGES = {
    "pending_business_capability": (
        "links business_capability #{business_capability_id}, which has not "
        "been projected into unified_capabilities yet. Run "
        "`flask project-capabilities --apply` first, then re-run this backfill."
    ),
    "no_ownership_evidence": (
        "has no business_capability_id and no marker identifying it as shared "
        "catalogue data, so its organisation cannot be determined. Give it a "
        "business_capability_id, or attribute an organisation by hand, then "
        "re-run this backfill. Fail-closed per settled design rule 3: a row "
        "is never guessed into shared reference scope just because its table "
        "has no organisation column."
    ),
}


def _record_quarantine(connection, row: _SourceRow, *, reason: str) -> None:
    """Persist one row this run could not attribute an organisation to.

    Reuses ``ErrorEvent`` (app/models/error_event.py), the same
    platform-wide, nullable-organisation, dedup-by-fingerprint surface
    ``backfill_review_queue_approvals.py`` already reuses for identical
    reasoning -- one canonical "a backfill needs a human" surface
    (/admin/errors and its digest email) instead of a second one for this
    consolidation.
    """
    from app.models.error_event import ErrorEvent

    fingerprint = f"backfill-capability-catalogs:{row.table}:{row.id}"[:64]
    now = datetime.utcnow()
    existing = ErrorEvent.query.filter_by(fingerprint=fingerprint, resolved=False).first()
    if existing:
        existing.occurrence_count = (existing.occurrence_count or 0) + 1
        existing.last_seen_at = now
        return
    detail = _QUARANTINE_MESSAGES[reason].format(
        business_capability_id=row.business_capability_id
    )
    db.session.add(ErrorEvent(
        fingerprint=fingerprint,
        source="server",
        level="WARNING",
        message=f"backfill-capability-catalogs: {row.table} #{row.id} {detail}",
        location=f"app.commands.backfill_capability_catalogs:{row.table}",
        organization_id=None,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        resolved=False,
    ))


def _resolve_quarantine(row: _SourceRow) -> None:
    from app.models.error_event import ErrorEvent

    fingerprint = f"backfill-capability-catalogs:{row.table}:{row.id}"[:64]
    existing = ErrorEvent.query.filter_by(fingerprint=fingerprint, resolved=False).first()
    if existing:
        existing.resolved = True
        existing.resolved_at = datetime.utcnow()


def _process_row(connection, row: _SourceRow) -> str:
    """Resolve one source row. Returns 'merged', 'canonical' or 'quarantined'.

    Fail closed (settled design rule 3, lead ruling): the only paths that
    reach `scope='tenant'`/`'reference'` below are a schema-guaranteed real
    organisation (`capabilities`), a resolved link to an already-owned
    capability, or a positively-identified catalogue marker. Every other row
    is quarantined -- never defaulted to reference just because its table has
    no organisation column.
    """

    if row.organization_id is not None:
        scope, organization_id = "tenant", row.organization_id
    elif row.business_capability_id is not None:
        target = _find_business_capability_projection(connection, row.business_capability_id)
        if target is None:
            _record_quarantine(connection, row, reason="pending_business_capability")
            return "quarantined"
        connection.execute(
            text(_RETIRE_SQL[row.table]), {"target": target, "id": row.id}
        )
        _resolve_quarantine(row)
        return "merged"
    elif row.is_known_catalogue:
        scope, organization_id = "reference", None
    else:
        _record_quarantine(connection, row, reason="no_ownership_evidence")
        return "quarantined"

    target = None
    if row.identifier:
        target = _find_by_identifier(
            connection, column=row.identifier_column, value=row.identifier,
            organization_id=organization_id,
        )
    if target is None:
        target = _find_by_name(
            connection, normalised_name=normalize_name(row.name),
            organization_id=organization_id,
        )

    if target is not None:
        connection.execute(
            text(_RETIRE_SQL[row.table]), {"target": target, "id": row.id}
        )
        return "merged"

    new_id = _insert_canonical(connection, row, scope=scope, organization_id=organization_id)
    connection.execute(
        text(_RETIRE_SQL[row.table]), {"target": new_id, "id": row.id}
    )
    return "canonical"


def _counts(connection) -> dict[str, int]:
    row = connection.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM capabilities) AS capabilities_total, "
            "(SELECT count(*) FROM capabilities WHERE retired_into_id IS NULL) AS capabilities_pending, "
            "(SELECT count(*) FROM enterprise_capabilities) AS enterprise_total, "
            "(SELECT count(*) FROM enterprise_capabilities WHERE retired_into_id IS NULL) AS enterprise_pending, "
            "(SELECT count(*) FROM archimate_capabilities) AS archimate_total, "
            "(SELECT count(*) FROM archimate_capabilities WHERE retired_into_id IS NULL) AS archimate_pending, "
            "(SELECT count(*) FROM technical_capabilities) AS technical_total, "
            "(SELECT count(*) FROM technical_capabilities WHERE retired_into_id IS NULL) AS technical_pending, "
            "(SELECT count(*) FROM unified_capabilities) AS unified_total"
        )
    ).mappings().one()
    return {key: int(value) for key, value in row.items()}


def run_backfill(connection, *, apply: bool) -> dict:
    """Measure, and optionally apply, the four-table capability backfill."""

    if not connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID}
    ).scalar_one():
        raise BackfillBlocked(
            "another capability catalog backfill holds the advisory lock"
        )

    before = _counts(connection)
    report: dict[str, object] = {
        "mode": "apply" if apply else "dry-run",
        "before": before,
        "after": dict(before),
        "merged": 0,
        "canonical": 0,
        "quarantined": 0,
        "pending_before": sum(
            before[f"{name}_pending"]
            for name in ("capabilities", "enterprise", "archimate", "technical")
        ),
    }
    if not apply:
        return report

    outcomes = {"merged": 0, "canonical": 0, "quarantined": 0}
    processed = 0
    for fetch in _FETCHERS:
        for row in fetch(connection):
            outcome = _process_row(connection, row)
            outcomes[outcome] += 1
            processed += 1

    report["merged"] = outcomes["merged"]
    report["canonical"] = outcomes["canonical"]
    report["quarantined"] = outcomes["quarantined"]
    report["after"] = _counts(connection)
    # Non-zero only while a row that was fetched this run is neither merged
    # nor quarantined -- a quarantined row IS accounted for (it is visible at
    # /admin/errors and retried next run) and must not, by itself, keep this
    # exit code non-zero forever. `_process_row` is exhaustive (every branch
    # returns one of the three outcomes or raises), so this is normally 0 and
    # only turns up a genuine gap in that exhaustiveness.
    report["unreconciled"] = processed - (
        outcomes["merged"] + outcomes["canonical"] + outcomes["quarantined"]
    )
    return report


@click.command("backfill-capability-catalogs")
@click.option("--dry-run", is_flag=True, help="Measure without writing.")
@click.option("--apply", "apply_changes", is_flag=True, help="Apply the backfill.")
@with_appcontext
def backfill_capability_catalogs(dry_run, apply_changes):
    """Retire capabilities/enterprise_capabilities/archimate_capabilities/technical_capabilities into unified_capabilities."""

    if dry_run == apply_changes:
        raise click.UsageError("choose exactly one of --dry-run or --apply")

    connection = db.session.connection()
    try:
        report = run_backfill(connection, apply=apply_changes)
    except BackfillBlocked as exc:
        raise click.ClickException(str(exc)) from exc
    if apply_changes:
        db.session.commit()

    click.echo(
        f"{report['mode']}: before={report['before']}, "
        f"pending_before={report['pending_before']}"
    )
    if apply_changes:
        click.echo(
            f"merged={report['merged']} canonical={report['canonical']} "
            f"quarantined={report['quarantined']}"
        )
        click.echo(f"after={report['after']}")
        if report["unreconciled"]:
            raise click.ClickException(
                f"{report['unreconciled']} row(s) remain neither merged nor "
                "quarantined after this run; investigate before relying on "
                "unified_capabilities for these stores."
            )


def init_app(app):
    app.cli.add_command(backfill_capability_catalogs)
