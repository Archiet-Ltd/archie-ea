"""SSO provider list and IdP group-to-role mapping (COM-005 / ENT-068).

This module no longer runs any part of the sign-in flow. The platform's
OIDC sign-in goes through the account OAuth client (authlib's Flask
``OAuth`` registry, one provider per process, configured from
``SSO_PROVIDERS``); the per-organisation SSO flow (one config row per
organisation, chosen by the signing-in user's email domain) goes through
:class:`app.services.sso_service.SSOService`. What stays here is the
``SSO_ENABLED`` / provider-availability check the sign-in page uses to show
or hide its SSO buttons, and the IdP group to role mapping the callback
handlers use once a user is authenticated: group-to-role mapping translates
IdP group memberships into the platform's ``enterprise_role`` field
(ENT-068).
"""

import logging

logger = logging.getLogger(__name__)

# ── Group-to-role mapping ────────────────────────────────────────────
# Keys are IdP group *display names*; values are platform enterprise_role
# values defined in app.models.user.VALID_ROLES.
DEFAULT_GROUP_ROLE_MAP = {
    "EA-Architects": "enterprise_architect",
    "Business-Architects": "business_architect",
    "Solution-Architects": "solution_architect",
    "ARB-Members": "arb_member",
    "Portfolio-Managers": "portfolio_manager",
    "Platform-Admins": "platform_admin",
    # These three shipped with sidebars, permissions and AI charters and no way
    # to be provisioned: an SSO-only customer had no group that maps to them.
    "CTO": "cto",
    "Procurement": "procurement",
    "Application-Managers": "application_manager",
    "Security-Architects": "security_architect",
    "Data-Architects": "data_architect",
    "Technology-Architects": "technology_architect",
    "Application-Architects": "application_architect",
    "Business-Owners": "business_owner",
    # R1-B36 (TB-0146): promoted from unassignable to assignable, 2026-10-04.
    "Finance": "finance",
    "Compliance": "compliance",
    "Risk": "risk",
    "Operations": "operations",
    "Non-Technical-Owners": "non_technical_owner",
}


class SSOError(Exception):
    """Raised when an SSO operation fails."""


class SSOService:
    """Provider availability and group-to-role mapping for SSO sign-in."""

    def __init__(self):
        self.enabled = False
        self.providers = {}
        self._group_role_map = dict(DEFAULT_GROUP_ROLE_MAP)

    # ── Initialization ───────────────────────────────────────────────

    def init_app(self, app):
        """Read SSO configuration from *app*.config and store provider metadata.

        Called once at app startup.  If ``SSO_ENABLED`` is falsy **and** the
        ``sso_authentication`` FeatureFlag is absent/disabled, the service
        stays dormant and all public methods short-circuit.
        """
        self.enabled = app.config.get("SSO_ENABLED", False)

        # Allow the DB-driven FeatureFlag to override config when available.
        if not self.enabled:
            try:
                from app.models.feature_flags import FeatureFlag

                flag = FeatureFlag.query.filter_by(key="sso_authentication").first()
                if flag and flag.is_active:
                    self.enabled = True
            except Exception as e:
                logger.debug("SSO feature flag check failed (DB not ready?): %s", e)

        if not self.enabled:
            logger.info("SSO disabled — skipping provider configuration.")
            return

        sso_cfg = app.config.get("SSO_PROVIDERS", {})

        # Azure AD
        azure = sso_cfg.get("azure", {})
        if azure.get("client_id"):
            self.providers["azure"] = {
                "client_id": azure["client_id"],
                "client_secret": azure["client_secret"],
                "metadata_url": azure.get("server_metadata_url", ""),
                "scope": azure.get("client_kwargs", {}).get("scope", "openid email profile"),
                "name": "Microsoft",
            }
            logger.info("SSO provider configured: Azure AD")

        # Okta
        okta = sso_cfg.get("okta", {})
        if okta.get("client_id"):
            self.providers["okta"] = {
                "client_id": okta["client_id"],
                "client_secret": okta["client_secret"],
                "metadata_url": okta.get("server_metadata_url", ""),
                "scope": okta.get("client_kwargs", {}).get("scope", "openid email profile"),
                "name": "Okta",
            }
            logger.info("SSO provider configured: Okta")

        # Custom group→role map from config (optional override)
        custom_map = app.config.get("SSO_GROUP_ROLE_MAP")
        if custom_map and isinstance(custom_map, dict):
            self._group_role_map = custom_map

    # ── Provider availability ────────────────────────────────────────

    def is_enabled(self):
        """Return True when SSO is active and at least one provider is configured."""
        return self.enabled and bool(self.providers)

    def available_providers(self):
        """Return list of configured provider keys (e.g. ``['azure', 'okta']``)."""
        return list(self.providers.keys())

    # ── Group-to-role mapping ────────────────────────────────────────

    def _load_db_group_role_map(self, organization_id):
        """Load active SSO group-to-role mappings from the database, for one org.

        Returns a dict of {group_name: role_name} for that org's active rows.
        Falls back to an empty dict if the table is not yet available.

        This runs during the SSO callback, before request-scoped tenant context
        (g.current_org_id) exists -- TenantMixin's automatic do_orm_execute
        filter is a no-op here (see app/middleware/tenant_isolation.py), so the
        organization_id predicate below is the only thing scoping this query.
        Previously this had no filter at all: any org's IdP group names could
        match a mapping created by an entirely different org, a real
        cross-tenant privilege-confusion risk at login time.
        """
        try:
            from app.models.miscellaneous import SSOGroupRoleMapping

            rows = SSOGroupRoleMapping.query.filter_by(
                is_active=True, organization_id=organization_id
            ).all()
            return {r.sso_group_name: r.role_name for r in rows}
        except Exception as exc:
            logger.debug("Could not load SSO mappings from DB (table ready?): %s", exc)
            return {}

    def map_groups_to_role(self, groups, organization_id):
        """Map a list of IdP group names to a single platform enterprise_role.

        Checks database mappings first (PLT-033); falls back to the in-memory
        config map (``_group_role_map``) populated from DEFAULT_GROUP_ROLE_MAP /
        SSO_GROUP_ROLE_MAP config.  If multiple groups match, the
        highest-privilege role wins (platform_admin > enterprise_architect >
        arb_member > portfolio_manager > solution_architect).

        ``organization_id`` scopes the DB-mapping lookup to the user's own org
        -- see `_load_db_group_role_map`'s docstring for why this can't rely on
        the ordinary automatic tenant filter.

        Returns the role string, or ``None`` if no groups match.
        """
        if not groups:
            return None

        # Priority order (highest first)
        priority = [
            "platform_admin",
            "enterprise_architect",
            "arb_member",
            "portfolio_manager",
            "solution_architect",
        ]

        # DB mappings take precedence; fall back to config map when DB is empty.
        db_map = self._load_db_group_role_map(organization_id)
        effective_map = self._group_role_map.copy()
        if db_map:
            effective_map = db_map  # DB fully overrides config when rows exist

        matched_roles = set()
        for group in groups:
            role = effective_map.get(group)
            if role:
                matched_roles.add(role)

        if not matched_roles:
            return None

        # Return highest-priority matched role
        for role in priority:
            if role in matched_roles:
                return role

        return matched_roles.pop()


# Module-level singleton — initialized via init_app() at startup
sso_service = SSOService()
