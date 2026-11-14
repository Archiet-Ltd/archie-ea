"""R1-B39: the aggregate-answer catalogue.

A declarative registry of named, parameterised questions. Each entry calls
into ``query_service.py`` (or a thin query over an existing canonical
store) and returns the same rows whether it is hit from the screen or
``/api/v1`` — one catalogue, not a second query engine.

Every entry's result rows carry a ``truth_class`` per CLAUDE.md's
drawn/derived/synchronised vocabulary, read from the row's own
``source_table`` (ADR 0008), never invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app import db


TRUTH_CLASS_DRAWN = "drawn"
TRUTH_CLASS_DERIVED = "derived"
TRUTH_CLASS_SYNCHRONISED = "synchronised"

# Tables that are themselves the system of record for the fact they carry
# (a human or an integration wrote the row directly) are "drawn". Tables
# that recompute a value from other rows are "derived". Nothing in this
# catalogue reads from a sync-mirror table today, so "synchronised" is
# available for a future entry but unused here — never guessed per row.
_DRAWN_SOURCE_TABLES = {"application_owners", "application_components", "company_profile"}


def truth_class_for(source_table: Optional[str]) -> str:
    """The drawn/derived/synchronised class for a citation's source table.

    Returns "derived" for anything not explicitly known to be drawn, since
    a value this catalogue computes (a count, a group) is derived from its
    rows even when every individual row is itself drawn.
    """
    if source_table in _DRAWN_SOURCE_TABLES:
        return TRUTH_CLASS_DRAWN
    return TRUTH_CLASS_DERIVED


def application_citation(application_id: int, name: str) -> Dict[str, Any]:
    """A citation to an application record a person can open.

    The target is the one tenant-scoped application record API
    (``/api/v1/applications/<id>``); opening it shows the application's name,
    owner and status, so the cited fact is checkable against the record.
    """
    return {
        "type": "application",
        "id": application_id,
        "name": name,
        "target": f"/api/v1/applications/{application_id}",
    }


def element_citation(element_id: int, name: str) -> Dict[str, Any]:
    """A citation to an ArchiMate element record a person can open.

    The target is the one tenant-scoped element record API
    (``/api/v1/elements/<id>``), which shows the element's name, type and
    layer — content the cited fact is checked against.
    """
    return {
        "type": "element",
        "id": element_id,
        "name": name,
        "target": f"/api/v1/elements/{element_id}",
    }


@dataclass
class CatalogueEntry:
    """One named question. ``params`` names the parameters this entry
    accepts (for the interpretation banner to show and the user to
    correct); ``run`` executes it and returns the answer dict."""

    id: str
    title: str
    description: str
    params: List[str] = field(default_factory=list)
    run: Callable[..., Dict[str, Any]] = None  # type: ignore[assignment]


def _applications_without_owner(organization_id: int, **_: Any) -> Dict[str, Any]:
    """"Which applications have no owner?" — grouped by business
    criticality (the one grouping dimension ApplicationComponent actually
    carries; there is no business-unit column to group by, and inventing
    one would be exactly the fabrication CLAUDE.md forbids)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.element_ownership_service import application_is_owned_expression

    # "Owned" is defined once, beside the owner store, and the dashboard's
    # owner coverage reads the same rule. An owner named at import that matches
    # no user is held as text on the application and still counts.
    unowned = (
        ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == organization_id,
            db.not_(application_is_owned_expression(ApplicationComponent)),
        )
        .all()
    )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    citations: List[Dict[str, Any]] = []
    for app in unowned:
        key = app.business_criticality or "not recorded"
        groups.setdefault(key, []).append(
            {
                "id": app.id,
                "name": app.name,
                "source_table": "application_components",
                "truth_class": truth_class_for("application_components"),
            }
        )
        citations.append(application_citation(app.id, app.name))

    # Nothing unowned is only a finding when there are applications to own:
    # with none recorded, "every application has an owner" would be vacuous.
    any_application = bool(unowned) or (
        ApplicationComponent.query.filter(ApplicationComponent.organization_id == organization_id).first()
        is not None
    )
    if unowned:
        reason = None
    elif any_application:
        reason = "every application in this organisation has a recorded owner"
    else:
        reason = "no applications are recorded in this organisation"

    return {
        "answer": "applications with no recorded owner, by business criticality",
        "groups": [
            {"group": group, "rows": rows} for group, rows in sorted(groups.items())
        ],
        "total": len(unowned),
        "citations": citations,
        "reason": reason,
        "complete": bool(not unowned and any_application),
    }


def _owner_names_by_application(
    organization_id: int, application_ids: List[int], ownership_types: Optional[tuple] = None,
) -> Dict[int, List[str]]:
    """{application id: the names of its recorded owners}, read through the one owner reader
    (a user, or a person recorded without an account, each named as the record names them; a
    user with no name is named by email)."""
    from app.models.application_owner import ApplicationOwner

    if not application_ids:
        return {}
    names: Dict[int, List[str]] = {}
    for application_id, rows in ApplicationOwner.get_display_rows_for_applications(
        application_ids, organization_id,
    ).items():
        for row in rows:
            if ownership_types and row["ownership_type"] not in ownership_types:
                continue
            name = row["user_name"] if row["user_name"] != "\u2014" else row["user_email"]
            if name:
                names.setdefault(application_id, []).append(name)
    return names


