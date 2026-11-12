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


# Older words other places still use (the batch import's, the old list's) and the
# value on the list each one now means. Keys are lower case.
OLD_STATUS_WORDS = {
    "planned": "design",
    "proposed": "design",
    "pending": "design",
    "deprecated": "retiring",
    "legacy": "retiring",
    "sunset": "retiring",
    "retired": "decommissioned",
    "eol": "decommissioned",
    "end-of-life": "decommissioned",
    "removed": "decommissioned",
    "inactive": "decommissioned",
}


# Words the batch import also reads as a value on the list (keys lower case).
STATUS_SYNONYMS = {
    "prod": "production",
    "live": "production",
    "operational": "production",
    "running": "production",
    "active": "production",
    "done": "production",
    "dev": "development",
    "developing": "development",
    "in dev": "development",
    "under development": "development",
    "test": "testing",
    "qa": "testing",
    "uat": "testing",
    "in test": "testing",
    "in testing": "testing",
    "stage": "staging",
    "preprod": "staging",
    "pre-prod": "staging",
    "pre-production": "staging",
    "planning": "design",
    "in development": "development",
    "end of life": "decommissioned",
    "decommission": "decommissioned",
}


# The headings a file may give its status column, in the order they are tried. The CSV,
# Excel and batch imports and the batch preview all read the column through this one list.
STATUS_COLUMN_HEADINGS = (
    "Status",
    "deployment_status",
    "Deployment Status",
    "App Status",
    "Application Status",
)


def _heading_key(heading) -> str:
    return " ".join(str(heading).replace("_", " ").split()).lower()


def status_cell(row):
    """(found, value) for the status column of a row keyed by the file's own headings."""
    by_key = {}
    for heading, value in (row or {}).items():
        by_key.setdefault(_heading_key(heading), value)
    for heading in STATUS_COLUMN_HEADINGS:
        key = _heading_key(heading)
        if key in by_key:
            return True, by_key[key]
    return False, None


def words_for_status(status):
    """A list value and every older word that now means it, all lower case."""
    return (status,) + tuple(word for word, mean in OLD_STATUS_WORDS.items() if mean == status)


def match_status(value):
    """The list value a stored or typed status is, ignoring case and padding; else None."""
    word = str(value or "").strip().lower()
    return word if word in APPLICATION_STATUS_VALUES else None


def canonical_status(value):
    """The list value for a status or an older word for it (any case); else None."""
    word = str(value or "").strip().lower()
    return match_status(word) or OLD_STATUS_WORDS.get(word)


def same_status(first, second) -> bool:
    """Whether two statuses are the same value, ignoring case and padding."""
    return str(first or "").strip().lower() == str(second or "").strip().lower()


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
    if current and str(current).strip() and match_status(current) is None:
        options.append((current, current))
    return options


def refusal_for(value) -> str:
    """The one sentence every screen, import and API gives for a refused status."""
    return f"Status '{str(value).strip()}' is not one of: " + ", ".join(APPLICATION_STATUS_VALUES)


class StatusRefused(ValueError):
    """A status that is not on the list; its text is the one refusal sentence."""

    def __init__(self, value):
        self.value = value
        super().__init__(refusal_for(value))


def status_for_write(value):
    """What to store for a status a person typed or a file held: (stored, refusal).

    A value on the list, an older word for one, or a word the import preview maps
    (any case, any padding) is stored in the list's form. A blank stores nothing
    (None), as the import preview maps a blank. Anything else is refused in words.
    """
    word = " ".join(str(value or "").split()).lower()
    if not word:
        return None, None
    stored = canonical_status(word) or STATUS_SYNONYMS.get(word)
    if stored:
        return stored, None
    return None, refusal_for(value)


def status_to_store(current, value):
    """What a model stores when `value` is set over `current`; raises StatusRefused.

    A value equal to the stored one passes unchanged, so an off-list value already
    stored is kept until the person picks one from the list.
    """
    if value is not None and current is not None and same_status(value, current):
        return value
    stored, refusal = status_for_write(value)
    if refusal:
        raise StatusRefused(value)
    return stored
