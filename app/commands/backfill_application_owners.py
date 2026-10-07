"""
flask backfill-application-owners — migrate legacy ownership into application_owners.

Migrates legacy ownership data into the application_owners table one
organisation at a time:

1. Rows from the application_ownership table (enterprise_intelligence.py).
2. Text business_owner / technical_owner columns on ApplicationComponent.

A text owner that matches no user in that organisation is recorded in a
per-organisation unresolved list and is never guessed.

Safe and idempotent:
  - Only creates rows that do not already exist.
  - Re-running is a no-op once everything is migrated.
  - Provenance (source_table, source_id) is recorded on each created row.
  - If the same (application_id, user_id, ownership_type) would be created
    by two sources (e.g. a legacy row and a text column), the second is
    recorded as a merge (retired_into_id on the legacy table) but no row
    is inserted.

Usage:
    flask --app manage backfill-application-owners
    flask --app manage backfill-application-owners --dry-run
    flask --app manage backfill-application-owners --org-ids 1,2
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

import click
from flask.cli import with_appcontext

from app import db

logger = logging.getLogger(__name__)


def _application_in_org(app_id: int, org_id: int):
    """Return the application only when it belongs to *org_id*."""
    from app.models.application_portfolio import ApplicationComponent

    return db.session.execute(
        db.select(ApplicationComponent)
        .where(ApplicationComponent.id == app_id)
        .where(ApplicationComponent.organization_id == org_id)
    ).scalar_one_or_none()


def _resolve_user_by_name(name: str, org_id: int) -> Optional[object]:
    """Find a user in *org_id* whose display name or email matches *name*.

    Tries, in order:
      1. Exact case-insensitive match on ``first_name || ' ' || last_name``
      2. Exact case-insensitive match on ``email``

    Returns ``None`` when no single user matches.  Does NOT match on
    first-name-only or last-name-only — the brief says "never guessed".
    """
    from app.models.user import User

    name_lower = name.strip().lower()

    # Full-name match
    users = (
        User.query.filter(User.organization_id == org_id)
        .filter(db.func.lower(db.func.concat(User.first_name, " ", User.last_name)) == name_lower)
        .all()
    )
    if len(users) == 1:
        return users[0]

    # Email match
    users = (
        User.query.filter(User.organization_id == org_id)
        .filter(db.func.lower(User.email) == name_lower)
        .all()
    )
    if len(users) == 1:
        return users[0]

    return None


def _record_unresolved(org_id: int, app_name: str, field: str, name: str, unresolved: List[Dict]) -> None:
    """Append an unresolved entry to the list."""
    unresolved.append({
        "organization_id": org_id,
        "application_name": app_name,
        "field": field,
        "name": name,
    })


def _owner_key_in_run(seen: set, app_id: int, user_id: int, ownership_type: str) -> bool:
    """Check whether (*app_id*, *user_id*, *ownership_type*) is already in *seen*."""
    key = (app_id, user_id, ownership_type)
    if key in seen:
        return True
    seen.add(key)
    return False


def _canonical_owner_id(existing_by_provenance, existing_by_type) -> Optional[int]:
    """Return the real ApplicationOwner id a legacy row should retire into."""
    if existing_by_provenance is not None:
        return existing_by_provenance.id
    if existing_by_type is not None and hasattr(existing_by_type, "id"):
        return existing_by_type.id
    return None


def backfill_owner_data(dry_run: bool = False, organization_ids: Optional[List[int]] = None) -> Dict:
    """Run the full backfill, returning stats per organisation.

    When *organization_ids* is given, only those organisations are processed.
    Each organisation is committed separately and then the session is expired
    so the next organisation starts from a clean read -- the call stack does
    not set a tenant context because every query carries an explicit
    ``organization_id`` predicate.
    """
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationOwnership

    if organization_ids:
        org_ids = sorted(organization_ids)
    else:
        org_ids = [
            row[0]
            for row in db.session.query(ApplicationComponent.organization_id).distinct().all()
            if row[0] is not None
        ]

    total_legacy_ownership = 0
    total_text_owners = 0
    total_skipped = 0
    total_merged = 0
    all_unresolved = {}

    for org_id in sorted(org_ids):
        click.echo(f"\n  Organisation {org_id}:")
        unresolved: List[Dict] = []
        # Track (application_id, user_id, ownership_type) keys added in THIS run
        seen_keys: set = set()

        # ── 1. Migrate application_ownership rows ───────────────────
        legacy_rows = (
            db.session.query(ApplicationOwnership)
            .filter(ApplicationOwnership.organization_id == org_id)
            .all()
        )
        for lo in legacy_rows:
            app_obj_lo = _application_in_org(lo.application_id, org_id)
            if app_obj_lo is None:
                _record_unresolved(
                    org_id,
                    f"App #{lo.application_id}",
                    "application_ownership (application outside organisation)",
                    lo.primary_contact or "(no contact)",
                    unresolved,
                )
                continue

            # Map legacy ownership_type to the new vocabulary
            legacy_type = (lo.ownership_type or "").lower()
            type_map = {
                "business owner": "business",
                "product owner": "business",
                "technical owner": "technical",
                "budget holder": "business",
            }
            new_type = type_map.get(legacy_type)
            if new_type is None:
                # Unknown legacy type: go to unresolved list, never guessed
                _record_unresolved(
                    org_id,
                    app_obj_lo.name,
                    "application_ownership (unknown type)",
                    lo.primary_contact or "(no name)",
                    unresolved,
                )
                continue

            # Check for existing row by provenance (source_table, source_id)
            existing_by_provenance = ApplicationOwner.query.filter(
                ApplicationOwner.application_id == lo.application_id,
                ApplicationOwner.source_table == "application_ownership",
                ApplicationOwner.source_id == lo.id,
                ApplicationOwner.organization_id == org_id,
            ).first()
            if existing_by_provenance:
                total_skipped += 1
                continue

            # Try to find a user from the contact info
            contact_name = lo.primary_contact or ""
            contact_email = getattr(lo, "contact_email", None) or ""
            user = None

            if contact_name:
                user = _resolve_user_by_name(contact_name, org_id)
            if user is None and contact_email:
                # Try e-mail match when there is no contact name
                user = _resolve_user_by_name(contact_email, org_id)

            if user is None:
                # No contact name either: record as "(no contact)"
                display_name = contact_name if contact_name else "(no contact)"
                _record_unresolved(org_id, app_obj_lo.name, "application_ownership", display_name, unresolved)
                continue

            # Check for duplicate (application_id, user_id, ownership_type) from any source
            if existing_by_provenance is None:
                existing_by_type = ApplicationOwner.query.filter(
                    ApplicationOwner.application_id == lo.application_id,
                    ApplicationOwner.user_id == user.id,
                    ApplicationOwner.ownership_type == new_type,
                    ApplicationOwner.organization_id == org_id,
                ).first()
                # Also check keys added earlier in this run
                if existing_by_type is None and _owner_key_in_run(seen_keys, lo.application_id, user.id, new_type):
                    existing_by_type = True  # treat as existing-to-be-merged

            if existing_by_provenance is not None or existing_by_type is not None:
                # Merge: mark the legacy row as retired but don't insert
                canonical_owner_id = _canonical_owner_id(existing_by_provenance, existing_by_type)
                if not dry_run and lo.retired_into_id != canonical_owner_id and canonical_owner_id is not None:
                    lo.retired_into_id = canonical_owner_id
                    total_merged += 1
                total_skipped += 1
                continue

            if not dry_run:
                owner = ApplicationOwner(
                    application_id=lo.application_id,
                    user_id=user.id,
                    organization_id=org_id,
                    ownership_type=new_type,
                    source_table="application_ownership",
                    source_id=lo.id,
                )
                db.session.add(owner)
                db.session.flush()  # get id for seen_keys
                _owner_key_in_run(seen_keys, lo.application_id, user.id, new_type)
                total_legacy_ownership += 1

        # ── 2. Mark migrated application_ownership rows as retired ──
        if not dry_run and legacy_rows:
            for lo in legacy_rows:
                if lo.retired_into_id:
                    continue
                migrated = ApplicationOwner.query.filter(
                    ApplicationOwner.organization_id == org_id,
                    ApplicationOwner.source_table == "application_ownership",
                    ApplicationOwner.source_id == lo.id,
                ).first()
                if migrated:
                    lo.retired_into_id = migrated.id
                    total_merged += 1

        # ── 3. Migrate text owner columns ───────────────────────────
        apps = (
            db.session.query(ApplicationComponent)
            .filter(ApplicationComponent.organization_id == org_id)
            .all()
        )
        for app_obj in apps:
            for field_name, new_type in [("business_owner", "business"), ("technical_owner", "technical")]:
                name = getattr(app_obj, field_name, None)
                if not name or not name.strip():
                    continue

                # Check for existing ApplicationOwner row of this type with provenance
                existing = (
                    db.session.query(ApplicationOwner)
                    .filter(
                        ApplicationOwner.application_id == app_obj.id,
                        ApplicationOwner.ownership_type == new_type,
                        ApplicationOwner.organization_id == org_id,
                        ApplicationOwner.source_table == field_name,
                    )
                    .first()
                )
                if existing:
                    total_skipped += 1
                    continue

                user = _resolve_user_by_name(name, org_id)
                if user is None:
                    _record_unresolved(org_id, app_obj.name, field_name, name.strip(), unresolved)
                    continue

                # Check for existing (application_id, user_id, ownership_type) -
                # may have been created earlier in this run
                existing_by_type = ApplicationOwner.query.filter(
                    ApplicationOwner.application_id == app_obj.id,
                    ApplicationOwner.user_id == user.id,
                    ApplicationOwner.ownership_type == new_type,
                    ApplicationOwner.organization_id == org_id,
                ).first()
                if existing_by_type is not None or _owner_key_in_run(seen_keys, app_obj.id, user.id, new_type):
                    total_skipped += 1
                    continue

                if not dry_run:
                    owner = ApplicationOwner(
                        application_id=app_obj.id,
                        user_id=user.id,
                        organization_id=org_id,
                        ownership_type=new_type,
                        source_table=field_name,
                        source_id=app_obj.id,
                    )
                    db.session.add(owner)
                    db.session.flush()
                    _owner_key_in_run(seen_keys, app_obj.id, user.id, new_type)
                    total_text_owners += 1

        if unresolved:
            all_unresolved[str(org_id)] = unresolved
            click.echo(f"    unresolved: {len(unresolved)} text owner(s) matched no user")
            for u in unresolved:
                click.echo(f"      {u['application_name']}: {u['field']} = \"{u['name']}\"")

        if not dry_run:
            db.session.commit()
            # Clear the session per organisation so tenant context is clean
            # for the next organisation, without detaching admin-scope objects.
            db.session.expire_all()

    # Print per-organisation unresolved list instead of writing to a hard-coded path
    if all_unresolved:
        click.echo("\n  Unresolved owners per organisation:")
        click.echo(json.dumps(all_unresolved, indent=2))

    return {
        "legacy_ownership_rows": total_legacy_ownership,
        "text_owner_fields": total_text_owners,
        "skipped_existing": total_skipped,
        "organisations_processed": len(org_ids),
        "unresolved_orgs": len(all_unresolved),
        "merged_legacy_rows": total_merged,
    }


@click.command("backfill-application-owners")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
@click.option("--org-ids", default=None, help="Comma-separated organisation IDs to process.")
@with_appcontext
def backfill_application_owners_command(dry_run, org_ids):
    """Migrate legacy ownership data into application_owners."""
    click.echo("backfill-application-owners:" + (" (dry-run)" if dry_run else ""))
    parsed_ids = [int(x.strip()) for x in org_ids.split(",")] if org_ids else None
    stats = backfill_owner_data(dry_run=dry_run, organization_ids=parsed_ids)
    click.echo("\n  Results:")
    for key, value in stats.items():
        click.echo(f"    {key}: {value}")
    click.echo("Done.")


def init_app(app):
    """Register the backfill-application-owners CLI command."""
    app.cli.add_command(backfill_application_owners_command)
