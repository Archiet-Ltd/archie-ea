"""Manual/local IndexNow ping: ``flask --app manage ping-indexnow``.

The production deploy's own IndexNow ping (every changed public URL, after
every successful deploy) lives in scripts/post_deploy_verify.py, which runs
on a bare GitHub Actions runner with no app dependencies installed and so
cannot import this Flask command. This command is the same ping, usable
locally or for a one-off re-submission, with full access to the app's own
page loader and config.

No-op (prints why and exits 0) when INDEXNOW_API_KEY is unset.
"""

import click
from flask import current_app
from flask.cli import with_appcontext


@click.command("ping-indexnow")
@click.option(
    "--base-url", default="https://entelim.org",
    help="Site origin to build absolute URLs and the key-location URL from.",
)
@with_appcontext
def ping_indexnow_command(base_url):
    """Ping IndexNow with every current public page URL. No-op if unset."""
    from app.services.indexnow_service import is_enabled, ping_indexnow
    from app.services.public_pages import feed_page_paths

    if not is_enabled(current_app):
        click.echo(
            "ping-indexnow: INDEXNOW_API_KEY is not set -- nothing to do."
        )
        return

    # feed_page_paths(), the same path list /sitemap.xml is built from, so
    # a manual ping can never submit a different URL set than the sitemap
    # advertises -- it already excludes a page withdrawn from discovery
    # (state: not_planned) and includes the /vs and /use-cases hub views.
    urls = [
        base_url.rstrip("/") + path for path in feed_page_paths()
    ]
    result = ping_indexnow(current_app, urls, base_url=base_url)
    click.echo(
        f"ping-indexnow: submitted {len(urls)} URL(s), "
        f"IndexNow responded {result.get('status_code') if result else 'n/a'}"
    )


def init_app(app):
    """Register the ping-indexnow CLI command."""
    app.cli.add_command(ping_indexnow_command)
