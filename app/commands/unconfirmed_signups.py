"""flask --app manage list-unconfirmed-signups / disable-unconfirmed-signups

Accounts created through the sign-up form whose address was never confirmed. Scripts leave these
behind; a person who simply has not clicked yet does too, which is why the second command is
told how old an account must be and never deletes anything.

    flask --app manage list-unconfirmed-signups
    flask --app manage disable-unconfirmed-signups --older-than 2d --dry-run
    flask --app manage disable-unconfirmed-signups --older-than 2d

The list shows the email domain only, never the local part.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import click

_AGE = re.compile(r"^(\d+)\s*([hd])$")


def parse_age(text: str) -> timedelta:
    """'2d' is two days, '12h' twelve hours."""
    found = _AGE.match((text or "").strip().lower())
    if not found:
        raise click.BadParameter("give an age such as 2d or 12h")
    amount = int(found.group(1))
    return timedelta(days=amount) if found.group(2) == "d" else timedelta(hours=amount)


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def unconfirmed_accounts(older_than: timedelta | None = None):
    """Unconfirmed, still-enabled accounts that were not made by an invitation, oldest first."""
    from app.models.user import User

    query = User.query.filter(
        User.confirmed.isnot(True),
        User.disabled_at.is_(None),
        User.password_hash.isnot(None),
    )
    if older_than is not None:
        # an account with no recorded sign-up time has no age to judge, so it is never selected here
        query = query.filter(User.signup_at <= _naive_utc(datetime.now(timezone.utc)) - older_than)
    return query.order_by(User.signup_at.asc().nullsfirst(), User.id).all()


def _sessions(user_id: int) -> int:
    from app.models.user_session import UserSession

    return UserSession.query.filter_by(user_id=user_id).count()


def _has_activity(user) -> bool:
    """True when the account did anything past signing up: a chat answer, or work in its organisation."""
    from app.services import signup_attribution

    return signup_attribution.first_answer_at(user) is not None


@click.command("list-unconfirmed-signups")
def list_unconfirmed_signups():
    """Report each unconfirmed sign-up: email domain, created date, confirmed, sessions, activity."""
    accounts = unconfirmed_accounts()
    click.echo("domain\tcreated\tconfirmed\tsessions\tactivity")
    for user in accounts:
        domain = (user.email or "").rsplit("@", 1)[-1].lower() or "unknown"
        click.echo("{}\t{}\t{}\t{}\t{}".format(
            domain, user.signup_at.date().isoformat() if user.signup_at else "unknown", "yes" if user.confirmed else "no",
            _sessions(user.id), "yes" if _has_activity(user) else "no",
        ))
    click.echo("list-unconfirmed-signups: {} unconfirmed account(s)".format(len(accounts)))


@click.command("disable-unconfirmed-signups")
@click.option("--older-than", "older_than", default="2d", show_default=True, help="Minimum age, such as 2d or 12h.")
@click.option("--dry-run", is_flag=True, help="Report what would be disabled without changing anything.")
def disable_unconfirmed_signups(older_than: str, dry_run: bool):
    """Mark old unconfirmed sign-ups inactive and end their sessions. Never deletes."""
    from app.extensions import db
    from app.services import session_registry

    accounts = [user for user in unconfirmed_accounts(parse_age(older_than)) if not _has_activity(user)]
    click.echo("disable-unconfirmed-signups: {} account(s) older than {} with no activity".format(len(accounts), older_than))
    if dry_run:
        for user in accounts:
            click.echo("  would disable a {} account created {}".format(
                (user.email or "").rsplit("@", 1)[-1].lower(), user.signup_at.date().isoformat()))
        click.echo("disable-unconfirmed-signups: dry run, 0 disabled")
        return
    now = _naive_utc(datetime.now(timezone.utc))
    for user in accounts:
        user.disabled_at = now
        session_registry.revoke_all_for_user(user.id, "admin")
    db.session.commit()
    click.echo("disable-unconfirmed-signups: {} disabled".format(len(accounts)))


def init_app(app):
    app.cli.add_command(list_unconfirmed_signups)
    app.cli.add_command(disable_unconfirmed_signups)
