"""flask vendor-catalogue -- the public vendor catalogue's research and review (ADR-0021).

    flask vendor-catalogue research [SLUG ...]   check each planned fact against its public
                                                 page and write drafts (never publishes)
    flask vendor-catalogue publish SLUG --reviewer NAME
                                                 a person's review: publish the sourced facts
    flask vendor-catalogue export                write the reviewed snapshot that ships
    flask vendor-catalogue load                  load the reviewed snapshot into this database
"""

import click
from flask.cli import with_appcontext


@click.group("vendor-catalogue")
def vendor_catalogue():
    """The public vendor catalogue."""


@vendor_catalogue.command("research")
@click.argument("slugs", nargs=-1)
@with_appcontext
def research(slugs):
    from app.services import vendor_catalogue_service as svc

    plan = svc.load_plan()
    chosen = [e for e in plan if not slugs or e["slug"] in slugs]
    if slugs and len(chosen) != len(set(slugs)):
        missing = set(slugs) - {e["slug"] for e in chosen}
        raise click.ClickException(f"not in the research plan: {', '.join(sorted(missing))}")
    for entry in chosen:
        report = svc.research_product(entry)
        click.echo(
            f"{entry['slug']}: {len(report['confirmed'])} confirmed, "
            f"{len(report['unconfirmed'])} left as drafts"
        )
        for name, url, reason in report["unconfirmed"]:
            click.echo(f"    draft  {name}  ({reason})  {url}")


@vendor_catalogue.command("publish")
@click.argument("slug")
@click.option("--reviewer", required=True, help="The person who checked the facts against their sources.")
@with_appcontext
def publish(slug, reviewer):
    from app.services import vendor_catalogue_service as svc

    result = svc.publish_product(slug, reviewer)
    click.echo(f"{slug}: {result['published']} published, {result['held']} held as drafts (no confirmed source)")


@vendor_catalogue.command("export")
@with_appcontext
def export():
    from app.services import vendor_catalogue_service as svc

    click.echo(f"wrote {svc.export_reviewed()} products to {svc.REVIEWED_PATH}")


@vendor_catalogue.command("load")
@with_appcontext
def load():
    from app.services import vendor_catalogue_service as svc

    counts = svc.load_reviewed()
    click.echo(f"loaded {counts['products']} products: {counts['published']} published facts, {counts['draft']} drafts")


def init_app(app):
    app.cli.add_command(vendor_catalogue)
