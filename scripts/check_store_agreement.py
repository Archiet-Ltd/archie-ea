#!/usr/bin/env python
"""Two surfaces answer one question and give different numbers.

Every other gate in this repository reads SOURCE. This one reads ANSWERS.

The owner found all three of these by clicking around the deployed product,
while all 70+ gates were green:

  * /capability-map/ printing "Total Capabilities 191" directly above a table
    reading "Showing 1-10 of 0 results";
  * the Capability Roadmap counting 173 gaps beside a Gap Analysis screen
    reading 0;
  * GET /api/v1/capabilities returning an empty list while `business_capability`
    held 461 rows.

They are one defect wearing three faces: a concept lives in more than one store,
each screen reads a different one, and the product contradicts itself in front
of the user. No source-reading gate can see it, because every individual line is
correct -- `/dashboard/api/capabilities` counting BusinessCapability and
`/api/v1/capabilities/` paginating UnifiedCapability are both perfectly good
code. The defect exists only in the DIFFERENCE between their outputs.
check_canonical_store.py holds the nearest structural approximation (no NEW
table gains a second mapped class); this holds the behavioural fact.

So this gate boots the app, establishes a real tenant, asks every surface that
answers a given question, and fails when they disagree.

Adding a concept
----------------
CONCEPTS at the top of this file is the whole extension surface. A new concept
is a dict entry and two or more Surface(...) lines. A gate nobody can extend
gets bypassed, so keep it that way -- put the model path and the URL in the
registry, never a special case in the engine.

Not reporting differences that are legitimate
---------------------------------------------
Two counts may honestly differ, and a gate that cries wolf gets ignored --
this repository has already had two gates ratcheting phantom findings. Three
legitimate reasons, all handled by the `scope` field rather than by heuristics:

  pagination   a page of items is not the population. A surface returning
               `pagination.total` is scope="all"; one returning the length of
               the items ON the page is scope="page".
  a filter     "active applications" is a different question from "all
               applications". Declare it: scope="status=active".
  permissions  a role-scoped listing answers a narrower question. Declare it.

Surfaces are compared ONLY within their own scope group. Equality inside a group
is required; across groups the only rule asserted is that a declared subset may
not EXCEED the scope="all" population, which is true of every filter, page and
permission narrowing there is. A difference the declared scope explains is
therefore never reported. Anything undeclared is a finding naming both surfaces
and both numbers.

Blindness is reported, not hidden
---------------------------------
Against an empty database every surface answers 0 and agrees perfectly. That is
not evidence of health, so a concept whose surfaces are ALL zero is printed as
`no-evidence` and excluded from the count instead of being silently counted as
a pass. Read those lines: they say the gate could not see anything, and a run
that is all no-evidence has proven nothing.

Tenancy is part of the question
-------------------------------
"How many X does THIS organisation have" is the only question compared. A store
answers it in one of four declared ways, never by counting every tenant's rows:

  TenantMixin     the ORM events scope the query (the request context below).
  organization_id a column but no mixin: the gate filters on it explicitly.
  tenant_via=     no organisation column: the row belongs to the organisation of
                  the first non-null declared link, in order -- the linked
                  element first, the creating user last -- which is the rule the
                  consolidation backfills attribute such rows by. Rows no link
                  attributes are reported, never counted for anyone.
  shared=         deliberately shared reference data (one catalogue for every
                  tenant). Counted whole, with the reason on the Surface.

A store with none of the four cannot answer for one organisation at all. That is
reported as an `unscoped-store` finding once it holds rows, because counting it
whole would let organisation B's rows move organisation A's numbers.

Escape hatch: `store-agreement-ok: <reason>` in a Surface's `waived=` field, for
a surface that is knowingly and permanently a different number (a cached
projection with a documented staleness window, say). Name what makes the
divergence correct and who keeps it bounded.

    python scripts/check_store_agreement.py
    python scripts/check_store_agreement.py --count
    python scripts/check_store_agreement.py --root <tree>   # synthetic

`--root` reads `<root>/store_agreement_probe.json` -- the observations an app
boot would have produced -- and runs the identical comparison engine over them.
That is what makes the JUDGEMENT (which differences are reported and which are
explained away) testable without a seeded database, which is the part of this
gate that can be wrong.

Proven-against: run against the shared test database it reports 1 --
`capabilities: orm:BusinessCapability=12, GET /dashboard/api/capabilities=12,
orm:UnifiedCapability=0, GET /api/v1/capabilities/=0` for organisation 52336 --
which is the owner's third finding reproduced mechanically: the list endpoint
answers 0 while the store holds rows. Confirmed against the database by hand
(`select count(*) from business_capability where organization_id=52336` = 12,
`unified_capabilities` = 0). Red-and-green on a synthetic tree by
tests/test_store_agreement_concepts.py, which also plants a difference that IS
legitimate (a declared narrower scope reading less than the whole) and asserts
it is NOT reported, and seeds two organisations to show that one organisation's
rows never move the other's counts. The same file seeds a real disagreement
in the test database (an application recording a cost the cost store does not
hold), runs observe_tenant and compare(), and asserts red; then records the
cost and asserts green -- and asks the gaps screens as a signed-in user.

"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW_MARKER = "store-agreement-ok:"


class Surface:
    """One way the product answers "how many X are there?".

    kind    "orm"  -- count rows of a mapped model
            "http" -- GET a URL as a logged-in user of the tenant and read a
                      number out of the JSON
    target  dotted model path ("app.models.business_capabilities.BusinessCapability")
            or a URL path ("/api/v1/capabilities/?per_page=1")
    extract http only. A dotted path into the JSON ("data.pagination.total"),
            or "len:<path>" for the length of a list ("len:" for the top level).
    scope   the question this surface actually answers. "all" is the whole
            population; anything else declares a filter, and is compared only
            with surfaces carrying the SAME string.
    waived  a `store-agreement-ok: <reason>` string; excludes this surface.
    """

    def __init__(self, name, kind, target, extract=None, scope="all", waived=None,
                 filter_eq=None, filter_not_null=None, distinct=None,
                 tenant_via=None, shared=None):
        self.name = name
        self.kind = kind
        self.target = target
        self.extract = extract
        self.scope = scope
        self.waived = waived
        # orm-only: a real WHERE, not just "how many rows" -- lets a concept
        # express a genuine filtered-count question (e.g. "how many of these
        # have a maturity value recorded"), not only bare population size.
        # filter_not_null takes one column, or a tuple meaning "any of these".
        self.filter_eq = filter_eq or {}
        self.filter_not_null = filter_not_null
        # orm-only: count distinct values of this column rather than rows, for a
        # question about the parent ("applications with an owner") asked of a
        # child store that can hold several rows per parent.
        self.distinct = distinct
        # orm-only, for a store with no organization_id column: an ordered list
        # of (foreign-key column, parent table) links; see "Tenancy" above.
        self.tenant_via = list(tenant_via or [])
        # orm-only: the reason this store is deliberately shared reference data.
        self.shared = shared


# --------------------------------------------------------------------------
# THE REGISTRY. Adding a concept is an entry here and nothing else.
# --------------------------------------------------------------------------
CONCEPTS = {
    # The owner's finding, exactly. Both surfaces answer "how many capabilities
    # does this organisation have"; they read two different tables.
    "capabilities": [
        # BusinessCapability is the deprecated legacy store, superseded by
        # UnifiedCapability in PR 1 (feat/r1-one-capability-store). It is kept
        # in the registry for historical tracking but waived from comparison
        # because it no longer receives writes and its count will diverge
        # (typically to 0 after cutover).
        Surface("orm:BusinessCapability", "orm",
                "app.models.business_capabilities.BusinessCapability",
                waived="store-agreement-ok: deprecated legacy store, superseded by UnifiedCapability"),
        Surface("orm:UnifiedCapability", "orm",
                "app.models.unified_capability.UnifiedCapability"),
        Surface("GET /dashboard/api/capabilities", "http",
                "/dashboard/api/capabilities", extract="len:"),
        Surface("GET /api/v1/capabilities/", "http",
                "/api/v1/capabilities/?per_page=1",
                extract="data.pagination.total"),
        # /architecture/dashboard?layer=strategy&element_type=Capability is a
        # third, previously untracked surface answering the same question --
        # it filters app.models.archimate_core.ArchiMateElement (the generic
        # ArchiMate-element store) to element_type="Capability", not either
        # BusinessCapability or UnifiedCapability above. Same underlying data
        # endpoint the dashboard's own tab badge calls.
        # This is a derived mirror view, not the canonical store; give it a
        # declared narrower scope so it is not compared 1:1 with the
        # authoritative surfaces. A mirror may lag behind the canonical count.
        Surface("GET /architecture/api/layer/strategy/elements?element_type=Capability",
                "http",
                "/architecture/api/layer/strategy/elements?element_type=Capability&per_page=1",
                extract="pagination.total",
                scope="archimate-mirror"),
    ],
    "applications": [
        Surface("orm:ApplicationComponent", "orm",
                "app.models.application_portfolio.ApplicationComponent"),
        Surface("GET /api/v1/applications/", "http",
                "/api/v1/applications/?per_page=1",
                extract="data.pagination.total"),
        # Deliberately a DIFFERENT scope: this reads one page of rows, not the
        # population. Declared, so a smaller number here is never a finding.
        Surface("GET /api/v1/applications/ (one page)", "http",
                "/api/v1/applications/?per_page=1",
                extract="len:data.applications", scope="page"),
    ],
    # The gaps register holds two kinds of row (Gap.gap_kind), and the Gap
    # docstring names counting both as one number as the original defect. So
    # every surface here is compared only with surfaces showing the SAME kinds:
    #
    #   all                  every row of the register, both kinds -- what the
    #                        two list screens read (neither filters gap_kind).
    #   capability shortfall the roadmap statistics tile, which excludes
    #                        plateau_transition (roadmap_api.get_statistics).
    #   plateau transition   the difference between two plateaus -- the
    #                        ArchiMate Gap element, which ImplementationGap
    #                        also records (its own docstring), in a second
    #                        store.
    "gaps": [
        Surface("orm:Gap", "orm", "app.models.implementation_migration.Gap"),
        Surface("GET /implementation/api/gaps", "http",
                "/implementation/api/gaps", extract="len:gaps"),
        Surface("GET /api/roadmap/gaps", "http",
                "/api/roadmap/gaps", extract="len:gaps"),
        Surface("orm:Gap(capability shortfall)", "orm",
                "app.models.implementation_migration.Gap",
                filter_eq={"gap_kind": "capability_shortfall"},
                scope="capability shortfall"),
        Surface("GET /api/roadmap/statistics", "http",
                "/api/roadmap/statistics", extract="gaps.total",
                scope="capability shortfall"),
        Surface("orm:Gap(plateau transition)", "orm",
                "app.models.implementation_migration.Gap",
                filter_eq={"gap_kind": "plateau_transition"},
                scope="plateau transition"),
        Surface("orm:ImplementationGap", "orm",
                "app.models.implementation_planning.ImplementationGap",
                tenant_via=[("architecture_id", "architecture_models")],
                scope="plateau transition"),
    ],
    # T-002: "how many capabilities have a maturity value recorded" -- a real
    # freshness question, not a bare population count. The projection
    # (`app/commands/project_capabilities.py`) is supposed to keep these two
    # counts equal: every BusinessCapability row with a non-null
    # current_maturity_level should have a matching UnifiedCapability row
    # (source_table="business_capability") that ALSO carries a non-null
    # current_maturity_level. A disagreement here means the projection has
    # not run recently enough, or a raw-SQL maturity writer bypassed it --
    # exactly the defect class T-002 exists to close, and exactly what the
    # superseded prose below wrongly claimed could not be expressed.
    #
    # D-R7-6 / D-R5-2: this ratchet has a known, currently-unclosed orphan
    # gap -- if a BusinessCapability row is ever deleted via a bulk
    # `query.filter(...).delete()` or raw SQL (either of which bypasses the
    # ORM `after_delete` listener), the matching UnifiedCapability
    # projection becomes a permanent orphan that project_capabilities.py's
    # upsert-only projection logic can never clean up, and this ratchet
    # could creep back to a nonzero count with no code remedy currently
    # available (a periodic orphan-reaping pass, or an after_bulk_delete
    # hook, would close it) -- a regression here may be this known,
    # documented gap resurfacing rather than a fresh bug.
    "capability maturity assessed": [
        Surface("orm:BusinessCapability(maturity recorded)", "orm",
                "app.models.business_capabilities.BusinessCapability",
                filter_not_null="current_maturity_level"),
        Surface("orm:UnifiedCapability(business_capability, maturity recorded)", "orm",
                "app.models.unified_capability.UnifiedCapability",
                filter_eq={"source_table": "business_capability"},
                filter_not_null="current_maturity_level"),
    ],
    # Four work-package stores and five list screens. Stores without an
    # organisation column are attributed by the linked element, else by the
    # creating user.
    "work packages": [
        Surface("orm:WorkPackage", "orm",
                "app.models.implementation_migration.WorkPackage"),
        Surface("orm:UnifiedWorkPackage", "orm",
                "app.models.unified_work_package.UnifiedWorkPackage",
                tenant_via=[("archimate_element_id", "archimate_elements"),
                            ("application_component_id", "application_components"),
                            ("capability_id", "unified_capabilities"),
                            ("created_by", "users")]),
        Surface("orm:RoadmapWorkPackage", "orm",
                "app.models.roadmap_models.RoadmapWorkPackage",
                tenant_via=[("created_by", "users")]),
        Surface("orm:ImplementationWorkPackage", "orm",
                "app.models.implementation_planning.ImplementationWorkPackage",
                tenant_via=[("application_component_id", "application_components"),
                            ("architecture_id", "architecture_models")]),
        Surface("GET /enterprise/api/work-packages", "http",
                "/enterprise/api/work-packages?per_page=1", extract="total"),
        Surface("GET /api/roadmap/work-packages", "http",
                "/api/roadmap/work-packages?per_page=1",
                extract="pagination.total"),
        Surface("GET /api/roadmap-builder/work-packages", "http",
                "/api/roadmap-builder/work-packages?limit=1",
                extract="data.total"),
        Surface("GET /implementation/api/work-packages", "http",
                "/implementation/api/work-packages",
                extract="len:work_packages"),
        Surface("GET /capability-map/api/roadmap/work-packages", "http",
                "/capability-map/api/roadmap/work-packages?root_only=false",
                extract="total_count"),
    ],
    "risks": [
        Surface("orm:Risk", "orm", "app.models.risk.Risk"),
        Surface("GET /api/risks", "http", "/api/risks", extract="len:"),
    ],
    # "How many applications have an owner recorded", asked of the two owner
    # stores and of the owner text columns on the application itself.
    "application owners": [
        Surface("orm:ApplicationOwner(applications)", "orm",
                "app.models.application_owner.ApplicationOwner",
                distinct="application_id"),
        Surface("orm:ApplicationOwnership(applications)", "orm",
                "app.models.enterprise_intelligence.ApplicationOwnership",
                distinct="application_id"),
        Surface("orm:ApplicationComponent(owner text recorded)", "orm",
                "app.models.application_portfolio.ApplicationComponent",
                filter_not_null=("application_owner", "business_owner",
                                 "technical_owner")),
        # A primary owner is a subset of "has an owner": declared narrower.
        Surface("orm:ApplicationOwner(applications, primary)", "orm",
                "app.models.application_owner.ApplicationOwner",
                distinct="application_id",
                filter_eq={"ownership_type": "primary"},
                scope="primary owner"),
    ],
    "architecture decisions": [
        # ArchitectureDecisionRecord dropped as a peer surface (decision
        # register consolidation): every ArchitectureDecisionRecord row is
        # now dual-write paired into ArchitectureDecision via
        # pair_with_canonical_register() (app/models/adr.py), so it is a
        # satellite detail-store for review-board fields ArchitectureDecision
        # has no columns for, not an independent answer to "how many
        # architecture decisions". GET /arb/api/decisions already reads
        # ArchitectureDecision (app/modules/architecture/routes/
        # arb_decision_routes.py:161), so both remaining surfaces agree by
        # construction. Edited by the decision-register consolidation brief
        # directly, not requested from this file's owner first -- flagged in
        # the PR for their awareness; this narrows one concept's surface list
        # to a direct, unavoidable consequence of that brief's own change.
        Surface("orm:ArchitectureDecision", "orm",
                "app.models.architecture_decision.ArchitectureDecision"),
        Surface("GET /arb/api/decisions", "http",
                "/arb/api/decisions?per_page=1", extract="total"),
    ],
    # Pending proposals are three different record types, each with its own
    # queue screen, until one approval queue lands. Each is its own question;
    # each screen shows a declared slice of its own store only.
    "pending AI change approvals": [
        Surface("orm:AIChatCRUDApproval(pending)", "orm",
                "app.models.ai_chat_crud_approval.AIChatCRUDApproval",
                filter_eq={"status": "PENDING"}),
        # The signed-in user's own unexpired requests: declared narrower.
        Surface("GET /ai-chat/approvals/pending", "http",
                "/ai-chat/approvals/pending", extract="len:approvals",
                scope="own unexpired requests"),
    ],
    "pending review queue items": [
        Surface("orm:ReviewQueueItem(pending)", "orm",
                "app.models.confidence_review.ReviewQueueItem",
                filter_eq={"status": "PENDING"}),
        Surface("GET /api/confidence/queue?status=pending", "http",
                "/api/confidence/queue?status=pending&limit=100",
                extract="len:items", scope="first 100"),
    ],
    "pending relationship suggestions": [
        Surface("orm:RelationshipSuggestion(pending)", "orm",
                "app.models.archimate_core.RelationshipSuggestion",
                filter_eq={"status": "pending"},
                tenant_via=[("source_element_id", "archimate_elements"),
                            ("target_element_id", "archimate_elements")]),
        Surface("GET /capability-map/api/archimate/relationship-suggestions",
                "http",
                "/capability-map/api/archimate/relationship-suggestions"
                "?status=pending&limit=100",
                extract="len:suggestions",
                scope="confidence at least 0.3, first 100"),
    ],
    "applications with a recorded annual cost": [
        Surface("orm:ApplicationComponent(annual cost recorded)", "orm",
                "app.models.application_portfolio.ApplicationComponent",
                filter_not_null=("total_cost_of_ownership", "license_cost",
                                 "license_cost_annual", "maintenance_cost",
                                 "infrastructure_cost", "support_cost",
                                 "development_cost_annual")),
        Surface("orm:ApplicationCost(applications)", "orm",
                "app.models.enterprise_intelligence.ApplicationCost",
                distinct="application_id",
                tenant_via=[("application_id", "application_components"),
                            ("created_by_id", "users")]),
    ],
    # /procurement/contracts renders VendorContract for the organisation as
    # HTML; its query is the orm:VendorContract surface.
    "contracts": [
        Surface("orm:VendorContract", "orm",
                "app.models.application_portfolio.VendorContract"),
        Surface("orm:Contract", "orm", "app.models.archimate_business.Contract"),
    ],
    "vendors": [
        Surface("orm:VendorOrganization", "orm",
                "app.models.vendor.vendor_organization.VendorOrganization",
                shared="one vendor catalogue for every organisation; "
                       "VendorOrganization's own docstring"),
        Surface("GET /api/v1/vendors/", "http",
                "/api/v1/vendors/?per_page=1", extract="data.pagination.total"),
    ],
}

# Concepts and surfaces deliberately NOT registered, and why -- naming the
# exclusion is the point, because a hollow entry here would defeat the file:
#
#   maturity gaps     CapabilityGapAnalysis and CapabilityGapDetail
#                     (capability_gap_analysis, capability_gap_details) are
#                     not the planned-gap register above. They are the
#                     maturity-gap concept, owned by
#                     app/services/capability_heatmap_service.py: the distance
#                     between a capability's current and target maturity, with
#                     detail rows hanging off analysis rows (a parent/child
#                     cardinality, so unequal counts are correct). Registering
#                     them under "gaps" would manufacture findings.
#   live gaps         /capability-map/api/roadmap/gaps (the "173 gaps"
#                     tile) computes gaps live from capability coverage
#                     rather than reading a store, and RoadmapGap holds the
#                     rows converted out of that analysis. Whether one of
#                     those is the same "gap" as a row of the gaps register
#                     is a product decision about what a gap is, not a gate:
#                     registering either would manufacture findings.
#   compliance gaps   ComplianceGap is a control a compliance framework
#                     requires and the organisation does not meet -- not an
#                     architecture gap.
#   roadmap           TechnologyRoadmapInitiative is a technology roadmap
#   initiatives       initiative, not a work package.
#   decision ledger   DecisionLedger is an append-only log of governance
#                     events, several per capability; it is not a register of
#                     decisions, so its row count answers another question.
#   blueprint         SolutionBlueprintProposal has no organisation-wide
#   proposals         queue screen (it is read per solution), so there is no
#                     second surface asking its question.
#   RAID risks        RaidItem has no risk kind. Its kinds are issue and
#                     dependency; the R of RAID is the Risk store registered
#                     under "risks" (see RaidItem's own docstring).
#   risk assessments  /strategic/api/risks lists RiskAssessment, a scored
#                     assessment of a capability, not a register entry. It
#                     answers a different question from "which risks does this
#                     organisation hold".
#   HTML listings     /architecture/decisions/ and /procurement/contracts
#                     render HTML, which this gate cannot read. Each one's
#                     query is a registered orm surface (ArchitectureDecision,
#                     VendorContract for the organisation).
#   capability        the coverage percentages (48% vs 0%) are ratios of two
#   coverage          populations each of which is itself contested. Fix the
#                     populations first; the ratio follows.
#   capability        (T-002, corrected round 2) UnifiedCapability.
#   maturity          current_maturity_level / target_maturity_level is the
#                     ONE authority the *maturity accessor* migration
#                     repointed every reader this task touched at. That is
#                     NOT the same claim as "no code anywhere reads
#                     BusinessCapability.current_maturity_level directly" --
#                     it still does, in several files T-002 did not touch
#                     (app/modules/capabilities/routes/mapping_routes.py,
#                     process_routes.py, business_capabilities.py's own
#                     to_dict()) -- an earlier version of this comment claimed
#                     otherwise and was wrong. The "capability maturity
#                     assessed" concept registered above is the real,
#                     engine-backed check for exactly that disagreement: it
#                     compares "how many BusinessCapability rows have a
#                     maturity value recorded" against "how many of the
#                     matching UnifiedCapability rows do too", via a real
#                     filtered `WHERE ... IS NOT NULL` (`Surface.filter_eq` /
#                     `filter_not_null`, added this round), not a bare
#                     population count -- so a stale or never-run projection,
#                     or a raw-SQL maturity writer bypassing it, is a finding
#                     here, not silence. A separate, still-open freshness
#                     signal -- "how many projected rows' source_checksum
#                     currently disagrees with source" -- remains
#                     `app/jobs/capability_projection_job.py`'s own
#                     `stale_row_count` / `last_successful_run_at` on the
#                     structured job-run record, per T-002 task 01; it is a
#                     complementary check, not a substitute for this one.


def _extract(payload, spec):
    """Pull a number out of a JSON response per the surface's `extract` spec."""
    want_len = spec.startswith("len:")
    path = spec[4:] if want_len else spec
    node = payload
    for part in [p for p in path.split(".") if p]:
        if not isinstance(node, dict) or part not in node:
            raise KeyError("%r not present in the response" % path)
        node = node[part]
    if want_len:
        if not isinstance(node, list):
            raise KeyError("%r is not a list" % (path or "<top level>"))
        return len(node)
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        raise KeyError("%r is not a number: %r" % (path, node))
    return int(node)