def _business_continuity_criticality(
    organization_id: int, criticality: str = "Critical", **_: Any
) -> Dict[str, Any]:
    """"What would stop the business trading?" — the systems, suppliers
    and owners for a given criticality level."""
    from app.models.application_portfolio import ApplicationComponent

    apps = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == organization_id,
        ApplicationComponent.business_criticality == criticality,
    ).all()

    owners_by_app = _owner_names_by_application(organization_id, [a.id for a in apps])

    rows = []
    citations: List[Dict[str, Any]] = []
    for app in apps:
        owners = owners_by_app.get(app.id)
        rows.append(
            {
                "id": app.id,
                "name": app.name,
                "vendor_name": getattr(app, "vendor_name", None) or "not recorded",
                "owners": owners or [],
                "owner_reason": None if owners else "no recorded owner for this application",
                "source_table": "application_components",
                "truth_class": truth_class_for("application_components"),
            }
        )
        citations.append(application_citation(app.id, app.name))

    return {
        "answer": f"systems at '{criticality}' criticality, their suppliers and owners",
        "criticality": criticality,
        "rows": rows,
        "total": len(rows),
        "citations": citations,
        "reason": None if rows else f"no applications are recorded at '{criticality}' criticality",
    }


def _overlapping_contracts(organization_id: int, **_: Any) -> Dict[str, Any]:
    """"Where do our contracts overlap?" (R1-B38 PB-0225) -- applications
    sharing a capability, grouped by that capability, with each
    application's contracts, combined contract value and nearest end date.

    Reads the existing contract_applications junction (R1-B38's own note:
    this needs only the query, not a new writer) and resolves each
    application's capability through BusinessCapability -- the write path
    ADR 0008 keeps live -- rather than UnifiedCapability directly, since no
    reverse (BusinessCapability id -> UnifiedCapability id) resolver exists
    yet; UnifiedCapability's own source_table/source_id provenance on each
    row is exactly this link, named here as a known shortcut rather than
    silently assumed solved.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.contract_application import ContractApplication

    contract_apps = (
        db.session.query(ContractApplication, VendorContract, ApplicationComponent)
        .join(VendorContract, VendorContract.id == ContractApplication.contract_id)
        .join(ApplicationComponent, ApplicationComponent.id == ContractApplication.application_id)
        .filter(ContractApplication.organization_id == organization_id)
        .all()
    )
    app_ids = {app.id for _, _, app in contract_apps}
    capability_by_app: Dict[int, List[Any]] = {}
    if app_ids:
        mapping_rows = (
            ApplicationCapabilityMapping.query.filter(
                ApplicationCapabilityMapping.organization_id == organization_id,
                ApplicationCapabilityMapping.application_component_id.in_(app_ids),
            )
            .all()
        )
        for m in mapping_rows:
            bucket = capability_by_app.setdefault(m.application_component_id, [])
            # A repeated (application, capability) mapping row must not
            # repeat the contract row the loop below builds from it.
            if m.business_capability_id not in bucket:
                bucket.append(m.business_capability_id)

    groups: Dict[str, Dict[str, Any]] = {}
    for contract_app, contract, app in contract_apps:
        for capability_id in capability_by_app.get(app.id, []):
            key = str(capability_id)
            bucket = groups.setdefault(key, {"capability_id": capability_id, "rows": []})
            bucket["rows"].append(
                {
                    "application_id": app.id,
                    "application_name": app.name,
                    "contract_id": contract.id,
                    "contract_name": contract.contract_name,
                    "contract_value": contract.contract_value,
                    "currency": contract.currency,
                    "end_date": contract.end_date.isoformat() if contract.end_date else None,
                    "source_table": "contract_applications",
                    "truth_class": truth_class_for("contract_applications"),
                }
            )

    # Only a capability shared by more than one application is an overlap.
    overlapping = {
        key: bucket for key, bucket in groups.items()
        if len({row["application_id"] for row in bucket["rows"]}) > 1
    }

    from app.models.business_capabilities import BusinessCapability
    from app.services import vendor_register

    counted_ids = {c.id for c in vendor_register.contract_ledger(organization_id)["counted"]}

    result_groups = []
    citations: List[Dict[str, Any]] = []
    cited_app_ids: set = set()
    for bucket in overlapping.values():
        capability = BusinessCapability.query.filter_by(
            id=bucket["capability_id"], organization_id=organization_id,
        ).first()
        # Review finding: a single contract can cover several applications
        # (contract_applications is a many-to-many join), so summing one row
        # per (app, contract) double-counts that contract's value for every
        # extra application sharing it. Sum each distinct contract once.
        # Only the contracts the one ledger counts add up (active, in date, in the organisation's currency).
        distinct_contracts = {
            row["contract_id"]: row["contract_value"]
            for row in bucket["rows"] if row["contract_value"] is not None and row["contract_id"] in counted_ids
        }
        combined_value = sum(float(v) for v in distinct_contracts.values())
        end_dates = [row["end_date"] for row in bucket["rows"] if row["end_date"]]
        result_groups.append(
            {
                "group": capability.name if capability else "not recorded",
                "rows": bucket["rows"],
                "combined_contract_value": combined_value if combined_value else None,
                "nearest_end_date": min(end_dates) if end_dates else None,
            }
        )
        # Each overlapping application is a record a person can open to see
        # the duplicate for themselves.
        for row in bucket["rows"]:
            if row["application_id"] not in cited_app_ids:
                cited_app_ids.add(row["application_id"])
                citations.append(application_citation(row["application_id"], row["application_name"]))

    return {
        "answer": "applications sharing a capability, with their overlapping contracts",
        "groups": sorted(result_groups, key=lambda g: g["group"]),
        "total": len(result_groups),
        "citations": citations,
        "reason": None if result_groups else "no capability is shared by more than one application with a contract",
    }


def _applications_and_owners(organization_id: int, **_: Any) -> Dict[str, Any]:
    """"What do we run and who owns each one?" (UC-S1-05) — the application
    inventory with each application's accountable owner and recorded annual
    cost, read from the one owner store and the one cost field.

    Owner is read through the same accountable-owner rule the owner store
    defines (``application_owner``/``business_owner`` text, or an
    ApplicationOwner row), never a second definition. Annual cost is the
    application's recorded total cost of ownership; a missing cost reads as
    "not recorded", never a fabricated zero.
    """
    from app.models.application_portfolio import ApplicationComponent

    from app.modules.dashboard.services.overview_figures import _active_applications, left_out_applications

    # The applications in use: the Systems list's own definition (not a copy, not
    # decommissioned, not an unpromoted import), so the count here is that list's count.
    apps = (
        db.session.execute(
            _active_applications()
            .where(ApplicationComponent.organization_id == organization_id, ApplicationComponent.deleted_at.is_(None))
            .order_by(ApplicationComponent.name)
        )
        .scalars()
        .all()
    )
    # The Overview's own left-out figure: an unpromoted repository import is not a decommissioned application.
    decommissioned = left_out_applications(organization_id)["decommissioned"]

    owners_by_app = _owner_names_by_application(
        organization_id, [a.id for a in apps], ownership_types=("primary", "business"))

    rows = []
    citations: List[Dict[str, Any]] = []
    for app in apps:
        owners = owners_by_app.get(app.id)
        # The owner the user reads: the person record if there is one, else
        # the owner text the application carries, else an honest "not recorded".
        owner_text = owners[0] if owners else (app.application_owner or app.business_owner or None)
        annual_cost = app.total_cost_of_ownership
        rows.append(
            {
                "id": app.id,
                "name": app.name,
                "owner": owner_text or "not recorded",
                "owner_reason": None if owner_text else "no accountable owner recorded for this application",
                "annual_cost": annual_cost,
                "annual_cost_reason": None if annual_cost is not None else "no annual cost recorded for this application",
                "source_table": "application_components",
                "truth_class": truth_class_for("application_components"),
            }
        )
        citations.append(application_citation(app.id, app.name))

    return {
        "answer": "the applications in use, with each one's accountable owner and annual cost",
        "rows": rows,
        "total": len(rows),
        "decommissioned_left_out": decommissioned,
        "citations": citations,
        "reason": None if rows else "no applications are recorded in this organisation",
    }


def _duplicate_tooling_by_capability(organization_id: int, **_: Any) -> Dict[str, Any]:
    """"Are we paying for two tools that do the same thing?" (UC-S2-03) —
    capabilities covered by more than one application, with each
    application's annual cost, so a reader sees the duplicate spend.

    Reads the one capability store (ApplicationCapabilityMapping ->
    BusinessCapability) and the one application cost field; it does not
    reimplement the overlap walk — it groups by capability the same way the
    overlapping-contracts entry does, but over every application mapped to a
    capability (not only those with a contract), since duplicate tooling is
    the question even when neither tool has a contract recorded yet.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    mapping_rows = (
        db.session.query(ApplicationCapabilityMapping, ApplicationComponent)
        .join(
            ApplicationComponent,
            ApplicationComponent.id == ApplicationCapabilityMapping.application_component_id,
        )
        .filter(
            ApplicationComponent.organization_id == organization_id,
        )
        .all()
    )

    apps_by_capability: Dict[int, Dict[int, ApplicationComponent]] = {}
    for mapping, app in mapping_rows:
        apps_by_capability.setdefault(mapping.business_capability_id, {})[app.id] = app

    result_groups = []
    citations: List[Dict[str, Any]] = []
    cited_app_ids: set = set()
    for capability_id, apps in apps_by_capability.items():
        if len(apps) < 2:
            continue  # one tool for a capability is not duplicate tooling
        capability = BusinessCapability.query.filter_by(
            id=capability_id, organization_id=organization_id,
        ).first()
        group_rows = []
        costs = []
        for app in sorted(apps.values(), key=lambda a: a.name or ""):
            cost = app.total_cost_of_ownership
            if cost is not None:
                costs.append(float(cost))
            group_rows.append(
                {
                    "application_id": app.id,
                    "application_name": app.name,
                    "annual_cost": cost,
                    "annual_cost_reason": None if cost is not None else "no annual cost recorded",
                    "source_table": "application_capabilities",
                    "truth_class": truth_class_for("application_capabilities"),
                }
            )
            if app.id not in cited_app_ids:
                cited_app_ids.add(app.id)
                citations.append(application_citation(app.id, app.name))
        result_groups.append(
            {
                "group": capability.name if capability else "not recorded",
                "rows": group_rows,
                "combined_annual_cost": sum(costs) if costs else None,
            }
        )

    return {
        "answer": "capabilities covered by more than one application, with the annual cost of each",
        "groups": sorted(result_groups, key=lambda g: g["group"]),
        "total": len(result_groups),
        "citations": citations,
        "reason": None if result_groups else "no capability is covered by more than one application",
    }


