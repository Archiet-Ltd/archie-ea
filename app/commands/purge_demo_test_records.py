"""flask purge-demo-test-records -- remove named test records from the demo organisations.

A security test left "ZZ-AUDIT ..." applications in the production demo
organisation and the preview demo organisation lists "Audit Test App"; every
prospect sees them. This command removes exactly those records and nothing else:

* applications whose name starts with "ZZ-AUDIT", and the application
  "Audit Test App" (exact name), inside a demo organisation;
* the vendor "ZZ-AUDIT Vendor Ltd" (exact name), when a demo organisation's
  contract uses it, no other organisation's contract does, and no contract survives
  the removed applications. Vendors are
  one shared catalogue, so a vendor another organisation uses is kept.

Demo organisations are identified the way ``seed-demo-company`` identifies its
own: by the recorded slug or name it writes (and the recorded name of the
production demo organisation). No other organisation is read or touched.

Dry run by default: lists each record and the rows that depend on it, removes
nothing. ``--apply`` removes them in one transaction through the product's own
delete path (the application delete route's ORM delete plus its mirror
ArchiMate element clean-up; the vendor route's ORM delete). A second
``--apply`` finds nothing.

    flask --app manage purge-demo-test-records            # dry run
    flask --app manage purge-demo-test-records --apply
"""

from __future__ import annotations

import click
from flask.cli import with_appcontext

from app import db
from app.commands.seed_demo_company import _ORG_NAME, _ORG_SLUG

# Recorded identity of the demo organisations: the seed's slug, and the names
# the seed and the production demo organisation carry.
DEMO_ORG_SLUGS = (_ORG_SLUG,)
DEMO_ORG_NAMES = (_ORG_NAME, "Northwind Industries (Demo)")

APPLICATION_PREFIX = "ZZ-AUDIT"
APPLICATION_NAMES = ("Audit Test App",)
VENDOR_NAMES = ("ZZ-AUDIT Vendor Ltd",)


def _demo_organisations():
    from app.models.organization import Organization

    return (
        Organization.query.filter(
            db.or_(Organization.slug.in_(DEMO_ORG_SLUGS), Organization.name.in_(DEMO_ORG_NAMES))
        )
        .order_by(Organization.id)
        .all()
    )


def _dependants(table, ids):
    """[(dependent table, count)] of rows that reference ``table`` rows ``ids``."""
    found = []
    for other in db.metadata.sorted_tables:
        for fk in other.foreign_keys:
            if fk.column.table is not table:
                continue
            sp = db.session.begin_nested()
            try:
                n = db.session.execute(
                    db.select(db.func.count()).select_from(other).where(fk.parent.in_(list(ids)))
                ).scalar()
                sp.commit()
            except Exception:  # a table the database does not have: nothing depends there
                sp.rollback()
                continue
            if n:
                found.append((other.name, int(n)))
    return sorted(set(found))


