"""The one roadmap, as a board: plateaus on cards, work packages on the cards.

Reads the one roadmap store through ``build_roadmap_slice`` (plateaus and work
packages) and shapes it for the roadmap board: four columns (Completed, In
progress, Planned, Future), one card per plateau, the plateau's work packages
listed on it. A lens narrows the same items; a filter narrows them to one
programme, solution or application. Nothing is computed that the store does not
hold: a plateau with no work package and no date is Planned, and says so.
"""
from __future__ import annotations

from datetime import date

from app.modules.genome.services.roadmap_slice import build_roadmap_slice

LENSES = ("strategic", "capability", "application", "technology", "product")
DEFAULT_LENS = "strategic"

LENS_LABELS = {
    "strategic": "Strategic",
    "capability": "Capability",
    "application": "Application",
    "technology": "Technology",
    "product": "Product",
}

LENS_HELP = {
    "strategic": "Every plateau and work package your organisation has planned.",
    "capability": "Work packages that change a business capability.",
    "application": "Work packages that change an application.",
    "technology": "Work packages in the technology architecture phase.",
    "product": "Everything still to do, grouped as Now, Next and Later.",
}

LEVELS = ("enterprise", "programme", "solution", "application")

# column key -> (title, icon)
COLUMNS = (
    ("completed", "Completed", "circle-check"),
    ("in-progress", "In progress", "clock"),
    ("planned", "Planned", "lightbulb"),
    ("future", "Future", "rocket"),
)
PRODUCT_COLUMNS = (
    ("in-progress", "Now", "clock"),
    ("planned", "Next", "lightbulb"),
    ("future", "Later", "rocket"),
)

# Dates further out than this many days are Future rather than Planned.
FUTURE_AFTER_DAYS = 365
VISIBLE_ITEMS = 3

_TECHNOLOGY_PHASES = {"d", "phase_d", "technology", "technology architecture"}


def _is_technology(wp: dict) -> bool:
    phase = (wp.get("togaf_phase") or "").strip().lower()
    element_type = (wp.get("element_type") or "").lower()
    return phase in _TECHNOLOGY_PHASES or "technology" in phase or "technology" in element_type


_LENS_TESTS = {
    "strategic": lambda wp: True,
    "product": lambda wp: True,
    "capability": lambda wp: wp.get("capability_id") is not None,
    "application": lambda wp: wp.get("application_component_id") is not None,
    "technology": _is_technology,
}


def _quarter(value) -> str | None:
    if not value:
        return None
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return "Q%d %d" % ((d.month - 1) // 3 + 1, d.year)


def _wp_matches(wp: dict, programme_id, solution_id, application_id) -> bool:
    if programme_id is not None and wp.get("programme_id") != programme_id:
        return False
    if solution_id is not None and not (
        wp.get("context") == "solution" and wp.get("context_id") == solution_id
    ):
        return False
    if application_id is not None and wp.get("application_component_id") != application_id:
        return False
    return True


def _card_status(wps: list, target_iso, today: date) -> str:
    statuses = [w.get("status") for w in wps]
    if statuses and all(s == "completed" for s in statuses):
        return "completed"
    if any(s == "in_progress" for s in statuses):
        return "in-progress"
    when = target_iso
    if not when:
        dated = [w.get("target_date") for w in wps if w.get("target_date")]
        when = max(dated) if dated else None
    if when:
        try:
            if (date.fromisoformat(when[:10]) - today).days > FUTURE_AFTER_DAYS:
                return "future"
        except ValueError:
            pass
    return "planned"


def build_roadmap_board(
    org_id: int,
    lens: str = DEFAULT_LENS,
    programme_id: int | None = None,
    solution_id: int | None = None,
    application_id: int | None = None,
    today: date | None = None,
) -> dict:
    """The board for one organisation, one lens and at most one filter set."""
    lens = lens if lens in LENSES else DEFAULT_LENS
    today = today or date.today()
    slice_dict = build_roadmap_slice(org_id)
    filtered = programme_id is not None or solution_id is not None or application_id is not None
    keep = _LENS_TESTS[lens]
    narrowed = lens not in ("strategic", "product")

    wps = [
        w for w in slice_dict["work_packages"]
        if keep(w) and _wp_matches(w, programme_id, solution_id, application_id)
    ]
    by_plateau: dict = {}
    for w in wps:
        by_plateau.setdefault(w["plateau_id"], []).append(w)

    cards = []
    for p in slice_dict["plateaus"]:
        mine = by_plateau.get(p["id"], [])
        # A narrowed view lists only the plateaus that hold something in it.
        if (narrowed or filtered) and not mine:
            continue
        cards.append(_card(p["id"], p["name"], p["target_date"], mine, today, p["provenance"]))
    loose = by_plateau.get(None, [])
    if loose:
        cards.append(_card(None, "Not on a plateau yet", None, loose, today))

    columns = PRODUCT_COLUMNS if lens == "product" else COLUMNS
    keys = {c[0] for c in columns}
    out_columns = []
    for key, title, icon in columns:
        mine = [c for c in cards if c["status"] == key]
        out_columns.append({"key": key, "title": title, "icon": icon, "cards": mine, "count": len(mine)})
    if lens == "product":
        # Finished plateaus have no place under Now, Next, Later.
        cards = [c for c in cards if c["status"] in keys]

    return {
        "lens": lens,
        "lens_label": LENS_LABELS[lens],
        "lens_help": LENS_HELP[lens],
        "columns": out_columns,
        "card_count": sum(c["count"] for c in out_columns),
        "work_package_count": sum(len(c["items"]) for c in cards),
        "plateau_total": len(slice_dict["plateaus"]),
        "work_package_total": len(slice_dict["work_packages"]),
        "filtered": filtered,
        "spec_hash": slice_dict["spec_hash"],
        "plateaus": [
            {"id": c["id"], "name": c["title"], "work_package_count": len(c["items"])}
            for c in cards
        ],
    }


def _card(plateau_id, title, target_iso, wps, today, provenance=None):
    done = sum(1 for w in wps if w.get("status") == "completed")
    quarter = _quarter(target_iso) or _quarter(
        max((w["target_date"] for w in wps if w.get("target_date")), default=None)
    )
    progress = (
        round(sum(w.get("percent_complete") or 0 for w in wps) / len(wps)) if wps else None
    )
    return {
        "id": plateau_id,
        "title": title,
        "provenance": provenance,
        "quarter": quarter,
        "status": _card_status(wps, target_iso, today),
        "items": [
            {"id": w["id"], "name": w["name"], "status": w.get("status"), "provenance": w["provenance"]}
            for w in wps
        ],
        "done": done,
        "progress": progress,
    }