def _github_sourced_records(organization_id: int, **_: Any) -> Dict[str, Any]:
    """"What came from GitHub?" (UC-S2-04) — the elements a GitHub connector
    wrote, read from the provenance the connector stamps on each record
    (``source_kind`` is 'connector' and ``source_label`` names the GitHub
    repository). Nothing here guesses provenance: a row is only listed when
    the connector itself recorded it.
    """
    from app.models.archimate_core import ArchiMateElement

    # The GitHub connector stamps ``source_kind='connector'`` and a
    # ``source_label`` that starts "GitHub <repository>" (app/connectors/
    # github.py stamp_origin); that label prefix is what tells a GitHub
    # record apart from any other connector's.
    elements = (
        ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == organization_id,
            ArchiMateElement.source_kind == "connector",
            ArchiMateElement.source_label.ilike("GitHub %"),
        )
        .order_by(ArchiMateElement.name)
        .all()
    )

    rows = []
    citations: List[Dict[str, Any]] = []
    for element in elements:
        rows.append(
            {
                "id": element.id,
                "name": element.name,
                "type": element.type,
                "origin": element.source_label,
                "recorded_at": element.source_recorded_at.isoformat() if element.source_recorded_at else None,
                "source_table": "archimate_elements",
                "truth_class": TRUTH_CLASS_SYNCHRONISED,
            }
        )
        citations.append(element_citation(element.id, element.name))

    return {
        "answer": "the records a GitHub connector brought into the model",
        "rows": rows,
        "total": len(rows),
        "citations": citations,
        "reason": None if rows else "no records in this organisation were brought in by a GitHub connector",
    }


