"""backfill-vendor-links: give every system the vendor record it already names.

Applications carried the vendor as free text (``vendor_name``) or through a
vendor product; contracts carried it for themselves. The vendor register
(app/services/vendor_register.py) reads one link, so this writes it: a vendor
record for every distinct vendor named on a system, and the link from each
system (and from each contract that has a system but no vendor) to it.

Idempotent; run after reconcile-schema:

    flask --app manage backfill-vendor-links --dry-run
    flask --app manage backfill-vendor-links
    flask --app manage backfill-vendor-links --org-id 3
"""

import click
from flask.cli import with_appcontext


@click.command("backfill-vendor-links")
@click.option("--dry-run", is_flag=True, help="Report what would change and write nothing.")
@click.option("--org-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def backfill_vendor_links(dry_run, org_id):
    from app.services.vendor_register import backfill_links

    counts = backfill_links(org_id=org_id, dry_run=dry_run)
    prefix = "would link" if dry_run else "linked"
    click.echo(
        f"{prefix}: {counts['systems_linked']} systems, {counts['contracts_linked']} contracts; "
        f"vendor records {'to create' if dry_run else 'created'}: {counts['vendors_created']}"
    )


def init_app(app):
    app.cli.add_command(backfill_vendor_links)
