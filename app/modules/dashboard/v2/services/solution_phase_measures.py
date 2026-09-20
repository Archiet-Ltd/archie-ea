"""Where the portfolio's solutions sit in the ADM cycle, read once.

Two maturity figures are derived from the same recorded phases, and they are
different measures on purpose:

* Phase maturity (a component of the Health Score): the share of solutions with
  a recorded phase that have reached phase C or later.
* Average solution maturity (Health Scorecard): the mean ADM phase progress of
  those solutions, phase D counting as 50% and phase H as 100%.

Both read the same solutions - every solution in the caller's organisation that
is not soft-deleted - so a reader can recompute either figure from the phase
distribution on the Health Scorecard. A solution with no recorded phase, or
one outside A-H, belongs to neither measure: it is counted as unclassified
rather than being read as phase A.
"""

from collections import Counter

from app import db

ADM_PHASES = "ABCDEFGH"

# Phases from which a solution counts as past the early design work.
ADVANCED_PHASES = "CDEFGH"

# Progress a solution is credited with at each ADM phase.
ADM_PHASE_PROGRESS_PCT = {
    "A": 12, "B": 25, "C": 37, "D": 50, "E": 62, "F": 75, "G": 87, "H": 100,
}


def summarise_phase_counts(rows):
    """Classify ``(raw adm_phase, number of solutions)`` pairs by normalised phase.

    Returns ``{"counts": {"A": n, ...}, "unclassified": n, "total": n}``.
    ``total`` includes the unclassified solutions; the counts do not. Values are
    normalised here, once, so imported spellings such as ``" c "`` land on phase C.
    """
    counts = {phase: 0 for phase in ADM_PHASES}
    unclassified = 0
    total = 0
    for raw, number in rows:
        number = int(number or 0)
        total += number
        phase = (raw or "").strip().upper()
        if phase in counts:
            counts[phase] += number
        else:
            unclassified += number
    return {"counts": counts, "unclassified": unclassified, "total": total}


def summarise_phases(phases):
    """Count raw ``adm_phase`` values by normalised phase (one value per solution)."""
    return summarise_phase_counts(Counter(phases).items())


def recorded_phase_summary():
    """Phase summary for the current organisation's live solutions.

    One grouped count, however many solutions there are. Reads through the
    tenant-scoped session, and leaves out solutions whose name carries the
    soft-delete prefix, as the solutions list does.
    """
    from app.models.solution_models import Solution

    rows = (
        db.session.query(Solution.adm_phase, db.func.count(Solution.id))
        .filter(~Solution.name.like("[DELETED]%"))
        .group_by(Solution.adm_phase)
        .all()
    )
    return summarise_phase_counts(rows)


def share_in_advanced_phases(summary):
    """Phase maturity: percentage (one decimal) of phased solutions in phase C or later.

    ``None`` when no solution has a recorded phase.
    """
    counts = summary["counts"]
    measured = sum(counts.values())
    if measured == 0:
        return None
    advanced = sum(counts[phase] for phase in ADVANCED_PHASES)
    return round((advanced / measured) * 100, 1)


def average_phase_progress(summary):
    """Average solution maturity: mean ADM phase progress, whole percent.

    ``None`` when no solution has a recorded phase.
    """
    counts = summary["counts"]
    measured = sum(counts.values())
    if measured == 0:
        return None
    progress = sum(ADM_PHASE_PROGRESS_PCT[phase] * n for phase, n in counts.items())
    return round(progress / measured)