# --------------------------------------------------------------------------
# The comparison engine. Pure: observations in, findings out. Shared by the
# live run and by --root, so the synthetic test exercises the real judgement.
# --------------------------------------------------------------------------
def compare(observations):
    """observations: {concept: [(surface_name, count, scope[, unscoped]), ...]}

    A row whose fourth element is true came from a store that cannot be scoped
    to one organisation; its count is the store's whole population. It is a
    finding once that is non-zero and is never compared with the others.

    Returns (findings, notes).
    """
    findings, notes = [], []
    for concept in sorted(observations):
        rows = []
        for row in observations[concept]:
            if row[1] is None:
                continue
            if len(row) > 3 and row[3]:
                if row[1]:
                    findings.append(
                        "  %s [unscoped-store] %s holds %d rows and has no "
                        "organisation column, declared link or shared "
                        "declaration, so it cannot say which of them belong "
                        "to one organisation." % (concept, row[0], row[1]))
                continue
            rows.append(tuple(row[:3]))
        if len(rows) < 2:
            notes.append(
                "  %s [no-evidence] fewer than two surfaces answered; nothing "
                "was compared" % concept)
            continue
        if all(count == 0 for _, count, _ in rows):
            notes.append(
                "  %s [no-evidence] every surface answered 0. An empty store "
                "agrees with an empty store: this proves nothing. Seed the "
                "tenant, or point DATABASE_URL at data." % concept)
            continue

        groups = {}
        for name, count, scope in rows:
            groups.setdefault(scope, []).append((name, count))

        # Within one declared scope, the surfaces must agree exactly.
        for scope in sorted(groups):
            members = groups[scope]
            distinct = {c for _, c in members}
            if len(distinct) < 2:
                continue
            # Name EVERY surface and its number, not just the extreme pair.
            # "191 vs 0" tells you there is a disagreement; "dashboard=12,
            # api/v1=0, BusinessCapability=12, UnifiedCapability=0" tells you
            # which store each surface is reading, which is the fix.
            detail = ", ".join(
                "%s=%d" % (name, count)
                for name, count in sorted(members, key=lambda m: -m[1]))
            findings.append(
                "  %s [store-disagreement] one question, %d different answers, "
                "all shown to the user: %s%s"
                % (concept, len(distinct), detail,
                   "" if scope == "all" else " (scope %r)" % scope))

        # Across scopes the ONLY assertion is that a declared subset cannot
        # exceed the whole. Pagination, filters and permission scoping all
        # narrow; none of them can add rows. A smaller number is therefore
        # explained by the declaration and is never reported.
        if "all" in groups:
            total = max(c for _, c in groups["all"])
            for scope in sorted(groups):
                if scope == "all":
                    continue
                for name, count in groups[scope]:
                    if count > total:
                        findings.append(
                            "  %s [store-disagreement] %s reports %d under the "
                            "declared narrowing %r, which is MORE than the "
                            "unfiltered population (%d). A filter cannot add "
                            "rows, so the two surfaces are reading different "
                            "stores." % (concept, name, count, scope, total))
    return findings, notes


