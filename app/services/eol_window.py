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
    """This organisation's applications that depend on the product."""
    return (
        ApplicationComponent.query.filter(
            ApplicationComponent.vendor_product_id == product_id,
            ApplicationComponent.organization_id == organization_id,
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
