"""The one vendor register: which vendors an organisation has, and what they supply.

An organisation's vendors are the vendors its applications and contracts name,
plus any it recorded by hand. Every vendor screen reads this module, so the
Vendors list, a vendor's page and the Mapping tab cannot disagree.

Stores used (none added here):
  * ``VendorOrganization`` – the vendor record. The name is unique across the
    platform, so two organisations naming "Okta" share the row; each sees only
    what its own applications and contracts attach to it.
  * ``ApplicationComponent.vendor_organization_id`` – the link from a system to
    its vendor (falling back to the vendor of its primary vendor product).
  * ``VendorContract.vendor_id`` – the link from a contract to its vendor.
  * Annual cost comes from ``application_cost_accessor``; renewals from the
    organisation's active contracts.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from flask import g, has_request_context
from sqlalchemy import event, func
from sqlalchemy.orm import Session

from app import db
from app.models.application_portfolio import ApplicationComponent, VendorContract
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

RENEWAL_WINDOW_DAYS = 90


def _org(org_id: Optional[int]) -> Optional[int]:
    if org_id is not None:
        return org_id
    if has_request_context():
        return getattr(g, "current_org_id", None)
    return None


def clean_name(name) -> str:
    return " ".join(str(name or "").split())


def find_vendor(name) -> Optional[VendorOrganization]:
    name = clean_name(name)
    if not name:
        return None
    return db.session.execute(
        db.select(VendorOrganization).where(func.lower(VendorOrganization.name) == name.lower())
    ).scalars().first()


def resolve_vendor(name, org_id: Optional[int] = None) -> Optional[VendorOrganization]:
    """The vendor called ``name``, created for the organisation if none exists."""
    name = clean_name(name)
    if not name:
        return None
    vendor = find_vendor(name)
    if vendor is None:
        vendor = VendorOrganization(name=name[:200], named_by_organization_id=_org(org_id))
        db.session.add(vendor)
        db.session.flush()
    return vendor


def set_application_vendor(application: ApplicationComponent, vendor: Optional[VendorOrganization]) -> None:
    """Record ``vendor`` as the supplier of ``application`` (None clears it)."""
    application.vendor_organization_id = vendor.id if vendor is not None else None
    application.vendor_name = vendor.name[:100] if vendor is not None else None


def _app_vendor_expr():
    """SQL for "the vendor of this application": the direct link, else its product's vendor."""
    product_vendor = (
        db.select(VendorProduct.vendor_organization_id)
        .where(VendorProduct.id == ApplicationComponent.vendor_product_id)
        .scalar_subquery()
    )
    return func.coalesce(ApplicationComponent.vendor_organization_id, product_vendor)


def _live_apps(org_id: int):
    return [ApplicationComponent.organization_id == org_id, ApplicationComponent.deleted_at.is_(None)]


def vendor_of(application: ApplicationComponent) -> Optional[int]:
    if application.vendor_organization_id:
        return application.vendor_organization_id
    product = application.primary_vendor_product
    return product.vendor_organization_id if product is not None else None


def systems_without_vendor(org_id: Optional[int] = None) -> List[ApplicationComponent]:
    """The organisation's systems that name no vendor, by name."""
    org_id = _org(org_id)
    if org_id is None:
        return []
    return list(db.session.execute(
        db.select(ApplicationComponent)
        .where(*_live_apps(org_id), _app_vendor_expr().is_(None))
        .order_by(func.lower(ApplicationComponent.name), ApplicationComponent.id)
    ).scalars())


def vendor_ids(org_id: Optional[int] = None) -> List[int]:
    """Every vendor the organisation names on a system or contract, or recorded itself."""
    org_id = _org(org_id)
    if org_id is None:
        return []
    ids = set(db.session.execute(
        db.select(_app_vendor_expr()).where(*_live_apps(org_id)).distinct()
    ).scalars())
    ids |= set(db.session.execute(
        db.select(VendorContract.vendor_id).where(
            VendorContract.organization_id == org_id, VendorContract.vendor_id.isnot(None)).distinct()
    ).scalars())
    ids |= set(db.session.execute(
        db.select(VendorOrganization.id).where(VendorOrganization.named_by_organization_id == org_id)
    ).scalars())
    ids.discard(None)
    return sorted(ids)


