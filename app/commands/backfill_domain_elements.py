"""flask backfill-domain-elements -- per-organisation backfill of missing ArchiMate elements.

Every domain record of the nine types must have exactly one ArchiMate element
node in its organisation. This command creates the missing elements for a given
organisation and reports duplicates.

Usage:
    flask --app manage backfill-domain-elements --org-id 7
    flask --app manage backfill-domain-elements --org-id 7 --dry-run

The command is idempotent: running it again on a fully-linked organisation
creates nothing and reports no duplicates.

Duplicates: when a domain record has more than one ArchiMateElement pointing
at it, the oldest element (lowest id) is kept and the others are listed with
their ids. No element is ever silently removed.
"""

import click
from flask.cli import with_appcontext

from app import db


# Each entry: (model_class, name_field, element_type, element_layer,
#              description_template)
# The model_class is imported lazily inside the handler.
_DOMAIN_TYPES = [
    # application
    ("app.models.application_portfolio.ApplicationComponent",
     "name", "ApplicationComponent", "Application",
     "Application: {name}"),
    # capability
    ("app.models.business_capabilities.BusinessCapability",
     "name", "Capability", "Strategy",
     "Business capability: {name}"),
    # risk
    ("app.models.risk.Risk",
     "title", "Assessment", "Motivation",
     "Risk: {title}"),
    # contract
    ("app.models.application_portfolio.VendorContract",
     "contract_name", "Contract", "Business",
     "Vendor contract: {contract_name}"),
    # interface
    ("app.models.application_layer.ApplicationInterface",
     "name", "ApplicationInterface", "Application",
     "{interface_type} interface"),
    # data entity
    ("app.models.process_data.DataEntity",
     "name", "DataObject", "application",
     "Data object for entity: {name}"),
    # work package
    ("app.models.implementation_migration.WorkPackage",
     "name", "WorkPackage", "Implementation",
     "Work package: {name}"),
]


def _import_model(path):
    """Import a model class from its dotted path."""
    module_path, class_name = path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def _resolve_name(row, name_field):
    """Get the display name from a row, handling different field names."""
    value = getattr(row, name_field, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"{row.__class__.__name__}-{row.id}"


def _truncate_name(name, max_len=100):
    """Truncate a name to fit ArchiMateElement.name (String(100))."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "\u2026"


def backfill_domain_elements(org_id, dry_run=False, session=None):
    """Create missing ArchiMate elements for every domain record type.

    ``session`` is optional; defaults to ``db.session``. Pass an explicit
    session in tests that wrap ``db.session`` in a rollback transaction.

    Returns a dict with per-type stats:
        {type_name: {"created": int, "scanned": int, "duplicates": [...], "errors": int}}
    """
    from app.models.archimate_core import ArchiMateElement

    if session is None:
        session = db.session

    stats = {}

    for model_path, name_field, element_type, element_layer, desc_template in _DOMAIN_TYPES:
        model = _import_model(model_path)
        type_key = model.__name__

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
                ArchiMateElement.type == element_type,
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

        if not dry_run:
            for record in records:
                try:
                    name = _resolve_name(record, name_field)
                    element = ArchiMateElement(
                        name=_truncate_name(name),
                        type=element_type,
                        layer=element_layer,
                        description=desc_template.format(
                            **{k: getattr(record, k, "") for k in
                               ["name", "title", "contract_name", "interface_type"]}
                        ) if desc_template else None,
                        organization_id=org_id,
                    )
                    session.add(element)
                    session.flush()
                    record.archimate_element_id = element.id
                    created += 1
                except Exception:
                    session.rollback()
                    errors += 1

            if created:
                session.flush()

        stats[type_key] = {
            "created": created,
            "scanned": len(records),
            "duplicates": duplicates,
            "errors": errors,
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
        click.echo(f"{type_name:30s} {s[action.split()[-1].lower() if dry_run else 'created']:8d} "
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

    if total_scanned == 0:
        click.echo("\nEvery domain record already has an ArchiMate element. Nothing to do.")


def init_app(app):
    """Register CLI command with app."""
    app.cli.add_command(backfill_domain_elements_command)