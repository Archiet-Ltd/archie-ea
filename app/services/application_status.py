"""The one list of deployment statuses an application can have.

The Overview edit form (a select) and the AI assistant's status tool both read
this list, so a person and the assistant can set the same values and no others.
A value already stored that is not in the list is shown as it is and is kept
until the person picks one from the list.
"""

from __future__ import annotations

APPLICATION_STATUSES = (
    ("design", "Design"),
    ("development", "Development"),
    ("testing", "Testing"),
    ("staging", "Staging"),
    ("production", "Production"),
    ("retiring", "Retiring"),
    ("decommissioned", "Decommissioned"),
)

APPLICATION_STATUS_VALUES = tuple(value for value, _label in APPLICATION_STATUSES)


# Statuses that mean the application is on its way out or gone: the two on the
# list, and the two older values still stored on some rows. Screens that flag an
# ended application (the banner, the red badge, the table's status) read this.
END_OF_LIFE_STATUSES = ("retiring", "decommissioned", "retired", "deprecated")


def is_end_of_life(status) -> bool:
    """Whether a stored status (any case, any padding) is an end-of-life one."""
    return str(status or "").strip().lower() in END_OF_LIFE_STATUSES


def status_options(current=None):
    """Options for a select: the list, plus the stored value when it is not on it."""
    options = list(APPLICATION_STATUSES)
    if current and current not in APPLICATION_STATUS_VALUES:
        options.append((current, current))
    return options
