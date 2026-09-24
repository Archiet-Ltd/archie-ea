"""COM-017 — Microsoft 365 integration service.

Provides:
    - SharePoint file upload via Microsoft Graph API (client_credentials OAuth2)
    - Teams ARB notifications via Incoming Webhook (Adaptive Cards)

Config is stored in ConnectorConfig with connector_type='m365'.
The config JSON field holds:
    tenant_id, client_id, client_secret (Fernet-encrypted),
    site_id, folder_path, teams_webhook_url, enabled (bool)

All public methods return None / no-op when config is missing or disabled.
All HTTP calls use timeout=10.
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Module-level token cache: connector_id → {access_token, expires_at}
_token_cache: dict = {}


class M365Service:
    """Microsoft 365 integration — SharePoint upload + Teams notifications."""

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def upload_to_sharepoint(
        self,
        org_id,
        file_content: bytes,
        filename: str,
        site_id: str,
        folder_path: str,
    ) -> Optional[str]:
        """Upload *file_content* to SharePoint and return the web URL.

        Uses PUT /v1.0/sites/{site_id}/drive/root:/{folder_path}/{filename}:/content.
        Returns the SharePoint webUrl on success, None on failure or if config is
        missing/disabled.
        """
        config = self._get_config()
        if not config:
            return None

        try:
            token = self._get_token(config)
            if not token:
                return None

            clean_folder = folder_path.strip("/")
            url = (
                f"{GRAPH_BASE}/sites/{site_id}/drive/root:/"
                f"{clean_folder}/{filename}:/content"
            )
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            }
            resp = requests.put(url, headers=headers, data=file_content, timeout=10)
            resp.raise_for_status()
            return resp.json().get("webUrl")
        except Exception:
            logger.exception("COM-017: SharePoint upload failed for '%s'", filename)
            return None

    def send_teams_notification(
        self,
        org_id,
        channel_webhook_url: str,
        title: str,
        message: str,
        action_url: str,
    ) -> None:
        """Post an Adaptive Card to a Teams Incoming Webhook.

        No-op when config is missing, disabled, or webhook_url is empty.
        """
        config = self._get_config()
        if not config:
            return
        if not channel_webhook_url:
            return

        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": title,
                                "weight": "Bolder",
                                "size": "Medium",
                            },
                            {
                                "type": "TextBlock",
                                "text": message,
                                "wrap": True,
                            },
                        ],
                        "actions": [
                            {
                                "type": "Action.OpenUrl",
                                "title": "Open in Entelim",
                                "url": action_url,
                            }
                        ],
                    },
                }
            ],
        }

        try:
            resp = requests.post(
                channel_webhook_url,
                json=card,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
        except Exception:
            logger.exception("COM-017: Teams notification failed (title='%s')", title)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _get_config(self):
        """Return the active M365 ConnectorConfig, or None if not configured."""
        try:
            from app.services.connector_framework import ConnectorConfig

            cfg = ConnectorConfig.query.filter_by(connector_type="m365").first()
            if not cfg:
                return None
            if not (cfg.config or {}).get("enabled", True):
                return None
            return cfg
        except Exception:
            logger.debug("COM-017: Could not load M365 config", exc_info=True)
            return None

    def _get_token(self, config) -> Optional[str]:
        """Return a valid OAuth2 access token, refreshing if expired.

        Tokens are cached in-memory per connector ID. Microsoft tokens last
        ~3600 s; we refresh 30 s early to avoid clock-skew failures.
        """
        connector_id = config.id
        cached = _token_cache.get(connector_id)
        if cached and cached["expires_at"] > time.time() + 30:
            return cached["access_token"]

        cfg_data = config.config or {}
        tenant_id = cfg_data.get("tenant_id", "")
        client_id = cfg_data.get("client_id", "")
        client_secret_stored = cfg_data.get("client_secret", "")

        if not (tenant_id and client_id and client_secret_stored):
            logger.warning("COM-017: M365 config is missing required credentials")
            return None

        try:
            from app.modules.codegen.services.credential_encryption import (
                decrypt_credential,
            )

            raw = client_secret_stored
            if isinstance(raw, str):
                raw = raw.encode()
            client_secret = decrypt_credential(raw)
        except Exception:
            logger.warning("COM-017: Failed to decrypt M365 client_secret")
            return None

        if not client_secret:
            logger.warning("COM-017: Decrypted client_secret is empty")
            return None

        url = _TOKEN_URL.format(tenant_id=tenant_id)
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }

        try:
            resp = requests.post(url, data=payload, timeout=10)
            resp.raise_for_status()
            token_data = resp.json()
            access_token = token_data["access_token"]
            expires_in = int(token_data.get("expires_in", 3600))
            _token_cache[connector_id] = {
                "access_token": access_token,
                "expires_at": time.time() + expires_in,
            }
            return access_token
        except Exception:
            logger.exception("COM-017: OAuth2 token fetch failed for M365")
            return None
