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
        "the limitation noted above. CORRECTION (D-05, lead review v2, "
        "2026-10-08): that route-layer row-scoping claim did not hold for "
        "the v1 admin blueprint's user-management routes (change-email, "
        "set-password and others) before PR 430 round 3 -- they judged "
        "authority as 'admin of ANY organisation' "
        "(admin_required/Permission.ADMINISTER, tied to the user's home "
        "organisation) rather than authority in the organisation the target "
        "user actually belongs to, so a user who was Administrator of their "
        "own org but only a Viewer in another could switch their active "
        "session to that org and still change another org's user's email or "
        "password. Those routes now check "
        "rbac_service.is_org_admin(current_user, g.current_org_id) instead "
        "(app/modules/admin/routes/admin_routes.py's "
        "_active_org_admin_required); this allow-list entry's own 'own row "
        "only' scope was never the part that was wrong"
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
    "roles": (
        "global reference data, not tenant-owned at all (app/models/user.py: "
        "Role) -- a small fixed catalogue (User/Architect/Administrator/"
        "Viewer/Approver) seeded once by Role.insert_roles() and shared by "
        "every organisation. An ordinary role assignment (an org admin "
        "inviting a teammate, or admin-add-user picking a role) does "
        "`user.role = some_role`, which SQLAlchemy records as a dirty Role "
        "row via the back-populated `Role.users` relationship even though no "
        "column on Role itself changes -- the write is really to the "
        "User/OrgRole side, already gated by its own route-level check "
        "(PR 430 round 3, lead review v2, 2026-10-08: this backref-only "
        "dirty was the specific cause of every invite/admin-add-user "
        "regression the review's write-path test batch found)"
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
        "to filter on. NOTE (D-06, lead review v2, 2026-10-08): the reason "
        "above is only about the WRITE this guard controls. On the READ "
        "side, sso_service.get_config_for_email() resolves email_domain by "
        "matching across every organisation's row, not only the caller's "
        "own -- a separate, pre-existing cross-tenant issue (one org can "
        "claim another org's email domain) that this allow-list entry does "
        "not fix and is out of scope here; see the dedicated brief for it"
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
    "saved_diagram_elements": (
        "element position within a saved composer diagram "
        "(app/models/archimate_core.py:SavedDiagramElement), reached only "
        "through its parent SavedDiagram, which IS TenantMixin and already "
        "fenced; this row carries no organization_id of its own because it "
        "is a child of that fenced parent, not an independent resource -- "
        "written on every ordinary composer save"
    ),
    "application_interface_metadata": (
        "extended technical metadata for one ArchiMate ApplicationInterface "
        "element (app/models/integration_metadata.py), reached only through "
        "its parent ArchiMateElement (archimate_element_id, unique, "
        "non-nullable FK), which IS TenantMixin and already fenced; written "
        "on an ordinary user's own Interface Register entry"
    ),
    "system_dependencies": (
        "a dependency edge between two ArchiMate elements "
        "(app/models/integration_metadata.py:SystemDependency), reached only "
        "through its source/target/interface ArchiMateElement foreign keys, "
        "all of which ARE TenantMixin and already fenced; written on an "
        "ordinary user's own Interface Register / dependency-mapping action"
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
    "import_audit_logs": (
        "audit trail for the Import Applications workflow "
        "(app/models/batch_import.py:ImportAuditLog), keyed by user_id with "
        "no organisation column at all -- same append-only, written-as-a-"
        "side-effect-of-an-authorised-action pattern as the other entries in "
        "this block; written automatically on an ordinary user's own import "
        "and import-restore actions"
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
    "audit_events": (
        "immutable, cryptographically hash-chained security-event log "
        "(app/security/audit.py: AuditEvent), the same append-only, "
        "written-as-a-side-effect-of-an-authorised-action pattern as "
        "soc2_audit_log/ai_audit_logs above -- written automatically on "
        "every signed-in user's own security-relevant actions (confirmed by "
        "running tests/smoke/test_archetype_journeys.py on this branch, PR "
        "430 round 3, 2026-10-08: an ordinary login wrote one of these and "
        "was refused, with the log line \"Failed to log audit event: "
        "non-platform-admin insert refused on unscoped table audit_events\" "
        "-- the guard was silently discarding this tamper-evident security "
        "log's own entries for every non-platform-admin action, which is "
        "the opposite of what a security log is for)"
    ),

    # ---------------------------------------------------------------
    # The user's own notification/preference row and billing's one-row-per-
    # organisation subscription record.
    "notifications": (
        "the user's own in-app notification (header bell icon), keyed only "
        "by user_id with no organisation column at all -- every signed-in "
        "user creates and marks-read only their own rows "
        "(app/_bootstrap/routes.py scopes every query to current_user.id); "
        "see app.models.models.Notification"
    ),
    "subscriptions": (
        "one billing row per organisation (app/models/subscription.py: "
        "Subscription) -- carries a non-nullable, unique organization_id FK "
        "but is not TenantMixin; app.services.billing_plans.ensure_subscription "
        "writes/updates this row on an ordinary org admin's own checkout, "
        "plan-change and cancellation actions for their own organisation. "
        "This table arguably should be TenantMixin and simply predates the "
        "mixin's introduction -- flagged for the registry follow-up (D-07) "
        "rather than converted here"
    ),

    # ---------------------------------------------------------------
    # Duplicate/similarity-detection and consolidation-reporting tables.
    # Every one of these is already listed in scripts/unfenced_tables.txt --
    # an earlier, separate, already-recorded decision that this entry does
    # not revisit -- and each is written as a normal side effect of an
    # ordinary user's own "run duplicate detection" action within their own
    # session, not an admin action (one of D-01's named broken flows: an
    # ordinary detection run was refused here before these entries existed).
    "duplicate_detection_runs": (
        "execution record for a duplicate-detection run "
        "(app/models/application_duplicate_detection.py:DuplicateDetectionRun); "
        "already listed in scripts/unfenced_tables.txt; written whenever an "
        "ordinary user runs duplicate detection for their own session's data"
    ),
    "duplicate_groups": (
        "a group of applications a detection run found similar "
        "(app/models/application_duplicate_detection.py:DuplicateGroup), "
        "child of DuplicateDetectionRun above; already listed in "
        "scripts/unfenced_tables.txt"
    ),
    "duplicate_analyses": (
        "the detailed similarity analysis for one duplicate group "
        "(app/models/application_duplicate_detection.py:DuplicateAnalysis); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "duplicate_app_process_mapping": (
        "process-mapping input to duplicate detection "
        "(app/models/application_duplicate_detection.py:ApplicationProcessMapping); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "consolidation_recommendations": (
        "a recommendation produced from a duplicate group "
        "(app/models/application_duplicate_detection.py:ConsolidationRecommendation); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "simple_duplicate_groups": (
        "the simplified-detection-mode equivalent of duplicate_groups "
        "(app/models/simple_duplicate_detection.py:SimpleDuplicateGroup); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "simple_detection_runs": (
        "the simplified-detection-mode equivalent of "
        "duplicate_detection_runs (app/models/simple_duplicate_detection.py:"
        "SimpleDetectionRun); already listed in scripts/unfenced_tables.txt"
    ),
    "unified_detection_runs": (
        "the current, consolidated detection-run record "
        "(app/models/unified_duplicate_detection.py:UnifiedDetectionRun), "
        "superseding the two modes above; already listed in "
        "scripts/unfenced_tables.txt"
    ),
    "unified_duplicate_groups": (
        "the current, consolidated duplicate-group record "
        "(app/models/unified_duplicate_detection.py:UnifiedDuplicateGroup); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "detection_schedules": (
        "a recurring-schedule configuration for duplicate detection "
        "(app/models/unified_duplicate_detection.py:DetectionSchedule); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "application_similarity_analysis": (
        "pairwise similarity scoring input to the consolidation workflow "
        "(app/models/application_consolidation.py:ApplicationSimilarityAnalysis); "
        "already listed in scripts/unfenced_tables.txt"
    ),
    "application_duplication_reports": (
        "a generated report summarising a consolidation analysis "
        "(app/models/application_consolidation.py:ApplicationDuplicationReport); "
        "already listed in scripts/unfenced_tables.txt"
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

    # ---------------------------------------------------------------
    # Operational telemetry and a share mechanism deliberately built to work
    # outside any tenant/session context.
    "error_events": (
        "aggregated server + client error telemetry (app/models/error_event.py), "
        "deliberately not tenant-scoped by the model's own design: an error is "
        "an operational fact about the platform, not the organisation's data, "
        "and organization_id/user_id are kept as plain nullable attribution "
        "columns rather than a filter -- read access is already restricted to "
        "platform admins at the route layer. An ordinary user's own session "
        "writes this table every time the client-error beacon reports a JS "
        "exception, or a server-side error occurs while they are signed in"
    ),
    "artefact_share_links": (
        "a revocable, single-organisation share token (app/models/artefact_share.py), "
        "deliberately not TenantMixin per the model's own docstring: the "
        "token must resolve from an unauthenticated request, where "
        "g.current_org_id is None and the automatic tenant filter is a "
        "documented no-op, so the mixin would misleadingly suggest a "
        "protection this path cannot use. organization_id is an explicit, "
        "mandatory, non-nullable column instead, and every owner-side route "
        "filters on it by hand. An ordinary org member creates one of these "
        "to share their own organisation's capability map/heatmap/roadmap"
    ),

    # ---------------------------------------------------------------
    # Confirmed by running tests/smoke/test_archetype_journeys.py against
    # this branch (PR 430 round 3, 2026-10-08): the enforcing guard refused
    # this exact table during an ordinary user's own in-app action, which is
    # one of this review round's 5 failing Level 10 journeys
    # (test_operations_subscribes_to_service_status_and_it_persists --
    # server log: "platform-write-guard: refused insert on UserPreference
    # (table=user_preferences)", then "POST /status/subscription" 403).
    "user_preferences": (
        "the user's own platform-behaviour settings (entry mode, AI-"
        "suggestion toggles, and -- per this table's reuse by the service-"
        "status feature -- the signed-in user's own status-page "
        "subscription), keyed only by user_id (unique, FK to users.id) with "
        "no organisation column at all; see app/models/ai_suggestion.py: "
        "UserPreference. Every signed-in user reads and writes only their "
        "own row (unique constraint on user_id makes a second row for the "
        "same user impossible); already treated as an owned, deletable-with-"
        "the-user row elsewhere in this codebase (admin_routes.py's "
        "api_bulk_delete_users _DELETE_OWNED list)"
    ),
}


def reason_for(table_name: str) -> str | None:
    """The allow-list reason for ``table_name``, or ``None`` if not listed."""
    return PLATFORM_WRITE_ALLOWLIST.get(table_name)