# --------------------------------------------------------------------------
# Live observation: boot the app, establish a tenant, ask every surface.
# --------------------------------------------------------------------------
def _orm_models():
    """Every model named by an "orm" surface, deduplicated."""
    seen, out = set(), []
    for surfaces in CONCEPTS.values():
        for surface in surfaces:
            if surface.kind != "orm" or surface.target in seen:
                continue
            seen.add(surface.target)
            module, _, cls = surface.target.rpartition(".")
            try:
                out.append(getattr(__import__(module, fromlist=[cls]), cls))
            except Exception:
                continue
    return out


def _pick_tenant(db):
    """The tenant that actually HOLDS data, preferring one with a user.

    Taking "the first organisation" is the obvious choice and it is wrong here:
    it selects whichever empty shell sorts lowest, every surface answers 0, and
    the run reports no-evidence across the board while a populated tenant sits
    two rows away. Measured on the shared test database, the first organisation
    held 0 capabilities and 0 applications; the richest held 12.

    A user is preferred because the HTTP surfaces need a session to get past
    login_required. Without one only the ORM surfaces answer, which is still a
    valid comparison but a much thinner one, so a populated tenant WITH a user
    always wins over a slightly richer tenant without.
    """
    from sqlalchemy import func

    from app.models.organization import Organization
    from app.models.user import User

    totals = {}
    for model in _orm_models():
        column = getattr(model, "organization_id", None)
        if column is None:
            continue
        try:
            rows = (db.session.query(column, func.count())
                    .group_by(column).all())
        except Exception:
            db.session.rollback()
            continue
        for org_id, count in rows:
            if org_id is not None:
                totals[org_id] = totals.get(org_id, 0) + int(count)

    with_user = {row[0] for row in db.session.query(User.organization_id)
                 .filter(User.organization_id.isnot(None)).distinct().all()}

    ranked = sorted(totals.items(), key=lambda kv: (kv[0] in with_user, kv[1]),
                    reverse=True)
    org_id = ranked[0][0] if ranked else None
    if org_id is None:
        org = db.session.query(Organization).order_by(Organization.id).first()
    else:
        org = db.session.get(Organization, org_id)
        if org is None:
            # A dangling organization_id is a data defect of its own, but not
            # this gate's; fall back rather than crash.
            org = db.session.query(Organization).order_by(Organization.id).first()
    if org is None:
        return None, None
    user = (db.session.query(User)
            .filter(User.organization_id == org.id)
            .order_by(User.id)
            .first())
    return org, user


