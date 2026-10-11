"""
flask reconcile-admin-flags — reconcile is_org_admin with is_admin().

Permission.ADMINISTER via is_admin() is the system of record for
"is this user an organisation administrator".  The ``is_org_admin`` database
column is a denormalised copy.  This command reconciles them per organisation,
listing every disagreement it found and what it changed.

Idempotent — re-running is a no-op once every row agrees.

Usage:
    flask --app manage reconcile-admin-flags
    flask --app manage reconcile-admin-flags --dry-run
"""
import click
from flask.cli import with_appcontext

from app import db
from app.models.organization import Organization
from app.models.user import Role, User


@click.command("reconcile-admin-flags")
@click.option("--dry-run", is_flag=True, help="Report disagreements without writing.")
@with_appcontext
def reconcile_admin_flags(dry_run):
    """Reconcile is_org_admin column with is_admin() per organisation."""
    Role.insert_roles()

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        click.echo("ERROR: Administrator role missing after insert_roles(); aborting.")
        raise SystemExit(1)

    # Read ids and names as plain values before the loop. db.session.remove()
    # below detaches any preloaded ORM objects between organisations, so the
    # loop must not hold onto Organization instances across that call.
    organisations = [
        (org.id, org.name)
        for org in Organization.query.order_by(Organization.id).all()
    ]
    total_disagreements = 0
    total_reconciled = 0

    for org_id, org_name in organisations:
        users = User.query.filter_by(organization_id=org_id).all()
        disagreements = []

        for user in users:
            column_value = bool(user._is_org_admin)
            canonical_value = bool(user.is_admin())
            if column_value != canonical_value:
                disagreements.append((user, column_value, canonical_value))

        if not disagreements:
            continue

        total_disagreements += len(disagreements)
        click.echo(
            f"Organisation {org_id} ({org_name}): {len(disagreements)} disagreement(s)"
            + (" (dry-run)" if dry_run else "")
        )

        for user, column_value, canonical_value in disagreements:
            click.echo(
                f"  user {user.id} ({user.email}): "
                f"is_org_admin={column_value}  is_admin()={canonical_value}"
            )
            if not dry_run:
                user._is_org_admin = canonical_value
                db.session.add(user)
                total_reconciled += 1

        if not dry_run:
            db.session.commit()
        db.session.remove()  # clear identity map between organisations

    if dry_run:
        click.echo(
            f"\nDry-run complete.  {total_disagreements} disagreement(s) across "
            f"{len(organisations)} organisation(s) would be reconciled."
        )
    else:
        click.echo(
            f"\nReconciled {total_reconciled} row(s) across "
            f"{len(organisations)} organisation(s)."
        )


def init_app(app):
    """Register the reconcile-admin-flags CLI command."""
    app.cli.add_command(reconcile_admin_flags)
