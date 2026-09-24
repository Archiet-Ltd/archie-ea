"""
Power Platform CoE Discovery Service
Maps to: docs/2026-03-26-microsoft-power-platform-governance-design.md §4

Credentials are stored in APISettings using:
  provider="power_platform_coe", key_label="default"
  api_key          → client_secret (Fernet-encrypted)
  jira_url         → tenant_id
  jira_email       → client_id
  custom_endpoint_url → environment URL
"""
import logging
import time
from datetime import datetime

import requests

from app import db

logger = logging.getLogger(__name__)

# Try to import msal — log warning if unavailable
try:
    import msal
    MSAL_AVAILABLE = True
except ImportError:
    MSAL_AVAILABLE = False
    logger.warning("msal not installed — Power Platform CoE token acquisition unavailable")


class PowerPlatformCoeService:
    """Idempotent, delta-aware Power Platform CoE discovery service."""

    POWER_APPS_ENDPOINT = "https://api.powerapps.com/providers/Microsoft.PowerApps/apps"
    API_VERSION = "2023-06-01"
    PAGE_SIZE = 250
    MAX_RETRIES = 3
    PROVIDER = "power_platform_coe"
    KEY_LABEL = "default"

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    @classmethod
    def _load_settings(cls) -> dict:
        """Load stored credentials from APISettings. Returns {} if not configured."""
        from app.models.models import APISettings
        row = APISettings.query.filter_by(
            provider=cls.PROVIDER, key_label=cls.KEY_LABEL
        ).first()
        if not row:
            return {}
        return {
            "tenant_id": row.jira_url or "",
            "client_id": row.jira_email or "",
            "client_secret": row.api_key or "",
            "env_url": row.custom_endpoint_url or "",
        }

    @classmethod
    def save_settings(cls, tenant_id: str, client_id: str, client_secret: str, env_url: str) -> None:
        """Persist credentials to APISettings (upsert)."""
        from app.models.models import APISettings
        row = APISettings.query.filter_by(
            provider=cls.PROVIDER, key_label=cls.KEY_LABEL
        ).first()
        if not row:
            row = APISettings(provider=cls.PROVIDER, key_label=cls.KEY_LABEL)
            db.session.add(row)
        row.jira_url = tenant_id
        row.jira_email = client_id
        row.api_key = client_secret
        row.custom_endpoint_url = env_url
        db.session.commit()

    # ------------------------------------------------------------------
    # Token acquisition
    # ------------------------------------------------------------------

    @staticmethod
    def _get_token(tenant_id: str, client_id: str, client_secret: str) -> "str | None":
        """Acquire service principal token for Power Platform API. Returns None on failure."""
        if not MSAL_AVAILABLE:
            logger.warning("msal not installed — Power Platform CoE token acquisition unavailable")
            return None
        try:
            app = msal.ConfidentialClientApplication(
                client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential=client_secret,
            )
            result = app.acquire_token_for_client(
                scopes=["https://service.powerapps.com/.default"]
            )
            token = result.get("access_token")
            if not token:
                logger.error(
                    "Power Platform token acquisition failed: %s",
                    result.get("error_description"),
                )
            return token
        except Exception as exc:
            logger.error("Power Platform token acquisition exception: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def test_connection(
        cls, tenant_id: str, client_id: str, client_secret: str, env_url: str
    ) -> dict:
        """Test credentials.

        Returns {"status": "ok", "environment": env_url} or
                {"status": "error", "message": ...}.
        """
        # F-08 note: env_url is NOT an SSRF sink. It is stored, echoed back in
        # the response below, and never fetched — every outbound call in this
        # class targets the fixed POWER_APPS_ENDPOINT constant, and the only
        # other URL followed is @odata.nextLink returned by Microsoft's own
        # API. If a future change ever fetches env_url directly, it must go
        # through app.utils.ssrf_guard.validate_outbound_url with a
        # *.dynamics.com / *.crm*.dynamics.com allow-list first.
        token = cls._get_token(tenant_id, client_id, client_secret)
        if not token:
            return {
                "status": "error",
                "message": "Token acquisition failed — check Tenant ID, Client ID, and Client Secret",
            }
        try:
            resp = requests.get(
                cls.POWER_APPS_ENDPOINT,
                headers={"Authorization": f"Bearer {token}"},
                params={"api-version": cls.API_VERSION, "$top": 1},
                timeout=10,
            )
            if resp.status_code in (200, 206):
                return {"status": "ok", "environment": env_url}
            return {"status": "error", "message": f"API returned {resp.status_code}"}
        except requests.RequestException as exc:
            return {"status": "error", "message": str(exc)}

    @classmethod
    def discover_apps(
        cls, tenant_id: str, client_id: str, client_secret: str
    ) -> list:
        """Return list of {"id", "name", "owner_email", "last_modified", "display_name"}.

        Never raises — returns empty list with logged error on total failure.
        Paginates via @odata.nextLink until exhaustion.
        Retries on 429 with exponential backoff (max MAX_RETRIES).
        """
        token = cls._get_token(tenant_id, client_id, client_secret)
        if not token:
            return []

        results = []
        url = cls.POWER_APPS_ENDPOINT
        params = {"api-version": cls.API_VERSION, "$top": cls.PAGE_SIZE}
        headers = {"Authorization": f"Bearer {token}"}

        while url:
            for attempt in range(cls.MAX_RETRIES):
                try:
                    resp = requests.get(
                        url,
                        headers=headers,
                        params=params if url == cls.POWER_APPS_ENDPOINT else None,
                        timeout=30,
                    )
                    if resp.status_code == 429:
                        wait = 2 ** attempt
                        logger.warning(
                            "Power Platform API rate limited — retrying in %ss", wait
                        )
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    for item in data.get("value", []):
                        props = item.get("properties", {})
                        results.append({
                            "id": item.get("name", ""),
                            "display_name": props.get("displayName", ""),
                            "name": props.get("displayName", item.get("name", "")),
                            "owner_email": props.get("owner", {}).get("email", ""),
                            "last_modified": props.get("lastModifiedTime", ""),
                        })
                    url = data.get("@odata.nextLink")
                    params = None  # nextLink already contains query params
                    break
                except Exception as exc:
                    logger.error(
                        "Power Platform discover_apps error (attempt %d): %s",
                        attempt + 1,
                        exc,
                    )
                    if attempt == cls.MAX_RETRIES - 1:
                        url = None  # stop pagination on final failure
                    else:
                        time.sleep(2 ** attempt)

        return results

    @classmethod
    def import_apps(cls, app_ids: list, discovered: list, user_id: int) -> dict:
        """Create ApplicationComponent records for each app_id not already in Entelim.

        Returns {"imported": N, "already_exists": M, "failed": F}.
        Idempotent — skips apps where source_identifier matches.
        Queues ARBAuditLog for apps without an Entelim owner.
        """
        from app.models.application_portfolio import ApplicationComponent
        from app.models.architecture_review_board import ARBAuditLog

        discovered_map = {a["id"]: a for a in discovered}

        imported = 0
        already_exists = 0
        failed = 0

        for app_id in app_ids:
            pp_app = discovered_map.get(app_id)
            if not pp_app:
                failed += 1
                continue

            # Idempotency — check source_identifier
            existing = ApplicationComponent.query.filter_by(
                source_identifier=app_id
            ).first()
            if existing:
                existing.provenance = {
                    "source": "power_platform_coe",
                    "last_synced": datetime.utcnow().isoformat(),
                    **pp_app,
                }
                db.session.add(existing)
                already_exists += 1
                continue

            try:
                app_rec = ApplicationComponent(
                    name=pp_app["display_name"] or pp_app["name"],
                    application_type="web",
                    data_source="power_platform_coe",
                    source_identifier=app_id,
                    provenance={
                        "source": "power_platform_coe",
                        "imported_at": datetime.utcnow().isoformat(),
                        **pp_app,
                    },
                    deployment_model="cloud",
                )
                db.session.add(app_rec)
                db.session.flush()  # get id before ARB log

                owner_email = pp_app.get("owner_email", "")
                if not owner_email:
                    arb_log = ARBAuditLog(
                        entity_type="Application",
                        entity_id=app_rec.id,
                        action="coe_import_ungoverned",
                        action_description=(
                            f"Power App '{app_rec.name}' imported from CoE — "
                            "no owner in Entelim. Requires assignment."
                        ),
                        user_id=user_id,
                        new_value={"source": "power_platform_coe", "original_id": app_id},
                    )
                    db.session.add(arb_log)

                imported += 1
            except Exception as exc:
                logger.error("Failed to import Power App %s: %s", app_id, exc)
                db.session.rollback()
                failed += 1
                continue

        try:
            db.session.commit()
        except Exception as exc:
            logger.error("Power Platform import commit failed: %s", exc)
            db.session.rollback()
            return {
                "imported": 0,
                "already_exists": already_exists,
                "failed": len(app_ids),
            }

        return {"imported": imported, "already_exists": already_exists, "failed": failed}