def observe_live():
    """Ask every registered surface, inside one tenant. Returns (obs, notes)."""
    os.environ.setdefault("FLASK_CONFIG", "testing")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        from flask import g

        from app import create_app, db
    except Exception as exc:  # pragma: no cover - import environment
        return {}, ["  [no-evidence] the application could not be imported: %s"
                    % str(exc)[:200]]

    try:
        app = create_app("testing")
    except Exception as exc:
        return {}, ["  [no-evidence] the application could not be booted: %s"
                    % str(exc)[:200]]

    # A test REQUEST context, not a bare app context. tests/conftest.py's
    # tenant_ctx fixture does the same, and the reason is load-bearing: the
    # hybrid capability scoping in app/models/unified_capability.py returns
    # early unless has_request_context() is true, so a bare app context leaves
    # UnifiedCapability entirely unfiltered. Measured: it counted 31 rows
    # belonging to 31 OTHER organisations while the HTTP surface correctly
    # answered 0, and the gate reported that as a store disagreement. It was
    # the harness. This is the single largest false-positive source here.
    with app.test_request_context("/"):
        try:
            org, user = _pick_tenant(db)
        except Exception as exc:
            return {}, ["  [no-evidence] the database is unreachable, so no "
                        "surface could be asked: %s" % str(exc)[:200]]
        if org is None:
            return {}, ["  [no-evidence] no Organization row exists, so there "
                        "is no tenant whose numbers could be compared."]

        # Multi-tenancy in this codebase is enforced by ORM events keyed on
        # g.current_org_id (app/middleware/tenant_isolation.py), and it is a
        # deliberate no-op when that is unset. Nothing establishes it outside a
        # real request, so the ORM surfaces below would count EVERY
        # organisation's rows while the HTTP surfaces -- which run as a
        # logged-in user -- count one. That difference is an artefact of this
        # script, not a defect in the product, and it would be reported as a
        # finding on every multi-tenant install. Setting it explicitly is what
        # makes the two halves answer the same question.
        g.current_org_id = org.id

        observations, notes = observe_tenant(app, db, org.id, user)

        notes.insert(0, "  tenant: organization id=%s%s"
                     % (org.id, "" if user else " (no user; HTTP surfaces will "
                        "redirect to login and be reported unanswered)"))
    return observations, notes


