"""
Idempotent synthetic production test organisations.

Creates exactly two organisations with known, stable slugs that are excluded
from metrics and billing queries. Running this command again is safe: it
updates the names and settings of existing organisations with the same slugs
rather than creating duplicates.

The two organisations exist so the post-deploy cross-organisation check in
scripts/deploy_verified.sh can sign in as each and prove neither can read the
other's records — a structural guarantee that tenant isolation holds after
every deploy, not just in CI.

Exclusion from metrics and billing is enforced by the organisation slugs:
``archie-prod-test-org-a`` and ``archie-prod-test-org-b``. Every metrics and
billing query that aggregates by organisation must filter these out. The
canonical filter is the helper ``is_production_test_org()`` below.
"""

from __future__ import annotations

import os

import click
from flask.cli import with_appcontext

# Stable slugs — never change these without updating every exclusion filter.
ORG_A_SLUG = "archie-prod-test-org-a"
ORG_B_SLUG = "archie-prod-test-org-b"

ORG_A_NAME = "Archie Production Test Organisation A"
ORG_B_NAME = "Archie Production Test Organisation B"

# The email domain used for test users in these organisations.
TEST_EMAIL_DOMAIN = os.environ.get(
    "PROD_TEST_ORG_EMAIL_DOMAIN", "archie-prod-test.example.com"
)


def is_production_test_org(org_slug: str | None) -> bool:
    """Return True when *org_slug* belongs to a synthetic production test org."""
    return org_slug in (ORG_A_SLUG, ORG_B_SLUG)


def production_test_org_slugs() -> tuple:
    """Return the two stable slugs as a tuple for SQL IN clauses."""
    return (ORG_A_SLUG, ORG_B_SLUG)


@click.command("seed-production-test-organisations")
@click.option(
    "--password",
    default=None,
    help="Password for the test users (default: read PROD_TEST_ORG_PASSWORD from environment)",
)
@with_appcontext
def seed_production_test_organisations_command(password):
    """Create or update the two synthetic production test organisations.

    Idempotent: running this again updates names and settings for existing
    organisations with the same slugs rather than creating duplicates.

    Each organisation gets one administrator user. The password is read from
    the PROD_TEST_ORG_PASSWORD environment variable, or passed via --password.
    """
    from flask import current_app

    db = current_app.extensions["sqlalchemy"]
    from app.models.user import Role

    pw = password or os.environ.get("PROD_TEST_ORG_PASSWORD")
    if not pw:
        raise click.ClickException(
            "PROD_TEST_ORG_PASSWORD must be set in the environment or passed via --password"
        )

    Role.insert_roles()
    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        raise click.ClickException("Administrator role not found after insert_roles()")

    created_a = _ensure_org(db, ORG_A_SLUG, ORG_A_NAME, admin_role, pw, "a")
    created_b = _ensure_org(db, ORG_B_SLUG, ORG_B_NAME, admin_role, pw, "b")

    db.session.commit()

    click.echo(f"  organisation A: {ORG_A_SLUG} ({'created' if created_a else 'already exists'})")
    click.echo(f"  organisation B: {ORG_B_SLUG} ({'created' if created_b else 'already exists'})")
    click.echo("  production test organisations ready")


def _ensure_org(db, slug, name, admin_role, password, suffix):
    """Create or update one test organisation and its admin user."""
    from app.models.organization import Organization
    from app.models.user import User

    org = Organization.query.filter_by(slug=slug).first()
    created = org is None

    if org is None:
        org = Organization(
            name=name,
            slug=slug,
            plan="free",
            is_active=True,
            settings={"production_test_org": True},
        )
        db.session.add(org)
        db.session.flush()
    else:
        org.name = name
        org.settings = dict(org.settings or {}, production_test_org=True)
        db.session.add(org)
        db.session.flush()

    # Ensure one admin user exists
    email = f"prod-test-{suffix}@{TEST_EMAIL_DOMAIN}"
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            first_name="Production",
            last_name=f"Test {suffix.upper()}",
            organization_id=org.id,
            role=admin_role,
            confirmed=True,
        )
        user.password = password
        db.session.add(user)
        db.session.flush()
    else:
        user.organization_id = org.id
        user.role = admin_role
        user.confirmed = True
        user.password = password
        db.session.add(user)
        db.session.flush()

    return created


def init_app(app):
    """Register the CLI command with the Flask app."""
    app.cli.add_command(seed_production_test_organisations_command)
