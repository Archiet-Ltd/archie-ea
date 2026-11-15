"""The requirements an ADM board shows, from the one requirement store.

``solution_requirements`` is the record behind the requirements backlog. A
requirement already names the solution it serves (``solution_id``) and the TOGAF
phase it belongs to (``togaf_phase``), so the board reads and writes those two
columns and keeps no table of its own.
"""
from typing import Any, Dict, Iterable, List, Optional

from app import db

# Highest priority first; a requirement with no priority sorts last.
_MOSCOW_RANK = {"MUST": 0, "SHOULD": 1, "COULD": 2, "WONT": 3}


def _phase_code(raw: Optional[str]) -> Optional[str]:
    """The board's phase code for a stored value, or None when no phase is set."""
    from app.services.kanban_projection_service import normalize_phase_code

    text = (raw or "").strip()
    return normalize_phase_code(text) if text else None


def _phase_label(code: Optional[str]) -> Optional[str]:
    from app.services.kanban_projection_service import ADM_PHASES

    return next((p["label"] for p in ADM_PHASES if p["code"] == code), None)


def _live_requirements():
    from app.models.solution_architect_models import SolutionRequirement

    return SolutionRequirement.query.filter(SolutionRequirement.deleted_at.is_(None))


def _sort_key(req) -> tuple:
    return (_MOSCOW_RANK.get((req.moscow_priority or "").upper(), 9), req.id)


def requirements_for_solutions(solution_ids: Iterable[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Each solution's requirements, highest priority first, keyed by solution id."""
    from app.models.solution_architect_models import SolutionRequirement

    ids = list(solution_ids)
    result: Dict[int, List[Dict[str, Any]]] = {sid: [] for sid in ids}
    if not ids:
        return result
    rows = sorted(
        _live_requirements().filter(SolutionRequirement.solution_id.in_(ids)).all(),
        key=_sort_key,
    )
    for req in rows:
        result[req.solution_id].append(
            {"id": req.id, "name": req.name, "moscow_priority": req.moscow_priority, "status": req.status}
        )
    return result


def board_requirements() -> List[Dict[str, Any]]:
    """Every requirement of the signed-in organisation, with its phase and solution."""
    from app.models.solution_models import Solution

    reqs = sorted(_live_requirements().all(), key=_sort_key)
    solution_ids = {r.solution_id for r in reqs if r.solution_id}
    names = {}
    if solution_ids:
        names = {s.id: s.name for s in Solution.query.filter(Solution.id.in_(solution_ids)).all()}
    out = []
    for req in reqs:
        code = _phase_code(req.togaf_phase)
        out.append({
            "id": req.id,
            "name": req.name,
            "moscow_priority": req.moscow_priority,
            "status": req.status,
            "phase": code,
            "phase_label": _phase_label(code),
            "solution_id": req.solution_id,
            "solution_name": names.get(req.solution_id),
        })
    return out


def link_requirement(requirement_id: int, solution_id: Optional[int]) -> Dict[str, Any]:
    """Point a requirement at the solution it serves, or at none.

    A requirement with no phase takes the phase of the solution it now serves,
    so the panel never lists a driven solution beside an unset phase. Both
    lookups are queries, so the tenant fence applies; another organisation's id
    reads as not found. Raises ``LookupError`` when either is not found.
    """
    from app.models.solution_models import Solution

    req = _live_requirements().filter_by(id=requirement_id).first()
    if req is None:
        raise LookupError("Requirement not found")
    if solution_id is None:
        req.solution_id = None
    else:
        solution = Solution.query.filter_by(id=solution_id).first()
        if solution is None:
            raise LookupError("Solution not found")
        req.solution_id = solution.id
        if not (req.togaf_phase or "").strip() and solution.adm_phase:
            req.togaf_phase = solution.adm_phase
    db.session.commit()
    return next(r for r in board_requirements() if r["id"] == req.id)