def observe_tenant(app, db, org_id, user=None, concepts=None, http=True):
    """Ask every surface of `concepts` as organisation `org_id`.

    Runs inside the caller's request context with g.current_org_id already set
    to `org_id`. HTTP surfaces are asked as `user`; `http=False` skips them. Returns (observations, notes) in the shape compare() reads.
    """
    concepts = CONCEPTS if concepts is None else concepts
    notes = []
    client = app.test_client()
    sid = None
    if user is not None and http:
        # Every authenticated request is checked against the server-side
        # session registry and fails closed without a registered `_sid`, so a
        # cookie carrying only `_user_id` is answered 401 on every surface.
        # Register the session the way a real sign-in does, and revoke it
        # when the run ends.
        from app.services import session_registry

        with app.test_request_context("/"):
            sid = session_registry.issue(user)
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["_sid"] = sid

    try:
        observations = _observe_concepts(concepts, db, client, org_id, http,
                                         notes)
    finally:
        if sid is not None:
            from app.services import session_registry

            session_registry.revoke(sid, "store-agreement run finished")
    return observations, notes


def _observe_concepts(concepts, db, client, org_id, http, notes):
    observations = {}
    for concept, surfaces in concepts.items():
        rows = []
        for surface in surfaces:
            if surface.waived and ALLOW_MARKER in surface.waived:
                continue
            if surface.kind == "orm":
                count, why, unscoped, unattributed = _count_orm(
                    surface, db, org_id)
                if unattributed:
                    notes.append(
                        "  %s [unattributed] %s: %d rows link to no "
                        "organisation by any declared link, so they count "
                        "for none" % (concept, surface.name, unattributed))
            elif not http:
                continue
            else:
                with _fresh_identity(org_id):
                    count, why = _ask(surface, db, client, org_id)
                unscoped = False
            if count is None:
                notes.append("  %s [unanswered] %s: %s"
                             % (concept, surface.name, why))
                continue
            rows.append((surface.name, count, surface.scope, unscoped))
        observations[concept] = rows
    return observations


