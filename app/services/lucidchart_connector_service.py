"""Lucidchart OAuth and document access service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from app import db
from app.models.connector_config import LucidchartConnectorConfig


class LucidchartConnectorError(RuntimeError):
    """Raised when Lucidchart connector operations cannot complete."""


class LucidchartConnectorService:
    """Encapsulate Lucidchart OAuth and document API calls."""

    AUTHORIZE_URL = "https://lucid.app/oauth2/authorize"
    TOKEN_URL = "https://api.lucid.co/oauth2/token"
    ROOT_CONTENTS_URL = "https://api.lucid.co/folders/root/contents"
    DOCUMENT_SEARCH_URL = "https://api.lucid.co/documents/search"
    DOCUMENT_CONTENTS_URL = "https://api.lucid.co/documents/{document_id}/contents"
    API_VERSION = "1"
    DEFAULT_SCOPES = ["documents:read", "folders:read", "offline_access"]
    TOKEN_REFRESH_SKEW_SECONDS = 30

    def get_config(self, organization_id: int) -> Optional[LucidchartConnectorConfig]:
        return LucidchartConnectorConfig.query.filter_by(
            organization_id=organization_id
        ).one_or_none()

    def get_or_create_config(self, organization_id: int) -> LucidchartConnectorConfig:
        config = self.get_config(organization_id)
        if config is not None:
            return config
        config = LucidchartConnectorConfig(organization_id=organization_id)
        db.session.add(config)
        db.session.flush()
        return config

    def build_authorization_url(
        self,
        config: LucidchartConnectorConfig,
        redirect_uri: str,
        state: str,
        scopes: Optional[List[str]] = None,
    ) -> str:
        if not config.client_id:
            raise LucidchartConnectorError("Lucidchart client_id is not configured.")

        requested_scopes = scopes or self.DEFAULT_SCOPES
        query = urlencode(
            {
                "response_type": "code",
                "client_id": config.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(requested_scopes),
                "state": state,
            }
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    def exchange_code_for_tokens(
        self,
        config: LucidchartConnectorConfig,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        self._require_client_credentials(config)
        try:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                },
                headers={"Accept": "application/json"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise LucidchartConnectorError(
                "Lucidchart token exchange request failed."
            ) from exc
        token_payload = self._parse_response(response, "Lucidchart token exchange")
        self._store_token_payload(config, token_payload)
        return token_payload

    def refresh_access_token(
        self,
        config: LucidchartConnectorConfig,
    ) -> Dict[str, Any]:
        self._require_client_credentials(config)
        refresh_token = self.get_refresh_token(config)
        if not refresh_token:
            raise LucidchartConnectorError(
                "Lucidchart refresh_token is not configured."
            )

        try:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                },
                headers={"Accept": "application/json"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise LucidchartConnectorError(
                "Lucidchart token refresh request failed."
            ) from exc
        token_payload = self._parse_response(response, "Lucidchart token refresh")
        self._store_token_payload(config, token_payload)
        return token_payload

    def get_access_token(self, config: LucidchartConnectorConfig) -> Optional[str]:
        """Return the organisation's stored Lucidchart access token, or None.

        Reads from ``OrgConnectorCredential`` via ``OrgCredentialVault`` —
        the retired ``LucidchartConnectorConfig.access_token`` column is no
        longer written to.
        """
        return self._retrieve_credential(config, "access_token")

    def get_refresh_token(self, config: LucidchartConnectorConfig) -> Optional[str]:
        """Return the organisation's stored Lucidchart refresh token, or None."""
        return self._retrieve_credential(config, "refresh_token")

    def _retrieve_credential(
        self, config: LucidchartConnectorConfig, credential_type: str
    ) -> Optional[str]:
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        return OrgCredentialVault().retrieve(
            org_id=config.organization_id,
            connector_type="lucidchart",
            credential_type=credential_type,
        )

    def ensure_access_token(self, config: LucidchartConnectorConfig) -> str:
        access_token = self.get_access_token(config)
        if not access_token:
            raise LucidchartConnectorError("Lucidchart access_token is not configured.")

        if config.token_expires_at is None or (
            config.token_expires_at
            <= datetime.utcnow() + timedelta(seconds=self.TOKEN_REFRESH_SKEW_SECONDS)
        ):
            self.refresh_access_token(config)
            access_token = self.get_access_token(config)

        if not access_token:
            raise LucidchartConnectorError("Lucidchart access_token refresh failed.")
        return access_token

    def list_documents(
        self,
        config: LucidchartConnectorConfig,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        access_token = self.ensure_access_token(config)
        headers = self._build_headers(access_token)

        if query:
            try:
                response = requests.post(
                    self.DOCUMENT_SEARCH_URL,
                    json={"query": query},
                    headers=headers,
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise LucidchartConnectorError(
                    "Lucidchart document search request failed."
                ) from exc
            payload = self._parse_response(response, "Lucidchart document search")
            return payload.get("documents") or payload.get("results") or []

        try:
            response = requests.get(
                self.ROOT_CONTENTS_URL,
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise LucidchartConnectorError(
                "Lucidchart root contents request failed."
            ) from exc
        payload = self._parse_response(response, "Lucidchart root contents")
        return payload.get("items") or payload.get("documents") or payload.get("results") or []

    def get_document_contents(
        self,
        config: LucidchartConnectorConfig,
        document_id: str,
    ) -> Dict[str, Any]:
        if not document_id:
            raise LucidchartConnectorError("document_id is required.")

        access_token = self.ensure_access_token(config)
        try:
            response = requests.get(
                self.DOCUMENT_CONTENTS_URL.format(document_id=document_id),
                headers=self._build_headers(access_token),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise LucidchartConnectorError(
                "Lucidchart document contents request failed."
            ) from exc
        return self._parse_response(response, "Lucidchart document contents")

    def _store_token_payload(
        self,
        config: LucidchartConnectorConfig,
        token_payload: Dict[str, Any],
    ) -> None:
        """Persist the OAuth token response.

        The access and refresh tokens are secrets, so they go through
        ``OrgCredentialVault`` into ``OrgConnectorCredential`` — encrypted
        with this organisation's own key — rather than onto the retired
        ``LucidchartConnectorConfig`` columns. Everything else here
        (expiry, scope, account id, enabled) is non-secret and stays on the
        config row where it lives today.
        """
        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token")
        expires_in = token_payload.get("expires_in")

        if access_token or refresh_token:
            from app.modules.codegen.services.credential_vault import OrgCredentialVault

            vault = OrgCredentialVault()
            if access_token:
                vault.store(
                    org_id=config.organization_id,
                    connector_type="lucidchart",
                    credential_type="access_token",
                    value=access_token,
                )
            if refresh_token:
                vault.store(
                    org_id=config.organization_id,
                    connector_type="lucidchart",
                    credential_type="refresh_token",
                    value=refresh_token,
                )
        if expires_in is not None:
            config.token_expires_at = datetime.utcnow() + timedelta(
                seconds=int(expires_in)
            )
        if token_payload.get("scope"):
            config.scope = token_payload["scope"]
        account_id = token_payload.get("account_id") or token_payload.get("accountId")
        if account_id:
            config.lucid_account_id = account_id
        config.enabled = True
        db.session.add(config)
        db.session.commit()

    def _require_client_credentials(self, config: LucidchartConnectorConfig) -> None:
        if not config.client_id:
            raise LucidchartConnectorError("Lucidchart client_id is not configured.")
        if not config.client_secret:
            raise LucidchartConnectorError(
                "Lucidchart client_secret is not configured."
            )

    def _build_headers(self, access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Lucid-Api-Version": self.API_VERSION,
        }

    def _parse_response(
        self,
        response: requests.Response,
        operation: str,
    ) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise LucidchartConnectorError(
                f"{operation} returned a non-JSON response."
            ) from exc

        if response.status_code >= 400:
            message = payload.get("error_description") or payload.get("error") or str(
                payload
            )
            raise LucidchartConnectorError(f"{operation} failed: {message}")

        return payload
