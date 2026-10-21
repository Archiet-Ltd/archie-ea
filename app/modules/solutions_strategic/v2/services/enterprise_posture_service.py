"""Enterprise posture lenses for the Chief Architect Workbench.

`ChiefArchitectService.portfolio_synthesis` answers "how are the *solutions*
doing?".  A Chief Architect also has to answer "how is the *enterprise* doing?"
— the capability model, the application portfolio, technology standards and
their exceptions, the roadmap, and the decision record.  This module supplies
those lenses.

Three rules govern every number in here, and they are the reason the module
exists rather than reusing an existing "health" service:

1.  **Only directly countable record facts.**  Every measure is a ``COUNT`` over
    a named column of a named model, with its own explicit denominator.  There
    is deliberately no composite score.  Several services in this tree compute a
    "health score" from hardcoded weights (start at 100, subtract ``gap * 15``,
    …); those are heuristics presented as measurements, and consuming one here
    would launder it into the Chief Architect's headline view.  A reader can
    reproduce every number below with a single SQL ``COUNT``.

2.  **A failure is reported, never rounded down to zero.**  Each lens is
    computed inside its own ``try``; on failure the lens goes to
    ``state="unavailable"`` with every value ``None``, which the template renders
    as an em dash.  A ``0`` here would be indistinguishable from a measured zero.

3.  **Missing information is a first-class result.**  ``missing`` counts records
    that exist but do not carry the field, so "we have not modelled this" is
    visible instead of silently shrinking a denominator.

Tenancy: every model read here carries ``TenantMixin``, so the
``do_orm_execute`` filter scopes these counts to ``g.current_org_id`` inside a
request.  Called outside a request context they are unscoped — see AGENTS.md.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from app import db

logger = logging.getLogger(__name__)

#: Records whose review/renewal date falls inside this window are "due soon"
#: rather than merely "dated". 90 days is the ARB renewal reminder horizon used
#: by ``ARBExceptionService.check_expiring_exceptions``; reused so the workbench
#: and the exception process agree on what "expiring" means.
EXPIRY_HORIZON_DAYS = 90


def _url(endpoint: str, **values: Any) -> Optional[str]:
    """Build a URL for ``endpoint``, or ``None`` when it is not registered.

    Blueprints in this app register non-fatally (AGENTS.md), so a bare
    ``url_for`` to a module that failed to import raises ``BuildError`` and 500s
    every page that renders it. Drill-down targets here span a dozen blueprints,
    so each one is resolved defensively and the template simply renders no link
    when the target is absent.
    """
    from flask import current_app, has_app_context, url_for

    if not has_app_context() or endpoint not in current_app.view_functions:
        return None
    try:
        return url_for(endpoint, **values)
    except Exception as exc:  # noqa: BLE001 - a link is never worth a 500
        logger.debug("drill-down url_for(%s) failed: %s", endpoint, exc)
        return None


def _first_url(*endpoints: str) -> Optional[str]:
    """First registered endpoint among ``endpoints``, or ``None``."""
    for endpoint in endpoints:
        built = _url(endpoint)
        if built is not None:
            return built
    return None


#: One plain sentence per measurement basis. The raw basis (model, column,
#: predicate) stays in ``source`` for machine readers; a person reads
#: ``source_text``. A basis with no entry here is derived from its field name by
#: ``plain_source`` so a new measure never prints a raw predicate.
SOURCE_LABELS: Dict[str, str] = {
    "BusinessCapability.current_maturity_level": "Counted from the maturity level recorded on each capability",
    "BusinessCapability.maturity_gap > 0": "Capabilities whose current maturity is below target",
    "ValueStream.current_cycle_time + target_cycle_time": "Value streams with both a current and a target cycle time recorded",
    "BusinessCapability.current_maturity_level IS NULL": "No maturity level recorded",
    "BusinessCapability.business_owner IS NULL": "No business owner recorded",
    "ValueStream.current_cycle_time IS NULL": "No current cycle time recorded",
    "ApplicationComponent.end_of_life_date < today": "End-of-life date has already passed",
    "ApplicationComponent.end_of_life_date <= today + 365d": "End-of-life date falls within the next year",
    "ApplicationComponent.lifecycle_status": "Counted from the lifecycle status recorded on each application",
    "ApplicationComponent.criticality": "Counted from the criticality recorded on each application",
    "ApplicationComponent.end_of_life_date IS NULL": "No end-of-life date recorded",
    "ApplicationComponent.lifecycle_status IS NULL": "No lifecycle status recorded",
    "ApplicationComponent.application_owner IS NULL": "No owner recorded",
    "TechnologyStandard.review_date < today": "Review date has already passed",
    "TechnologyStandard.sunset_date < today": "Sunset date has already passed",
    "ARBException.status='approved' AND revoked_at IS NULL": "Approved and not withdrawn",
    "ARBException.expires_at": "Counted from the expiry date recorded on each exception",
    "TechnologyStandard.review_date IS NULL": "No review date recorded",
    "TechnologyStandard.owner_id IS NULL": "No owner recorded",
    "ARBException.expires_at IS NULL": "No expiry date recorded",
    "WorkPackage.target_date < today AND completed_date IS NULL": "Target date has passed and the work is not complete",
    "Plateau.target_date": "Counted from the target date recorded on each plateau",
    "Gap.resolution_status NOT IN (resolved, closed, complete, completed)": "Gaps that are still open",
    "Gap.target_resolution_date < today": "Target resolution date has already passed",
    "WorkPackage.target_date IS NULL": "No target date recorded",
    "Plateau.target_date IS NULL": "No target date recorded",
    "Gap.owner IS NULL": "No owner recorded",
    "ArchitectureDecision.status = 'accepted'": "Decisions recorded as accepted",
    "ArchitectureDecision.status = 'proposed'": "Decisions recorded as proposed",
    "ArchitectureDecision.superseded_by_id": "Decisions that have been replaced by a later decision",
    "ArchitectureDecision.rationale IS NULL": "No rationale recorded",
    "ArchitectureDecision.consequences IS NULL": "No consequences recorded",
    "ArchitectureDecision.decided_at IS NULL": "No decision date recorded",
}


def plain_source(source: Optional[str]) -> str:
    """The sentence a person reads for a measurement basis.

    Looked up once in ``SOURCE_LABELS``; otherwise derived from the field name
    ("business_owner IS NULL" reads "No business owner recorded").
    """
    if not source:
        return ""
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    first = source.split(" ")[0]
    field = first.split(".")[-1]
    words = field.replace("_id", "").replace("_", " ").strip() or "value"
    if " IS NULL" in source:
        return f"No {words} recorded"
    return f"Counted from the {words} recorded on each record"


def _measure(
    label: str,
    value: Optional[int],
    *,
    source: str,
    of: Optional[int] = None,
    hint: Optional[str] = None,
    href: Optional[str] = None,
) -> Dict[str, Any]:
    """A measured fact: a count, its denominator, and the column it came from.

    ``source`` is rendered in the UI verbatim so a reader can trace the number to
    a model and column without leaving the page.
    """
    return {
        "kind": "measured",
        "label": label,
        "value": value,
        "of": of,
        "source": source,
        "source_text": plain_source(source),
        "hint": hint,
        "href": href,
    }


def _missing(
    label: str,
    count: Optional[int],
    *,
    source: str,
    of: Optional[int] = None,
    href: Optional[str] = None,
) -> Dict[str, Any]:
    """Records that exist but do not carry the field named by ``source``."""
    return {
        "kind": "missing",
        "label": label,
        "value": count,
        "of": of,
        "source": source,
        "source_text": plain_source(source),
        "hint": None,
        "href": href,
    }


def _attention(
    *,
    key: str,
    source_label: str,
    name: str,
    title: str,
    severity: str,
    reason: str,
    next_action: str,
    action_url: Optional[str],
    count: Optional[int] = None,
) -> Dict[str, Any]:
    """An attention-queue row in the shape ``ChiefArchitectService`` already sorts.

    ``_prioritise_attention`` keys on ``severity``, ``age_days``, ``name`` and
    ``id``; enterprise lenses are aggregate rather than per-record, so they carry
    ``age_days=None`` (sorted after dated items of the same severity) and a
    stable string ``id``.
    """
    return {
        "id": key,
        "kind": "enterprise",
        "source_label": source_label,
        "name": name,
        "title": title,
        "status": "not_ready",
        "severity": severity,
        "age_days": None,
        "reason": reason,
        "score": None,
        "flagged": count,
        "evidence_url": action_url,
        "next_action": next_action,
        "action_url": action_url,
    }


def _unavailable_lens(key: str, label: str, icon: str, reason: str) -> Dict[str, Any]:
    """A lens whose query failed: no measures, an explicit reason, no zeroes."""
    return {
        "key": key,
        "label": label,
        "icon": icon,
        "state": "unavailable",
        "reason": reason,
        "total": None,
        "total_label": None,
        "href": None,
        "measures": [],
        "missing": [],
    }


class EnterprisePostureService:
    """Cross-domain, count-only lenses over the enterprise architecture record."""

    @staticmethod
    def _today() -> date:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).date()

    # ── lens: capability & value streams ────────────────────────────────────
    @classmethod
    def _capability_lens(cls) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        from app.models.business_capabilities import BusinessCapability
        from app.models.unified_capability import ValueStream

        cap_href = _url("capability_map.index")
        vs_href = _url("value_stream.index")

        total = BusinessCapability.query.count()
        assessed = BusinessCapability.query.filter(
            BusinessCapability.current_maturity_level.isnot(None)
        ).count()
        with_gap = BusinessCapability.query.filter(
            BusinessCapability.maturity_gap.isnot(None),
            BusinessCapability.maturity_gap > 0,
        ).count()
        no_owner = BusinessCapability.query.filter(
            db.or_(
                BusinessCapability.business_owner.is_(None),
                BusinessCapability.business_owner == "",
            )
        ).count()

        streams = ValueStream.query.count()
        streams_measured = ValueStream.query.filter(
            ValueStream.current_cycle_time.isnot(None),
            ValueStream.target_cycle_time.isnot(None),
        ).count()

        lens = {
            "key": "capability",
            "label": "Capability & value streams",
            "icon": "grid-3x3",
            "state": "measured" if (total or streams) else "empty",
            "reason": None,
            "total": total,
            "total_label": "business capabilities",
            "href": cap_href,
            "measures": [
                _measure(
                    "Maturity assessed",
                    assessed,
                    of=total,
                    source="BusinessCapability.current_maturity_level",
                    href=cap_href,
                ),
                _measure(
                    "Below target maturity",
                    with_gap,
                    of=total,
                    source="BusinessCapability.maturity_gap > 0",
                    href=cap_href,
                ),
                _measure(
                    "Value streams with cycle time measured",
                    streams_measured,
                    of=streams,
                    source="ValueStream.current_cycle_time + target_cycle_time",
                    href=vs_href,
                ),
            ],
            "missing": [
                _missing(
                    "Capabilities with no maturity assessment",
                    total - assessed,
                    of=total,
                    source="BusinessCapability.current_maturity_level IS NULL",
                    href=cap_href,
                ),
                _missing(
                    "Capabilities with no business owner",
                    no_owner,
                    of=total,
                    source="BusinessCapability.business_owner IS NULL",
                    href=cap_href,
                ),
                _missing(
                    "Value streams with no measured cycle time",
                    streams - streams_measured,
                    of=streams,
                    source="ValueStream.current_cycle_time IS NULL",
                    href=vs_href,
                ),
            ],
        }

        attention: List[Dict[str, Any]] = []
        unassessed = total - assessed
        if total and unassessed:
            attention.append(
                _attention(
                    key="capability-unassessed",
                    source_label="Capability model",
                    name="Capability model",
                    title=f"{unassessed} of {total} capabilities have no maturity assessment",
                    severity="high" if unassessed * 2 >= total else "medium",
                    reason=(
                        "Capability maturity drives investment prioritisation; "
                        "unassessed capabilities cannot be ranked against the rest."
                    ),
                    next_action="Assess maturity for the unassessed capabilities",
                    action_url=cap_href,
                    count=unassessed,
                )
            )
        return lens, attention

    # ── lens: application portfolio ─────────────────────────────────────────
    @classmethod
    def _application_lens(cls) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        from app.models.application_portfolio import ApplicationComponent

        today = cls._today()
        horizon = today + timedelta(days=365)
        apps_href = _first_url(
            "unified_applications.application_list",
            "unified_applications.rationalization_dashboard",
        )

        total = ApplicationComponent.query.count()
        with_lifecycle = ApplicationComponent.query.filter(
            ApplicationComponent.lifecycle_status.isnot(None),
            ApplicationComponent.lifecycle_status != "",
        ).count()
        with_criticality = ApplicationComponent.query.filter(
            ApplicationComponent.criticality.isnot(None),
            ApplicationComponent.criticality != "",
        ).count()
        past_eol = ApplicationComponent.query.filter(
            ApplicationComponent.end_of_life_date.isnot(None),
            ApplicationComponent.end_of_life_date < today,
        ).count()
        eol_within_year = ApplicationComponent.query.filter(
            ApplicationComponent.end_of_life_date.isnot(None),
            ApplicationComponent.end_of_life_date >= today,
            ApplicationComponent.end_of_life_date <= horizon,
        ).count()
        dated_eol = ApplicationComponent.query.filter(
            ApplicationComponent.end_of_life_date.isnot(None)
        ).count()
        no_owner = ApplicationComponent.query.filter(
            db.or_(
                ApplicationComponent.application_owner.is_(None),
                ApplicationComponent.application_owner == "",
            )
        ).count()

        lens = {
            "key": "application",
            "label": "Application portfolio",
            "icon": "layout-grid",
            "state": "measured" if total else "empty",
            "reason": None,
            "total": total,
            "total_label": "application components",
            "href": apps_href,
            "measures": [
                _measure(
                    "Past end of life",
                    past_eol,
                    of=dated_eol,
                    source="ApplicationComponent.end_of_life_date < today",
                    hint="Denominator is applications carrying an end-of-life date.",
                    href=apps_href,
                ),
                _measure(
                    "End of life within 12 months",
                    eol_within_year,
                    of=dated_eol,
                    source="ApplicationComponent.end_of_life_date <= today + 365d",
                    href=apps_href,
                ),
                _measure(
                    "Lifecycle status recorded",
                    with_lifecycle,
                    of=total,
                    source="ApplicationComponent.lifecycle_status",
                    href=apps_href,
                ),
                _measure(
                    "Criticality recorded",
                    with_criticality,
                    of=total,
                    source="ApplicationComponent.criticality",
                    href=apps_href,
                ),
            ],
            "missing": [
                _missing(
                    "Applications with no end-of-life date",
                    total - dated_eol,
                    of=total,
                    source="ApplicationComponent.end_of_life_date IS NULL",
                    href=apps_href,
                ),
                _missing(
                    "Applications with no lifecycle status",
                    total - with_lifecycle,
                    of=total,
                    source="ApplicationComponent.lifecycle_status IS NULL",
                    href=apps_href,
                ),
                _missing(
                    "Applications with no recorded owner",
                    no_owner,
                    of=total,
                    source="ApplicationComponent.application_owner IS NULL",
                    href=apps_href,
                ),
            ],
        }

        attention: List[Dict[str, Any]] = []
        if past_eol:
            attention.append(
                _attention(
                    key="application-past-eol",
                    source_label="Application portfolio",
                    name="Application portfolio",
                    title=f"{past_eol} application(s) are past their recorded end-of-life date",
                    severity="critical",
                    reason=(
                        "These applications carry an end-of-life date that has already passed, "
                        "so they are running unsupported unless the date is stale."
                    ),
                    next_action="Retire, upgrade, or correct the end-of-life date",
                    action_url=apps_href,
                    count=past_eol,
                )
            )
        if eol_within_year:
            attention.append(
                _attention(
                    key="application-eol-soon",
                    source_label="Application portfolio",
                    name="Application portfolio",
                    title=f"{eol_within_year} application(s) reach end of life within 12 months",
                    severity="high",
                    reason="Obsolescence is dated and inside the annual planning horizon.",
                    next_action="Add these to the roadmap as work packages",
                    action_url=apps_href,
                    count=eol_within_year,
                )
            )
        undated = total - dated_eol
        if total and undated:
            attention.append(
                _attention(
                    key="application-eol-unknown",
                    source_label="Application portfolio",
                    name="Application portfolio",
                    title=f"{undated} of {total} applications have no end-of-life date",
                    severity="medium",
                    reason=(
                        "Obsolescence exposure cannot be measured for these records — "
                        "they are absent from the numerator and the denominator above."
                    ),
                    next_action="Record end-of-life dates so obsolescence can be measured",
                    action_url=apps_href,
                    count=undated,
                )
            )
        return lens, attention

    # ── lens: standards & exceptions ────────────────────────────────────────
    @classmethod
    def _standards_lens(cls) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        from app.models.architecture_review_board import ARBException
        from app.models.technology_standard import TechnologyStandard

        today = cls._today()
        horizon_days = EXPIRY_HORIZON_DAYS
        standards_href = _first_url("arb.standards", "arb.dashboard")
        exceptions_href = _first_url("arb.standards", "arb.dashboard")

        active = TechnologyStandard.query.filter(
            TechnologyStandard.is_active.is_(True)
        ).count()
        total_standards = TechnologyStandard.query.count()
        overdue_review = TechnologyStandard.query.filter(
            TechnologyStandard.is_active.is_(True),
            TechnologyStandard.review_date.isnot(None),
            TechnologyStandard.review_date < today,
        ).count()
        dated_review = TechnologyStandard.query.filter(
            TechnologyStandard.is_active.is_(True),
            TechnologyStandard.review_date.isnot(None),
        ).count()
        sunset_passed = TechnologyStandard.query.filter(
            TechnologyStandard.is_active.is_(True),
            TechnologyStandard.sunset_date.isnot(None),
            TechnologyStandard.sunset_date < today,
        ).count()
        unowned = TechnologyStandard.query.filter(
            TechnologyStandard.is_active.is_(True),
            TechnologyStandard.owner_id.is_(None),
        ).count()

        # An exception is "live" when it has been approved and neither denied nor
        # revoked. Status strings are the ARBExceptionStatus values persisted by
        # ARBExceptionService; anything else is an in-flight request, not a
        # standing deviation.
        live_exceptions = ARBException.query.filter(
            ARBException.status == "approved",
            ARBException.revoked_at.is_(None),
        )
        live_count = live_exceptions.count()
        expired_live = live_exceptions.filter(
            ARBException.expires_at.isnot(None),
            db.func.date(ARBException.expires_at) < today,
        ).count()
        expiring_live = live_exceptions.filter(
            ARBException.expires_at.isnot(None),
            db.func.date(ARBException.expires_at) >= today,
            db.func.date(ARBException.expires_at)
            <= today + timedelta(days=horizon_days),
        ).count()
        undated_live = live_exceptions.filter(ARBException.expires_at.is_(None)).count()

        lens = {
            "key": "standards",
            "label": "Standards & exceptions",
            "icon": "book-marked",
            "state": "measured" if (total_standards or live_count) else "empty",
            "reason": None,
            "total": active,
            "total_label": "active technology standards",
            "href": standards_href,
            "measures": [
                _measure(
                    "Standards past their review date",
                    overdue_review,
                    of=dated_review,
                    source="TechnologyStandard.review_date < today",
                    hint="Denominator is active standards carrying a review date.",
                    href=standards_href,
                ),
                _measure(
                    "Active standards past their sunset date",
                    sunset_passed,
                    of=active,
                    source="TechnologyStandard.sunset_date < today",
                    href=standards_href,
                ),
                _measure(
                    "Approved exceptions in force",
                    live_count,
                    source="ARBException.status='approved' AND revoked_at IS NULL",
                    href=exceptions_href,
                ),
                _measure(
                    f"Exceptions expiring within {horizon_days} days",
                    expiring_live,
                    of=live_count,
                    source="ARBException.expires_at",
                    href=exceptions_href,
                ),
            ],
            "missing": [
                _missing(
                    "Active standards with no review date",
                    active - dated_review,
                    of=active,
                    source="TechnologyStandard.review_date IS NULL",
                    href=standards_href,
                ),
                _missing(
                    "Active standards with no owner",
                    unowned,
                    of=active,
                    source="TechnologyStandard.owner_id IS NULL",
                    href=standards_href,
                ),
                _missing(
                    "Exceptions in force with no expiry date",
                    undated_live,
                    of=live_count,
                    source="ARBException.expires_at IS NULL",
                    href=exceptions_href,
                ),
            ],
        }

        attention: List[Dict[str, Any]] = []
        if expired_live:
            attention.append(
                _attention(
                    key="standards-exception-expired",
                    source_label="Standards & exceptions",
                    name="Standards & exceptions",
                    title=f"{expired_live} approved exception(s) are past their expiry date",
                    severity="critical",
                    reason=(
                        "The exception is still recorded as approved but its expiry date has "
                        "passed, so a deviation is in force without a current mandate."
                    ),
                    next_action="Renew, revoke, or close the expired exceptions",
                    action_url=exceptions_href,
                    count=expired_live,
                )
            )
        if undated_live:
            attention.append(
                _attention(
                    key="standards-exception-undated",
                    source_label="Standards & exceptions",
                    name="Standards & exceptions",
                    title=f"{undated_live} exception(s) in force have no expiry date",
                    severity="high",
                    reason="An exception with no expiry cannot be reviewed on a cycle.",
                    next_action="Set an expiry date so the exception returns for review",
                    action_url=exceptions_href,
                    count=undated_live,
                )
            )
        if overdue_review:
            attention.append(
                _attention(
                    key="standards-review-overdue",
                    source_label="Standards & exceptions",
                    name="Standards & exceptions",
                    title=f"{overdue_review} active standard(s) are past their review date",
                    severity="medium",
                    reason="The standard is still enforced but its scheduled review has lapsed.",
                    next_action="Review and re-approve, or retire the standard",
                    action_url=standards_href,
                    count=overdue_review,
                )
            )
        return lens, attention

    # ── lens: roadmap ───────────────────────────────────────────────────────
    @classmethod
    def _roadmap_lens(cls) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        from app.models.implementation_migration import Gap, Plateau, WorkPackage

        today = cls._today()
        roadmap_href = _first_url(
            "adm_kanban_view.plateau_roadmap", "adm_kanban_view.adm_roadmap_timeline"
        )
        gap_href = _first_url("adm_kanban_view.gap_analysis", "enterprise.gap_analysis")

        plateaus = Plateau.query.count()
        dated_plateaus = Plateau.query.filter(Plateau.target_date.isnot(None)).count()

        packages = WorkPackage.query.count()
        dated_packages = WorkPackage.query.filter(
            WorkPackage.target_date.isnot(None)
        ).count()
        overdue_packages = WorkPackage.query.filter(
            WorkPackage.target_date.isnot(None),
            WorkPackage.target_date < today,
            WorkPackage.completed_date.is_(None),
        ).count()

        # Gap.resolution_status is a free-form string; count what is explicitly
        # closed rather than guessing at the open vocabulary, so an unfamiliar
        # status is treated as open (visible) rather than silently resolved.
        closed_states = ("resolved", "closed", "complete", "completed")
        # D3: exclude interface-register plateau-transition gaps -- this
        # posture metric answers "capability gaps", not interface gaps.
        _capability_gaps = Gap.query.filter(Gap.gap_kind != "plateau_transition")
        open_gaps = _capability_gaps.filter(
            db.or_(
                Gap.resolution_status.is_(None),
                db.func.lower(Gap.resolution_status).notin_(closed_states),
            )
        ).count()
        total_gaps = _capability_gaps.count()
        overdue_gaps = _capability_gaps.filter(
            db.or_(
                Gap.resolution_status.is_(None),
                db.func.lower(Gap.resolution_status).notin_(closed_states),
            ),
            Gap.target_resolution_date.isnot(None),
            Gap.target_resolution_date < today,
        ).count()
        unowned_gaps = _capability_gaps.filter(
            db.or_(
                Gap.resolution_status.is_(None),
                db.func.lower(Gap.resolution_status).notin_(closed_states),
            ),
            db.or_(Gap.owner.is_(None), Gap.owner == ""),
        ).count()

        lens = {
            "key": "roadmap",
            "label": "Roadmap & work packages",
            "icon": "route",
            "state": "measured" if (plateaus or packages or total_gaps) else "empty",
            "reason": None,
            "total": packages,
            "total_label": "work packages",
            "href": roadmap_href,
            "measures": [
                _measure(
                    "Work packages past target date",
                    overdue_packages,
                    of=dated_packages,
                    source="WorkPackage.target_date < today AND completed_date IS NULL",
                    hint="Denominator is work packages carrying a target date.",
                    href=roadmap_href,
                ),
                _measure(
                    "Plateaus with a target date",
                    dated_plateaus,
                    of=plateaus,
                    source="Plateau.target_date",
                    href=roadmap_href,
                ),
                _measure(
                    "Open gaps",
                    open_gaps,
                    of=total_gaps,
                    source="Gap.resolution_status NOT IN (resolved, closed, complete, completed)",
                    href=gap_href,
                ),
                _measure(
                    "Open gaps past target resolution date",
                    overdue_gaps,
                    of=open_gaps,
                    source="Gap.target_resolution_date < today",
                    href=gap_href,
                ),
            ],
            "missing": [
                _missing(
                    "Work packages with no target date",
                    packages - dated_packages,
                    of=packages,
                    source="WorkPackage.target_date IS NULL",
                    href=roadmap_href,
                ),
                _missing(
                    "Plateaus with no target date",
                    plateaus - dated_plateaus,
                    of=plateaus,
                    source="Plateau.target_date IS NULL",
                    href=roadmap_href,
                ),
                _missing(
                    "Open gaps with no owner",
                    unowned_gaps,
                    of=open_gaps,
                    source="Gap.owner IS NULL",
                    href=gap_href,
                ),
            ],
        }

        attention: List[Dict[str, Any]] = []
        if overdue_packages:
            attention.append(
                _attention(
                    key="roadmap-overdue-packages",
                    source_label="Roadmap",
                    name="Roadmap",
                    title=f"{overdue_packages} work package(s) are past their target date",
                    severity="high",
                    reason="The target date has passed and no completion date is recorded.",
                    next_action="Re-plan or close the overdue work packages",
                    action_url=roadmap_href,
                    count=overdue_packages,
                )
            )
        if overdue_gaps:
            attention.append(
                _attention(
                    key="roadmap-overdue-gaps",
                    source_label="Roadmap",
                    name="Roadmap",
                    title=f"{overdue_gaps} open gap(s) are past their target resolution date",
                    severity="high",
                    reason="The gap is still open and its target resolution date has passed.",
                    next_action="Re-plan the gap or record its resolution",
                    action_url=gap_href,
                    count=overdue_gaps,
                )
            )
        if unowned_gaps:
            attention.append(
                _attention(
                    key="roadmap-unowned-gaps",
                    source_label="Roadmap",
                    name="Roadmap",
                    title=f"{unowned_gaps} open gap(s) have no owner",
                    severity="medium",
                    reason="An unowned gap has nobody accountable for closing it.",
                    next_action="Assign an owner to each open gap",
                    action_url=gap_href,
                    count=unowned_gaps,
                )
            )
        return lens, attention

    # ── lens: decisions ─────────────────────────────────────────────────────
    @classmethod
    def _decision_lens(cls) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        # F-15, Capgemini dry-run: this lens used to count
        # app.models.adr.ArchitectureDecisionRecord, which maps a separate,
        # orphaned `architecture_decision_records` table that no UI links to.
        # The ADR list every template actually links to
        # (arch_decisions.list_decisions) is backed by
        # app.models.architecture_decision.ArchitectureDecision over the
        # `architecture_decisions` table — see the note in
        # app/modules/architecture/routes/adr_routes.py:list_adrs. Count that
        # one so this posture tile agrees with the real ADR page.
        from app.models.architecture_decision import ArchitectureDecision

        decisions_href = _first_url(
            "arch_decisions.list_decisions",
            "governance.adr_list",
            "arb.decision_register_page",
        )

        total = ArchitectureDecision.query.count()
        proposed = ArchitectureDecision.query.filter(
            db.func.lower(ArchitectureDecision.status) == "proposed"
        ).count()
        accepted = ArchitectureDecision.query.filter(
            db.func.lower(ArchitectureDecision.status) == "accepted"
        ).count()
        superseded = ArchitectureDecision.query.filter(
            ArchitectureDecision.superseded_by_id.isnot(None)
        ).count()
        undated = ArchitectureDecision.query.filter(
            ArchitectureDecision.decided_at.is_(None)
        ).count()
        no_rationale = ArchitectureDecision.query.filter(
            db.or_(
                ArchitectureDecision.rationale.is_(None),
                ArchitectureDecision.rationale == "",
            )
        ).count()
        no_consequences = ArchitectureDecision.query.filter(
            db.or_(
                ArchitectureDecision.consequences.is_(None),
                ArchitectureDecision.consequences == "",
            )
        ).count()

        lens = {
            "key": "decision",
            "label": "Architecture decisions",
            "icon": "gavel",
            "state": "measured" if total else "empty",
            "reason": None,
            "total": total,
            "total_label": "decision records",
            "href": decisions_href,
            "measures": [
                _measure(
                    "Accepted",
                    accepted,
                    of=total,
                    source="ArchitectureDecision.status = 'accepted'",
                    href=decisions_href,
                ),
                _measure(
                    "Awaiting a decision",
                    proposed,
                    of=total,
                    source="ArchitectureDecision.status = 'proposed'",
                    href=decisions_href,
                ),
                _measure(
                    "Superseded",
                    superseded,
                    of=total,
                    source="ArchitectureDecision.superseded_by_id",
                    href=decisions_href,
                ),
            ],
            "missing": [
                _missing(
                    "Decisions with no rationale",
                    no_rationale,
                    of=total,
                    source="ArchitectureDecision.rationale IS NULL",
                    href=decisions_href,
                ),
                _missing(
                    "Decisions with no recorded consequences",
                    no_consequences,
                    of=total,
                    source="ArchitectureDecision.consequences IS NULL",
                    href=decisions_href,
                ),
                _missing(
                    "Decisions with no decision date",
                    undated,
                    of=total,
                    source="ArchitectureDecision.decided_at IS NULL",
                    href=decisions_href,
                ),
            ],
        }

        attention: List[Dict[str, Any]] = []
        if proposed:
            attention.append(
                _attention(
                    key="decision-proposed",
                    source_label="Architecture decisions",
                    name="Architecture decisions",
                    title=f"{proposed} decision record(s) are still proposed",
                    severity="medium",
                    reason="A proposed decision is not yet binding on delivery teams.",
                    next_action="Take the decisions through to accepted or rejected",
                    action_url=decisions_href,
                    count=proposed,
                )
            )
        if total and no_rationale:
            attention.append(
                _attention(
                    key="decision-no-rationale",
                    source_label="Architecture decisions",
                    name="Architecture decisions",
                    title=f"{no_rationale} of {total} decision records carry no rationale",
                    severity="medium",
                    reason=(
                        "A decision without recorded rationale cannot be revisited safely "
                        "when its context changes."
                    ),
                    next_action="Record the rationale behind each decision",
                    action_url=decisions_href,
                    count=no_rationale,
                )
            )
        return lens, attention

    # ── assembly ────────────────────────────────────────────────────────────
    #: Lens builders in display order. Each returns ``(lens, attention_items)``.
    _LENSES: tuple[tuple[str, str, str, str], ...] = (
        ("capability", "Capability & value streams", "grid-3x3", "_capability_lens"),
        ("application", "Application portfolio", "layout-grid", "_application_lens"),
        ("standards", "Standards & exceptions", "book-marked", "_standards_lens"),
        ("roadmap", "Roadmap & work packages", "route", "_roadmap_lens"),
        ("decision", "Architecture decisions", "gavel", "_decision_lens"),
    )

    @classmethod
    def enterprise_posture(cls) -> Dict[str, Any]:
        """Every enterprise lens, plus the attention items they raise.

        A lens that raises is reported as ``unavailable`` with ``None`` values —
        the rest of the workbench still renders. That partial-data case is the
        normal one on a part-populated tenant, not an edge case.
        """
        lenses: List[Dict[str, Any]] = []
        attention: List[Dict[str, Any]] = []
        unavailable = 0

        for key, label, icon, method_name in cls._LENSES:
            builder: Callable[[], tuple[Dict[str, Any], List[Dict[str, Any]]]] = getattr(
                cls, method_name
            )
            try:
                lens, lens_attention = builder()
            except Exception as exc:  # noqa: BLE001 - one lens must not lose the page
                logger.warning("enterprise lens %s unavailable: %s", key, exc)
                unavailable += 1
                lenses.append(
                    _unavailable_lens(
                        key,
                        label,
                        icon,
                        "This lens could not be measured; its records were not readable.",
                    )
                )
                # The session may be in a failed-transaction state after a bad
                # query (see docs/known-issues/schema-drift-on-existing-databases.md,
                # where one UndefinedColumn cascades into InFailedSqlTransaction for
                # every later query). Roll back so the remaining lenses can run.
                try:
                    db.session.rollback()
                except Exception:  # noqa: BLE001
                    logger.debug("rollback after failed lens %s also failed", key)
                continue
            lenses.append(lens)
            attention.extend(lens_attention)

        measured = [lens for lens in lenses if lens["state"] == "measured"]
        return {
            "lenses": lenses,
            "attention": attention,
            "lenses_total": len(lenses),
            "lenses_measured": len(measured),
            "lenses_unavailable": unavailable,
            # Coverage of the *lenses*, not of the architecture: how much of this
            # view could be measured at all. Withheld when nothing was readable,
            # because 0% and "we could not tell" are different statements.
            "state": "unavailable" if unavailable == len(cls._LENSES) else "measured",
        }
