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

from datetime import date
from typing import Dict, List, Optional, Tuple

from flask import g, has_request_context
from sqlalchemy import and_, event, func, or_
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


def _undeleted_apps(org_id: int):
    return [ApplicationComponent.organization_id == org_id, ApplicationComponent.deleted_at.is_(None)]


def _live_apps(org_id: int):
    """The organisation's live systems: the Systems list's own definition, and not deleted."""
    from app.modules.dashboard.services.overview_figures import _active_applications

    return _undeleted_apps(org_id) + [
        ApplicationComponent.id.in_(_active_applications().with_only_columns(ApplicationComponent.id))]


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


def is_active(contract, today: Optional[date] = None) -> bool:
    """The one definition of an active contract: its status is active, and it has no end date or one not yet
    past. Every screen that counts, lists or prices active contracts asks this, or ``active_condition``."""
    today = today or date.today()
    return contract.status == "active" and (contract.end_date is None or contract.end_date >= today)


def active_condition(today: Optional[date] = None):
    """The same definition as a query condition on ``VendorContract``."""
    today = today or date.today()
    return and_(VendorContract.status == "active",
                or_(VendorContract.end_date.is_(None), VendorContract.end_date >= today))


def renewal_date_of(contract, today: Optional[date] = None) -> Optional[date]:
    """The one date a contract comes up for renewal: its renewal date, else its end date, else None. An active
    contract whose renewal date has already passed has renewed, so its next date is its end date and never a day
    in the past. The vendor register and the renewals dashboard both read it, so they cannot name different
    dates for one contract."""
    today = today or date.today()
    if contract.renewal_date and contract.renewal_date < today and is_active(contract, today):
        return contract.end_date
    return contract.renewal_date or contract.end_date


RENEWAL_BAND_DAYS = (("critical", 30), ("warning", RENEWAL_WINDOW_DAYS), ("upcoming", 180))


def renewal_band(days: Optional[int]) -> str:
    """The one banding of days to a renewal, used by the renewals tiles, their list and the vendor pages. A
    contract exactly 30, 90 or 180 days away is in the nearer band, as the labels "within 30 days" say."""
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    for band, limit in RENEWAL_BAND_DAYS:
        if days <= limit:
            return band
    return "ok"


def days_until(target: Optional[date], today: Optional[date] = None) -> Optional[int]:
    """Whole days from today to a date, negative once past, None with no date. The one place a contract
    date is turned into a day count."""
    if target is None:
        return None
    return (target - (today or date.today())).days


def get_days_until_renewal(contract, today: Optional[date] = None) -> Optional[int]:
    """Whole days from today to the contract's one renewal date, negative once past, None with no date."""
    today = today or date.today()
    return days_until(renewal_date_of(contract, today), today)


def get_renewal_urgency(contract, today: Optional[date] = None) -> str:
    """The band of the contract's days to renewal: critical (up to 30), warning (up to 90), upcoming (up to
    180), ok (later), expired or unknown. Every screen and brief that says how soon a contract renews reads
    this, so none of them can band the same contract differently."""
    return renewal_band(get_days_until_renewal(contract, today))


def renewal_is_due(days: Optional[int]) -> bool:
    """Whether a renewal is within the 90 day window the vendors page and the 90 day tile both name."""
    return renewal_band(days) in ("critical", "warning")


def next_renewal(contracts, today: Optional[date] = None) -> Optional[date]:
    """The earliest renewal date among the active contracts, else None. Never a date already past."""
    today = today or date.today()
    dates = [d for d in (renewal_date_of(c, today) for c in contracts if is_active(c, today))
             if d and d >= today]
    return min(dates) if dates else None


NOT_COUNTED_INACTIVE = "Not counted: the contract is not active"
NOT_COUNTED_SYSTEM = "Not counted: it sits on a system that is decommissioned or removed"
NOT_COUNTED_NO_VENDOR = "Not counted for this vendor: the system it sits on names no vendor"
NOT_ADDED_SYSTEM_COST = "Not added: the system it sits on records its own cost"


def not_added_cause(code: str, reporting_currency: str) -> str:
    """Why an active contract in another currency is not added: the one wording, without its lead-in."""
    return "stated in %s, and this organisation reports in %s" % (code.upper(), reporting_currency)


def not_added_reason(code: str, reporting_currency: str) -> str:
    """The words every screen uses for an active contract stated in another currency."""
    return "Not added: " + not_added_cause(code, reporting_currency)