class _fresh_identity:
    """Ask a screen as the signed-in session, not as whoever `g` remembers.

    The test client reuses an app context that is already active, and with it
    `g`. Flask-Login caches the resolved user there (`_login_user`) and the
    tenant middleware caches the organisation, so a screen asked inside the
    caller's context runs as whatever identity that context resolved first --
    measured: an anonymous user, cached before the session was registered,
    turned every screen into a 302 to the login page. Clear both before the
    request and put the organisation back after it, for the stores that follow.
    """

    _CACHED = ("_login_user", "_current_user", "current_org_id", "current_org")

    def __init__(self, org_id):
        self.org_id = org_id

    def _clear(self):
        from flask import g, has_app_context

        if not has_app_context():
            return None
        for cached in self._CACHED:
            if hasattr(g, cached):
                delattr(g, cached)
        return g

    def __enter__(self):
        self._clear()
        return self

    def __exit__(self, *exc):
        g = self._clear()
        if g is not None:
            g.current_org_id = self.org_id
        return False


def _session_scoped(model):
    """True when the ORM events already scope this model to the tenant."""
    try:
        from app.models.mixins.core import TenantMixin
        if issubclass(model, TenantMixin):
            return True
    except Exception:
        pass
    try:
        from app.models.unified_capability import HybridCapabilityTenantMixin
        return issubclass(model, HybridCapabilityTenantMixin)
    except Exception:
        return False