def systems_of(vendor_id: int, org_id: Optional[int] = None) -> List[ApplicationComponent]:
    org_id = _org(org_id)
    if org_id is None:
        return []
    return list(db.session.execute(
        db.select(ApplicationComponent)
        .where(*_live_apps(org_id), _app_vendor_expr() == vendor_id)
        .order_by(func.lower(ApplicationComponent.name), ApplicationComponent.id)
    ).scalars())


def contracts_of(vendor_id: int, org_id: Optional[int] = None) -> List[VendorContract]:
    org_id = _org(org_id)
    if org_id is None:
        return []
    return list(db.session.execute(
        db.select(VendorContract)
        .where(VendorContract.organization_id == org_id, VendorContract.vendor_id == vendor_id)
        .order_by(VendorContract.end_date.asc().nullslast(), VendorContract.id)
    ).scalars())


def next_renewal(contracts, today: Optional[date] = None) -> Optional[date]:
    """The earliest end date still ahead among the active contracts, else None."""
    today = today or date.today()
    ends = [c.end_date for c in contracts
            if (c.status or "active") == "active" and c.end_date and c.end_date >= today]
    return min(ends) if ends else None


def annual_cost(systems) -> Optional[float]:
    """Sum of the systems' run cost: what is recorded on each, else what its active contracts state.

    None when none is stated. Uses the one run-cost accessor, so a vendor row agrees with the Overview.
    """
    return annual_cost_with_basis(systems)[0]


def annual_cost_with_basis(systems):
    """(total, basis): the basis says where the figure comes from, for the vendor row to show."""
    from app.services.application_cost_accessor import get_run_cost_with_source

    stated = [get_run_cost_with_source(s) for s in systems]
    stated = [(v, src) for v, src in stated if v is not None]
    if not stated:
        return None, None
    from_contracts = any(src.startswith("from ") for _, src in stated)
    recorded = any(not src.startswith("from ") for _, src in stated)
    basis = ("from systems and contracts" if from_contracts and recorded
             else "from active contracts" if from_contracts else "recorded on the systems")
    return sum(v for v, _ in stated), basis


def register(org_id: Optional[int] = None, today: Optional[date] = None) -> List[Dict]:
    """One row per vendor, renewals due within 90 days first, then by name."""
    org_id = _org(org_id)
    today = today or date.today()
    ids = vendor_ids(org_id)
    if not ids:
        return []
    vendors = db.session.execute(
        db.select(VendorOrganization).where(VendorOrganization.id.in_(ids))
    ).scalars().all()
    rows = []
    for v in vendors:
        systems = systems_of(v.id, org_id)
        renewal = next_renewal(contracts_of(v.id, org_id), today)
        cost, basis = annual_cost_with_basis(systems)
        rows.append({
            "id": v.id,
            "name": v.name,
            "vendor_type": v.vendor_type,
            "website": v.website,
            "country": v.headquarters_location,
            "description": v.description,
            "status": v.status,
            "systems": [{"id": s.id, "name": s.name} for s in systems],
            "system_count": len(systems),
            "annual_cost": cost,
            "annual_cost_basis": basis,
            "next_renewal": renewal.isoformat() if renewal else None,
            "renewal_due": bool(renewal and renewal <= today + timedelta(days=RENEWAL_WINDOW_DAYS)),
        })
    rows.sort(key=lambda r: (
        not r["renewal_due"],
        r["next_renewal"] if r["renewal_due"] else "",
        r["name"].lower(),
    ))
    return rows


