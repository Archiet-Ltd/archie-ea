"""The platform-write guard's allow-list (lead review of PR 430, Part 1).

``app.middleware.tenant_isolation``'s ``_refuse_unscoped_platform_write`` (the
``before_flush`` listener installed alongside the existing tenant filters)
refuses every INSERT/UPDATE/DELETE an authenticated, non-platform-admin user
makes against a model that is NOT a ``TenantMixin`` subclass, unless that
model's table name is listed here with a reason.

Keyed by ``__tablename__`` rather than the class object: importing every
model eagerly from this file to build a dict of class objects risks import
cycles (models import middleware indirectly through ``app.extensions`` in a
few places), and a table name is a stable, unique handle the enumeration test
can read straight off ``mapper.local_table.name`` without importing the class
either.

Every entry's reason is mandatory and must say WHY an ordinary authenticated
user (not a platform admin) legitimately needs to write this table, not just
restate that it is exempt. ``tests/test_platform_write_guard_enumeration.py``
is the ratchet: a new non-``TenantMixin`` model with no entry here fails that
test until a reviewer makes an explicit decision — allow-list it with a
reason, or leave it refused.

Do NOT add an entry here as a way to make a test pass without having actually
decided the row above applies. Part 2/3 of the lead's brief fix the specific
routes named in the PR 430 review (vendors, VendorProduct, custom fields,
ArchiMate patterns/templates, ScoringConfiguration, AI persona prompts, system
settings, confidence thresholds) instead of exempting them here — none of
those tables appear below.
"""

from __future__ import annotations

