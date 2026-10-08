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
    # "roles" REMOVED from this allow-list (PR 430 round 4, closes D-01/D-02):
    # it was listed here only because `user.role = some_role` made SQLAlchemy
    # report the Role row as dirty via the back-populated `Role.users`
    # collection, even though no column on Role itself ever changed -- a
    # false positive, not a real write to global reference data. That root
    # cause is now fixed directly in app/middleware/tenant_isolation.py (the
    # guard checks session.is_modified(obj, include_collections=False) for
    # a dirty object, which is False for a collection-only/backref-only
    # change), so the false positive this entry worked around no longer
    # happens -- see test_platform_write_guard_backref_dirty.py for the
    # before/after proof. "roles" is genuine GLOBAL reference data (a small
    # fixed catalogue -- User/Architect/Administrator/Viewer/Approver --
    # seeded once by Role.insert_roles() and shared by every organisation,
    # per the mechanical classification pass: no organization_id column, no
    # FK chain to a tenant-owned table): a *direct* write to a Role row by a
    # non-platform-admin is correctly refused now, matching DEF-3's finding
    # that only a platform admin should create/update/delete roles.
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
        "action (app/modules/applications/routes/import_export_routes.py, "
        "app/modules/import_batch/services/import_audit_service.py). "
        "CORRECTED (D-07, PR 430 round 4): import-RESTORE specifically does "
        "not write this table -- it writes the separate, singular "
        "'import_audit_log' table (app/models/import_audit.py:"
        "ImportSessionLog, app/services/import_restore_service.py), which "
        "this round 3 reason wrongly described as the same table (DEF-4, "
        "review-pr430-v3.md); see the 'import_audit_log' entry below"
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
    #
    # CORRECTED (D-07, PR 430 round 4): the mechanical FK-chain classification
    # pass (build-report-pr430-round4-v1.md) found every table in this block
    # has NO organization_id column and NO foreign-key chain to any
    # tenant-owned table -- they are schema-level global, not "their own
    # session's data" as the round-3 reasons previously claimed. That wording
    # was false (DEF-7, review-pr430-v3.md): every organisation shares one
    # global pool of detection runs/groups, which is exactly the DEF-2
    # vulnerability (any signed-in user's "run detection" deletes every other
    # organisation's groups and members) -- a route/schema-level scoping gap
    # tracked and fixed in the route-fix split PR, not here.
    #
    # Kept on this allow-list regardless, deliberately: removing the entries
    # would make the structural guard refuse the ordinary "run duplicate
    # detection" action for every tenant right now (the exact regression
    # round 2 already hit once), while DEF-2's real fix -- adding
    # organization_id and scoping the routes -- is a schema change for the
    # split PR to make, not a reason to break the feature in the meantime.
    "duplicate_detection_runs": (
        "execution record for a duplicate-detection run "
        "(app/models/application_duplicate_detection.py:DuplicateDetectionRun) "
        "-- NOT organisation-scoped at the schema level (see the correction "
        "above); kept allow-listed so the feature keeps working while DEF-2 "
        "is fixed in the route-fix split PR"
    ),
    "duplicate_groups": (
        "a group of applications a detection run found similar "
        "(app/models/application_duplicate_detection.py:DuplicateGroup), "
        "child of DuplicateDetectionRun above -- same correction and same "
        "reason for staying allow-listed"
    ),
    "duplicate_analyses": (
        "the detailed similarity analysis for one duplicate group "
        "(app/models/application_duplicate_detection.py:DuplicateAnalysis) "
        "-- same correction and same reason for staying allow-listed"
    ),
    "duplicate_app_process_mapping": (
        "process-mapping input to duplicate detection "
        "(app/models/application_duplicate_detection.py:ApplicationProcessMapping) "
        "-- same correction and same reason for staying allow-listed"
    ),
    "consolidation_recommendations": (
        "a recommendation produced from a duplicate group "
        "(app/models/application_duplicate_detection.py:ConsolidationRecommendation) "
        "-- same correction and same reason for staying allow-listed"
    ),
    "simple_duplicate_groups": (
        "the simplified-detection-mode equivalent of duplicate_groups "
        "(app/models/simple_duplicate_detection.py:SimpleDuplicateGroup) -- "
        "same correction and same reason for staying allow-listed"
    ),
    "simple_detection_runs": (
        "the simplified-detection-mode equivalent of "
        "duplicate_detection_runs (app/models/simple_duplicate_detection.py:"
        "SimpleDetectionRun) -- same correction and same reason for staying "
        "allow-listed"
    ),
    "unified_detection_runs": (
        "the current, consolidated detection-run record "
        "(app/models/unified_duplicate_detection.py:UnifiedDetectionRun), "
        "superseding the two modes above -- same correction and same reason "
        "for staying allow-listed"
    ),
    "unified_duplicate_groups": (
        "the current, consolidated duplicate-group record "
        "(app/models/unified_duplicate_detection.py:UnifiedDuplicateGroup) -- "
        "same correction and same reason for staying allow-listed"
    ),
    "detection_schedules": (
        "a recurring-schedule configuration for duplicate detection "
        "(app/models/unified_duplicate_detection.py:DetectionSchedule) -- "
        "same correction and same reason for staying allow-listed"
    ),
    "application_similarity_analysis": (
        "pairwise similarity scoring input to the consolidation workflow "
        "(app/models/application_consolidation.py:ApplicationSimilarityAnalysis) "
        "-- same correction and same reason for staying allow-listed"
    ),
    "application_duplication_reports": (
        "a generated report summarising a consolidation analysis "
        "(app/models/application_consolidation.py:ApplicationDuplicationReport) "
        "-- same correction and same reason for staying allow-listed"
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


    # ---------------------------------------------------------------
    # PR 430 round 4 (D-01/D-02 follow-up): every remaining table in
    # scripts/unfenced_tables.txt, classified mechanically by
    # scripts/classify_unfenced_tables.py against the live SQLAlchemy
    # metadata -- not guessed. Each reason names the real column or FK chain
    # the classifier found (one hop maximum): either the table's own
    # organization_id column, a direct foreign key to a tenant-owned table,
    # or a foreign key to an intermediate table that itself is tenant-owned.
    # A table the classifier could not connect to any tenant-owned table
    # within one hop is GLOBAL CATALOGUE instead and stays off this list --
    # see build-report-pr430-round4-v1.md for the complete classification
    # (every TENANT_CHILD entry's FK chain and every GLOBAL table's reason).
    #
    # "users" is deliberately excluded as valid FK-chain evidence, directly
    # and one hop removed: a FK to users.id (created_by_id, updated_by_id,
    # reviewed_by_id, ...) proves attribution ("who acted"), not that the
    # child table's rows are confined to that user's organisation's data.
    # The first version of this classification pass used users.id as
    # sufficient evidence and wrongly marked vendor_organizations,
    # custom_field_definitions, archimate_patterns/viewpoint_templates,
    # confidence_thresholds, threshold_configurations, scoring_configurations
    # and external_systems as TENANT_CHILD -- every one of those directly
    # contradicts an explicit decision already on record (vendor_organizations'
    # own docstring and tests/test_vendor_tenancy_policy.py; this PR's own
    # round 1/2 description naming custom field definitions, ArchiMate
    # pattern/template creation and /reviews/api/thresholds as needing a
    # genuine platform admin). Caught before merge by cross-checking the
    # mechanical output against that record, not by inspection alone.
    "acceptance_criteria": (
        "mechanical classification (PR 430 round 4): acceptance_criteria.requirement_id -> requirements.id (direct FK to a tenant-owned table)"
    ),
    "acm_property_templates": (
        "mechanical classification (PR 430 round 4): acm_property_templates.organization_id (own column)"
    ),
    "adm_audit_logs": (
        "mechanical classification (PR 430 round 4): adm_audit_logs.board_id -> kanban_boards.id (direct FK to a tenant-owned table)"
    ),
    "adm_board_portfolio_links": (
        "mechanical classification (PR 430 round 4): adm_board_portfolio_links.board_id -> kanban_boards.id (direct FK to a tenant-owned table)"
    ),
    "adm_compliance_checkpoints": (
        "mechanical classification (PR 430 round 4): adm_compliance_checkpoints.approval_id -> adm_phase_approvals.id (direct FK to a tenant-owned table)"
    ),
    "adm_stakeholder_concurrences": (
        "mechanical classification (PR 430 round 4): adm_stakeholder_concurrences.approval_id -> adm_phase_approvals.id (direct FK to a tenant-owned table)"
    ),
    "adr_capability_links": (
        "mechanical classification (PR 430 round 4): adr_capability_links.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "adr_process_links": (
        "mechanical classification (PR 430 round 4): adr_process_links.adr_id -> architecture_decision_records.id (direct FK to a tenant-owned table)"
    ),
    "analysis_audit_logs": (
        "mechanical classification (PR 430 round 4): analysis_audit_logs.analysis_id -> options_analysis.id (direct FK to a tenant-owned table)"
    ),
    "analysis_recommendations": (
        "mechanical classification (PR 430 round 4): analysis_recommendations.analysis_id -> options_analysis.id (direct FK to a tenant-owned table)"
    ),
    "analysis_scenarios": (
        "mechanical classification (PR 430 round 4): analysis_scenarios.analysis_id -> options_analysis.id (direct FK to a tenant-owned table)"
    ),
    "application_business_actor_mapping": (
        "mechanical classification (PR 430 round 4): application_business_actor_mapping.business_actor_id -> business_actors.id (direct FK to a tenant-owned table)"
    ),
    "application_business_metrics": (
        "mechanical classification (PR 430 round 4): application_business_metrics.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_capability": (
        "mechanical classification (PR 430 round 4): application_capability.archimate_application_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "application_capability_coverage": (
        "mechanical classification (PR 430 round 4): application_capability_coverage.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_capability_mapping": (
        "mechanical classification (PR 430 round 4): application_capability_mapping.organization_id (own column)"
    ),
    "application_collaborations": (
        "mechanical classification (PR 430 round 4): application_collaborations.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "application_component_embeddings": (
        "mechanical classification (PR 430 round 4): application_component_embeddings.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_costs": (
        "mechanical classification (PR 430 round 4): application_costs.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_documents": (
        "mechanical classification (PR 430 round 4): application_documents.organization_id (own column)"
    ),
    "application_interactions": (
        "mechanical classification (PR 430 round 4): application_interactions.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "application_interface_mapping": (
        "mechanical classification (PR 430 round 4): application_interface_mapping.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_owners": (
        "mechanical classification (PR 430 round 4): application_owners.organization_id (own column)"
    ),
    "application_performance_metrics": (
        "mechanical classification (PR 430 round 4): application_performance_metrics.organization_id (own column)"
    ),
    "application_process_support": (
        "mechanical classification (PR 430 round 4): application_process_support.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_requirement_mapping": (
        "mechanical classification (PR 430 round 4): application_requirement_mapping.requirement_id -> requirements.id (direct FK to a tenant-owned table)"
    ),
    "application_roi": (
        "mechanical classification (PR 430 round 4): application_roi.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_services": (
        "mechanical classification (PR 430 round 4): application_services.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "application_technology_instances": (
        "mechanical classification (PR 430 round 4): application_technology_instances.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_technology_mapping": (
        "mechanical classification (PR 430 round 4): application_technology_mapping.system_software_id -> technology_system_software.id (direct FK to a tenant-owned table)"
    ),
    "application_usage": (
        "mechanical classification (PR 430 round 4): application_usage.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_vendor_product_mappings": (
        "mechanical classification (PR 430 round 4): application_vendor_product_mappings.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "application_versioning": (
        "mechanical classification (PR 430 round 4): application_versioning.organization_id (own column)"
    ),
    "approval_workflow": (
        "mechanical classification (PR 430 round 4): approval_workflow.decision_id -> capability_governance_decision.id (direct FK to a tenant-owned table)"
    ),
    "arb_adversarial_reviews": (
        "mechanical classification (PR 430 round 4): arb_adversarial_reviews.review_item_id -> arb_review_items.id (direct FK to a tenant-owned table)"
    ),
    "arb_change_requests": (
        "mechanical classification (PR 430 round 4): arb_change_requests.arb_review_item_id -> arb_review_items.id (direct FK to a tenant-owned table)"
    ),
    "arb_compliance_checks": (
        "mechanical classification (PR 430 round 4): arb_compliance_checks.review_item_id -> arb_review_items.id (direct FK to a tenant-owned table)"
    ),
    "arb_conditions": (
        "mechanical classification (PR 430 round 4): arb_conditions.review_item_id -> arb_review_items.id (direct FK to a tenant-owned table)"
    ),
    "arb_derogations": (
        "mechanical classification (PR 430 round 4): arb_derogations.arb_review_item_id -> arb_review_items.id (direct FK to a tenant-owned table)"
    ),
    "arb_governance_standards": (
        "mechanical classification (PR 430 round 4): arb_governance_standards.organization_id (own column)"
    ),
    "arb_submission_packs": (
        "mechanical classification (PR 430 round 4): arb_submission_packs.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "arb_workflow_stages": (
        "mechanical classification (PR 430 round 4): arb_workflow_stages.organization_id (own column)"
    ),
    "archimate_audit_logs": (
        "mechanical classification (PR 430 round 4): archimate_audit_logs.retired_into_id -> soc2_audit_log.id (direct FK to a tenant-owned table)"
    ),
    "archimate_capabilities": (
        "mechanical classification (PR 430 round 4): archimate_capabilities.business_capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "archimate_capability_mapping": (
        "mechanical classification (PR 430 round 4): archimate_capability_mapping.archimate_capability_id -> archimate_capabilities -> (business_capability_id->business_capability.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "archimate_element_comments": (
        "mechanical classification (PR 430 round 4): archimate_element_comments.element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "archimate_viewpoint_snapshots": (
        "mechanical classification (PR 430 round 4): archimate_viewpoint_snapshots.viewpoint_id -> saved_diagrams.id (direct FK to a tenant-owned table)"
    ),
    "architecture_change_notices": (
        "mechanical classification (PR 430 round 4): architecture_change_notices.change_request_id -> architecture_change_requests.id (direct FK to a tenant-owned table)"
    ),
    "architecture_documents": (
        "mechanical classification (PR 430 round 4): architecture_documents.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "architecture_generation_runs": (
        "mechanical classification (PR 430 round 4): architecture_generation_runs.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "architecture_review_findings": (
        "mechanical classification (PR 430 round 4): architecture_review_findings.element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "architecture_vision_documents": (
        "mechanical classification (PR 430 round 4): architecture_vision_documents.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "batch_import_application": (
        "mechanical classification (PR 430 round 4): batch_import_application.committed_application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "batch_import_checkpoint": (
        "mechanical classification (PR 430 round 4): batch_import_checkpoint.application_id -> batch_import_application -> (committed_application_id->application_components.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "batch_import_element": (
        "mechanical classification (PR 430 round 4): batch_import_element.application_id -> batch_import_application -> (committed_application_id->application_components.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "business_capability_embeddings": (
        "mechanical classification (PR 430 round 4): business_capability_embeddings.business_capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "business_interactions": (
        "mechanical classification (PR 430 round 4): business_interactions.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "capability_actor_raci": (
        "mechanical classification (PR 430 round 4): capability_actor_raci.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "capability_archimate_classifications": (
        "mechanical classification (PR 430 round 4): capability_archimate_classifications.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "capability_framework_configuration": (
        "mechanical classification (PR 430 round 4): capability_framework_configuration.organization_id (own column)"
    ),
    "capability_gap_details": (
        "mechanical classification (PR 430 round 4): capability_gap_details.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "capability_investment_plans": (
        "mechanical classification (PR 430 round 4): capability_investment_plans.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "capability_prioritization": (
        "mechanical classification (PR 430 round 4): capability_prioritization.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "capability_process_mapping": (
        "mechanical classification (PR 430 round 4): capability_process_mapping.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "capability_tag_associations": (
        "mechanical classification (PR 430 round 4): capability_tag_associations.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "capability_taxonomy_audit": (
        "mechanical classification (PR 430 round 4): capability_taxonomy_audit.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "capability_taxonomy_violations": (
        "mechanical classification (PR 430 round 4): capability_taxonomy_violations.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "card_applications": (
        "mechanical classification (PR 430 round 4): card_applications.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "card_archimate_elements": (
        "mechanical classification (PR 430 round 4): card_archimate_elements.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "card_capabilities": (
        "mechanical classification (PR 430 round 4): card_capabilities.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "card_dependencies": (
        "mechanical classification (PR 430 round 4): card_dependencies.source_card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "card_initiatives": (
        "mechanical classification (PR 430 round 4): card_initiatives.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "card_systems": (
        "mechanical classification (PR 430 round 4): card_systems.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "change_impact_assessments": (
        "mechanical classification (PR 430 round 4): change_impact_assessments.change_request_id -> architecture_change_requests.id (direct FK to a tenant-owned table)"
    ),
    "change_management_records": (
        "mechanical classification (PR 430 round 4): change_management_records.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "cobit_capability_mapping": (
        "mechanical classification (PR 430 round 4): cobit_capability_mapping.capability_id -> enterprise_capabilities -> (business_capability_id->business_capability.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "code_artifacts": (
        "mechanical classification (PR 430 round 4): code_artifacts.source_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "code_quality_metrics": (
        "mechanical classification (PR 430 round 4): code_quality_metrics.software_module_id -> software_modules -> (archimate_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "codegen_chat_messages": (
        "mechanical classification (PR 430 round 4): codegen_chat_messages.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_data_imports": (
        "mechanical classification (PR 430 round 4): codegen_data_imports.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_drift_reports": (
        "mechanical classification (PR 430 round 4): codegen_drift_reports.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_generation_history": (
        "mechanical classification (PR 430 round 4): codegen_generation_history.codegen_generation_id -> codegen_generations -> (solution_id->solutions.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "codegen_generations": (
        "mechanical classification (PR 430 round 4): codegen_generations.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_solution_connectors": (
        "mechanical classification (PR 430 round 4): codegen_solution_connectors.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_solution_instances": (
        "mechanical classification (PR 430 round 4): codegen_solution_instances.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_solution_rules": (
        "mechanical classification (PR 430 round 4): codegen_solution_rules.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_solution_versions": (
        "mechanical classification (PR 430 round 4): codegen_solution_versions.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_system_boundary_solutions": (
        "mechanical classification (PR 430 round 4): codegen_system_boundary_solutions.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_test_run_steps": (
        "mechanical classification (PR 430 round 4): codegen_test_run_steps.test_run_id -> codegen_test_runs -> (solution_id->solutions.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "codegen_test_runs": (
        "mechanical classification (PR 430 round 4): codegen_test_runs.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "codegen_workflow_designs": (
        "mechanical classification (PR 430 round 4): codegen_workflow_designs.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "compliance_checks": (
        "mechanical classification (PR 430 round 4): compliance_checks.requirement_id -> requirements.id (direct FK to a tenant-owned table)"
    ),
    "compliance_gaps": (
        "mechanical classification (PR 430 round 4): compliance_gaps.retired_into_id -> gaps.id (direct FK to a tenant-owned table)"
    ),
    "compliance_governance_reports": (
        "mechanical classification (PR 430 round 4): compliance_governance_reports.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "compliance_requirements": (
        "mechanical classification (PR 430 round 4): compliance_requirements.applies_to_capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "compliance_scan_reports": (
        "mechanical classification (PR 430 round 4): compliance_scan_reports.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "composite_structures": (
        "mechanical classification (PR 430 round 4): composite_structures.child_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "consolidation_candidates": (
        "mechanical classification (PR 430 round 4): consolidation_candidates.duplicate_application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "consolidation_list_entries": (
        "mechanical classification (PR 430 round 4): consolidation_list_entries.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "consolidation_opportunities": (
        "mechanical classification (PR 430 round 4): consolidation_opportunities.target_application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "constraints": (
        "mechanical classification (PR 430 round 4): constraints.goal_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "contract_applications": (
        "mechanical classification (PR 430 round 4): contract_applications.organization_id (own column)"
    ),
    "copilot_insights": (
        "mechanical classification (PR 430 round 4): copilot_insights.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "cross_layer_relationships": (
        "mechanical classification (PR 430 round 4): cross_layer_relationships.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "decision_capability_links": (
        "mechanical classification (PR 430 round 4): decision_capability_links.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "decommission_plans": (
        "mechanical classification (PR 430 round 4): decommission_plans.score_id -> application_rationalization_scores.id (direct FK to a tenant-owned table)"
    ),
    "deliverables": (
        "mechanical classification (PR 430 round 4): deliverables.work_package_id -> work_packages.id (direct FK to a tenant-owned table)"
    ),
    "deployment_pipelines": (
        "mechanical classification (PR 430 round 4): deployment_pipelines.organization_id (own column)"
    ),
    "design_patterns": (
        "mechanical classification (PR 430 round 4): design_patterns.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "document_analyses": (
        "mechanical classification (PR 430 round 4): document_analyses.llm_interaction_id -> llm_interactions.id (direct FK to a tenant-owned table)"
    ),
    "document_analysis_edits": (
        "mechanical classification (PR 430 round 4): document_analysis_edits.analysis_id -> document_analyses -> (llm_interaction_id->llm_interactions.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "ea_workflow_definitions": (
        "mechanical classification (PR 430 round 4): ea_workflow_definitions.organization_id (own column)"
    ),
    "element_template_usage": (
        "mechanical classification (PR 430 round 4): element_template_usage.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "enterprise_architecture_frameworks": (
        "mechanical classification (PR 430 round 4): enterprise_architecture_frameworks.organization_id (own column)"
    ),
    "enterprise_capabilities": (
        "mechanical classification (PR 430 round 4): enterprise_capabilities.business_capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "equipment": (
        "mechanical classification (PR 430 round 4): equipment.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "experience_elements": (
        "mechanical classification (PR 430 round 4): experience_elements.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "framework_adoptions": (
        "mechanical classification (PR 430 round 4): framework_adoptions.organization_id (own column)"
    ),
    "framework_configuration_templates": (
        "mechanical classification (PR 430 round 4): framework_configuration_templates.organization_id (own column)"
    ),
    "framework_extensions": (
        "mechanical classification (PR 430 round 4): framework_extensions.organization_id (own column)"
    ),
    "framework_migration_mappings": (
        "mechanical classification (PR 430 round 4): framework_migration_mappings.organization_id (own column)"
    ),
    "framework_validation_rules": (
        "mechanical classification (PR 430 round 4): framework_validation_rules.organization_id (own column)"
    ),
    "functional_requirement": (
        "mechanical classification (PR 430 round 4): functional_requirement.function_id -> business_function.id (direct FK to a tenant-owned table)"
    ),
    "gap_analysis_recommendations": (
        "mechanical classification (PR 430 round 4): gap_analysis_recommendations.analysis_id -> capability_gap_analysis.id (direct FK to a tenant-owned table)"
    ),
    "gap_remediation_reports": (
        "mechanical classification (PR 430 round 4): gap_remediation_reports.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "gap_solution_options": (
        "mechanical classification (PR 430 round 4): gap_solution_options.gap_detail_id -> capability_gap_details -> (capability_id->unified_capabilities.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "generation_pipelines": (
        "mechanical classification (PR 430 round 4): generation_pipelines.vendor_template_id -> vendor_stack_templates.id (direct FK to a tenant-owned table)"
    ),
    "governance_capability_links": (
        "mechanical classification (PR 430 round 4): governance_capability_links.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "governance_system_links": (
        "mechanical classification (PR 430 round 4): governance_system_links.decision_id -> capability_governance_decision.id (direct FK to a tenant-owned table)"
    ),
    "governance_workflows": (
        "mechanical classification (PR 430 round 4): governance_workflows.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "implementation_gaps": (
        "mechanical classification (PR 430 round 4): implementation_gaps.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "implementation_plateaus": (
        "mechanical classification (PR 430 round 4): implementation_plateaus.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "implementation_work_packages": (
        "mechanical classification (PR 430 round 4): implementation_work_packages.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "import_audit_log": (
        "mechanical classification (PR 430 round 4): import_audit_log.organization_id (own column)"
    ),
    "industry_apqc_framework": (
        "mechanical classification (PR 430 round 4): industry_apqc_framework.organization_id (own column)"
    ),
    "industry_apqc_process": (
        "mechanical classification (PR 430 round 4): industry_apqc_process.organization_id (own column)"
    ),
    "industry_frameworks": (
        "mechanical classification (PR 430 round 4): industry_frameworks.organization_id (own column)"
    ),
    "initiative_success_metrics": (
        "mechanical classification (PR 430 round 4): initiative_success_metrics.initiative_id -> portfolio_initiatives -> (archimate_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "integration_contracts": (
        "mechanical classification (PR 430 round 4): integration_contracts.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "integration_impact_registers": (
        "mechanical classification (PR 430 round 4): integration_impact_registers.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "interface_consumer": (
        "mechanical classification (PR 430 round 4): interface_consumer.consumer_application_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "itil_capability_mapping": (
        "mechanical classification (PR 430 round 4): itil_capability_mapping.capability_id -> enterprise_capabilities -> (business_capability_id->business_capability.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "jira_issues": (
        "mechanical classification (PR 430 round 4): jira_issues.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "jira_projects": (
        "mechanical classification (PR 430 round 4): jira_projects.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "jira_sync_tracking": (
        "mechanical classification (PR 430 round 4): jira_sync_tracking.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "kanban_card_attachments": (
        "mechanical classification (PR 430 round 4): kanban_card_attachments.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "kanban_card_comments": (
        "mechanical classification (PR 430 round 4): kanban_card_comments.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "kanban_card_history": (
        "mechanical classification (PR 430 round 4): kanban_card_history.card_id -> kanban_cards.id (direct FK to a tenant-owned table)"
    ),
    "llm_interactions": (
        "mechanical classification (PR 430 round 4): llm_interactions.organization_id (own column)"
    ),
    "manufacturing_capabilities": (
        "mechanical classification (PR 430 round 4): manufacturing_capabilities.unified_capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "manufacturing_improvement_initiatives": (
        "mechanical classification (PR 430 round 4): manufacturing_improvement_initiatives.manufacturing_capability_id -> manufacturing_capabilities -> (unified_capability_id->unified_capabilities.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "manufacturing_plants": (
        "mechanical classification (PR 430 round 4): manufacturing_plants.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "migration_plan_documents": (
        "mechanical classification (PR 430 round 4): migration_plan_documents.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "milestones": (
        "mechanical classification (PR 430 round 4): milestones.project_id -> projects.id (direct FK to a tenant-owned table)"
    ),
    "model_providers": (
        "mechanical classification (PR 430 round 4): model_providers.organization_id (own column)"
    ),
    "motivation_assessments": (
        "mechanical classification (PR 430 round 4): motivation_assessments.model_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "motivation_constraints": (
        "mechanical classification (PR 430 round 4): motivation_constraints.model_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "motivation_drivers": (
        "mechanical classification (PR 430 round 4): motivation_drivers.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "motivation_meanings": (
        "mechanical classification (PR 430 round 4): motivation_meanings.model_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "motivation_outcomes": (
        "mechanical classification (PR 430 round 4): motivation_outcomes.goal_id -> goals.id (direct FK to a tenant-owned table)"
    ),
    "motivation_stakeholders": (
        "mechanical classification (PR 430 round 4): motivation_stakeholders.model_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "motivation_values": (
        "mechanical classification (PR 430 round 4): motivation_values.model_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "non_functional_requirement": (
        "mechanical classification (PR 430 round 4): non_functional_requirement.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "other_relationships": (
        "mechanical classification (PR 430 round 4): other_relationships.source_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "pipeline_stages": (
        "mechanical classification (PR 430 round 4): pipeline_stages.pipeline_id -> generation_pipelines -> (vendor_template_id->vendor_stack_templates.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "planning_deliverables": (
        "mechanical classification (PR 430 round 4): planning_deliverables.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "platform_ai_interactions": (
        "mechanical classification (PR 430 round 4): platform_ai_interactions.pipeline_id -> generation_pipelines -> (vendor_template_id->vendor_stack_templates.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "platform_capabilities": (
        "mechanical classification (PR 430 round 4): platform_capabilities.technology_stack_id -> technology_stacks -> (vendor_template_id->vendor_stack_templates.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "platform_migration_scopes": (
        "mechanical classification (PR 430 round 4): platform_migration_scopes.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "policy_exemptions": (
        "mechanical classification (PR 430 round 4): policy_exemptions.violation_id -> policy_violations.id (direct FK to a tenant-owned table)"
    ),
    "portfolio_initiatives": (
        "mechanical classification (PR 430 round 4): portfolio_initiatives.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "process_actor_raci": (
        "mechanical classification (PR 430 round 4): process_actor_raci.actor_id -> business_actors.id (direct FK to a tenant-owned table)"
    ),
    "process_embeddings": (
        "mechanical classification (PR 430 round 4): process_embeddings.process_id -> industry_apqc_process.id (direct FK to a tenant-owned table)"
    ),
    "process_role_raci": (
        "mechanical classification (PR 430 round 4): process_role_raci.role_id -> business_roles.id (direct FK to a tenant-owned table)"
    ),
    "production_lines": (
        "mechanical classification (PR 430 round 4): production_lines.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "production_orders": (
        "mechanical classification (PR 430 round 4): production_orders.production_line_id -> production_lines -> (archimate_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "project_constraints": (
        "mechanical classification (PR 430 round 4): project_constraints.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "project_notes": (
        "mechanical classification (PR 430 round 4): project_notes.project_id -> projects.id (direct FK to a tenant-owned table)"
    ),
    "project_resources": (
        "mechanical classification (PR 430 round 4): project_resources.project_id -> projects.id (direct FK to a tenant-owned table)"
    ),
    "published_api_specs": (
        "mechanical classification (PR 430 round 4): published_api_specs.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "quality_attributes": (
        "mechanical classification (PR 430 round 4): quality_attributes.applies_to_capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "quality_frameworks": (
        "mechanical classification (PR 430 round 4): quality_frameworks.organization_id (own column)"
    ),
    "rationalization_audit_entries": (
        "mechanical classification (PR 430 round 4): rationalization_audit_entries.retired_into_id -> soc2_audit_log.id (direct FK to a tenant-owned table)"
    ),
    "rationalization_benefits": (
        "mechanical classification (PR 430 round 4): rationalization_benefits.application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "refactoring_tracking": (
        "mechanical classification (PR 430 round 4): refactoring_tracking.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "reference_model": (
        "mechanical classification (PR 430 round 4): reference_model.organization_id (own column)"
    ),
    "reference_model_capability": (
        "mechanical classification (PR 430 round 4): reference_model_capability.organization_id (own column)"
    ),
    "relationship_suggestions": (
        "mechanical classification (PR 430 round 4): relationship_suggestions.source_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "replacement_plans": (
        "mechanical classification (PR 430 round 4): replacement_plans.source_app_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "required_capabilities": (
        "mechanical classification (PR 430 round 4): required_capabilities.analysis_id -> options_analysis.id (direct FK to a tenant-owned table)"
    ),
    "requirement_change_log": (
        "mechanical classification (PR 430 round 4): requirement_change_log.req_id -> solution_requirements.id (direct FK to a tenant-owned table)"
    ),
    "requirement_dependencies": (
        "mechanical classification (PR 430 round 4): requirement_dependencies.req_id -> solution_requirements.id (direct FK to a tenant-owned table)"
    ),
    "requirements_traceability_matrices": (
        "mechanical classification (PR 430 round 4): requirements_traceability_matrices.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "review_decisions": (
        "mechanical classification (PR 430 round 4): review_decisions.review_item_id -> review_queue_items.id (direct FK to a tenant-owned table)"
    ),
    "rfp_templates": (
        "mechanical classification (PR 430 round 4): rfp_templates.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "roadmap_gaps": (
        "mechanical classification (PR 430 round 4): roadmap_gaps.source_application_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "roadmap_work_packages": (
        "mechanical classification (PR 430 round 4): roadmap_work_packages.retired_into_id -> unified_work_packages.id (direct FK to a tenant-owned table)"
    ),
    "runtime_compliance_checks": (
        "mechanical classification (PR 430 round 4): runtime_compliance_checks.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "saved_diagram_relationships": (
        "mechanical classification (PR 430 round 4): saved_diagram_relationships.diagram_id -> saved_diagrams.id (direct FK to a tenant-owned table)"
    ),
    "savings_realizations": (
        "mechanical classification (PR 430 round 4): savings_realizations.opportunity_id -> consolidation_opportunities -> (target_application_id->application_components.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "security_scans": (
        "mechanical classification (PR 430 round 4): security_scans.code_artifact_id -> code_artifacts -> (source_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "service_dependency": (
        "mechanical classification (PR 430 round 4): service_dependency.dependent_service_id -> business_services.id (direct FK to a tenant-owned table)"
    ),
    "service_level_agreements": (
        "mechanical classification (PR 430 round 4): service_level_agreements.contract_id -> vendor_contracts.id (direct FK to a tenant-owned table)"
    ),
    "service_realization": (
        "mechanical classification (PR 430 round 4): service_realization.service_id -> business_services.id (direct FK to a tenant-owned table)"
    ),
    "sla_violations": (
        "mechanical classification (PR 430 round 4): sla_violations.sla_id -> service_level_agreements -> (contract_id->vendor_contracts.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "software_dependencies": (
        "mechanical classification (PR 430 round 4): software_dependencies.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "software_modules": (
        "mechanical classification (PR 430 round 4): software_modules.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "solution_adr_direct": (
        "mechanical classification (PR 430 round 4): solution_adr_direct.adr_id -> architecture_decision_records.id (direct FK to a tenant-owned table)"
    ),
    "solution_ai_backtesting": (
        "mechanical classification (PR 430 round 4): solution_ai_backtesting.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_ai_reasoning_states": (
        "mechanical classification (PR 430 round 4): solution_ai_reasoning_states.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_app_elements": (
        "mechanical classification (PR 430 round 4): solution_app_elements.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_apqc_processes": (
        "mechanical classification (PR 430 round 4): solution_apqc_processes.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_archimate_elements": (
        "mechanical classification (PR 430 round 4): solution_archimate_elements.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_assessments_sad": (
        "mechanical classification (PR 430 round 4): solution_assessments_sad.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_benefit_realizations": (
        "mechanical classification (PR 430 round 4): solution_benefit_realizations.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_business_elements": (
        "mechanical classification (PR 430 round 4): solution_business_elements.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_capability_mappings": (
        "mechanical classification (PR 430 round 4): solution_capability_mappings.capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "solution_change_requests": (
        "mechanical classification (PR 430 round 4): solution_change_requests.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_code_bundles": (
        "mechanical classification (PR 430 round 4): solution_code_bundles.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_comments": (
        "mechanical classification (PR 430 round 4): solution_comments.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_compliance_mappings": (
        "mechanical classification (PR 430 round 4): solution_compliance_mappings.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "solution_compositions": (
        "mechanical classification (PR 430 round 4): solution_compositions.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_cost_comparisons": (
        "mechanical classification (PR 430 round 4): solution_cost_comparisons.session_id -> solution_analysis_sessions.id (direct FK to a tenant-owned table)"
    ),
    "solution_cost_yearly_projections": (
        "mechanical classification (PR 430 round 4): solution_cost_yearly_projections.cost_model_id -> solution_cost_models.id (direct FK to a tenant-owned table)"
    ),
    "solution_elements": (
        "mechanical classification (PR 430 round 4): solution_elements.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "solution_embeddings": (
        "mechanical classification (PR 430 round 4): solution_embeddings.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_feasibility_reviews": (
        "mechanical classification (PR 430 round 4): solution_feasibility_reviews.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_fit_gap_entries": (
        "mechanical classification (PR 430 round 4): solution_fit_gap_entries.capability_id -> capabilities.id (direct FK to a tenant-owned table)"
    ),
    "solution_governance_exceptions": (
        "mechanical classification (PR 430 round 4): solution_governance_exceptions.principle_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "solution_integration_flows": (
        "mechanical classification (PR 430 round 4): solution_integration_flows.source_app_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "solution_investment_phases": (
        "mechanical classification (PR 430 round 4): solution_investment_phases.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_layer_elements": (
        "mechanical classification (PR 430 round 4): solution_layer_elements.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_lessons_learned": (
        "mechanical classification (PR 430 round 4): solution_lessons_learned.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_migration_dependencies": (
        "mechanical classification (PR 430 round 4): solution_migration_dependencies.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_notifications": (
        "mechanical classification (PR 430 round 4): solution_notifications.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_org_impacts": (
        "mechanical classification (PR 430 round 4): solution_org_impacts.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_outcome_tracking": (
        "mechanical classification (PR 430 round 4): solution_outcome_tracking.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_plateaus": (
        "mechanical classification (PR 430 round 4): solution_plateaus.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_principles_sad": (
        "mechanical classification (PR 430 round 4): solution_principles_sad.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_quality_attributes": (
        "mechanical classification (PR 430 round 4): solution_quality_attributes.constraint_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "solution_risk_snapshots": (
        "mechanical classification (PR 430 round 4): solution_risk_snapshots.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_scoring_configs": (
        "mechanical classification (PR 430 round 4): solution_scoring_configs.organization_id (own column)"
    ),
    "solution_session_versions": (
        "mechanical classification (PR 430 round 4): solution_session_versions.session_id -> solution_analysis_sessions.id (direct FK to a tenant-owned table)"
    ),
    "solution_slas": (
        "mechanical classification (PR 430 round 4): solution_slas.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_stakeholder_concerns": (
        "mechanical classification (PR 430 round 4): solution_stakeholder_concerns.stakeholder_id -> solution_stakeholders.id (direct FK to a tenant-owned table)"
    ),
    "solution_stakeholder_mappings": (
        "mechanical classification (PR 430 round 4): solution_stakeholder_mappings.stakeholder_id -> solution_stakeholders.id (direct FK to a tenant-owned table)"
    ),
    "solution_stakeholders_sad": (
        "mechanical classification (PR 430 round 4): solution_stakeholders_sad.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_tco_items": (
        "mechanical classification (PR 430 round 4): solution_tco_items.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_tech_elements": (
        "mechanical classification (PR 430 round 4): solution_tech_elements.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_templates": (
        "mechanical classification (PR 430 round 4): solution_templates.source_solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "solution_workflow_tasks": (
        "mechanical classification (PR 430 round 4): solution_workflow_tasks.workflow_id -> solution_workflows.id (direct FK to a tenant-owned table)"
    ),
    "spec_webhooks": (
        "mechanical classification (PR 430 round 4): spec_webhooks.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "sprints": (
        "mechanical classification (PR 430 round 4): sprints.board_id -> kanban_boards.id (direct FK to a tenant-owned table)"
    ),
    "strategy_resources": (
        "mechanical classification (PR 430 round 4): strategy_resources.owning_organization_id -> business_actors.id (direct FK to a tenant-owned table)"
    ),
    "structural_groupings": (
        "mechanical classification (PR 430 round 4): structural_groupings.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "structural_junctions": (
        "mechanical classification (PR 430 round 4): structural_junctions.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "structural_locations": (
        "mechanical classification (PR 430 round 4): structural_locations.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "system_boundaries": (
        "mechanical classification (PR 430 round 4): system_boundaries.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "system_deployments": (
        "mechanical classification (PR 430 round 4): system_deployments.system_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "system_hierarchies": (
        "mechanical classification (PR 430 round 4): system_hierarchies.child_system_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "system_interfaces": (
        "mechanical classification (PR 430 round 4): system_interfaces.target_system_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "system_lifecycles": (
        "mechanical classification (PR 430 round 4): system_lifecycles.replacement_system_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "tasks": (
        "mechanical classification (PR 430 round 4): tasks.project_id -> projects.id (direct FK to a tenant-owned table)"
    ),
    "tco_calculations": (
        "mechanical classification (PR 430 round 4): tco_calculations.vendor_product_id -> vendor_products -> (archimate_product_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "technical_capabilities": (
        "mechanical classification (PR 430 round 4): technical_capabilities.retired_into_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "technical_capability_vendor_mappings": (
        "mechanical classification (PR 430 round 4): technical_capability_vendor_mappings.technical_capability_id -> technical_capabilities -> (retired_into_id->unified_capabilities.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "technical_debt": (
        "mechanical classification (PR 430 round 4): technical_debt.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "technology_capability": (
        "mechanical classification (PR 430 round 4): technology_capability.business_capability_id -> business_capability.id (direct FK to a tenant-owned table)"
    ),
    "technology_roadmap_initiatives": (
        "mechanical classification (PR 430 round 4): technology_roadmap_initiatives.retired_into_id -> unified_work_packages.id (direct FK to a tenant-owned table)"
    ),
    "technology_stacks": (
        "mechanical classification (PR 430 round 4): technology_stacks.vendor_template_id -> vendor_stack_templates.id (direct FK to a tenant-owned table)"
    ),
    "test_cases": (
        "mechanical classification (PR 430 round 4): test_cases.requirement_id -> requirements.id (direct FK to a tenant-owned table)"
    ),
    "traceability_links": (
        "mechanical classification (PR 430 round 4): traceability_links.solution_id -> solutions.id (direct FK to a tenant-owned table)"
    ),
    "uml_attributes": (
        "mechanical classification (PR 430 round 4): uml_attributes.uml_element_id -> uml_elements -> (archimate_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "uml_elements": (
        "mechanical classification (PR 430 round 4): uml_elements.archimate_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "uml_methods": (
        "mechanical classification (PR 430 round 4): uml_methods.uml_element_id -> uml_elements -> (archimate_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "unified_application_capability_mapping": (
        "mechanical classification (PR 430 round 4): unified_application_capability_mapping.unified_capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "unified_capabilities": (
        "mechanical classification (PR 430 round 4): unified_capabilities.organization_id (own column)"
    ),
    "unified_capability_application_mappings": (
        "mechanical classification (PR 430 round 4): unified_capability_application_mappings.application_component_id -> application_components.id (direct FK to a tenant-owned table)"
    ),
    "unified_capability_process_mapping": (
        "mechanical classification (PR 430 round 4): unified_capability_process_mapping.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "unified_capability_technology_mapping": (
        "mechanical classification (PR 430 round 4): unified_capability_technology_mapping.capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "unified_capability_vendor_organization_mappings": (
        "mechanical classification (PR 430 round 4): unified_capability_vendor_organization_mappings.unified_capability_id -> unified_capabilities.id (direct FK to a tenant-owned table)"
    ),
    "vendor_archimate_templates": (
        "mechanical classification (PR 430 round 4): vendor_archimate_templates.element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "vendor_capability_hierarchy": (
        "mechanical classification (PR 430 round 4): vendor_capability_hierarchy.template_id -> vendor_stack_templates.id (direct FK to a tenant-owned table)"
    ),
    "vendor_integration_catalog": (
        "mechanical classification (PR 430 round 4): vendor_integration_catalog.template_id -> vendor_stack_templates.id (direct FK to a tenant-owned table)"
    ),
    "vendor_options": (
        "mechanical classification (PR 430 round 4): vendor_options.analysis_id -> options_analysis.id (direct FK to a tenant-owned table)"
    ),
    "vendor_process_hierarchy": (
        "mechanical classification (PR 430 round 4): vendor_process_hierarchy.template_id -> vendor_stack_templates.id (direct FK to a tenant-owned table)"
    ),
    "vendor_product_apqc_mapping": (
        "mechanical classification (PR 430 round 4): vendor_product_apqc_mapping.vendor_product_id -> vendor_products -> (archimate_product_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "vendor_product_embeddings": (
        "mechanical classification (PR 430 round 4): vendor_product_embeddings.vendor_product_id -> vendor_products -> (archimate_product_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "vendor_product_pricing": (
        "mechanical classification (PR 430 round 4): vendor_product_pricing.organization_id (own column)"
    ),
    "vendor_products": (
        "mechanical classification (PR 430 round 4): vendor_products.archimate_product_element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "vendor_proof_points": (
        "mechanical classification (PR 430 round 4): vendor_proof_points.vendor_option_id -> vendor_options -> (analysis_id->options_analysis.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "vendor_responses": (
        "mechanical classification (PR 430 round 4): vendor_responses.vendor_option_id -> vendor_options -> (analysis_id->options_analysis.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "vendor_risk_assessments": (
        "mechanical classification (PR 430 round 4): vendor_risk_assessments.vendor_product_id -> vendor_products -> (archimate_product_element_id->archimate_elements.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "vendor_selection_reports": (
        "mechanical classification (PR 430 round 4): vendor_selection_reports.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "viewpoint_stakeholder_mappings": (
        "mechanical classification (PR 430 round 4): viewpoint_stakeholder_mappings.viewpoint_id -> archimate_viewpoints.id (direct FK to a tenant-owned table)"
    ),
    "viewpoint_views": (
        "mechanical classification (PR 430 round 4): viewpoint_views.architecture_model_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "workflow_cleanup_nodes": (
        "mechanical classification (PR 430 round 4): workflow_cleanup_nodes.pipeline_id -> workflow_pipelines -> (architecture_id->architecture_models.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "workflow_completion_summaries": (
        "mechanical classification (PR 430 round 4): workflow_completion_summaries.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
    "workflow_edges": (
        "mechanical classification (PR 430 round 4): workflow_edges.pipeline_id -> workflow_pipelines -> (architecture_id->architecture_models.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "workflow_executions": (
        "mechanical classification (PR 430 round 4): workflow_executions.pipeline_id -> workflow_pipelines -> (architecture_id->architecture_models.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "workflow_instance_archimate_elements": (
        "mechanical classification (PR 430 round 4): workflow_instance_archimate_elements.element_id -> archimate_elements.id (direct FK to a tenant-owned table)"
    ),
    "workflow_nodes": (
        "mechanical classification (PR 430 round 4): workflow_nodes.pipeline_id -> workflow_pipelines -> (architecture_id->architecture_models.id) (one-hop FK chain to a tenant-owned table)"
    ),
    "workflow_pipelines": (
        "mechanical classification (PR 430 round 4): workflow_pipelines.architecture_id -> architecture_models.id (direct FK to a tenant-owned table)"
    ),
    "workflow_run_watchers": (
        "mechanical classification (PR 430 round 4): workflow_run_watchers.workflow_instance_id -> ea_workflow_instances.id (direct FK to a tenant-owned table)"
    ),
}


def reason_for(table_name: str) -> str | None:
    """The allow-list reason for ``table_name``, or ``None`` if not listed."""
    return PLATFORM_WRITE_ALLOWLIST.get(table_name)