def _resolve_service_element(organization_id: int, service: Optional[str]):
    """The one ArchiMate element in *organization_id* a service name names, or ``None``.
    The name means what ``element_each_name_means`` says it means, the same element the
    overview, the cards and a typed question use; an absent name resolves to nothing."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.dashboard.services.overview_figures import element_named

    element_id = element_named(organization_id, service)
    if element_id is None:
        return None
    return ArchiMateElement.query.filter_by(id=element_id, organization_id=organization_id).first()


def _first_recorded(element_ids: List[int], organization_id: int) -> Dict[int, Any]:
    """When each element was first written, from the one change history."""
    from app.models.entity_history import EntityHistory

    rows = (
        db.session.query(EntityHistory.record_id, db.func.min(EntityHistory.valid_from))
        .filter(
            EntityHistory.table_name == "archimate_elements",
            EntityHistory.organization_id == organization_id,
            EntityHistory.record_id.in_(element_ids),
        )
        .group_by(EntityHistory.record_id)
        .all()
    )
    return {record_id: first for record_id, first in rows}


def _source_line(element, first_recorded=None) -> str:
    """Where an element came from and when, in the words the dashboard uses:
    "from <source>, as of <date>", or "added by hand on <date>" when no source
    is recorded. Never blank."""
    when = element.source_recorded_at or element.recorded_at or first_recorded
    date = "%d %s" % (when.day, when.strftime("%b %Y")) if when else None
    label = (element.source_label or "").strip()
    if getattr(element, "source_kind", None) == "sample":
        return "Sample data, as of %s" % date if date else "Sample data"
    if label:
        return "from %s, as of %s" % (label, date) if date else "from %s" % label
    return "added by hand on %s" % date if date else "added by hand"


def _single_points_of_failure(organization_id: int, limit: int = 10) -> Dict[str, Any]:
    """The systems the most other things depend on, by how many
    relationships point at them (their in-degree) in the one relationship
    store. These are the single points of failure; each is a record to open."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    from app.models.archimate_element_types import system_kind_types

    # In-degree per target element, scoped to this organisation's relationships;
    # only systems count, so a process or function is never listed as one.
    counts = (
        db.session.query(
            ArchiMateRelationship.target_id,
            db.func.count(ArchiMateRelationship.id).label("in_degree"),
        )
        .join(ArchiMateElement, ArchiMateElement.id == ArchiMateRelationship.target_id)
        .filter(
            ArchiMateRelationship.organization_id == organization_id,
            ArchiMateElement.organization_id == organization_id,
            ArchiMateElement.type.in_(system_kind_types()),
            ArchiMateElement.deleted_at.is_(None),
        )
        .group_by(ArchiMateRelationship.target_id)
        .having(db.func.count(ArchiMateRelationship.id) > 1)
        .order_by(db.func.count(ArchiMateRelationship.id).desc(), ArchiMateRelationship.target_id)
        .limit(limit)
        .all()
    )
    if not counts:
        return {
            "answer": "the systems the most depends on",
            "rows": [],
            "total": 0,
            "citations": [],
            "reason": "nothing in this organisation has more than one thing depending on it yet",
        }

    target_ids = [target_id for target_id, _ in counts]
    elements = {
        e.id: e
        for e in ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(target_ids),
            ArchiMateElement.organization_id == organization_id,
        ).all()
    }

    first_recorded = _first_recorded(target_ids, organization_id)
    rows = []
    citations: List[Dict[str, Any]] = []
    for target_id, in_degree in counts:
        element = elements.get(target_id)
        if element is None:
            continue
        rows.append(
            {
                "id": element.id,
                "name": element.name,
                "depends_on_count": int(in_degree),
                "source_line": _source_line(element, first_recorded.get(element.id)),
                "source_table": "archimate_relationships",
                "truth_class": truth_class_for("archimate_relationships"),
            }
        )
        citations.append(element_citation(element.id, element.name))

    return {
        "answer": "the systems the most other things depend on — the single points of failure",
        "rows": rows,
        "total": len(rows),
        "citations": citations,
        "reason": None if rows else "nothing in this organisation has more than one thing depending on it yet",
    }


