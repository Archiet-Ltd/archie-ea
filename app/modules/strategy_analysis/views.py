"""What the Strategy screens show: the competitor matrix and the SWOT and PESTLE drafts, read from the engine.

Every statement carries numbered marks; the marks resolve to one list of sources under the screen, the
footnote form of a consulting report. A statement with no source never reaches here (the engine removes it).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import strategy_draft_service as engine

NOTHING_FOUND = "Nothing found in public sources"
THREAT_VARIANT = {"High": "destructive", "Medium": "secondary", "Low": "outline"}


class _Numbering:
    """Display numbers for sources, in the order a reader meets them, one number per address."""

    def __init__(self):
        self.by_url: Dict[str, int] = {}
        self.sources: List[Dict[str, Any]] = []

    def marks(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for s in sources:
            url = s.get("url")
            if url not in self.by_url:
                self.by_url[url] = len(self.sources) + 1
                self.sources.append({**s, "n": self.by_url[url]})
            out.append({"n": self.by_url[url], "title": s.get("title"), "url": url})
        return sorted(out, key=lambda m: m["n"])


def job_state(org_id: int) -> Dict[str, Any]:
    """Where the latest drafting job is: idle, drafting, or failed with a sentence."""
    from app.models.job import JobStatus
    from app.services.job_queue_service import get_job_queue_service

    jobs = get_job_queue_service().list_jobs(org_id, limit=1, task=engine.STRATEGY_TASK)
    if not jobs:
        return {"state": "idle"}
    job = jobs[0]
    result = job.result or {}
    if job.status in (JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value):
        return {"state": "drafting", "text": "The drafts are being worked out from your profile and public sources. "
                                              "This takes a minute."}
    if job.status in (JobStatus.FAILED.value, JobStatus.CANCELLED.value) or result.get("success") is False:
        return {"state": "failed", "text": "The drafts could not be written this time and nothing was changed. "
                                           "Run the analysis again."}
    return {"state": "idle"}


def _run_line(org) -> Optional[str]:
    run = (org.settings or {}).get("strategy_drafts") or {}
    compared = run.get("compared") or []
    try:
        when = datetime.fromisoformat(run["run_at"]).strftime("%d %b %Y")
    except (KeyError, ValueError):
        return None
    if not compared:
        return "Drafted on %s from your profile and public sources." % when
    names = compared[0] if len(compared) == 1 else ", ".join(compared[:-1]) + " and " + compared[-1]
    return "Compared with %s on %s." % (names, when)


def _failed_analyses(org) -> List[str]:
    run = (org.settings or {}).get("strategy_drafts") or {}
    return [engine.ANALYSES[k]["title"] for k, v in (run.get("analyses") or {}).items()
            if v.get("state") == "failed" and k in engine.ANALYSES]


def competitor_matrix(org) -> Dict[str, Any]:
    """The comparison matrix: one column per competitor, highest threat first, the five questions as rows.

    While drafts wait for a person the matrix shows the drafts; once accepted it shows the accepted statements."""
    drafts = engine.pending_statements(org.id, "competitors")
    mode = "draft" if drafts else "confirmed"
    statements = drafts if drafts else engine.accepted_statements(org.id, "competitors")
    numbering = _Numbering()
    by_competitor: Dict[str, Dict[str, Any]] = {}
    for s in sorted(statements, key=lambda s: (s.get("id") or -(s.get("element_id") or 0))):
        col = by_competitor.setdefault(
            s["group"], {"name": s["group"], "rows": {}, "element_id": s.get("competitor_element_id")})
        col["rows"].setdefault(s["row"], []).append(s)
    columns = []
    for name, col in by_competitor.items():
        threat = (col["rows"].get("threat") or [{}])[0].get("text")
        columns.append({"name": name, "element_id": col["element_id"], "threat": threat,
                        "variant": THREAT_VARIANT.get(threat, "outline"), "rows": col["rows"]})
    columns.sort(key=lambda c: (engine.THREAT_ORDER.get(c["threat"], 3), c["name"].lower()))
    # numbers follow reading order: column by column, row by row
    for col in columns:
        cells = {}
        for key, _label in engine.COMPETITOR_ROWS:
            items = col["rows"].get(key, [])[:(1 if key in ("position", "threat") else engine.MAX_PER_LIST)]
            cells[key] = [{"text": i["text"], "percent": i["percent"], "marks": numbering.marks(i["sources"])}
                          for i in items]
        col["cells"] = cells
        col["anchor"] = "competitor-%s" % (col["element_id"] or "".join(ch for ch in col["name"].lower() if ch.isalnum()))
    confirmed = None
    if mode == "confirmed" and statements:
        confirmed = {"by": next((s["confirmed_by_name"] for s in statements if s.get("confirmed_by_name")), None),
                     "at": None}
        stamp = max((s["confirmed_at"] for s in statements if s.get("confirmed_at")), default=None)
        if stamp:
            confirmed["at"] = datetime.fromisoformat(stamp).strftime("%d %b %Y")
    return {
        "mode": mode, "columns": columns, "rows": engine.COMPETITOR_ROWS, "sources": numbering.sources,
        "empty": not columns, "confirmed": confirmed, "run_line": _run_line(org), "failed": _failed_analyses(org),
        "job": job_state(org.id), "can_run": engine.profile_confirmed(org),
        "none_named": not engine.competitors_named(org), "nothing_found": NOTHING_FOUND,
    }


def _canvas_link(org_id: int, key: str) -> Optional[Dict[str, Any]]:
    """The organisation's canvas for this framework, or None before anything is accepted."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES
    from app.models.business_model import BusinessModelCanvas

    template = engine.ANALYSES[key]["canvas"]
    canvas = BusinessModelCanvas.query.filter_by(organization_id=org_id, canvas_type=template).order_by(
        BusinessModelCanvas.id).first()
    if canvas is None:
        return None
    return {"id": canvas.id, "name": CANVAS_TEMPLATES[template]["name"]}


def framework_drafts(org) -> Dict[str, Any]:
    """The SWOT and PESTLE drafts waiting for a person, by box in each framework's own order, with the marks that
    resolve to one list of sources; and where the accepted ones now live."""
    groups = {g["key"]: g for g in engine.grouped_drafts(org.id)}
    numbering = _Numbering()
    cards = []
    for key in ("swot", "pestle"):
        boxes = []
        for sub in groups[key]["subgroups"]:
            boxes.append({"key": sub["key"], "label": sub["label"], "rows": [
                {"id": r["id"], "text": r["text"], "percent": r["percent"], "marks": numbering.marks(r["sources"])}
                for r in sub["rows"]]})
        cards.append({"key": key, "title": engine.ANALYSES[key]["title"], "count": groups[key]["count"],
                      "boxes": boxes, "canvas": _canvas_link(org.id, key)})
    return {"cards": cards, "sources": numbering.sources, "pending": sum(c["count"] for c in cards),
            "competitor_pending": groups["competitors"]["count"], "run_line": _run_line(org),
            "failed": _failed_analyses(org), "job": job_state(org.id), "can_run": engine.profile_confirmed(org)}
