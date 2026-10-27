"""The strategy slice of the genome: the competitor analysis, SWOT and PESTLE a person accepted, with their sources.

The AI chat and the assistant connector read it through ``get_slice("strategy", org_id)`` like every other
domain. Only accepted statements are read; a draft nobody accepted is not part of the model. Each competitor is
one record holding the five answers of the comparison matrix, and each SWOT or PESTLE statement is one record,
every one carrying the sources it rests on (title, address, publisher, date read, how sure).

Pure and deterministic over the organisation's records: no model, no clock, no network.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from app.services import strategy_draft_service as engine

ROW_KEYS = {"strength": "strengths", "weakness": "weaknesses", "difference": "differences"}


def _spec_hash(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _numbered(statements: List[Dict[str, Any]]):
    """(statements with local source numbers, the sources those numbers name)."""
    sources: List[Dict[str, Any]] = []
    index: Dict[str, int] = {}
    out = []
    for s in statements:
        nums = []
        for src in s.get("sources") or []:
            if src.get("url") not in index:
                index[src.get("url")] = len(sources) + 1
                sources.append({"n": len(sources) + 1, "title": src.get("title"), "url": src.get("url"),
                                "publisher": src.get("publisher"), "read_on": src.get("read_on"),
                                "percent": src.get("percent")})
            nums.append(index[src.get("url")])
        out.append({"text": s["text"], "sources": sorted(set(nums)), "percent": s.get("percent")})
    return out, sources


def _canvas_ids(org_id: int) -> Dict[str, int]:
    """The organisation's SWOT and PESTLE canvases, where an accepted statement can be seen in its box."""
    from app.models.business_model import BusinessModelCanvas

    out: Dict[str, int] = {}
    for analysis in ("swot", "pestle"):
        canvas = BusinessModelCanvas.query.filter_by(
            organization_id=org_id, canvas_type=engine.ANALYSES[analysis]["canvas"]).order_by(BusinessModelCanvas.id).first()
        if canvas is not None:
            out[analysis] = canvas.id
    return out


def build_strategy_slice(org_id: int) -> Dict[str, Any]:
    accepted = engine.accepted_statements(org_id, "competitors")
    by_actor: Dict[Any, Dict[str, Any]] = {}
    for s in reversed(accepted):  # oldest first, so a list reads in the order it was drafted
        key = s["competitor_element_id"] or s["group"]
        col = by_actor.setdefault(key, {"name": s["group"], "element_id": s["competitor_element_id"], "rows": {}})
        col["rows"].setdefault(s["row"], []).append(s)
    competitors = []
    for col in by_actor.values():
        rows = col["rows"]
        flat = [s for row in engine.COMPETITOR_ROWS for s in rows.get(row[0], [])]
        numbered, sources = _numbered(flat)
        mapping = {id(src): num for src, num in zip(flat, numbered)}
        pick = lambda row: [mapping[id(s)] for s in rows.get(row, [])]  # noqa: E731
        competitors.append({
            "element_id": col["element_id"], "name": col["name"],
            "position": (pick("position") or [None])[0], "threat": (pick("threat") or [None])[0],
            "strengths": pick("strength"), "weaknesses": pick("weakness"), "differences": pick("difference"),
            "sources": sources})
    competitors.sort(key=lambda c: (engine.THREAT_ORDER.get((c["threat"] or {}).get("text"), 3), c["name"].lower()))
    frameworks = []
    for analysis in ("swot", "pestle"):
        for s in reversed(engine.accepted_statements(org_id, analysis)):
            numbered, sources = _numbered([s])
            frameworks.append({"element_id": s["element_id"], "analysis": analysis, "box": s["group"],
                               "statement": numbered[0], "sources": sources})
    return {"competitors": competitors, "frameworks": frameworks, "canvases": _canvas_ids(org_id),
            "spec_hash": _spec_hash({"c": competitors, "f": frameworks})}
