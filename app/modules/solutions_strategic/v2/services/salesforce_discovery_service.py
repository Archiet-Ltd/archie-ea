"""Salesforce Org Discovery Service (PROG-003).

Sibling of PowerPlatformCoeService — same credential storage, same
discover→import shape, same idempotency contract.

Credentials are stored in APISettings using:
  provider="salesforce_org", key_label="default"
  jira_url            → instance URL (https://yourorg.my.salesforce.com)
  jira_email          → connected-app client_id (consumer key)
  api_key             → connected-app client_secret (Fernet-encrypted)

Auth: OAuth2 client-credentials flow against
  {instance_url}/services/oauth2/token
(the Connected App must have "Enable Client Credentials Flow" with a
run-as user). Plain `requests` — no new dependency.

Discovery returns the org's governable surface as candidate
ApplicationComponents:
  - Lightning/custom apps  (SOQL: AppDefinition)
  - Installed AppExchange packages (Tooling API: InstalledSubscriberPackage)
Each query degrades independently — a missing permission for one object
must not blank the other.
"""

import logging
import time
from datetime import datetime

import requests

from app import db
from app.utils.ssrf_guard import (
    BlockedOutboundURL,
    validate_salesforce_instance_url,
)

logger = logging.getLogger(__name__)


class SalesforceDiscoveryService:
    """Idempotent Salesforce org discovery — apps + installed packages."""

    API_VERSION = "v60.0"
    MAX_RETRIES = 3
    PROVIDER = "salesforce_org"
    KEY_LABEL = "default"

    # ------------------------------------------------------------------
    # Credential helpers (APISettings field reuse mirrors Power Platform)
    # ------------------------------------------------------------------

    @classmethod
    def _load_settings(cls) -> dict:
        from app.models.models import APISettings
        row = APISettings.query.filter_by(
            provider=cls.PROVIDER, key_label=cls.KEY_LABEL
        ).first()
        if not row:
            return {}
        return {
            "instance_url": row.jira_url or "",
            "client_id": row.jira_email or "",
            "client_secret": row.api_key or "",
        }

    @classmethod
    def save_settings(cls, instance_url: str, client_id: str, client_secret: str) -> None:
        """Persist credentials. Raises BlockedOutboundURL for a rejected host.

        F-08: validating on save as well as on fetch means the administrator is
        told immediately, rather than the URL being stored and only silently
        refused later at discovery time.
        """
        validate_salesforce_instance_url(instance_url)
        from app.models.models import APISettings
        row = APISettings.query.filter_by(
            provider=cls.PROVIDER, key_label=cls.KEY_LABEL
        ).first()
        if not row:
            row = APISettings(provider=cls.PROVIDER, key_label=cls.KEY_LABEL)
            db.session.add(row)
        row.jira_url = instance_url.rstrip("/")
        row.jira_email = client_id
        if client_secret:
            row.api_key = client_secret
        db.session.commit()

    # ------------------------------------------------------------------
    # Token acquisition
    # ------------------------------------------------------------------

    @staticmethod
    def _get_token(instance_url: str, client_id: str, client_secret: str) -> "str | None":
        """OAuth2 client-credentials token. Returns None on failure (logged).

        F-08: ``instance_url`` arrives verbatim from the admin integration form
        and is concatenated into the request URL below, so before this fix an
        administrator could point the server at any host reachable from it,
        including link-local metadata endpoints, and read the outcome from the
        status code this method logs. ``validate_salesforce_instance_url``
        constrains the host to Salesforce's own suffixes AND requires every
        resolved address to be publicly routable. ``allow_redirects=False``
        closes the other half: a 302 from a permitted host into an internal
        range would otherwise be followed silently.
        """
        try:
            validate_salesforce_instance_url(instance_url)
        except BlockedOutboundURL as exc:
            logger.warning("Salesforce instance URL rejected: %s", exc)
            return None
        try:
            resp = requests.post(
                f"{instance_url.rstrip('/')}/services/oauth2/token",
                allow_redirects=False,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.error(
                    "Salesforce token acquisition failed (%s): %s",
                    resp.status_code, resp.text[:300],
                )
                return None
            return resp.json().get("access_token")
        except requests.RequestException as exc:
            logger.error("Salesforce token acquisition exception: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def test_connection(cls, instance_url: str, client_id: str, client_secret: str) -> dict:
        """Returns {"status": "ok", "org_id": ...} or {"status": "error", "message": ...}."""
        token = cls._get_token(instance_url, client_id, client_secret)
        if not token:
            return {
                "status": "error",
                "message": "Token acquisition failed — check Instance URL, Consumer Key, "
                           "and Consumer Secret (Connected App needs the Client Credentials flow enabled).",
            }
        try:
            resp = requests.get(
                f"{instance_url.rstrip('/')}/services/data/{cls.API_VERSION}/limits",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
                allow_redirects=False,  # F-08: never follow a hop off the allow-listed host
            )
            if resp.status_code == 200:
                return {"status": "ok", "instance_url": instance_url}
            return {"status": "error", "message": f"API returned {resp.status_code}"}
        except requests.RequestException as exc:
            return {"status": "error", "message": str(exc)}

    @classmethod
    def _query(cls, instance_url: str, token: str, soql: str, tooling: bool = False) -> list:
        """Run a SOQL query with pagination + 429 backoff. Returns records ([] on failure)."""
        # F-08: _query is reachable with a caller-supplied instance_url, so it
        # re-validates rather than trusting that _get_token already did.
        try:
            validate_salesforce_instance_url(instance_url)
        except BlockedOutboundURL as exc:
            logger.warning("Salesforce instance URL rejected in query: %s", exc)
            return []
        base = instance_url.rstrip("/")
        path = f"/services/data/{cls.API_VERSION}/{'tooling/' if tooling else ''}query"
        url = f"{base}{path}"
        params = {"q": soql}
        headers = {"Authorization": f"Bearer {token}"}
        records = []
        while url:
            for attempt in range(cls.MAX_RETRIES):
                try:
                    resp = requests.get(
                        url,
                        headers=headers,
                        params=params,
                        timeout=30,
                        allow_redirects=False,  # F-08
                    )
                    if resp.status_code == 429:
                        time.sleep(2 ** attempt)
                        continue
                    if resp.status_code != 200:
                        logger.warning(
                            "Salesforce query failed (%s) for %r: %s",
                            resp.status_code, soql[:60], resp.text[:200],
                        )
                        return records
                    data = resp.json()
                    records.extend(data.get("records", []))
                    next_url = data.get("nextRecordsUrl")
                    url = f"{base}{next_url}" if next_url else None
                    params = None
                    break
                except Exception as exc:
                    logger.error("Salesforce query error (attempt %d): %s", attempt + 1, exc)
                    if attempt == cls.MAX_RETRIES - 1:
                        return records
                    time.sleep(2 ** attempt)
        return records

    @classmethod
    def discover_apps(cls, instance_url: str, client_id: str, client_secret: str) -> list:
        """Return [{"id", "name", "display_name", "kind", "namespace", "owner_email"}].

        Never raises — partial results on per-query failure, [] on auth failure.
        """
        token = cls._get_token(instance_url, client_id, client_secret)
        if not token:
            return []

        results = []

        # 1. Lightning / custom apps (standard SOQL — AppDefinition)
        for rec in cls._query(
            instance_url, token,
            "SELECT DurableId, Label, DeveloperName, NamespacePrefix, Description "
            "FROM AppDefinition",
        ):
            results.append({
                "id": f"sf-app:{rec.get('DurableId')}",
                "name": rec.get("Label") or rec.get("DeveloperName") or "",
                "display_name": rec.get("Label") or "",
                "kind": "lightning_app",
                "namespace": rec.get("NamespacePrefix") or "",
                "description": rec.get("Description") or "",
                "owner_email": "",  # AppDefinition carries no owner
            })

        # 2. Installed AppExchange packages (Tooling API)
        for rec in cls._query(
            instance_url, token,
            "SELECT Id, SubscriberPackage.Name, SubscriberPackage.NamespacePrefix, "
            "SubscriberPackageVersion.Name "
            "FROM InstalledSubscriberPackage",
            tooling=True,
        ):
            pkg = rec.get("SubscriberPackage") or {}
            ver = rec.get("SubscriberPackageVersion") or {}
            results.append({
                "id": f"sf-pkg:{rec.get('Id')}",
                "name": pkg.get("Name") or "",
                "display_name": pkg.get("Name") or "",
                "kind": "installed_package",
                "namespace": pkg.get("NamespacePrefix") or "",
                "description": f"AppExchange package · version {ver.get('Name') or '?'}",
                "owner_email": "",
            })

        return results

    @classmethod
    def import_apps(
        cls,
        app_ids: list,
        discovered: list,
        user_id: int,
        programme_initiative_id: "int | None" = None,
    ) -> dict:
        """Create ApplicationComponent records; idempotent via source_identifier.

        When programme_initiative_id is given, imported (and re-synced) apps
        are linked into that programme's Current-State Baseline solution via
        ProgrammeGovernanceService.link_apps_to_baseline.
        Returns {"imported", "already_exists", "failed", "linked_to_programme"}.
        """
        from app.models.application_portfolio import ApplicationComponent
        from app.models.architecture_review_board import ARBAuditLog

        discovered_map = {a["id"]: a for a in discovered}
        imported = already_exists = failed = 0
        touched_app_ids = []

        for app_id in app_ids:
            sf_app = discovered_map.get(app_id)
            if not sf_app:
                failed += 1
                continue

            existing = ApplicationComponent.query.filter_by(
                source_identifier=app_id
            ).first()
            if existing:
                existing.provenance = {
                    "source": cls.PROVIDER,
                    "last_synced": datetime.utcnow().isoformat(),
                    **sf_app,
                }
                db.session.add(existing)
                touched_app_ids.append(existing.id)
                already_exists += 1
                continue

            try:
                app_rec = ApplicationComponent(
                    name=sf_app["display_name"] or sf_app["name"],
                    description=sf_app.get("description") or None,
                    application_type="saas",
                    vendor_name="Salesforce",
                    data_source=cls.PROVIDER,
                    source_identifier=app_id,
                    provenance={
                        "source": cls.PROVIDER,
                        "imported_at": datetime.utcnow().isoformat(),
                        **sf_app,
                    },
                    deployment_model="cloud",
                )
                db.session.add(app_rec)
                db.session.flush()
                touched_app_ids.append(app_rec.id)

                if not sf_app.get("owner_email"):
                    db.session.add(ARBAuditLog(
                        entity_type="Application",
                        entity_id=app_rec.id,
                        action="salesforce_import_ungoverned",
                        action_description=(
                            f"Salesforce {sf_app.get('kind', 'app')} '{app_rec.name}' "
                            "imported from org discovery — no owner in Entelim. "
                            "Requires assignment."
                        ),
                        user_id=user_id,
                        new_value={"source": cls.PROVIDER, "original_id": app_id},
                    ))
                imported += 1
            except Exception as exc:
                logger.error("Failed to import Salesforce app %s: %s", app_id, exc)
                db.session.rollback()
                failed += 1
                continue

        linked = 0
        if programme_initiative_id and touched_app_ids:
            try:
                from app.modules.solutions_strategic.v2.services.programme_governance_service import (
                    ProgrammeGovernanceService,
                )
                linked = ProgrammeGovernanceService.link_apps_to_baseline(
                    int(programme_initiative_id), touched_app_ids, user_id
                )
                # PROG-005: discovery imports capture a drift snapshot
                db.session.flush()
                ProgrammeGovernanceService.snapshot_programme(
                    int(programme_initiative_id), user_id, source="salesforce_import"
                )
            except Exception as exc:
                logger.error("Programme linkage/snapshot failed (apps still imported): %s", exc)

        try:
            db.session.commit()
        except Exception as exc:
            logger.error("Salesforce import commit failed: %s", exc)
            db.session.rollback()
            return {"imported": 0, "already_exists": already_exists,
                    "failed": len(app_ids), "linked_to_programme": 0}

        return {"imported": imported, "already_exists": already_exists,
                "failed": failed, "linked_to_programme": linked}
