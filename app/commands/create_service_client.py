"""flask --app manage create-service-client — a machine identity for /api/v1.

A service client is a confidential OAuth client bound to one service user (a
non-human account owned by an organisation). A client-credentials grant
against it issues an api:read token whose organisation is the service user's.
The client may only ever be issued the scopes it is registered with.

Operators run this once per machine client; the printed client_secret is the
only time the plaintext exists.

    flask --app manage create-service-client --org-id 5 --name "Model Reader"

The service user gets no password (sign-in refuses service accounts outright)
and the read-only Viewer role, so it holds no role powers beyond the scopes
of the tokens issued to it. No new screens: this is the only way a service
client is created.
"""

from __future__ import annotations

import re
import secrets

import click


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "client"


@click.command("create-service-client")
@click.option("--org-id", required=True, type=int, help="The owning organisation id.")
@click.option("--name", required=True, help="Human-readable name for the client.")
@click.option(
    "--scope",
    default="api:read",
    show_default=True,
    help="Space-separated scopes this client may request (api:* only).",
)
def create_service_client(org_id: int, name: str, scope: str):
    from app.extensions import db
    from app.models.organization import Organization
    from app.models.user import ROLE_NON_TECHNICAL_OWNER, Role, User
    from app.modules.oauth_provider.models import OAuthClient, _new_client_id, hash_secret

    organization = db.session.get(Organization, org_id)
    if organization is None:
        raise click.ClickException(f"No organisation with id {org_id}.")

    scopes = [item for item in scope.split() if item]
    if not scopes:
        raise click.ClickException("At least one scope is required.")

    from app.modules.oauth_provider.scopes import API_SCOPES

    if not set(scopes) <= set(API_SCOPES):
        raise click.ClickException("Only api:* scopes may be registered on a service client.")

    viewer = Role.query.filter_by(name="Viewer").first()
    if viewer is None:
        Role.insert_roles()
        viewer = Role.query.filter_by(name="Viewer").first()

    email = f"svc-{_slugify(name)}-{secrets.token_hex(4)}@{organization.slug}.local"
    service_user = User(
        email=email,
        first_name=f"Service: {name}",
        last_name="",
        organization_id=organization.id,
        role=viewer,
        confirmed=True,
        is_service_user=True,
        enterprise_role=ROLE_NON_TECHNICAL_OWNER,
    )
    db.session.add(service_user)
    db.session.flush()

    client_secret = secrets.token_urlsafe(32)
    client = OAuthClient(
        client_id=_new_client_id("svc"),
        client_name=name,
        client_secret_hash=hash_secret(client_secret),
        grant_types="client_credentials",
        token_endpoint_auth_method="client_secret_post",
        scope=" ".join(scopes),
        service_user_id=service_user.id,
        is_active=True,
    )
    db.session.add(client)
    db.session.commit()

    click.echo(f"create-service-client: created client {client.client_id}")
    click.echo(f"  organisation: {organization.name} (id {organization.id})")
    click.echo(f"  service user: {service_user.email} (id {service_user.id})")
    click.echo(f"  scopes: {' '.join(scopes)}")
    click.echo(f"client_id: {client.client_id}")
    click.echo(f"client_secret: {client_secret}")


def init_app(app):
    app.cli.add_command(create_service_client)