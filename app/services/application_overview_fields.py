"""The text fields of the application Overview edit form and how long each may be.

One place answers "how many characters can this field hold": the length of the
database column that stores it. The edit form's ``maxlength`` and the save
route's check both read it, so what the form lets a person type is what the
route accepts and what the column can keep. Long-text columns have no length of
their own, so they carry the limit written here.
"""

from __future__ import annotations

# form key -> (column name, label, limit for a column with no length of its own)
OVERVIEW_TEXT_FIELDS = {
    "name": ("name", "Name", None),
    "application_category": ("application_category", "Category", None),
    "version": ("version", "Version", None),
    "businessDomain": ("business_domain", "Business Domain", None),
    "businessOwner": ("business_owner", "Business Owner", None),
    "techOwner": ("technical_lead", "Technical Lead", None),
    "devTeam": ("development_team", "Development Team", None),
    "supportLevel": ("support_team", "Support Team", None),
    "description": ("description", "Description", 5000),
    "technology_stack": ("technology_stack", "Technology Stack", 500),
}


def overview_limits() -> dict:
    """{form key: most characters the field may hold}, read from the columns."""
    from app.models.application_portfolio import ApplicationComponent

    columns = ApplicationComponent.__table__.columns
    limits = {}
    for key, (column, _label, fallback) in OVERVIEW_TEXT_FIELDS.items():
        length = getattr(columns[column].type, "length", None)
        limits[key] = length or fallback
    return limits