def _closest_recorded_names(organization_id: int, service: str, limit: int = 5) -> List[str]:
    """The names of this organisation's systems close to *service*, closest
    first. A name is close only when it shares a word, or the beginning of a word, with *service* (stop
    words ignored) or scores at the duplicate detector's likely-duplicate
    threshold, using the one name scorer the duplicate detector uses; a name
    that is neither is never listed. Only this organisation's own records are
    read."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.archimate_element_types import system_kind_types
    from app.modules.duplicate_detection.services.duplicate_detection_utils import DuplicateDetectionUtils

    names = {
        name
        for (name,) in db.session.query(ArchiMateElement.name)
        .filter(
            ArchiMateElement.organization_id == organization_id,
            ArchiMateElement.deleted_at.is_(None),
            ArchiMateElement.type.in_(system_kind_types()),
        )
        .all()
        if name
    }
    from difflib import SequenceMatcher

    from app.modules.ai_chat.services.solution_ai_service import _STOPWORDS

    wanted = DuplicateDetectionUtils.tokenize(service) - _STOPWORDS
    scored = []
    for name in names:
        score = DuplicateDetectionUtils.is_duplicate(service, name, mode="fuzzy", threshold=0.0)[1]
        words = DuplicateDetectionUtils.tokenize(name) - _STOPWORDS
        # a typed beginning of a word ("Bill" for "Billing") shares that word
        shares_a_word = bool(wanted & words) or any(
            len(typed) >= 3 and word.startswith(typed) for typed in wanted for word in words
        )
        if shares_a_word or score >= DuplicateDetectionUtils.LIKELY_DUPLICATE_THRESHOLD:
            # Equal word overlap is ordered by how alike the spellings are
            # ("Payments service" before "Notification service" for "Payment
            # service"), then by name; it never decides who is listed.
            spelling = SequenceMatcher(None, service.lower(), name.lower()).ratio()
            scored.append((-score, -spelling, name.lower(), name))
    scored.sort()
    return [name for *_, name in scored[:limit]]


def _applications_url() -> Optional[str]:
    """The page listing the organisation's recorded applications."""
    from flask import has_request_context, url_for

    # A link is built only for a page being served; a call from a command or a
    # scheduled job has no address to link to.
    return url_for("unified_applications.application_list") if has_request_context() else None


def risk_citation(risk_id: int, name: str) -> Dict[str, Any]:
    """A citation to a risk a person can open (its own page shows the figure and the factors)."""
    return {"type": "risk", "id": risk_id, "name": name, "target": f"/risks/{risk_id}"}


