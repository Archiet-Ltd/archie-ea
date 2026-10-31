"""A plain startup question answered from the aggregate-answer catalogue.

The chat asks this after it has looked for an element the question names and
before it asks the model. The interpreter maps the question onto one catalogue
entry; the entry runs against the active organisation's records. Nothing here
computes an answer: it only decides whether the question is one the catalogue
answers, and turns each citation into a page a person can open.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from flask import url_for

from app.modules.intelligence.services.nl_query_interpreter import asks_for_superlative, interpret
from app.modules.intelligence.services.query_catalogue import CATALOGUE, run_entry

logger = logging.getLogger(__name__)

# What a person can do when the records hold nothing to answer from, by entry.
NEXT_STEPS = {
    "applications_without_owner": "Add your applications to the portfolio, then record who owns each one.",
    "business_continuity_criticality": "Record a business criticality on each application.",
    "overlapping_contracts": "Record contracts against your applications and map them to capabilities.",
    "applications_and_owners": "Add your applications to the portfolio, with an owner and an annual cost for each.",
    "duplicate_tooling_by_capability": "Map each application to the capabilities it covers.",
    "github_sourced_records": "Connect a GitHub repository so its records come into the model.",
    "what_breaks_if_fails": "Name a service that is in your model, or draw how your systems depend on each other.",
}


def _keep(question: str, interpretation: Dict[str, Any]) -> bool:
    """False for a question the interpreter matched only on a broad phrase.

    "What breaks" is matched on short phrases such as "if the"; without a named
    service or a request for single points of failure, that would take over
    ordinary conversation, so those go to the model instead.
    """
    if interpretation.get("entry_id") not in CATALOGUE:
        return False
    if interpretation["entry_id"] != "what_breaks_if_fails":
        return True
    return (
        bool(interpretation.get("params", {}).get("service"))
        or "single point" in question.lower()
        or asks_for_superlative(question)
    )


def _page_for(citation: Dict[str, Any]) -> Optional[str]:
    if citation.get("type") == "page":
        # A page of the company's own website the fact was read from: it opens where it lives.
        target = citation.get("target") or ""
        return target if target.startswith(("http://", "https://")) else None
    ident = citation.get("id")
    if not isinstance(ident, int):
        return None
    if citation.get("type") == "application":
        return url_for("unified_applications.application_detail", id=ident)
    if citation.get("type") == "element":
        return url_for("archimate.element_impact_graph_page", element_id=ident)
    return None


def answer_catalogue_question(message: str, organization_id: int) -> Optional[Dict[str, Any]]:
    """The catalogue's answer to *message* with each citation as a page link, or
    ``None`` when the question is not one the catalogue answers."""
    interpretation = interpret(message)
    if not _keep(message, interpretation):
        return None
    entry_id = interpretation["entry_id"]
    result = run_entry(entry_id, organization_id, **interpretation["params"])
    seen: Set[Tuple[Any, Any]] = set()
    records: List[Dict[str, Any]] = []
    rows_by_id = {row.get("id"): row for row in result.get("rows") or [] if isinstance(row, dict)}
    for citation in result.get("citations") or []:
        page = _page_for(citation)
        key = (citation.get("type"), citation.get("id"))
        if page is None or key in seen:
            continue
        seen.add(key)
        record = {"name": citation.get("name") or "Unnamed record", "kind": citation["type"], "url": page}
        row = rows_by_id.get(citation.get("id")) or {}
        count = row.get("depends_on_count")
        if count:
            record["dependents"] = "%d %s" % (count, "thing depends on it" if count == 1 else "things depend on it")
        if row.get("source_line"):
            record["source_line"] = row["source_line"]
        elif citation.get("source_line"):
            record["source_line"] = citation["source_line"]
        if citation["type"] == "page":
            record["external"] = True
        records.append(record)
    return {
        "entry_id": entry_id,
        "title": result.get("title") or CATALOGUE[entry_id].title,
        "summary": result.get("answer"),
        "total": result.get("total"),
        "reason": result.get("reason"),
        # A finding of "none" (every application is owned) needs no next step.
        "complete": bool(result.get("complete")),
        # A name that matched nothing recorded: the records are not at fault.
        "unmatched": result.get("matched") is False,
        "next_step": (result.get("next_step") or NEXT_STEPS.get(entry_id)) if not records and not result.get("complete") else None,
        "next_url": result.get("next_url") if not records else None,
        "next_label": result.get("next_label") if not records else None,
        "records": records,
    }