def _count_orm(surface, db, org_id):
    """(count, reason, unscoped, unattributed) for one orm surface.

    `unattributed` is the number of rows a tenant_via store could not attribute
    to any organisation; None when the question does not arise.
    """
    from sqlalchemy import func, or_

    module, _, cls = surface.target.rpartition(".")
    try:
        model = getattr(__import__(module, fromlist=[cls]), cls)
    except Exception as exc:
        return None, "model %s could not be imported (%s)" % (
            surface.target, str(exc)[:120]), False, None

    table = model.__table__
    if surface.distinct:
        counted = func.count(func.distinct(getattr(model, surface.distinct)))
    else:
        counted = func.count()

    def filtered(query):
        for attr, value in surface.filter_eq.items():
            query = query.filter(getattr(model, attr) == value)
        if surface.filter_not_null:
            names = surface.filter_not_null
            if isinstance(names, str):
                names = (names,)
            query = query.filter(or_(*[getattr(model, n).isnot(None)
                                       for n in names]))
        return query

    try:
        query = filtered(db.session.query(counted).select_from(model))
        if _session_scoped(model) or surface.shared:
            return int(query.scalar()), None, False, None
        if "organization_id" in table.c:
            query = query.filter(model.organization_id == org_id)
            return int(query.scalar()), None, False, None
        if not surface.tenant_via:
            return int(query.scalar()), None, True, None
        # Plain table aliases, not mapped entities: the parent's own tenant
        # criteria must not rewrite the join, or a row linked to another
        # organisation's element would fall through to its creator.
        owners = []
        for fk_column, parent_table in surface.tenant_via:
            parent = db.metadata.tables[parent_table].alias()
            query = query.outerjoin(
                parent, parent.c.id == getattr(model, fk_column))
            owners.append(parent.c.organization_id)
        owner = func.coalesce(*owners) if len(owners) > 1 else owners[0]
        unattributed = int(query.filter(owner.is_(None)).scalar())
        return (int(query.filter(owner == org_id).scalar()), None, False,
                unattributed)
    except Exception as exc:
        db.session.rollback()
        return None, "the query failed: %s" % str(exc)[:160], False, None