def contract_ledger(org_id: Optional[int] = None, today: Optional[date] = None) -> Dict:
    """The one rule for which contracts a cost figure adds up, applied to every contract at once.

    An active contract counts for the vendor of the live system it sits on, else for the vendor it names
    when it sits on no system. A contract on a decommissioned, removed or copied system counts nowhere.
    Returns ``{"by_vendor": {vendor id: [contracts]}, "unassigned": [contracts that name no vendor],
    "left_out": {contract id: reason}, "counted": every counted contract, "superseded": {contract id: reason}
    for counted contracts whose cost is not added because the system they sit on records its own}``. A
    contract whose end date has passed counts nowhere, whatever its status. The Vendors row, the vendor
    page, Spend and the next renewal all read this and nothing else.
    """
    from app.services.application_cost_accessor import get_annual_cost

    org_id = _org(org_id)
    today = today or date.today()
    ledger: Dict = {"by_vendor": {}, "unassigned": [], "left_out": {}, "counted": [], "superseded": {},
                    "other_currency": [], "other_by_vendor": {}}
    if org_id is None:
        return ledger
    from app.services.application_cost_accessor import get_reporting_currency

    currency = get_reporting_currency(org_id)
    owner_of = {}
    records_cost = set()
    for system, owner in db.session.execute(
            db.select(ApplicationComponent, _app_vendor_expr()).where(*_live_apps(org_id))).all():
        owner_of[system.id] = owner
        if get_annual_cost(system) is not None:
            records_cost.add(system.id)
    for c in db.session.execute(
            db.select(VendorContract).where(VendorContract.organization_id == org_id)
            .order_by(VendorContract.id)).scalars():
        if not is_active(c, today):
            ledger["left_out"][c.id] = (NOT_COUNTED_INACTIVE if c.status != "active" else
                                        "Not counted: the contract ended on %s" % c.end_date.strftime("%d %b %Y"))
        elif c.application_id is not None and c.application_id not in owner_of:
            ledger["left_out"][c.id] = NOT_COUNTED_SYSTEM
        elif (c.currency or currency).upper() != currency:
            # an amount in another currency is never added under the organisation's symbol
            ledger["left_out"][c.id] = not_added_reason(c.currency, currency)
            ledger["other_currency"].append(c)
            owner = c.vendor_id if c.application_id is None else owner_of[c.application_id]
            if owner is not None:
                ledger["other_by_vendor"].setdefault(owner, []).append(c)
        else:
            owner = c.vendor_id if c.application_id is None else owner_of[c.application_id]
            ledger["counted"].append(c)
            if c.application_id in records_cost:
                ledger["superseded"][c.id] = NOT_ADDED_SYSTEM_COST
            if owner is None:
                ledger["unassigned"].append(c)
            else:
                ledger["by_vendor"].setdefault(owner, []).append(c)
    return ledger


def _stack_by_currency(contracts) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for c in contracts:
        if c.annual_cost is None:
            continue
        row = out.setdefault(c.currency.upper(), {"total": 0.0, "contracts": 0})
        row["total"] += float(c.annual_cost)
        row["contracts"] += 1
    return out


def other_currency_totals(ledger: Dict) -> Dict[str, Dict]:
    """The active contracts stated in a currency other than the organisation's, by currency:
    {code: {"total": annual cost they state, "contracts": how many}}. They are listed apart, never added in."""
    return _stack_by_currency(ledger.get("other_currency", []))


def other_currency_by_system(ledger: Dict) -> Dict[int, Dict[str, Dict]]:
    """The same, for the contracts that sit on a system: {system id: {code: {"total", "contracts"}}}."""
    on_system: Dict[int, list] = {}
    for c in ledger.get("other_currency", []):
        if c.application_id is not None:
            on_system.setdefault(c.application_id, []).append(c)
    return {app_id: _stack_by_currency(rows) for app_id, rows in on_system.items()}


def counted_by_system(ledger: Dict) -> Dict[int, list]:
    """The counted contracts that sit on a system: {system id: [contracts]}."""
    on_system: Dict[int, list] = {}
    for c in ledger["counted"]:
        if c.application_id is not None:
            on_system.setdefault(c.application_id, []).append(c)
    return on_system


def stated_annual_cost(contracts) -> Tuple[Optional[float], int]:
    """(total, how many state one) of the annual cost these counted contracts state; total None when none does."""
    from app.modules.procurement.services import get_contract_amounts, total_of_stated

    amounts = [a for a in (get_contract_amounts(c)["annual_cost"] for c in contracts) if a is not None]
    total, _ = total_of_stated(amounts)
    return (float(total) if total is not None else None), len(amounts)