def backfill_links(org_id: Optional[int] = None, dry_run: bool = False) -> Dict[str, int]:
    """Link every system (and contract) to the vendor it names. Safe to re-run.

    A system with no direct link takes the vendor of its primary vendor product,
    else the vendor called by its ``vendor_name`` (created when missing). A
    contract with no vendor takes the vendor of the system it is for.
    """
    org_ids = [org_id] if org_id is not None else list(db.session.execute(
        db.text("SELECT id FROM organizations ORDER BY id")).scalars())
    counts = {"systems_linked": 0, "vendors_created": 0, "contracts_linked": 0}
    for oid in org_ids:
        apps = db.session.execute(
            db.select(ApplicationComponent).where(
                ApplicationComponent.organization_id == oid,
                ApplicationComponent.vendor_organization_id.is_(None),
            )
        ).scalars().all()
        for application in apps:
            vendor_id = None
            if application.vendor_product_id:
                product = db.session.get(VendorProduct, application.vendor_product_id)
                vendor_id = product.vendor_organization_id if product else None
            if vendor_id is None and clean_name(application.vendor_name):
                existing = find_vendor(application.vendor_name)
                if existing is None:
                    counts["vendors_created"] += 1
                if dry_run:
                    counts["systems_linked"] += 1
                    continue
                vendor_id = resolve_vendor(application.vendor_name, oid).id
            if vendor_id is not None:
                counts["systems_linked"] += 1
                if not dry_run:
                    application.vendor_organization_id = vendor_id
        if not dry_run:
            db.session.flush()
        contracts = db.session.execute(
            db.select(VendorContract).where(
                VendorContract.organization_id == oid,
                VendorContract.vendor_id.is_(None),
                VendorContract.application_id.isnot(None),
            )
        ).scalars().all()
        for contract in contracts:
            application = db.session.get(ApplicationComponent, contract.application_id)
            vendor_id = vendor_of(application) if application is not None else None
            if vendor_id is not None:
                counts["contracts_linked"] += 1
                if not dry_run:
                    contract.vendor_id = vendor_id
    if not dry_run:
        db.session.commit()
    return counts


@event.listens_for(Session, "before_flush")
def _write_vendor_link(session, flush_context, instances):
    """Naming a vendor on a system links it: the name and the link never drift apart."""
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, ApplicationComponent):
            continue
        name_hist = db.inspect(obj).attrs.vendor_name.history
        link_hist = db.inspect(obj).attrs.vendor_organization_id.history
        if not name_hist.has_changes():
            continue
        if link_hist.has_changes() and obj.vendor_organization_id is not None:
            continue  # the link was set directly, with the name
        name = clean_name(obj.vendor_name)
        if not name:
            obj.vendor_organization_id = None
            continue
        with session.no_autoflush:
            vendor = find_vendor(name)
        if vendor is None:
            # A nested flush is not allowed here, so the vendor is written directly.
            vendor_id = session.connection().execute(
                VendorOrganization.__table__.insert()
                .values(name=name[:200], named_by_organization_id=obj.organization_id or _org(None))
                .returning(VendorOrganization.__table__.c.id)
            ).scalar_one()
        else:
            vendor_id = vendor.id
        obj.vendor_organization_id = vendor_id


def page_summary() -> Optional[Dict]:
    """What the Vendors page opens with: the plain answer and the systems still unmapped.

    None when the register cannot be read, so the page says so instead of
    showing a zero that reads as "no vendors".
    """
    from flask import current_app
    from flask_login import current_user

    org_id = _org(None)
    if org_id is None:
        return None
    try:
        rows = register(org_id)
        coverage = system_coverage(org_id)
    except Exception:  # noqa: BLE001 - the page reports it, rather than showing zeros
        db.session.rollback()
        current_app.logger.exception("Vendor register could not be read")
        return None
    # Who reads the total is the Costs page's rule, the same one the rows and the register API follow.
    from app.decorators.requires_role import may_see_spend_pages

    costs = [r["annual_cost"] for r in rows if r["annual_cost"] is not None]
    show_cost = may_see_spend_pages(current_user)
    return {
        "vendor_count": len(rows),
        "supplied_count": coverage["supplied_count"],
        "total_systems": coverage["total_systems"],
        "renewals_due": sum(1 for r in rows if r["renewal_due"]),
        "annual_cost": sum(costs) if (costs and show_cost) else None,
        "show_cost": show_cost,
        "unmapped_count": coverage["unmapped_count"],
        "unmapped": coverage["unmapped"],
        "decommissioned": coverage["decommissioned"],
    }