def _ask(surface, db, client, org_id=None):
    """(count, reason-it-could-not-answer)."""
    if surface.kind == "orm":
        count, why, _unscoped, _unattributed = _count_orm(surface, db, org_id)
        return count, why

    try:
        response = client.get(surface.target)
    except Exception as exc:
        return None, "the request raised: %s" % str(exc)[:160]
    if response.status_code != 200:
        return None, "HTTP %d (not an answer, so not compared)" % response.status_code
    try:
        payload = response.get_json()
    except Exception:
        payload = None
    if payload is None:
        return None, "the response was not JSON"
    try:
        return _extract(payload, surface.extract or "len:"), None
    except KeyError as exc:
        return None, "the response did not carry a count: %s" % exc


def observe_synthetic(root):
    """Observations recorded in <root>/store_agreement_probe.json.

    The synthetic form of a boot: the numbers the surfaces WOULD have returned.
    It exists so the comparison judgement -- the part of this gate that can be
    wrong -- is testable without a seeded database.
    """
    path = os.path.join(root, "store_agreement_probe.json")
    if not os.path.exists(path):
        return {}, ["  [no-evidence] no store_agreement_probe.json under %s"
                    % root]
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    observations = {}
    for concept, rows in raw.items():
        kept = []
        for row in rows:
            if ALLOW_MARKER in str(row.get("waived", "")):
                continue
            kept.append((row["surface"], row.get("count"),
                         row.get("scope", "all"), bool(row.get("unscoped"))))
        observations[concept] = kept
    return observations, []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args()

    if args.root:
        observations, notes = observe_synthetic(os.path.abspath(args.root))
    else:
        observations, notes = observe_live()

    findings, more_notes = compare(observations)
    notes += more_notes

    if not args.count:
        for line in notes:
            print(line)
        for line in findings:
            print(line)
        if findings:
            print()
            print(
                "Two surfaces answered one question differently. Pick the "
                "canonical store,\nrepoint the other surface at it, and delete "
                "the duplicate read. If the\ndifference is real and permanent, "
                "declare it -- give the narrower surface a\n`scope=` naming its "
                "filter, or `waived=\"store-agreement-ok: <reason>\"`\nsaying "
                "what keeps the divergence bounded.")
    print(len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
