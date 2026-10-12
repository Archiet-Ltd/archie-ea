"""flask --app manage prune-oauth-clients — remove dynamically-registered
OAuth clients nobody has used.

A client registered through POST /oauth/register (RFC 7591) is
unauthenticated and rate-limited, not approved — anything that can reach the
endpoint can create one. Most of these are a real assistant connecting once
and staying in use; the rest are abandoned registration attempts, dead
integrations, or rate-limit noise. A client with no successful token
exchange in the last 30 days (configurable), that is itself also older than
that window, is deleted.

    flask --app manage prune-oauth-clients --dry-run
    flask --app manage prune-oauth-clients
    flask --app manage prune-oauth-clients --days 14
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import click


@click.command("prune-oauth-clients")
@click.option("--dry-run", is_flag=True, help="Report what would be deleted without deleting it.")
@click.option("--days", default=30, type=int, help="Inactivity window in days (default 30).")
def prune_oauth_clients(dry_run: bool, days: int):
    from app.extensions import db
    from app.modules.oauth_provider.models import OAuthClient, OAuthToken

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    before_count = OAuthClient.query.count()

    recently_used_client_ids = {
        row[0]
        for row in db.session.query(OAuthToken.client_id)
        .filter(OAuthToken.issued_at >= cutoff)
        .distinct()
        .all()
    }

    candidates = OAuthClient.query.filter(OAuthClient.created_at < cutoff).all()
    to_delete = [c for c in candidates if c.client_id not in recently_used_client_ids]

    click.echo(f"prune-oauth-clients: {before_count} client(s) before, {len(to_delete)} eligible for deletion")

    if dry_run:
        for client in to_delete:
            click.echo(f"  would delete {client.client_id} ({client.client_name or 'unnamed'})")
        click.echo(f"prune-oauth-clients: {before_count} client(s) before (dry run, 0 deleted)")
        return

    for client in to_delete:
        db.session.delete(client)
    db.session.commit()

    after_count = OAuthClient.query.count()
    click.echo(f"prune-oauth-clients: {before_count} client(s) before, {after_count} after")


def init_app(app):
    app.cli.add_command(prune_oauth_clients)