def _risk_cost(organization_id: int, risk: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """"What does this risk cost us?" -- the yearly loss recorded for the named risk
    (every risk with one when none is named), read from the cost facts the Open FAIR
    run wrote, with who entered the factors and when. A risk with no loss worked out
    is named as such; no figure is ever estimated here."""
    from app.models.risk import Risk
    from app.modules.ai_chat.services.solution_ai_service import _STOPWORDS
    from app.modules.duplicate_detection.services.duplicate_detection_utils import DuplicateDetectionUtils
    from app.services import open_fair

    risks = Risk.query.filter(Risk.organization_id == organization_id).order_by(Risk.id).all()
    if risk:
        wanted = DuplicateDetectionUtils.tokenize(risk) - _STOPWORDS
        risks = [r for r in risks if wanted and wanted <= DuplicateDetectionUtils.tokenize(r.title or "")]
        if not risks:
            return {
                "answer": "what the '%s' risk costs each year" % risk,
                "rows": [], "total": 0, "citations": [], "matched": False,
                "reason": "nothing recorded matches '%s'" % risk,
                "next_step": "Ask again with the name of a risk in your register, or record '%s' there." % risk,
            }
    losses = open_fair.losses_for_risks(organization_id, [r.id for r in risks])
    rows: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    for r in sorted((r for r in risks if r.id in losses), key=lambda r: -losses[r.id]["likely"]):
        words = open_fair.describe(losses[r.id])
        by = " by %s" % words["by"] if words["by"] else ""
        rows.append({
            "id": r.id,
            "name": r.title,
            "yearly_loss_likely": words["likely"],
            "yearly_loss_range": words["range"],
            "as_of": words["date"],
            "source_line": "%s most likely a year, range %s. %s. Entered%s on %s." % (
                words["likely"], words["range"], words["method"], by, words["date"]),
            "source_table": "cost_facts",
            "truth_class": truth_class_for("cost_facts"),
        })
        citations.append(risk_citation(r.id, r.title))
    named = [r.title for r in risks] if risk else []
    unpriced = [r.title for r in risks if r.id not in losses] if risk else []
    if rows and unpriced:
        reason = "no yearly loss has been worked out yet for: %s" % "; ".join(unpriced)
    elif rows:
        reason = None
    elif risk and len(risks) > 1:
        reason = "no yearly loss has been worked out yet for any of the %d risks matching '%s': %s" % (
            len(risks), risk, "; ".join(named))
    elif risk:
        reason = "no yearly loss has been worked out for '%s' yet" % risks[0].title
    elif risks:
        reason = "no risk has a yearly loss worked out yet"
    else:
        reason = "no risks are recorded in this organisation"
    if risk and len(risks) == 1:
        answer = "what the '%s' risk costs each year" % risks[0].title
    elif risk:
        answer = "what the %d risks matching '%s' cost each year" % (len(risks), risk)
    else:
        answer = "what our risks cost each year"
    out = {
        "answer": answer,
        "rows": rows,
        "total": len(rows),
        "citations": citations,
        "reason": reason,
    }
    if not rows and risk and risks:
        out["next_step"] = "Open the risk and enter the four factors to work out its yearly loss." if len(risks) == 1 else (
            "Ask again with the full name of one of these risks: %s." % "; ".join(named))
        if len(risks) == 1:
            out["next_label"] = "Open the risk"
            out["next_url"] = _risk_url(risks[0].id)
    return out




def _top_risks(organization_id: int, limit: Any = None, **_: Any) -> Dict[str, Any]:
    """"What are our top risks?" -- the open risks of the register, highest score first, each with its
    level, score, owner and what it touches. Read from the one risk register (``risks``) through
    ``risk_service.ranked_risks``; the order is the recorded score, ties by title, and nothing is
    invented when the register holds none."""
    from app.models.risk import RiskStatus
    from app.services import risk_service

    try:
        shown = max(1, min(int(limit), 25)) if limit else 5
    except (TypeError, ValueError):
        shown = 5
    rows_all = [r for r in risk_service.ranked_risks(organization_id)["rows"]
                if r["status"] != RiskStatus.CLOSED.value]
    rows_all.sort(key=lambda r: (-(r["risk_score"] or 0), (r["title"] or "").lower(), r["risk_id"]))
    owners = {}
    if rows_all:
        from app.models.risk import Risk
        owners = dict(db.session.execute(
            db.select(Risk.id, Risk.owner).where(Risk.organization_id == organization_id)).all())
    rows: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    for r in rows_all[:shown]:
        touches = ", ".join(t["name"] for t in r["touches"][:3]) or "no linked records yet"
        rows.append({
            "id": r["risk_id"],
            "name": r["title"],
            "source_line": "%s, %s of 25. Owner: %s. Touches: %s." % (
                (r["risk_level"] or "Level not recorded"), r["risk_score"],
                owners.get(r["risk_id"]) or "not recorded", touches),
            "source_table": "risks",
            "truth_class": truth_class_for("risks"),
        })
        citations.append(risk_citation(r["risk_id"], r["title"]))
    if rows:
        answer = "the %d highest-scoring open risks in the risk register" % len(rows)
        reason = "%d open risks are recorded; these score highest" % len(rows_all)
    else:
        answer = "the highest-scoring open risks in the risk register"
        reason = "no open risks are recorded in the risk register yet"
    return {"answer": answer, "rows": rows, "total": len(rows_all), "citations": citations, "reason": reason}


def _risk_url(risk_id: int) -> Optional[str]:
    from flask import has_request_context, url_for

    return url_for("risk.risk_page", risk_id=risk_id) if has_request_context() else None


def _what_breaks_if_fails(organization_id: int, service: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """"What breaks if this service fails?" (UC-S2-01) — the one impact
    envelope, called for the named service. Impact is never recomputed here:
    the named service is resolved to an element and the canonical impact walk
    (IntelligenceQueryService.cross_layer_impact, through the answer envelope)
    produces the rows and the citations.

    With no service named, or a name that resolves to nothing, it returns a
    plain reason rather than a guessed answer.
    """
    element = _resolve_service_element(organization_id, service)
    if element is None:
        if service:
            # A service was named but matched nothing: say so, and offer the
            # names this organisation does record. The organisation's most
            # depended-on systems are never returned here -- they are not the
            # named service's dependants, and listed as rows they read as such.
            closest = _closest_recorded_names(organization_id, service)
            return {
                "answer": "what breaks if '%s' fails" % service,
                "service": service,
                "matched": False,
                "rows": [],
                "total": 0,
                "citations": [],
                "closest_names": closest,
                "reason": "nothing recorded matches '%s'" % service,
                "next_step": (
                    "Closest recorded names: %s. Ask again with one of them, or record '%s' in your model."
                    % (", ".join(closest), service)
                    if closest
                    else "Nothing similar is recorded. Ask again with a recorded name, or record '%s' in your model."
                    % service
                ),
                "next_url": None if closest else _applications_url(),
                "next_label": "See your recorded applications" if not closest and _applications_url() else None,
            }
        # No single service was named ("the single points of failure in our
        # stack"): the systems the most other things depend on are the single
        # points of failure. Count how many relationships point at each
        # element (its in-degree) from the one relationship store; the most
        # depended-on are the answer, each one a record to open.
        return _single_points_of_failure(organization_id)

    from app.modules.intelligence.services.answer_envelope import (
        KIND_IMPACT,
        build_envelope,
        _compute,
        normalise_params,
    )

    params = normalise_params(KIND_IMPACT, {"element_id": element.id, "direction": "downstream"})
    envelope = build_envelope(KIND_IMPACT, params, _compute(KIND_IMPACT, params))

    # The impact walk names each element in the envelope's element map
    # (keyed by the element id as text), not on the row itself.
    element_names = {
        str(eid): meta.get("name") for eid, meta in (envelope.get("elements") or {}).items()
    }

    rows = []
    for row in envelope["rows"]:
        eid = row.get("element_id")
        relation = row.get("relation") or {}
        provenance = relation.get("provenance") if isinstance(relation.get("provenance"), dict) else {}
        rows.append(
            {
                "id": eid,
                "name": element_names.get(str(eid)),
                "truth_class": row.get("truth_class"),
                "source_table": "archimate_elements",
                # The date this connection was worked out (a derived one) or
                # recorded (a drawn one); None when the row carries neither.
                "as_of": relation.get("computed_at") or provenance.get("recorded_at"),
            }
        )
    # The impact envelope already cites every relationship and element the
    # answer rests on; name the service itself so the reader can open the
    # system they asked about even when its blast radius is empty, then each
    # reached element so the person can open what breaks.
    citations: List[Dict[str, Any]] = [element_citation(element.id, element.name)]
    for row in envelope["rows"]:
        target = row.get("element_id")
        if isinstance(target, int):
            citations.append(element_citation(target, element_names.get(str(target)) or ""))

    return {
        "answer": f"what breaks if '{element.name}' fails",
        "service": element.name,
        "rows": rows,
        "total": len(rows),
        "citations": citations,
        "computed_at": envelope.get("computed_at"),
        "reason": None if rows else f"nothing recorded depends on '{element.name}' yet",
    }


def capability_citation(capability_id: int, name: str, source_line: Optional[str] = None) -> Dict[str, Any]:
    """A citation to a business capability a person can open (its fact sheet)."""
    citation = {
        "type": "capability",
        "id": capability_id,
        "name": name,
        "target": f"/enterprise/capabilities/{capability_id}/fact-sheet",
    }
    if source_line:
        citation["source_line"] = source_line
    return citation


def _resolve_application(organization_id: int, name: Optional[str]):
    """(application, others): the one application of *organization_id* a name
    names, else ``None`` and the applications it was ambiguous between.

    An exact name (any case) wins; otherwise a name contained in exactly one
    application's name. Never a guess between several.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.utils.tenant_users import escape_like_literal

    wanted = " ".join((name or "").split())
    if not wanted:
        return None, []
    base = ApplicationComponent.query.filter(ApplicationComponent.organization_id == organization_id)
    exact = base.filter(db.func.lower(ApplicationComponent.name) == wanted.lower()).all()
    if len(exact) == 1:
        return exact[0], []
    found = exact or base.filter(ApplicationComponent.name.ilike(f"%{escape_like_literal(wanted)}%", escape="\\")).order_by(ApplicationComponent.name).all()
    if len(found) == 1:
        return found[0], []
    return None, found[:5]


def _capabilities_of_application(organization_id: int, service: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """"Which capabilities does <application> support?" -- read at question time
    from the same mapping store, through the same reader, as the application
    page's Mapped Capabilities, so the two always agree. Each capability is cited
    to its own fact sheet; an application with none says so and points to where
    one is mapped."""
    from app.services.application_capabilities import application_capability_links
    from app.utils.recorded_figures import coverage_text

    application, others = _resolve_application(organization_id, service)
    if application is None:
        if others:
            return {
                "answer": f"which capabilities '{service}' supports",
                "matched": False, "rows": [], "total": 0, "citations": [],
                "reason": "more than one application is called like '%s': %s" % (
                    service, ", ".join(a.name for a in others)),
                "next_step": "Ask again with the full name of one of them.",
            }
        return {
            "answer": f"which capabilities '{service}' supports" if service else "which capabilities an application supports",
            "service": service, "matched": False, "rows": [], "total": 0, "citations": [],
            "reason": ("nothing recorded matches '%s'" % service) if service else "no application was named",
            "next_step": "Ask again with the name of an application in your model.",
        }

    rows, citations = [], []
    for mapping, capability in application_capability_links(application.id, organization_id):
        # A unified capability points at its business capability by source_id.
        raw_id = capability.source_id if capability.__class__.__name__ == "UnifiedCapability" else capability.id
        try:
            capability_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        support = (getattr(mapping, "support_level", None) or "").replace("_", " ").capitalize()
        coverage = getattr(mapping, "coverage_percentage", None)
        line = " · ".join(part for part in (
            f"{support} support" if support else "", f"Coverage: {coverage_text(coverage)}") if part) or None
        rows.append({
            "id": capability_id, "name": capability.name, "support_level": support or None,
            "coverage_percentage": coverage, "source_table": "application_capability_mapping",
            "truth_class": TRUTH_CLASS_DRAWN,
        })
        citations.append(capability_citation(capability_id, capability.name, line))

    result = {
        "answer": f"the capabilities {application.name} supports",
        "service": application.name, "application_id": application.id,
        "rows": rows, "total": len(rows), "citations": citations,
        "reason": None if rows else f"{application.name} has no capability mapped to it yet",
    }
    if not rows:
        from flask import url_for

        result["next_step"] = "Map a capability to it on its Capabilities tab."
        result["next_label"] = f"Open {application.name}"
        result["next_url"] = url_for("unified_applications.application_detail", id=application.id, tab="capabilities")
    return result


# What each plain question about the company asks for: the profile fields that answer it, in the order shown.
PROFILE_TOPICS = {
    "products": ("What do we sell?", "what the company sells",
                 ("product", "value_proposition", "business_model", "revenue_model", "tagline")),
    "customers": ("Who are our customers?", "who the company's customers are",
                  ("target_market", "market_segment", "case_study")),
    "location": ("Where are we based?", "where the company is based",
                 ("hq_address", "hq_city", "hq_region", "hq_country", "geographic_focus")),
    "technology": ("What technology does our website use?", "the technology the company's website uses", ("technology",)),
    "leaders": ("Who leads the company?", "who leads the company", ("key_person",)),
    "overview": ("What do we know about our company?", "what is known about the company", None),
}


def _read_on(fetched_at: Optional[str]) -> Optional[str]:
    from datetime import datetime

    try:
        when = datetime.fromisoformat(str(fetched_at))
    except (TypeError, ValueError):
        return None
    return "%d %s" % (when.day, when.strftime("%b %Y"))


def _address(url: Optional[str]) -> str:
    return (url or "").replace("https://", "").replace("http://", "").rstrip("/")


def _profile_next_step(organization_id: int, has_site: bool, pending: int):
    """What lets the chat answer when the confirmed facts hold nothing for the question, and where to do it."""
    try:
        from flask import url_for

        if pending:
            return "Confirm what we read from your website, and the chat can answer from it.", url_for("account.welcome", step=3), "Confirm what we read"
        if has_site:
            return ("Read your website again, or confirm more of what it states.",
                    url_for("account.welcome"), "Read the website again")
        return ("Give your company website, and what it states comes here to confirm.", url_for("account.welcome"), "Give your website")
    except RuntimeError:  # outside a request there is no page to point to
        return None, None, None


def _company_profile(organization_id: int, topic: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """"What do we sell? Who are our customers? Where are we based? What does our website use? Who leads
    us?" Answered only from the facts a person confirmed from their own website (the organisation's
    company profile), each cited to the page it was read from. A fact the person corrected is cited as
    theirs. Nothing is inferred here and nothing unconfirmed is used."""
    from app.models.organization import Organization
    from app.modules.organization.website_crawl import confirm, pending_website_proposals

    topic = topic if topic in PROFILE_TOPICS else "overview"
    title, summary, fields = PROFILE_TOPICS[topic]
    org = db.session.get(Organization, organization_id)
    entries = [e for e in (confirm.profile_entries(org) if org is not None else [])
               if fields is None or e["field"] in fields]
    if fields:
        order = {name: i for i, name in enumerate(fields)}
        entries.sort(key=lambda e: order[e["field"]])

    rows: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries):
        page = entry.get("source_url")
        read = _read_on(entry.get("fetched_at"))
        who = entry.get("confirmed_by_name") or "a member"
        origin = "from %s%s" % (_address(page), ", as of %s" % read if read else "")
        line = "%s, %s by %s" % (origin, "corrected" if entry.get("corrected") else "confirmed", who)
        name = "%s: %s" % (entry["label"], entry["text"])
        rows.append({"id": index, "name": name, "source_line": line, "source_table": "company_profile",
                     "truth_class": truth_class_for("company_profile")})
        if page:
            citations.append({"type": "page", "id": index, "name": name, "target": page, "source_line": line})

    has_site = bool(((org.settings or {}).get("onboarding") or {}).get("website")) if org is not None else False
    pending = len(pending_website_proposals(organization_id)) if not rows else 0
    if rows:
        reason = None
    elif pending:
        reason = "what was read from your website has not been confirmed yet"
    elif has_site:
        reason = "your confirmed facts say nothing about this yet"
    else:
        reason = "no company website has been read for this organisation"
    next_step, next_url, next_label = (None, None, None) if rows else _profile_next_step(organization_id, has_site, pending)
    return {
        "answer": "%s, from the facts you confirmed from your website" % summary,
        "title": title,
        "topic": topic,
        "rows": rows,
        "total": len(rows),
        "citations": citations,
        "reason": reason,
        "next_step": next_step, "next_url": next_url, "next_label": next_label,
    }


CATALOGUE: Dict[str, CatalogueEntry] = {
    "applications_without_owner": CatalogueEntry(
        id="applications_without_owner",
        title="Which applications have no owner?",
        description="Applications with no ApplicationOwner row, grouped by business criticality.",
        params=[],
        run=_applications_without_owner,
    ),
    "business_continuity_criticality": CatalogueEntry(
        id="business_continuity_criticality",
        title="What would stop the business trading?",
        description="Systems, suppliers and owners at a given business-criticality level.",
        params=["criticality"],
        run=_business_continuity_criticality,
    ),
    "overlapping_contracts": CatalogueEntry(
        id="overlapping_contracts",
        title="Where do our contracts overlap?",
        description="Applications sharing a capability, with their contracts, combined value and nearest end date.",
        params=[],
        run=_overlapping_contracts,
    ),
    "applications_and_owners": CatalogueEntry(
        id="applications_and_owners",
        title="What do we run and who owns each one?",
        description="The application inventory with each application's accountable owner and recorded annual cost.",
        params=[],
        run=_applications_and_owners,
    ),
    "duplicate_tooling_by_capability": CatalogueEntry(
        id="duplicate_tooling_by_capability",
        title="Are we paying for two tools that do the same thing?",
        description="Capabilities covered by more than one application, with each application's annual cost.",
        params=[],
        run=_duplicate_tooling_by_capability,
    ),
    "github_sourced_records": CatalogueEntry(
        id="github_sourced_records",
        title="What came from GitHub?",
        description="The records a GitHub connector brought into the model, by connector-stamped provenance.",
        params=[],
        run=_github_sourced_records,
    ),
    "company_profile": CatalogueEntry(
        id="company_profile",
        title="What do we know about our company?",
        description="What the company sells, who its customers are, where it is based, what technology its site uses and who leads it, from the facts confirmed from its own website, each cited to the page it was read from.",
        params=["topic"],
        run=_company_profile,
    ),
    "top_risks": CatalogueEntry(
        id="top_risks",
        title="What are our top risks?",
        description="The open risks in the risk register with the highest scores, each with its level, owner and what it touches.",
        params=["limit"],
        run=_top_risks,
    ),
    "risk_cost": CatalogueEntry(
        id="risk_cost",
        title="What does a risk cost us?",
        description="The yearly loss worked out for a risk with the Open FAIR method: a most likely figure and a 90 per cent range, with who entered the factors and when.",
        params=["risk"],
        run=_risk_cost,
    ),
    "capabilities_of_application": CatalogueEntry(
        id="capabilities_of_application",
        title="Which capabilities does an application support?",
        description="The business capabilities mapped to a named application, read from the same mapping the application page shows, each cited to its fact sheet.",
        params=["service"],
        run=_capabilities_of_application,
    ),
    "what_breaks_if_fails": CatalogueEntry(
        id="what_breaks_if_fails",
        title="What breaks if a service fails?",
        description="The blast radius of a named service's failure, from the one impact answer.",
        params=["service"],
        run=_what_breaks_if_fails,
    ),
}


def run_entry(entry_id: str, organization_id: int, **params: Any) -> Dict[str, Any]:
    """Run one catalogue entry by id. Raises KeyError for an unknown id —
    callers (the route and the interpreter) turn that into an honest 404,
    never a silent empty answer."""
    entry = CATALOGUE[entry_id]
    return entry.run(organization_id=organization_id, **params)


def list_entries() -> List[Dict[str, Any]]:
    return [
        {"id": e.id, "title": e.title, "description": e.description, "params": e.params}
        for e in CATALOGUE.values()
    ]