def system_coverage(org_id: Optional[int] = None) -> Optional[Dict]:
    """How many of the organisation's live systems name a vendor: the one count both vendor pages state.

    A system counts when it is not decommissioned (the Overview's definition of an application), so
    ``decommissioned`` is reported beside it for the page to say what was left out. None when the
    organisation or the data cannot be read.
    """
    from app.modules.dashboard.services.overview_figures import _active_applications

    org_id = _org(org_id)
    if org_id is None:
        return None
    active = _active_applications().where(ApplicationComponent.organization_id == org_id,
                                          ApplicationComponent.deleted_at.is_(None))
    total = db.session.execute(
        db.select(func.count()).select_from(active.with_only_columns(ApplicationComponent.id).subquery())
    ).scalar_one()
    unmapped = db.session.execute(
        active.where(_app_vendor_expr().is_(None)).order_by(func.lower(ApplicationComponent.name),
                                                           ApplicationComponent.id)
    ).scalars().all()
    # copies are left out of the Systems list altogether, so they are not "decommissioned" either
    every = db.session.execute(
        db.select(func.count(ApplicationComponent.id)).where(
            *_live_apps(org_id), ~ApplicationComponent.name.ilike("(Duplicate)%", escape="\\"))).scalar_one()
    return {
        "total_systems": total,
        "supplied_count": total - len(unmapped),
        "unmapped_count": len(unmapped),
        "unmapped": [{"id": a.id, "name": a.name, "lifecycle_status": a.lifecycle_status} for a in unmapped[:200]],
        "decommissioned": every - total,
    }


def list_context() -> Dict:
    """What the Vendors page renders from: its register rows (cost shown to the people the Costs page
    admits, each row's active-contract figures) and the system coverage. One function for every route
    that renders the page, so no route can state a different count."""
    import logging

    from flask_login import current_user

    from app.decorators.requires_role import may_see_spend_pages
    from app.modules.procurement.services import get_contract_amounts, total_of_stated

    log = logging.getLogger(__name__)
    try:
        rows = register()
    except Exception:  # noqa: BLE001 - the page says the list could not be read
        db.session.rollback()
        rows = []
    cost_visible = may_see_spend_pages(current_user)
    for r in rows:
        r["cost_visible"] = cost_visible
        if not cost_visible:
            r["annual_cost"] = None
            r["annual_cost_basis"] = None
    try:
        org_id = _org(None)
        by_vendor = {}
        if org_id is not None and rows:
            for c in VendorContract.query.filter(
                VendorContract.organization_id == org_id,
                VendorContract.status == "active",
                VendorContract.vendor_id.in_([r["id"] for r in rows]),
            ).all():
                by_vendor.setdefault(c.vendor_id, []).append(get_contract_amounts(c)["annual_cost"])
        for r in rows:
            costs = by_vendor.get(r["id"], [])
            r["contract_count"] = len(costs)
            r["contract_annual_cost"] = (total_of_stated(costs)[0] if costs else None) if cost_visible else None
    except Exception:  # noqa: BLE001 - the columns are left out rather than shown wrong
        db.session.rollback()
        log.exception("Vendor contract figures could not be read")
    try:
        coverage = system_coverage()
    except Exception:  # noqa: BLE001 - fall back to the register's own counts
        db.session.rollback()
        log.exception("Vendor page system counts could not be read")
        coverage = None
    return {"register_rows": rows, "system_coverage": coverage, "system_counts": coverage}
