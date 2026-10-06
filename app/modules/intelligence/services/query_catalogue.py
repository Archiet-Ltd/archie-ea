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
_DRAWN_SOURCE_TABLES = {"application_owners", "application_components"}


def truth_class_for(source_table: Optional[str]) -> str:
    """The drawn/derived/synchronised class for a citation's source table.

    Returns "derived" for anything not explicitly known to be drawn, since
    a value this catalogue computes (a count, a group) is derived from its
    rows even when every individual row is itself drawn.
    """
    if source_table in _DRAWN_SOURCE_TABLES:
        return TRUTH_CLASS_DRAWN
    return TRUTH_CLASS_DERIVED


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
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    owned_ids = {
        row[0]
        for row in db.session.query(ApplicationOwner.application_id)
        .filter(
            ApplicationOwner.organization_id == organization_id,
            ApplicationOwner.application_id.isnot(None),
        )
        .all()
    }

    unowned = (
        ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == organization_id,
        )
        .all()
    )
    unowned = [a for a in unowned if a.id not in owned_ids]

    groups: Dict[str, List[Dict[str, Any]]] = {}
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

    return {
        "answer": "applications with no recorded owner, by business criticality",
        "groups": [
            {"group": group, "rows": rows} for group, rows in sorted(groups.items())
        ],
        "total": len(unowned),
        "reason": None if unowned else "every application in this organisation has a recorded owner",
    }


def _business_continuity_criticality(
    organization_id: int, criticality: str = "Critical", **_: Any
) -> Dict[str, Any]:
    """"What would stop the business trading?" — the systems, suppliers
    and owners for a given criticality level."""
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent

    apps = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == organization_id,
        ApplicationComponent.business_criticality == criticality,
    ).all()

    owners_by_app: Dict[int, List[str]] = {}
    if apps:
        owner_rows = (
            db.session.query(ApplicationOwner)
            .filter(
                ApplicationOwner.organization_id == organization_id,
                ApplicationOwner.application_id.in_([a.id for a in apps]),
            )
            .all()
        )
        if owner_rows:
            from app.models.user import User

            # Scoped to this organisation explicitly: a row's user_id is
            # already drawn from an org-filtered ApplicationOwner row, but
            # the tenant-scoping gate requires the predicate at every query
            # site, not just transitively through a join.
            owner_user_ids = {row.user_id for row in owner_rows}
            users_by_id = {
                u.id: u
                for u in User.query.filter(
                    User.id.in_(owner_user_ids),
                    User.organization_id == organization_id,
                ).all()
            }
            for row in owner_rows:
                user = users_by_id.get(row.user_id)
                name = user.email if user else "not recorded"
                owners_by_app.setdefault(row.application_id, []).append(name)

    rows = []
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

    return {
        "answer": f"systems at '{criticality}' criticality, their suppliers and owners",
        "criticality": criticality,
        "rows": rows,
        "total": len(rows),
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

    result_groups = []
    for bucket in overlapping.values():
        capability = BusinessCapability.query.filter_by(
            id=bucket["capability_id"], organization_id=organization_id,
        ).first()
        # Review finding: a single contract can cover several applications
        # (contract_applications is a many-to-many join), so summing one row
        # per (app, contract) double-counts that contract's value for every
        # extra application sharing it. Sum each distinct contract once.
        distinct_contracts = {
            row["contract_id"]: row["contract_value"]
            for row in bucket["rows"] if row["contract_value"] is not None
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

    return {
        "answer": "applications sharing a capability, with their overlapping contracts",
        "groups": sorted(result_groups, key=lambda g: g["group"]),
        "total": len(result_groups),
        "reason": None if result_groups else "no capability is shared by more than one application with a contract",
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
