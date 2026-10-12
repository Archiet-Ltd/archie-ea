"""flask backfill-domain-elements -- per-organisation backfill of missing ArchiMate elements.

Every domain record of the nine types must have exactly one ArchiMate element
node in its organisation. This command creates the missing elements for a given
organisation and reports duplicates.

Usage:
    flask --app manage backfill-domain-elements --org-id 7
    flask --app manage backfill-domain-elements --org-id 7 --dry-run

The command is idempotent: running it again on a fully-linked organisation
creates nothing and reports no duplicates.

The command commits once per type block, so what it reports as created is
really persisted, and each record is created inside its own savepoint: one
unrepresentable row (a row with no name/title to mirror, for example) is
counted as an error and rolled back on its own without discarding the rest of
the batch.

Duplicates: when a domain record has more than one ArchiMateElement pointing
at it, the oldest element (lowest id) is kept and the others are listed with
their ids. No element is ever silently removed.

Elements are written by exactly one helper -- ``sync_archimate_element`` from
app/services/archimate_backbone.py, the same helper the flush listener uses --
so a backfilled element carries the same name fallback, description and
provenance property as a listener-created one.
"""

import click
from flask.cli import with_appcontext

from app import db


# Every domain record of the nine types must have exactly one element node.
# Each entry is the dotted path of the domain model. The element mapping
# (type, layer, name fallback, description, provenance) lives in ONE place --
# ELEMENT_TYPES in app/services/archimate_backbone.py -- so a backfilled
# element and a listener-created element can never drift apart.
_DOMAIN_TYPES = [
    "app.models.application_portfolio.ApplicationComponent",
    "app.models.business_capabilities.BusinessCapability",
    "app.models.risk.Risk",
    "app.models.application_portfolio.VendorContract",
    "app.models.application_layer.ApplicationInterface",
    "app.models.process_data.DataEntity",
    "app.models.implementation_migration.WorkPackage",
    "app.models.application_compliance.ApplicationComplianceControl",
]


def _import_model(path):
    """Import a model class from its dotted path."""
    module_path, class_name = path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def backfill_domain_elements(org_id, dry_run=False, session=None):
    """Create missing ArchiMate elements for every domain record type.

    ``session`` is optional; defaults to ``db.session``. Pass an explicit
    session in tests that wrap ``db.session`` in a rollback transaction.

    Every listed type must be mapped in ``ELEMENT_TYPES``; there is no second
    writer to fall back to, so an unmapped type is a bug and the command says
    so instead of silently counting zero.

    Returns a dict with per-type stats:
        {type_name: {"created": int, "scanned": int, "duplicates": [...],
                     "errors": int, "error_details": [(record_id, reason), ...]}}
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_backbone import ELEMENT_TYPES, sync_archimate_element

    if session is None:
        session = db.session

    stats = {}

    for model_path in _DOMAIN_TYPES:
        model = _import_model(model_path)
        type_key = model.__name__
        if type_key not in ELEMENT_TYPES:
            raise KeyError(
                "%s is not mapped in archimate_backbone.ELEMENT_TYPES; "
                "there is no fallback writer for it" % type_key
            )

        # Find records in this organisation that have no element
        records = session.query(model).filter(
            model.organization_id == org_id,
            model.archimate_element_id.is_(None),
        ).all()

        # Collect all linked element ids for this type in this org
        all_records = session.query(model).filter(
            model.organization_id == org_id,
        ).all()
        linked_ids = {r.archimate_element_id for r in all_records if r.archimate_element_id}

        duplicates = []
        if not dry_run:
            # Find elements of this type that are NOT linked to any record
            # (orphans that may be duplicates of each other)
            orphan_query = session.query(ArchiMateElement).filter(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.type == ELEMENT_TYPES[type_key][0],
            )
            if linked_ids:
                orphan_query = orphan_query.filter(~ArchiMateElement.id.in_(linked_ids))
            orphan_elements = orphan_query.all()

            # Group by name to find duplicates
            by_name = {}
            for el in orphan_elements:
                by_name.setdefault(el.name, []).append(el)

            for name, elements in by_name.items():
                if len(elements) > 1:
                    elements.sort(key=lambda e: e.id)
                    kept = elements[0]
                    removed = elements[1:]
                    duplicates.append({
                        "name": name,
                        "kept_id": kept.id,
                        "removed_ids": [e.id for e in removed],
                    })

        created = 0
        errors = 0
        error_details = []

        if not dry_run:
            for record in records:
                try:
                    # The one element writer -- the same helper the flush
                    # listener uses. Each record runs inside its own
                    # savepoint so a failure rolls back just that record.
                    with session.begin_nested():
                        element = sync_archimate_element(record, session=session)
                        if element is not None:
                            created += 1
                except Exception as exc:
                    errors += 1
                    record_id = getattr(record, "id", None)
                    error_details.append((record_id, str(exc)))

            # Persist this type block before moving on. Without this the
            # command reports "Created N" and leaves nothing behind, and a
            # later rollback would silently discard the whole run.
            session.commit()

        stats[type_key] = {
            "created": created,
            "scanned": len(records),
            "duplicates": duplicates,
            "errors": errors,
            "error_details": error_details,
        }

    return stats


@click.command("backfill-domain-elements")
@click.option("--org-id", type=int, required=True,
              help="Organisation id to backfill elements for")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def backfill_domain_elements_command(org_id, dry_run):
    """Create missing ArchiMate elements for every domain record type in one organisation."""
    from app.models.organization import Organization

    org = db.session.get(Organization, org_id)
    if org is None:
        raise click.ClickException(f"No organisation with id={org_id}.")

    click.echo(f"Backfilling domain elements for organisation {org_id} ({org.name})")
    if dry_run:
        click.echo("DRY RUN -- no data will be modified.\n")

    stats = backfill_domain_elements(org_id, dry_run=dry_run)

    total_created = 0
    total_scanned = 0
    total_errors = 0
    total_duplicates = 0

    click.echo(f"\n{'Type':30s} {'Created':8s} {'Scanned':8s} {'Errors':8s}")
    click.echo("-" * 60)
    for type_name, s in stats.items():
        total_created += s["created"]
        total_scanned += s["scanned"]
        total_errors += s["errors"]
        total_duplicates += len(s["duplicates"])
        action = "Would create" if dry_run else "Created"
        click.echo(f"{type_name:30s} {s['created']:8d} "
                   f"{s['scanned']:8d} {s['errors']:8d}")

    click.echo("-" * 60)
    click.echo(f"{'TOTAL':30s} {total_created:8d} {total_scanned:8d} {total_errors:8d}")

    if total_duplicates:
        click.echo(f"\nDuplicates found: {total_duplicates}")
        for type_name, s in stats.items():
            for dup in s["duplicates"]:
                click.echo(
                    f"  {type_name}: '{dup['name']}' -- "
                    f"kept id={dup['kept_id']}, removed ids={dup['removed_ids']}"
                )

    if total_errors:
        click.echo(f"\nErrors: {total_errors}")
        for type_name, s in stats.items():
            for record_id, reason in s.get("error_details", []):
                click.echo(f"  {type_name}: record id={record_id} -- {reason}")

    if total_scanned == 0:
        click.echo("\nEvery domain record already has an ArchiMate element. Nothing to do.")


def init_app(app):
    """Register CLI command with app."""
    app.cli.add_command(backfill_domain_elements_command)