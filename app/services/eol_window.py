"""Shared end-of-support window machinery.

The end-of-support alert scanner and the CTO scorecard exposure list read
the same three things from the technology-product register: the
organisation's configured lead time, calendar-month arithmetic, and the
dependents + ownership readers. Each lives here once; the two services
import rather than reimplement.

The 18-month default and the month arithmetic are deliberately in this one
module: an organisation that has never touched the setting still gets the
brief's 18-month warning, and "N months from now" is clamped to the target
month's own length (31 Jan + 1 month lands on 28/29 Feb) -- the one
subtlety plain timedelta arithmetic gets wrong.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from app import db
from app.models.application_owner import ApplicationOwner
from app.models.application_portfolio import ApplicationComponent
from app.models.organization import Organization

DEFAULT_EOL_LEAD_MONTHS = 18


def lead_months_for(organization_id: int) -> int:
    """The organisation's configured alert lead time, in months.

    Absence (no row, or no key in `settings`) reads as the documented
    default, never as "alerts disabled" -- an organisation that has never
    touched this setting still gets the brief's 18-month warning.
    """
    org = db.session.get(Organization, organization_id)
    if org is None or not org.settings:
        return DEFAULT_EOL_LEAD_MONTHS
    value = org.settings.get("alert_eol_lead_months")
    if not isinstance(value, int) or value <= 0:
        return DEFAULT_EOL_LEAD_MONTHS
    return value


def add_months(start: datetime, months: int) -> datetime:
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


def dependents(product_id: int, organization_id: int) -> List[ApplicationComponent]:
    """This organisation's applications that depend on the product.

    Soft-deleted applications are excluded (deleted_at IS NULL).
    """
    return (
        ApplicationComponent.query.filter(
            ApplicationComponent.vendor_product_id == product_id,
            ApplicationComponent.organization_id == organization_id,
            ApplicationComponent.deleted_at.is_(None),
        )
        .order_by(ApplicationComponent.name)
        .all()
    )


def owner_summary(application_id: int, organization_id: int) -> str:
    """The organisation's recorded owners for an application, comma-joined.

    Absence reads as "no owner recorded" -- the same honest empty the alert
    scan uses -- never a guessed or computed-looking name.
    """
    rows = ApplicationOwner.get_display_rows_for_application(application_id, organization_id)
    named = [row["user_name"] for row in rows if row.get("user_name")]
    return ", ".join(named) if named else "no owner recorded"


def owner_summaries(application_ids, organization_id) -> dict:
    """Batch owner summaries for multiple applications.

    Returns a dict mapping each application_id to its owner summary string.
    Two queries total regardless of input size.
    """
    if not application_ids:
        return {}
    by_app = ApplicationOwner.get_display_rows_for_applications(
        list(application_ids), organization_id
    )
    result = {}
    for app_id, rows in by_app.items():
        named = [row["user_name"] for row in rows if row.get("user_name")]
        result[app_id] = ", ".join(named) if named else "no owner recorded"
    return result


def technologies_reaching_end_of_support(months=None):
    """The technology-layer elements whose support ends within the window.

    Returns a list of dicts -- id, name, type, support_end_date,
    extended_support_end_date, support_source -- soonest date first, for the
    current organisation only. The element query carries the tenant filter
    automatically (ArchiMateElement is tenant-scoped), so this function adds
    no organisation filter of its own and no raw SQL.

    ``months`` defaults to this module's shared 18-month default -- the one
    place the window is defined -- and must otherwise be a whole number of
    months. An element with no support end date recorded is never listed.
    """
    if months is None:
        months = DEFAULT_EOL_LEAD_MONTHS
    elif not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError("months must be a whole number of months")

    cutoff = add_months(datetime.utcnow(), months)

    from app.models.archimate_core import ArchiMateElement

    rows = (
        db.session.query(ArchiMateElement)
        .filter(
            ArchiMateElement.layer == "Technology",
            ArchiMateElement.support_end_date.isnot(None),
            ArchiMateElement.support_end_date <= cutoff,
        )
        .order_by(ArchiMateElement.support_end_date.asc(), ArchiMateElement.id.asc())
        .all()
    )

    return [
        {
            "id": row.id,
            "name": row.name,
            "type": row.type,
            "support_end_date": row.support_end_date,
            "extended_support_end_date": row.extended_support_end_date,
            "support_source": row.support_source,
        }
        for row in rows
    ]
