"""
flask migrate-connector-row-secrets -- move a legacy Jira/M365 connector
secret off the ConnectorConfig row and into the per-organisation vault.

F1 (cloud-review-pr448-a85f082b-v1.md): connector_routes.py's save path has
stored Jira's api_token and M365's client_secret only in OrgCredentialVault
since the N1/M1 fix rounds, and jira_connector_service.py / m365_service.py
now read the vault first -- but a connector saved *before* that change still
carries its secret only on the row (``ConnectorConfig.config``), encrypted
with the row-level Fernet key, not the organisation's vault key. The round-2
review asked for an idempotent copy command; this is it (round 3 found it
still did not exist).

For every ConnectorConfig row of connector_type "jira" or "m365":
  - If the organisation already has a vault credential for that connector
    type's credential_type (api_token / client_secret), the row's legacy key
    is simply removed -- the vault value already won on read, and leaving
    the superseded value on the row is exactly the exposure F2 also closes
    for an in-flight rotation.
  - Otherwise, decrypt the row's value (it may be stored as bytes or as a
    decoded str -- the same both-shapes handling F1 added to the read
    paths), store it into the vault, and remove it from the row.

Reports counts only -- never the secret value, in output or in logs.
Idempotent: a second run finds no legacy key left on any row and migrates
nothing further. Never deletes a row, never touches the retired
OrgConnectorConfig path (migrate-connector-credentials already covers that
for ServiceNow/Lucidchart).

Usage:
    flask --app manage migrate-connector-row-secrets
    flask --app manage migrate-connector-row-secrets --dry-run
"""

from __future__ import annotations

import logging

import click
from flask.cli import with_appcontext

from app import db

logger = logging.getLogger(__name__)

# connector_type -> (vault credential_type, {row config keys that held it})
_LEGACY_SECRET_KEYS = {
    "jira": ("api_token", ("api_token", "api_token_encrypted")),
    "m365": ("client_secret", ("client_secret",)),
}


def _decrypt_row_value(raw):
    """Decrypt a row-stored secret, whether it was saved as bytes or as a
    decoded str (F1: main's own save path decoded Fernet ciphertext to str
    before storing it in the JSON config column)."""
    from app.modules.codegen.services.credential_encryption import decrypt_credential

    if not raw:
        return None
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    try:
        return decrypt_credential(data)
    except Exception:
        logger.warning("migrate-connector-row-secrets: could not decrypt a legacy row value")
        return None


def migrate_connector_row_secrets(*, dry_run: bool = False) -> dict:
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    vault = OrgCredentialVault()
    stats = {
        "rows_scanned": 0,
        "legacy_values_found": 0,
        "moved_to_vault": 0,
        "row_already_superseded_by_vault": 0,
        "row_keys_cleared": 0,
        "undecryptable_skipped": 0,
    }

    for connector_type, (credential_type, row_keys) in _LEGACY_SECRET_KEYS.items():
        rows = ConnectorConfig.query.filter_by(connector_type=connector_type).all()
        for cfg in rows:
            stats["rows_scanned"] += 1
            config = dict(cfg.config or {})
            present_keys = [k for k in row_keys if config.get(k)]
            if not present_keys:
                continue
            stats["legacy_values_found"] += 1

            existing_vault_value = vault.retrieve(cfg.organization_id, connector_type, credential_type)
            if existing_vault_value:
                stats["row_already_superseded_by_vault"] += 1
            else:
                decrypted = None
                for key in present_keys:
                    decrypted = _decrypt_row_value(config.get(key))
                    if decrypted:
                        break
                if not decrypted:
                    stats["undecryptable_skipped"] += 1
                    continue
                if not dry_run:
                    vault.store(
                        org_id=cfg.organization_id,
                        connector_type=connector_type,
                        credential_type=credential_type,
                        value=decrypted,
                    )
                stats["moved_to_vault"] += 1

            if not dry_run:
                for key in present_keys:
                    config.pop(key, None)
                cfg.config = config
            stats["row_keys_cleared"] += len(present_keys)

    if not dry_run:
        db.session.commit()

    return stats


@click.command("migrate-connector-row-secrets")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
@with_appcontext
def migrate_connector_row_secrets_command(dry_run):
    """Move a legacy Jira/M365 connector row secret into the vault."""
    click.echo("migrate-connector-row-secrets:" + (" (dry-run)" if dry_run else ""))
    stats = migrate_connector_row_secrets(dry_run=dry_run)
    click.echo("\n  Results:")
    for key, value in stats.items():
        click.echo(f"    {key}: {value}")
    click.echo("Done.")


def init_app(app):
    """Register the migrate-connector-row-secrets CLI command."""
    app.cli.add_command(migrate_connector_row_secrets_command)