def find_records():
    """Everything the command would remove: a list of dicts, in delete order."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.vendor.vendor_organization import VendorOrganization

    records = []
    demo_org_ids = []
    for org in _demo_organisations():
        demo_org_ids.append(org.id)
        apps = (
            ApplicationComponent.query.filter(
                ApplicationComponent.organization_id == org.id,
                db.or_(
                    ApplicationComponent.name.startswith(APPLICATION_PREFIX, autoescape=True),
                    ApplicationComponent.name.in_(APPLICATION_NAMES),
                ),
            )
            .order_by(ApplicationComponent.id)
            .all()
        )
        for app in apps:
            deps = _dependants(ApplicationComponent.__table__, [app.id])
            if app.archimate_element_id:
                rels = ArchiMateRelationship.query.filter(
                    db.or_(
                        ArchiMateRelationship.source_id == app.archimate_element_id,
                        ArchiMateRelationship.target_id == app.archimate_element_id,
                    )
                ).count()
                deps.append(("archimate_elements (mirror)", 1))
                if rels:
                    deps.append(("archimate_relationships (mirror)", rels))
            records.append({
                "kind": "application", "organisation": org.slug or org.name,
                "organization_id": org.id, "id": app.id, "name": app.name, "dependants": deps,
            })
    removed_app_ids = {r["id"] for r in records if r["kind"] == "application"}
    for vendor in VendorOrganization.query.filter(VendorOrganization.name.in_(VENDOR_NAMES)).all():
        # An organisation uses a vendor through its contracts. Vendors are one
        # shared catalogue, so one is removed only when a demo organisation's
        # contract uses it, nothing outside the demo organisations does, and
        # no contract survives the removed applications.
        contracts = VendorContract.query.filter(VendorContract.vendor_id == vendor.id).all()
        if not any(c.organization_id in demo_org_ids for c in contracts):
            continue
        outside = [c for c in contracts if c.organization_id not in demo_org_ids]
        surviving = [c for c in contracts if c.application_id not in removed_app_ids]
        keep = None
        if outside:
            keep = "also used by %d contract(s) of other organisations" % len(outside)
        elif surviving:
            keep = "still used by %d contract(s) of applications that stay" % len(surviving)
        deps = [("vendor_contracts (with their applications)", len(contracts))]
        records.append({
            "kind": "vendor", "organisation": "shared catalogue", "organization_id": None,
            "id": vendor.id, "name": vendor.name, "dependants": deps, "keep": keep,
        })
    return records, demo_org_ids


def _remove_application(app_id):
    """The application delete route's path: ORM delete, then the mirror element."""
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.applications.routes._helpers import _delete_mirror_archimate_element

    app = ApplicationComponent.query.filter_by(id=app_id).one()
    element_id = app.archimate_element_id
    db.session.delete(app)
    db.session.flush()
    mirror = _delete_mirror_archimate_element(element_id)
    if mirror["errors"]:
        raise click.ClickException(
            "Application %s: its ArchiMate element is still referenced elsewhere: %s"
            % (app_id, "; ".join(mirror["errors"]))
        )


def _remove_vendor(vendor_id):
    """The vendor delete route's path: ORM delete."""
    from app.models.vendor.vendor_organization import VendorOrganization

    db.session.delete(VendorOrganization.query.filter_by(id=vendor_id).one())
    db.session.flush()


def purge(apply: bool = False):
    """Return the records found; remove them in one transaction when ``apply``."""
    from app.jobs.tenant_safe_job import platform_scope

    with platform_scope("purge-demo-test-records: removes named test records from demo organisations"):
        records, demo_org_ids = find_records()
        if apply:
            try:
                for rec in records:
                    if rec["kind"] == "application":
                        _remove_application(rec["id"])
                for rec in records:
                    if rec["kind"] == "vendor" and not rec.get("keep"):
                        _remove_vendor(rec["id"])
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
    return records, demo_org_ids


@click.command("purge-demo-test-records")
@click.option("--apply", "apply_", is_flag=True, default=False,
              help="Remove the records. Without it nothing is removed (dry run).")
@with_appcontext
def purge_demo_test_records_command(apply_):
    """Remove named test records from the demo organisations (dry run by default)."""
    for org in _demo_organisations():
        click.echo("demo organisation: %s (slug %s, id %d)" % (org.name, org.slug, org.id))
    records, _ = purge(apply=apply_)
    if not records:
        click.echo("nothing to remove")
        return
    verb = "removed" if apply_ else "would remove"
    for rec in records:
        if rec.get("keep"):
            click.echo("  kept   %s %s %r: %s" % (rec["kind"], rec["id"], rec["name"], rec["keep"]))
            continue
        click.echo("  %s %s %s in %s: %r" % (verb, rec["kind"], rec["id"], rec["organisation"], rec["name"]))
        for table, n in rec["dependants"]:
            click.echo("      and its dependants: %d row(s) in %s" % (n, table))
    if not apply_:
        click.echo("dry run: nothing removed; run again with --apply to remove them")


def init_app(app):
    app.cli.add_command(purge_demo_test_records_command)
