"""DE-14: the closed reason-code vocabulary (FR-15, NFR-2).

One vocabulary, read by both the REST and MCP surfaces (SEC-15; sdd-v2.md
§ API-8). No endpoint may invent an absence string inline — a new absence
condition adds a member here, and nowhere else.

Every code renders as an informative empty state with a one-click fix, never
a blank, a zero or an estimate (UX principle P-3; the ``fabricated-data``
gate enforces this mechanically at the template layer, out of scope here).
"""

from __future__ import annotations

# sdd-v2.md § API-8 — the original sixteen members, the two T-004 additions,
# the one T-005 addition, the Portfolio, Programme and Strategy lenses' four
# additions, the Accountability lens's two plus its withdrawal reason,
# role-gating's addition, the four T-S1 additions, the programme lens's own
# plateau/gap pair, the two maturity-read-helper additions, the
# Portfolio-block's three additions, the licence-sync addition and the four
# connection-explanation additions and the impact criticality/recovery-
# objectives/contract additions below (fifty-two total, re-derived rather
# than trusted from any one side's own stale count), exactly, nothing
# invented.
REASON_CODES = frozenset(
    {
        "no_ownership_recorded",
        "no_maturity_recorded",
        "derivation_not_computed",
        "derivation_stale",
        "no_crosswalk_match",
        "no_crosswalk_row",
        "crosswalk_element_deleted",
        "external_id_maps_to_many_elements",
        "source_unavailable",
        "no_risk_recorded",
        "no_realising_element",
        "no_initiative_linked",
        "initiative_not_linked_to_model",
        "review_item_not_visible",
        "insufficient_samples_for_p95",
        "feed_not_connected",
        # T-004 (US-1) additions: absence conditions on the cross-layer
        # impact read path that the original sixteen do not cover. Added
        # here, and nowhere else, per this module's own rule that no
        # endpoint may invent an absence string inline.
        "no_tenant_context",
        "element_not_found",
        # T-005 (US-5) addition: the yield endpoint's p95 bucket-edge read
        # (D3) has no honest number to report when the 95th percentile falls
        # in the histogram's +Inf overflow bucket -- reporting the top
        # declared boundary (5.0) as if it were the measured value would be
        # exactly the fabrication CLAUDE.md's "never invent data" rule
        # forbids.
        "p95_above_highest_bucket",
        # L3/L6 brief (2026-09-21) addition: Ask's Portfolio lens resolves an
        # ArchiMate element to its ApplicationComponent (the row the
        # rationalization/duplicate/TCO pages are keyed on) before it can
        # offer a deep link. Not every element is one -- most are not -- and
        # that is an honest absence, not an error.
        "no_application_component",
        # L5 brief (2026-09-22) additions: Ask's Programme lens seeds from
        # UnifiedWorkPackage rows linked to the picked element. Most
        # elements have none -- an honest absence, matching L6's
        # no_risk_recorded precedent -- and a real work package with no
        # estimated_cost is a distinct fact from "unknown": nobody budgeted
        # it, not that the figure failed to load.
        "no_work_package_recorded",
        "not_costed",
        # L2 brief (2026-09-22) addition: Ask's Strategy lens seeds from
        # PortfolioInitiative rows linked to the picked element (the
        # existing "no_initiative_linked" member above covers that honest
        # absence). A real initiative with no total_budget is a distinct
        # fact from "unknown" -- nobody budgeted it -- the same distinction
        # L5's not_costed draws for a different field pair
        # (UnifiedWorkPackage.estimated_cost/actual_cost); not_costed
        # itself is not reused here so each code stays tied to one field
        # pair's own absence condition.
        "no_budget_recorded",
        # L4 brief (2026-09-22) additions: Ask's Accountability lens resolves
        # an element to its ApplicationComponent (reusing L3's own
        # resolution) then lists ApplicationOwnership rows for it.
        # no_ownership_records covers the honest-empty case -- a real
        # component with zero ownership rows -- distinct from the
        # pre-existing no_ownership_recorded (a single element's owner
        # field inside the L1 impact traversal, a different table and a
        # different absence condition). capacity_not_available is not a
        # per-request absence at all: no Workforce/Skill/Headcount model
        # exists anywhere in this codebase, so every accountability
        # response, success included, honestly discloses that gap rather
        # than silently answering only half the lens's own question.
        "no_ownership_records",
        "capacity_not_available",
        # Role-gating brief (2026-09-22): financial figures on the Strategy
        # and Programme lenses (budget/cost variance) are redacted at the
        # route layer for roles without budget authority (mirrors
        # ROLE_SECTION_ACCESS's existing role-gating precedent, applied here
        # per-field rather than per-page). Redaction is honest, not silent:
        # the field is None and this reason names why, the same discipline
        # not_costed/no_budget_recorded already use for a different kind of
        # absence.
        "financial_data_restricted",
        # PR #107 defect remediation (2026-09-23): the L4 Accountability
        # lens's ownership read is withdrawn -- the original implementation
        # read ApplicationOwnership/OrganizationUnit directly with a real,
        # unreviewed tenant-isolation gap on OrganizationUnit (no
        # TenantMixin, no tenant predicate on the fetch), rather than reuse
        # the existing tenant-checked _resolve_owners_batch pattern. Every
        # accountability response carries this reason until a shared,
        # tenant-safe reader exists -- see
        # IntelligenceQueryService.accountability_for_element's docstring.
        # Distinct from a "decision pending" state: the data source is
        # already decided, only the safe reader is missing.
        "ownership_reader_not_built",
        # T-S1 (value streams at risk, curated path) additions: absence
        # conditions the original vocabulary has no member for. T-S1 emits
        # the first two -- no value stream recorded for this tenant, and a
        # value stream with no capability recorded against it by any path.
        # The other two are reserved for T-S3, which adds the graph path
        # (an explicit or derived dependency) this task deliberately does
        # not read -- they are not reachable until that task lands.
        "no_value_stream_recorded",
        "no_capability_linked",
        "value_stream_not_linked_to_model",
        "dependency_direction_unknown",
        # Impact criticality and recovery objectives: absence conditions on the
        # cross-layer impact read path that the existing vocabulary does not
        # cover. Added here, and nowhere else, per this module's own rule that
        # no endpoint may invent an absence string inline.
        "no_criticality_recorded",
        "no_recovery_objective_recorded",
        # Impact answer: an application with no contract recorded against it.
        "no_contract_recorded",
        # L7 (Data lens) additions: Ask's Data lens lists the DataObject rows
        # linked to the picked element. Most elements have none, an honest
        # absence; an object with neither a steward nor an owner recorded is a
        # distinct fact (nobody is named, not that the name failed to load);
        # and an object with no lineage edge recorded in or out has no known
        # flow. Each stays tied to its own absence condition.
        "no_data_recorded",
        "no_steward_recorded",
        "no_lineage_recorded",
        # Compliance (under L6) additions: Ask's Compliance question reads the
        # controls an application is mapped to. No mapping is an honest
        # absence (never "0% compliant"); a mapped control with neither
        # evidence nor a verification date is a distinct fact from "not
        # mapped"; and no policy scan on record means the last-scan time is
        # unknown, not that everything passed.
        "no_compliance_controls_recorded",
        "no_control_evidence",
        "no_policy_scan_recorded",
        # The programme lens's own plateau/gap block: a work package's stored
        # plateau_id/gap_id may be unset (a nullable FK), or, in principle,
        # point at a record belonging to a different tenant (the FK itself
        # carries no tenant check, so the select that resolves it is what
        # enforces the boundary) -- both are honest absences, not errors.
        "no_plateau_recorded",
        "no_gap_recorded",
        # Maturity read helper additions: the canonical maturity surface's
        # one batched read (CapabilityHeatmapService.maturity_for_elements /
        # .maturity_for_capability_ids) has two absence conditions the
        # existing "no_maturity_recorded" does not distinguish -- a current
        # level recorded with no target to compare it against, and (reserved
        # for a later reader) an answer with no Capability element in its
        # chain at all.
        "no_maturity_target_recorded",
        "no_capability_in_chain",
        # The Portfolio lens's component block adds cost, health and
        # licence facts read from the columns already entered on the
        # resolved ApplicationComponent (and the two tables keyed off it).
        # Each part of that block carries its own absence condition,
        # distinct from the pre-existing no_application_component (no
        # component at all): a component can exist with no cost figures
        # entered, no owner-recorded health status, no fiscal-period cost
        # row and no licence entitlement rows, independently of one
        # another.
        "no_cost_recorded",
        "no_health_recorded",
        "no_licence_recorded",
        # A licence's usage columns are only ever populated once a sync has
        # run against the source system; quantity_used carries a column
        # default of zero for a licence that has never been synced, so
        # comparing it against quantity_entitled would report an invented
        # under-use finding rather than a measurement. Distinct from
        # no_licence_recorded above, which means no licence rows exist for
        # the component at all.
        "licence_usage_not_synced",
        # Connection explanation additions: the "Why?" read of one derived
        # row (services/explanation.py) turns its stored chain back into the
        # drawn relationships. A chain id that no longer resolves inside the
        # organisation, a relationship nobody is recorded as having drawn or
        # with no creation time, and a rule id the rule table does not
        # describe are each an honest absence shown as "not recorded" --
        # never dropped, never filled in. An element on the chain that does
        # not resolve reuses the existing element_not_found.
        "relationship_not_recorded",
        "drawn_by_not_recorded",
        "drawn_at_not_recorded",
        "rule_not_recorded",
    }
)


class UnknownReasonCodeError(ValueError):
    """A caller asked for a reason code outside the DE-14 closed vocabulary."""


def is_valid_reason_code(code: str) -> bool:
    """True when *code* is a member of the closed vocabulary."""
    return code in REASON_CODES


def validate_reason_code(code: str) -> str:
    """Return *code* unchanged if it is a member of the closed vocabulary.

    Raises ``UnknownReasonCodeError`` otherwise. This is the enforcement
    point that keeps a later endpoint from inventing an absence string
    inline (API-8) — call it wherever a reason code is about to leave this
    module, rather than passing a raw string through unchecked.
    """
    if code not in REASON_CODES:
        raise UnknownReasonCodeError(
            f"{code!r} is not a member of the DE-14 reason-code vocabulary"
        )
    return code


__all__ = [
    "REASON_CODES",
    "UnknownReasonCodeError",
    "is_valid_reason_code",
    "validate_reason_code",
]