# table_name -> reason a non-platform-admin authenticated user legitimately
# writes this table despite it carrying no organization_id tenant filter.
PLATFORM_WRITE_ALLOWLIST: dict[str, str] = {
    # ---------------------------------------------------------------
    # The user's own row. Profile edits (name, password, MFA enrolment,
    # onboarding fields, welcome-banner dismissal, SSO/session bookkeeping
    # columns the user's own login path updates) are the single most
    # frequent authenticated write in the product and must keep working for
    # every signed-in user, not only platform admins.
    #
    # LIMITATION (flagged per the brief): this guard is class-level, not
    # row-level. Allow-listing "users" exempts every write to ANY User row
    # from this guard, not only the acting user's own row -- it cannot by
    # itself stop a non-admin from flushing a change to *someone else's*
    # User row (a different user_id) if some other code path ever permitted
    # that object into the session. That narrower "own row only" guarantee
    # has to come from the route/service layer (current_user.id == target
    # user id checks on every profile-edit endpoint, and the separate
    # Role/Permission grant endpoints already requiring admin decorators),
    # the same way it does today without this guard. This entry only
    # restores the pre-guard status quo for ordinary profile self-service;
    # it does not newly enforce "own row only" where that wasn't already
    # enforced one layer up.
    "users": (
        "ordinary profile self-service (name, password, MFA enrolment, "
        "onboarding fields, welcome-banner dismissal) -- every signed-in "
        "user edits their own User row; row-scoping ('own row only') is "
        "enforced at the route layer, not by this class-level guard -- see "
        "the limitation noted above"
    ),

    # ---------------------------------------------------------------
    # Session / auth-token plumbing. Deliberately NOT TenantMixin (see each
    # model's own docstring) because it is read/written before
    # g.current_org_id is established (login) or across an org boundary the
    # acting user does not yet belong to (invitation accept).
    "user_sessions": (
        "server-side session registry written on every login/logout by "
        "every signed-in user, read before g.current_org_id is set for the "
        "request -- see app/models/user_session.py's own docstring on why "
        "it is deliberately not TenantMixin"
    ),
    "pending_invitations": (
        "an org admin invites a user into their OWN organisation (route-"
        "layer checks scope the invite to the inviting admin's "
        "organization_id); the invitee must also be able to see and accept "
        "it before they hold any role in the target org, i.e. before "
        "g.current_org_id could ever equal that org -- TenantMixin's filter "
        "would hide the invitation from the one person who needs to accept it"
    ),
    "org_roles": (
        "the role grant a user holds within one organisation -- written when "
        "an org admin assigns/changes a member's role in THEIR OWN org, or "
        "when an invitation is accepted; route-layer checks scope writes to "
        "the admin's own organization_id. Deliberately not TenantMixin: a "
        "role grant must be creatable for an org the acting user is only "
        "just joining (invitation accept), before g.current_org_id reflects "
        "that org"
    ),

    # ---------------------------------------------------------------
    # Per-organisation third-party connector / federation configuration.
    # One row per org, written by that org's own admin connecting their own
    # tools -- manually organization_id-scoped rather than TenantMixin for
    # the same "written before/around the request's own org context" reasons
    # as the identity plumbing above (SSO config is read during the login
    # redirect decision, before a session/org context exists yet).
    "sso_configs": (
        "per-organisation SAML/OIDC federation config, written by that "
        "org's own admin for their own org; read during the pre-login IdP "
        "redirect decision, before the request has any org/session context "
        "to filter on"
    ),
    "org_connector_configs": (
        "per-organisation third-party connector credentials (ServiceNow, "
        "Jira, M365), written by that org's own admin connecting their own "
        "org's tools; route-layer checks scope writes to the admin's own "
        "organization_id"
    ),
    "devops_connector_configs": (
        "per-organisation GitHub/Azure DevOps connector credentials, written "
        "by that org's own admin for their own org; same pattern as "
        "org_connector_configs above"
    ),
    "lucidchart_connector_configs": (
        "per-organisation Lucidchart OAuth connector config, written by "
        "that org's own admin for their own org; same pattern as "
        "org_connector_configs above"
    ),
    "sync_logs": (
        "a connector sync-run log row -- reached only through its parent "
        "ConnectorConfig (app/models/connector_config.py:ConnectorConfig), "
        "which IS a TenantMixin model and already fenced by the existing "
        "tenant filter; the log row itself carries no organization_id "
        "because it is a child of that fenced parent, not an independent "
        "global resource"
    ),

    # ---------------------------------------------------------------
    # Audit trails. Append-only by design (see each model's own docstring);
    # written automatically as a side effect of an ordinary, already-
    # authorised action elsewhere in the request, by every authenticated
    # user regardless of role. Blocking these would not stop an attacker
    # from acting -- it would stop the record of normal, legitimate actions
    # from being kept at all.
    "soc2_audit_log": (
        "SOC 2 append-only audit log, written automatically on every "
        "controlled-entity mutation across the whole product by whichever "
        "authenticated user performed that (separately authorised) action; "
        "see app/models/audit_log.py's own docstring"
    ),
    "ai_audit_logs": (
        "immutable log of every AI/LLM invocation, written automatically "
        "whenever any signed-in user uses an AI feature -- most of the "
        "product's AI surface is used by ordinary, non-platform-admin users"
    ),
    "ai_chat_audit_logs": (
        "comprehensive audit trail for AI Chat operations, written "
        "automatically on every chat message/CRUD/approval event for every "
        "signed-in user of the AI chat feature"
    ),
    "ai_chat_approval_audit_log": (
        "immutable transition log for an AIChatCRUDApproval -- reached only "
        "through its parent AIChatCRUDApproval row "
        "(app/models/ai_chat_crud_approval.py), which IS a TenantMixin model "
        "and already fenced; this log is a child of that fenced parent"
    ),

    # ---------------------------------------------------------------
    # Analytics / telemetry. Written as an automatic side effect of normal
    # product usage (and in PublicVisitorEvent's case, of visiting public
    # marketing pages, cookielessly, by visitors who may or may not be
    # signed in) -- never themselves the resource an attacker would be
    # targeting, and blocking them would silently blind the product's own
    # usage metering/billing and analytics rather than stop any real misuse.
    "usage_events": (
        "per-event metering record for billing/seat enforcement, written "
        "automatically on every significant action (solution created, AI "
        "query, codegen run, login, ...) by every signed-in user"
    ),
    "usage_analytics": (
        "page-view/feature-interaction/error telemetry for product "
        "usage, written automatically for every signed-in user's ordinary "
        "navigation of the product"
    ),
    "public_visitor_events": (
        "cookieless first-party analytics for the public marketing pages "
        "(app/middleware/public_analytics_middleware.py); reachable by a "
        "signed-in user browsing the public site in the same session, not "
        "only anonymous visitors"
    ),

    # ---------------------------------------------------------------
    # Public, pre-signup marketing forms. No organisation exists yet for the
    # submitter in the common case (anonymous visitor), but neither form is
    # restricted to anonymous callers -- a signed-in user can still reach
    # and submit them from the public marketing pages.
    "waitlist_signups": (
        "public homepage waitlist signup form (app/main/views.py); reachable "
        "by a signed-in user browsing the public marketing homepage, not "
        "only anonymous visitors, and carries no organisation data at all"
    ),
    "product_inquiries": (
        "public '/offers/inquire' sales-contact form (app/main/views.py); "
        "reachable by a signed-in user browsing the public offers pages, "
        "not only anonymous visitors, and carries no organisation data at all"
    ),
}


def reason_for(table_name: str) -> str | None:
    """The allow-list reason for ``table_name``, or ``None`` if not listed."""
    return PLATFORM_WRITE_ALLOWLIST.get(table_name)
