"""flask customer-zero-load: write the organisation's own products into its model.

    flask --app manage customer-zero-load --organization 7
    flask --app manage customer-zero-load --organization 7 --file data/customer_zero/entelim.json --dry-run

Reads every product file in data/customer_zero (or the files named), and writes each product
and its capabilities to the one organisation named. Safe to run again after every deploy:
nothing unchanged is rewritten, and a capability removed from a file is marked withdrawn.
"""
import click
from flask.cli import with_appcontext


@click.command("customer-zero-load")
@click.option("--organization", "organization_id", required=True, type=int, help="The organisation whose model is written.")
@click.option("--file", "files", multiple=True, type=click.Path(exists=True, dir_okay=False), help="A product file; default is every file in data/customer_zero.")
@click.option("--dry-run", is_flag=True, default=False, help="Report what would change and write nothing.")
@with_appcontext
def customer_zero_load_cmd(organization_id, files, dry_run):
    from app.services import customer_zero_model as model

    paths = model.product_files(files)
    if not paths:
        raise click.ClickException("No product files found.")
    for path in paths:
        try:
            counts = model.load_product(path, organization_id, dry_run=dry_run)
        except model.ProductFileError as exc:
            raise click.ClickException(str(exc))
        click.echo("%s%s: %d created, %d updated, %d unchanged, %d withdrawn" % (
            "DRY RUN - " if dry_run else "", counts["product"], counts["created"], counts["updated"],
            counts["unchanged"], counts["withdrawn"]))


def init_app(app):
    app.cli.add_command(customer_zero_load_cmd)
