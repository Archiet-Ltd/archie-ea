"""Attribute pre-existing ai_chat_feedback rows to an organisation.

`AIChatFeedback` gained `TenantMixin`, so `do_orm_execute` now filters every read
by `organization_id`. Rows written before that have it NULL and are therefore
invisible to every tenant — including the admin feedback dashboard, which is the
only consumer.

Those rows come from the org-less fallback branch the endpoint used when
`g.current_org_id` was unavailable. The attribution is determinable rather than
guessed: `ai_chat_feedback.user_id` -> `users.id` -> `users.organization_id`.

Idempotent (only touches NULLs) and non-destructive. A row whose user has since
been deleted, or whose user has no organisation, is left NULL and reported —
inventing an owner for it would be worse than leaving it unattributed.

    flask --app manage backfill-feedback-org --dry-run
    flask --app manage backfill-feedback-org

NOT YET REGISTERED. `app/_bootstrap/cli.py` had another session's uncommitted work
in it when this landed, and staging that file would have swept their changes into
this branch. Add this beside the other backfill registrations (~line 65) to make
the command visible to `flask --app manage --help`:

    try:
        from app.commands import backfill_ai_chat_feedback_org
        backfill_ai_chat_feedback_org.init_app(app)
    except Exception as e:
        app.logger.warning(f"Failed to register feedback backfill CLI: {e}")

Until then the module is inert — it defines a command and registers nothing.
"""
import click
from flask.cli import with_appcontext


def init_app(app):
    app.cli.add_command(backfill_feedback_org)


@click.command("backfill-feedback-org")
@click.option("--dry-run", is_flag=True, help="Report what would change, write nothing.")
@with_appcontext
def backfill_feedback_org(dry_run):
    """Set organization_id on ai_chat_feedback rows that have none."""
    from app.extensions import db

    total = db.session.execute(
        db.text("SELECT COUNT(*) FROM ai_chat_feedback")
    ).scalar() or 0
    orphaned = db.session.execute(
        db.text("SELECT COUNT(*) FROM ai_chat_feedback WHERE organization_id IS NULL")
    ).scalar() or 0

    if orphaned == 0:
        click.echo(f"ai_chat_feedback: {total} row(s), none unattributed — nothing to do.")
        return

    resolvable = db.session.execute(
        db.text(
            "SELECT COUNT(*) FROM ai_chat_feedback f "
            "JOIN users u ON u.id = f.user_id "
            "WHERE f.organization_id IS NULL AND u.organization_id IS NOT NULL"
        )
    ).scalar() or 0
    unresolvable = orphaned - resolvable

    click.echo(f"ai_chat_feedback: {total} row(s), {orphaned} unattributed.")
    click.echo(f"  resolvable via users.organization_id: {resolvable}")
    if unresolvable:
        click.echo(
            f"  NOT resolvable (user deleted or has no org): {unresolvable} — left NULL"
        )

    if dry_run:
        click.echo("dry run — nothing written.")
        return

    updated = db.session.execute(
        # tenancy-ok: one-time backfill, retirement 2026-12-31
        db.text(
            "UPDATE ai_chat_feedback f SET organization_id = u.organization_id "
            "FROM users u "
            "WHERE u.id = f.user_id "
            "AND f.organization_id IS NULL "
            "AND u.organization_id IS NOT NULL"
        )
    ).rowcount
    db.session.commit()
    click.echo(f"attributed {updated} row(s).")