def stated_contract_value(contracts) -> Tuple[Optional[float], int]:
    """(total, how many state one) of the total contract value these counted contracts state; total None when none does."""
    from app.modules.procurement.services import get_contract_amounts, total_of_stated

    amounts = [a for a in (get_contract_amounts(c)["contract_value"] for c in contracts) if a is not None]
    total, _ = total_of_stated(amounts)
    return (float(total) if total is not None else None), len(amounts)


def counted_contracts_by_vendor(org_id: Optional[int] = None) -> Dict[int, List[VendorContract]]:
    return contract_ledger(org_id)["by_vendor"]


def contracts_with_standing(vendor_id: int, org_id: Optional[int] = None, ledger: Optional[Dict] = None):
    """The contracts a vendor's page lists: (contract, reason or None). None means it is counted in the cost.

    Lists what is counted for the vendor and what names the vendor but counts elsewhere or nowhere.
    """
    org_id = _org(org_id)
    ledger = ledger or contract_ledger(org_id)
    counted = ledger["by_vendor"].get(vendor_id, [])
    counted_ids = {c.id for c in counted}
    no_vendor_ids = {c.id for c in ledger["unassigned"]}
    rows = [(c, ledger.get("superseded", {}).get(c.id)) for c in counted]
    for c in contracts_of(vendor_id, org_id):
        if c.id in counted_ids:
            continue
        rows.append((c, ledger["left_out"].get(c.id)
                     or (NOT_COUNTED_NO_VENDOR if c.id in no_vendor_ids else
                         "Not counted here: it is counted under the vendor of the system it sits on")))
    rows.sort(key=lambda r: (r[0].end_date is None, r[0].end_date or date.max, r[0].id))
    return rows


def annual_cost(systems, vendor_id: Optional[int] = None, org_id: Optional[int] = None) -> Optional[float]:
    """The vendor's cost a year: what is recorded on each system, else what its counted contracts state."""
    return annual_cost_with_basis(systems, vendor_id, org_id)[0]


def annual_cost_with_basis(systems, vendor_id: Optional[int] = None, org_id: Optional[int] = None, counted=None):
    """(total, basis): the basis says where the figure comes from, for the vendor row to show.

    ``counted`` is the vendor's counted contracts; when not given they come from the one ledger.
    """
    from app.modules.procurement.services import get_contract_amounts, total_of_stated
    from app.services.application_cost_accessor import get_run_cost_with_source

    if counted is None:
        counted = contract_ledger(org_id)["by_vendor"].get(vendor_id, []) if vendor_id is not None else []
    on_system: Dict[int, list] = {}
    for c in counted:
        if c.application_id is not None:
            on_system.setdefault(c.application_id, []).append(c)
    stated = [get_run_cost_with_source(s, on_system.get(s.id, [])) for s in systems]
    stated = [(v, src) for v, src in stated if v is not None]
    if vendor_id is not None:
        loose = [c for c in counted if c.application_id is None]
        amounts = [a for a in (get_contract_amounts(c)["annual_cost"] for c in loose) if a is not None]
        if amounts:
            stated.append((float(total_of_stated(amounts)[0]),
                           "from %d active contract%s" % (len(amounts), "" if len(amounts) == 1 else "s")))
    if not stated:
        return None, None
    from_contracts = any(src.startswith("from ") for _, src in stated)
    recorded = any(not src.startswith("from ") for _, src in stated)
    basis = ("from systems and contracts" if from_contracts and recorded
             else "from active contracts" if from_contracts else "recorded on the systems")
    return sum(v for v, _ in stated), basis


def unassigned_cost(ledger: Dict) -> Optional[float]:
    """The annual cost the counted contracts that name no vendor state, else None."""
    from app.modules.procurement.services import get_contract_amounts, total_of_stated

    return total_of_stated(get_contract_amounts(c)["annual_cost"] for c in ledger["unassigned"])[0]


def total_annual_cost(rows: List[Dict], ledger: Dict) -> Optional[float]:
    """The one total every screen states: the sum of the register's vendor rows, plus the counted
    contracts that name no vendor (shown apart, never inside a vendor's row). None when nothing states a cost."""
    parts = [r["annual_cost"] for r in rows if r["annual_cost"] is not None]
    loose = unassigned_cost(ledger)
    if loose is not None:
        parts.append(loose)
    return sum(parts) if parts else None


def contracts_in_total(ledger: Dict) -> int:
    """How many active contracts in the organisation's currency state an annual cost. A contract on a system
    that records its own cost is one of them: it is an active contract, though the system's figure stands in
    for it in the annual total."""
    return len([c for c in ledger["counted"] if c.annual_cost is not None])


