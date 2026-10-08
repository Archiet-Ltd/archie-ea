"""R1-B85 (PB-0043): end-of-support deadline alerts.

Scans VendorProduct.end_of_life_date (the technology-product model's own
end-of-support field) for every product an organisation's applications
depend on, and creates one tracked inbox item -- via R1-B07's one approval
queue -- when a product's end-of-life date is within the organisation's
configured lead time (default 18 months, overridable per organisation via
Organization.settings["alert_eol_lead_months"]).

Not a second alerting pipeline: this writes through
app.modules.ai_chat.services.ai_chat_approval_service.create_approval_record,
the same inbox every other proposal in the product lands in, and reads
ownership through ApplicationOwner.get_display_rows_for_application (ADR-0008,
the one ownership reader) -- never re-querying ApplicationOwner directly.

VendorProduct itself is deliberately NOT tenant-scoped (global vendor
reference data, matching VendorOrganization's own docstring) -- the
dependents are each individual organisation's ApplicationComponent rows
that point at it, so scanning is always driven by "this organisation's
applications", never by a bare VendorProduct scan with no org in the loop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from app import db
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.models.organization import Organization
from app.models.vendor.vendor_organization import VendorProduct

_DEFAULT_LEAD_MONTHS = 18
_SOURCE_TABLE = "vendor_products"
_OPERATION_TYPE = "end_of_support_alert"


def lead_months_for(organization_id: int) -> int:
    """The organisation's configured alert lead time, in months.

    Absence (no row, or no key in `settings`) reads as the documented
    default, never as "alerts disabled" -- an organisation that has never
    touched this setting still gets the brief's 18-month warning.
    """
    org = db.session.get(Organization, organization_id)
    if org is None or not org.settings:
        return _DEFAULT_LEAD_MONTHS
    value = org.settings.get("alert_eol_lead_months")
    if not isinstance(value, int) or value <= 0:
        return _DEFAULT_LEAD_MONTHS
    return value


def _add_months(start: datetime, months: int) -> datetime:
    """Calendar-month addition without a third-party dependency.

    Clamps the day to the target month's own length (e.g. 31 Jan + 1 month
    lands on 28/29 Feb, not an invalid date) -- the one subtlety plain
    timedelta arithmetic gets wrong for "N months from now".
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = start.day
    while True:
        try:
            return start.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1


def _dependents(product_id: int, organization_id: int) -> List[ApplicationComponent]:
    return (
        ApplicationComponent.query.filter(
            ApplicationComponent.vendor_product_id == product_id,
            ApplicationComponent.organization_id == organization_id,
        )
        .order_by(ApplicationComponent.name)
        .all()
    )


def _owner_summary(application_id: int, organization_id: int) -> str:
    rows = ApplicationOwner.get_display_rows_for_application(application_id, organization_id)
    named = [row["user_name"] for row in rows if row.get("user_name")]
    return ", ".join(named) if named else "no owner recorded"


def _existing_alert(product_id: int, organization_id: int):
    """The live (pending or already-decided) alert for this product in this
    organisation, if one already exists -- the de-duplication check a
    second scan relies on so the same deadline never raises twice."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    return (
        AIChatCRUDApproval.query.filter(
            AIChatCRUDApproval.organization_id == organization_id,
            AIChatCRUDApproval.operation_type == _OPERATION_TYPE,
            AIChatCRUDApproval.source_table == _SOURCE_TABLE,
            AIChatCRUDApproval.source_id == product_id,
        )
        .first()
    )


def scan_organization_for_eol_alerts(organization_id: int) -> Dict[str, Any]:
    """Create one inbox item per technology product crossing this
    organisation's end-of-support lead-time threshold, skipping products
    with no dependent application and products already alerted.

    Returns {"created": [...], "skipped_existing": [...], "lead_months": int}.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    lead_months = lead_months_for(organization_id)
    cutoff = _add_months(datetime.utcnow(), lead_months)

    products = (
        VendorProduct.query.filter(
            VendorProduct.end_of_life_date.isnot(None),
            VendorProduct.end_of_life_date <= cutoff,
        )
        .order_by(VendorProduct.end_of_life_date)
        .all()
    )

    created: List[int] = []
    skipped_existing: List[int] = []

    for product in products:
        dependents = _dependents(product.id, organization_id)
        if not dependents:
            # No application in this organisation depends on this product --
            # nothing to alert this organisation about.
            continue
        if _existing_alert(product.id, organization_id) is not None:
            skipped_existing.append(product.id)
            continue

        dependent_lines = [
            f"{app.name} (owner: {_owner_summary(app.id, organization_id)})"
            for app in dependents
        ]
        summary = (
            f"{product.name} reaches end of support on "
            f"{product.end_of_life_date.date().isoformat()} -- "
            f"{len(dependents)} dependent application(s)"
        )
        approval = create_approval_record(
            organization_id=organization_id,
            operation_type=_OPERATION_TYPE,
            entity_type="vendor_product",
            entity_id=product.id,
            summary=summary,
            operation_payload={
                "vendor_product_id": product.id,
                "vendor_product_name": product.name,
                "end_of_life_date": product.end_of_life_date.date().isoformat(),
                "lead_months": lead_months,
                "dependent_applications": dependent_lines,
            },
            source_table=_SOURCE_TABLE,
            source_id=product.id,
        )
        created.append(approval.id)

    db.session.commit()
    return {"created": created, "skipped_existing": skipped_existing, "lead_months": lead_months}


def scan_all_organizations_for_eol_alerts() -> Dict[int, Dict[str, Any]]:
    """Run the scan for every organisation -- the scheduled job's entry
    point. Each organisation's result is independent; one organisation's
    failure does not roll back another's (each call commits its own work)."""
    results: Dict[int, Dict[str, Any]] = {}
    for org_id in db.session.execute(db.select(Organization.id)).scalars():
        results[org_id] = scan_organization_for_eol_alerts(org_id)
    return results