def contracts_annual_value(ledger: Dict) -> Optional[float]:
    """The annual cost the same contracts state (``contracts_in_total`` counts them): what the active contracts
    cost a year. None when no contract states one."""
    stated = [c.annual_cost for c in ledger["counted"] if c.annual_cost is not None]
    return sum(stated) if stated else None


def active_left_apart(ledger: Dict) -> Dict:
    """The active contracts that ``contracts_in_total`` does not count, so a screen can say why:
    ``{"no_cost": how many state no annual cost, "other_currency": {code: {"total", "contracts"}},
    "on_retired_system": how many sit on a decommissioned or removed system}``."""
    return {
        "no_cost": len([c for c in ledger["counted"] + ledger.get("other_currency", []) if c.annual_cost is None]),
        "other_currency": other_currency_totals(ledger),
        "on_retired_system": len([r for r in ledger["left_out"].values() if r == NOT_COUNTED_SYSTEM]),
    }


def counted_cost_by_category(ledger: Dict) -> Dict[str, float]:
    """Annual cost of the counted contracts, by contract category (contracts that state one only)."""
    out: Dict[str, float] = {}
    for c in ledger["counted"]:
        if c.annual_cost is not None:
            out[c.contract_category or "uncategorised"] = out.get(c.contract_category or "uncategorised", 0) + c.annual_cost
    return out


def register(org_id: Optional[int] = None, today: Optional[date] = None) -> List[Dict]:
    """One row per vendor: renewals due within 90 days first, then vendors with a renewal ahead, then those with none, each by name."""
    org_id = _org(org_id)
    today = today or date.today()
    ids = vendor_ids(org_id)
    if not ids:
        return []
    vendors = db.session.execute(
        db.select(VendorOrganization).where(VendorOrganization.id.in_(ids))
    ).scalars().all()
    systems_by_vendor: Dict[int, List[ApplicationComponent]] = {}
    for system, owner in db.session.execute(
            db.select(ApplicationComponent, _app_vendor_expr())
            .where(*_live_apps(org_id))
            .order_by(func.lower(ApplicationComponent.name), ApplicationComponent.id)).all():
        if owner is not None:
            systems_by_vendor.setdefault(owner, []).append(system)
    counted_by_vendor = contract_ledger(org_id, today)["by_vendor"]
    rows = []
    for v in vendors:
        systems = systems_by_vendor.get(v.id, [])
        renewal = next_renewal(counted_by_vendor.get(v.id, []), today)
        cost, basis = annual_cost_with_basis(systems, v.id, org_id, counted_by_vendor.get(v.id, []))
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
            "renewal_due": bool(renewal and renewal_is_due((renewal - today).days)),
        })
    rows.sort(key=lambda r: (
        not r["renewal_due"],
        r["next_renewal"] if r["renewal_due"] else "",
        r["next_renewal"] is None,
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
        ledger = contract_ledger(org_id)
    except Exception:  # noqa: BLE001 - the page reports it, rather than showing zeros
        db.session.rollback()
        current_app.logger.exception("Vendor register could not be read")
        return None
    # Who reads the total is the Costs page's rule, the same one the rows and the register API follow.
    from app.decorators.requires_role import may_see_spend_pages

    total = total_annual_cost(rows, ledger)
    show_cost = may_see_spend_pages(current_user)
    return {
        "vendor_count": len(rows),
        "supplied_count": coverage["supplied_count"],
        "total_systems": coverage["total_systems"],
        "renewals_due": sum(1 for r in rows if r["renewal_due"]),
        "annual_cost": total if show_cost else None,
        "unassigned_count": len(ledger["unassigned"]),
        "unassigned_cost": unassigned_cost(ledger) if show_cost else None,
        "other_currency": other_currency_totals(ledger) if show_cost else {},
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
            *_undeleted_apps(org_id), ~ApplicationComponent.name.ilike("(Duplicate)%", escape="\\"))).scalar_one()
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
        from app.services.application_cost_accessor import get_reporting_currency

        ledger = contract_ledger(_org(None))
        reporting = get_reporting_currency()
        for r in rows:
            counted = ledger["by_vendor"].get(r["id"], [])
            abroad = ledger["other_by_vendor"].get(r["id"], [])
            costs = [get_contract_amounts(c)["annual_cost"] for c in counted]
            r["contract_count"] = len(costs) + len(abroad)
            r["other_currency_reasons"] = [not_added_reason(code, reporting) for code in
                                           sorted({c.currency.upper() for c in abroad})]
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
