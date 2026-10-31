"""R1-B30 (TB-0106): the one answer envelope and its store.

Every question the intelligence module answers (what breaks if this fails, what
breaks if these fail together, which risks reach this service) is computed by
``IntelligenceQueryService`` -- the one canonical walk -- and wrapped here in a
single envelope:

    rows, summary, reasons, elements   what the query service returned
    truth_class                        drawn / derived (query_catalogue's
                                       vocabulary), None when there are no rows
    confidence, confidence_basis       lowest derived-row confidence, or the
                                       statement that every row is a drawn fact
    citations                          the records every row rests on
    freshness, computed_at             when it was worked out and how current
                                       the derived facts under it were
    address                            the permanent address it is stored at

The envelope is stored once, as an immutable ``AnswerRecord``, and opened later
by its address as the same snapshot (same rows, same computed time). Nothing
here walks the graph, reads the risk register or resolves an owner itself.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.extensions import db
from app.middleware.tenant_context import current_org_id
from app.modules.intelligence.services.query_catalogue import (
    TRUTH_CLASS_DERIVED,
    TRUTH_CLASS_DRAWN,
)
from app.modules.intelligence.services.query_service import (
    ELEMENT_NOT_FOUND_REASON,
    NO_SEED_ELEMENTS_REASON,
    NO_TENANT_CONTEXT_REASON,
    ROWS_TRUNCATED_REASON,
    SET_IMPACT_MAX_ROWS,
    SET_IMPACT_MAX_SEEDS,
    IntelligenceQueryService,
)

KIND_IMPACT = "impact"
KIND_SET_IMPACT = "set_impact"
KIND_RISK_PROPAGATION = "risk_propagation"
KIND_DATA_FLOW = "data_flow"
ANSWER_KINDS = (KIND_IMPACT, KIND_SET_IMPACT, KIND_RISK_PROPAGATION, KIND_DATA_FLOW)

CONFIDENCE_LOWEST_DERIVED = "lowest_derived_row"
CONFIDENCE_DRAWN_ONLY = "drawn_facts_only"
CONFIDENCE_NO_ROWS = "no_rows"

# Citations kept per answer; the rows themselves are already held to the query
# service's row ceiling.
MAX_CITATIONS = 5000

# The largest request body the ask route reads; far above any legitimate
# question (25 element ids) and well below the application-wide ceiling.
MAX_BODY_BYTES = 1024 * 1024

ADDRESS_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class AnswerParamError(ValueError):
    """The question was malformed (a 400)."""


class AnswerNotAskable(Exception):
    """The question cannot be answered for this caller. ``reason`` is a member
    of the closed reason-code vocabulary; an element that does not exist and
    one that belongs to another organisation are the same case."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #


def _as_bool(raw: Any, name: str, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise AnswerParamError(f"{name} must be true or false")


def _as_int(raw: Any, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise AnswerParamError(f"{name} must be a positive whole number")
    return raw


def as_whole_number(raw: Any, name: str = "value") -> Any:
    """A positive whole number from an int or a string of digits, else *raw*
    unchanged (so ``normalise_params`` refuses it by name). The one coercer for
    ids that arrive as text from a model or a client."""
    if isinstance(raw, str):
        text = raw.strip()
        # ASCII decimal digits only: str.isdigit() accepts superscripts and
        # other digit-like characters that int() then refuses.
        if text and text.isascii() and text.isdecimal():
            try:
                return _as_int(int(text), name)
            except (ValueError, AnswerParamError):
                return raw
    return raw


# Natural-language cues for the three questions the service answers. The
# assistant uses it to tell an estate question (which must be answered from a
# stored answer) from general conversation.
_QUESTION_CUES = (
    (
        KIND_DATA_FLOW,
        re.compile(r"\bdata\s+(flows?|lineage)\b|\bwhere does\b.+\bdata\b.+\b(flow|go)\b|\blineage\b", re.I),
    ),
    (KIND_RISK_PROPAGATION, re.compile(r"\b(risks?)\b.*\b(reach|affect|touch|expos|propagat)", re.I)),
    (KIND_SET_IMPACT, re.compile(r"\b(fail|fails|go down|are down|outage)\b.*\btogether\b|\btogether\b.*\b(fail|down)", re.I)),
    (
        KIND_IMPACT,
        re.compile(
            r"what\s+(breaks|stops|fails|is affected|would break|depends)|"
            r"\bblast radius\b|\bimpact (of|if)\b|"
            r"\bif\b.+\b(fails?|goes down|is retired|is decommissioned|is removed|is replaced|was retired|was decommissioned|was removed|was replaced)\b|"
            r"\bwhat\s+(if|happens if)\b.*\b(retire|decommission|remove|replace)\b|"
            r"\b(depends?|depend) on\b|\bwhat (calls|uses)\b|\bwho owns\b|"
            r"\b(relies|rely|relied) on\b|\bconnected to\b|\b(everything|anything|which|what|list)\b.*\b(uses?|relies|connected)\b",
            re.I,
        ),
    ),
)


def classify_estate_question(message: Any) -> Optional[str]:
    """The answer kind a user message asks, or None for anything else."""
    if not isinstance(message, str) or not message.strip():
        return None
    for kind, pattern in _QUESTION_CUES:
        if pattern.search(message):
            return kind
    return None


def normalise_params(kind: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """The stored, canonical form of a question's parameters.

    Only the keys the kind understands are kept, so the stored params are
    exactly what the answer was computed from.
    """
    if kind not in ANSWER_KINDS:
        raise AnswerParamError(f"question must be one of {list(ANSWER_KINDS)}")
    if not isinstance(raw, dict):
        raise AnswerParamError("the question must be an object")

    max_depth = raw.get("max_depth", 3)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not (1 <= max_depth <= 10):
        raise AnswerParamError("max_depth must be a whole number between 1 and 10")
    include_derived = _as_bool(raw.get("include_derived"), "include_derived", False)

    if kind == KIND_IMPACT:
        return {
            "element_id": _as_int(raw.get("element_id"), "element_id"),
            "max_depth": max_depth,
            "include_derived": include_derived,
            "direction": _direction(raw),
        }

    if kind == KIND_RISK_PROPAGATION:
        return {
            "element_id": _as_int(raw.get("element_id"), "element_id"),
            "max_depth": max_depth,
            "include_derived": include_derived,
        }

    if kind == KIND_DATA_FLOW:
        # The data object asked about; readers under a writer are always worked
        # out, so there is no include_derived switch.
        return {
            "element_id": _as_int(raw.get("element_id"), "element_id"),
            "max_depth": max_depth,
        }

    element_ids_raw = raw.get("element_ids")
    element_ids: List[int] = []
    if element_ids_raw is not None:
        if not isinstance(element_ids_raw, list):
            raise AnswerParamError("element_ids must be a list of element ids")
        seen: set = set()
        for item in element_ids_raw:
            value = _as_int(item, "element_ids")
            if value in seen:
                continue
            seen.add(value)
            element_ids.append(value)
            if len(element_ids) > SET_IMPACT_MAX_SEEDS:
                # Stop reading: an answer covers at most this many elements, so
                # a longer list is refused without being walked to its end.
                raise AnswerParamError(
                    f"name at most {SET_IMPACT_MAX_SEEDS} elements in one question"
                )
    element_filter = raw.get("filter")
    cleaned_filter: Optional[Dict[str, str]] = None
    if element_filter is not None:
        if not isinstance(element_filter, dict):
            raise AnswerParamError("filter must be an object with a type and/or a layer")
        cleaned_filter = {}
        for key in ("type", "layer"):
            value = element_filter.get(key)
            if value is None or value == "":
                continue
            if not isinstance(value, str) or len(value) > 64:
                raise AnswerParamError(f"filter.{key} must be a short piece of text")
            cleaned_filter[key] = value
        if not cleaned_filter:
            raise AnswerParamError("filter needs an element type or a layer")
    if not element_ids and not cleaned_filter:
        raise AnswerParamError("name at least one element, or a filter")
    params: Dict[str, Any] = {
        "element_ids": element_ids,
        "max_depth": max_depth,
        "include_derived": include_derived,
        "direction": _direction(raw),
    }
    if cleaned_filter:
        params["filter"] = cleaned_filter
    return params


def _direction(raw: Dict[str, Any]) -> str:
    direction = raw.get("direction", "downstream")
    if direction not in ("downstream", "upstream", "both"):
        raise AnswerParamError("direction must be downstream, upstream or both")
    return direction


# --------------------------------------------------------------------------- #
# Wrapping
# --------------------------------------------------------------------------- #


def _jsonable(value: Any) -> Any:
    """A value the JSON column can hold: datetimes as ISO text, decimals as
    numbers, tuples as lists, nothing else changed."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def _relations_of(kind: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    if kind in (KIND_IMPACT, KIND_DATA_FLOW):
        return [row["relation"]]
    if kind == KIND_SET_IMPACT:
        return list(row["relations"])
    return []


def _row_truth_class(kind: str, row: Dict[str, Any]) -> str:
    """A row is drawn when any drawn relationship reaches it; derived when only
    derived ones do."""
    if kind == KIND_RISK_PROPAGATION:
        return TRUTH_CLASS_DERIVED if row.get("path_kind") == "derived" else TRUTH_CLASS_DRAWN
    kinds = {rel.get("kind") for rel in _relations_of(kind, row)}
    return TRUTH_CLASS_DRAWN if "explicit" in kinds else TRUTH_CLASS_DERIVED


def _citations(kind: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cited: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}

    origins: Dict[Tuple[str, int], Dict[str, Any]] = {}

    def cite(kind_of: str, ident: Any, connector_source: Optional[Dict[str, Any]] = None) -> None:
        if isinstance(ident, int):
            cited.setdefault((kind_of, ident), connector_source)

    for row in rows:
        if kind == KIND_RISK_PROPAGATION:
            cite("risk", row.get("risk_id"))
            for relationship_id in row.get("path_relationships") or []:
                cite("relationship", relationship_id)
            cite("derived_relationship", row.get("path_derived_id"))
            for control in row.get("controls") or []:
                cite("compliance_control", control.get("control_id"))
            continue
        for relation in _relations_of(kind, row):
            connector_sources = relation.get("origins") or {}
            for relationship_id in relation.get("chain") or []:
                # A link a connector wrote cites the repository and commit it was read at.
                cite("relationship", relationship_id, connector_sources.get(str(relationship_id)))
            cite("derived_relationship", relation.get("derived_id"))
            # A copy rests on the lineage row that records it.
            cite("data_lineage", relation.get("lineage_id"))
            # The relationship a recorded row rests on carries its origin and
            # date, so the citation says where the fact came from.
            origin = relation.get("provenance")
            if relation.get("kind") == "explicit" and isinstance(origin, dict):
                record_id = origin.get("relationship_id")
                if isinstance(record_id, int):
                    origins[("relationship", record_id)] = {
                        "origin": origin.get("text"),
                        "recorded_at": origin.get("recorded_at"),
                        "confidence": relation.get("confidence"),
                    }
    out = []
    for t, i in sorted(cited):
        citation: Dict[str, Any] = {"type": t, "id": i}
        citation.update(origins.get((t, i), {}))
        if cited[(t, i)]:
            # Where a connector read the link (repository, file, commit, read time).
            citation["source"] = cited[(t, i)]
        out.append(citation)
    return out[:MAX_CITATIONS]


def build_envelope(
    kind: str,
    params: Dict[str, Any],
    result: Dict[str, Any],
    *,
    computed_at: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Wrap a query service result in the envelope (no address yet: that is
    issued when it is stored)."""
    computed_at = computed_at or _dt.datetime.utcnow()
    rows = _jsonable(result["rows"])
    for row in rows:
        row["truth_class"] = _row_truth_class(kind, row)

    if not rows:
        truth_class: Optional[str] = None
    elif any(row["truth_class"] == TRUTH_CLASS_DERIVED for row in rows):
        truth_class = TRUTH_CLASS_DERIVED
    else:
        truth_class = TRUTH_CLASS_DRAWN

    derived_confidences: List[float] = []
    derived_computed: List[str] = []
    stale_rows = 0
    for original in result["rows"]:
        for relation in _relations_of(kind, original):
            if relation.get("kind") == "derived":
                if relation.get("confidence") is not None:
                    derived_confidences.append(float(relation["confidence"]))
                if relation.get("computed_at"):
                    derived_computed.append(_jsonable(relation["computed_at"]))
            if relation.get("stale"):
                stale_rows += 1
        if kind == KIND_RISK_PROPAGATION and original.get("path_kind") == "derived":
            if original.get("path_confidence") is not None:
                derived_confidences.append(float(original["path_confidence"]))

    if not rows:
        confidence, basis = None, CONFIDENCE_NO_ROWS
    elif derived_confidences:
        confidence, basis = min(derived_confidences), CONFIDENCE_LOWEST_DERIVED
    else:
        confidence, basis = None, CONFIDENCE_DRAWN_ONLY

    summary = _jsonable(result["summary"])
    freshness = {
        "computed_at": computed_at.isoformat() + "Z",
        "derivation_state": summary.get("derivation_state"),
        "stale_rows": stale_rows,
        "oldest_derived_computed_at": min(derived_computed) if derived_computed else None,
    }
    extra = {
        key: _jsonable(result[key])
        for key in ("seed_element_ids", "unresolved_seed_ids", "root_element_id", "maturity_flags")
        if key in result
    }
    return {
        "question_kind": kind,
        "params": params,
        "truth_class": truth_class,
        "confidence": confidence,
        "confidence_basis": basis,
        "rows": rows,
        "summary": {**summary, "total": result.get("total", len(rows)), **extra},
        "reasons": list(result.get("reasons") or []),
        "elements": _jsonable(result.get("elements") or {}),
        "citations": _citations(kind, result["rows"]),
        "freshness": freshness,
        "truncated": bool(result.get("truncated", False)),
        "computed_at": computed_at,
    }


# --------------------------------------------------------------------------- #
# Asking, storing, opening
# --------------------------------------------------------------------------- #


def _name_the_element_asked_about(result: Dict[str, Any], element_id: int) -> None:
    """Add the asked-about element to the answer's names when no row named it.

    The walk's identity map holds only what its rows name, so an element nothing
    depends on yet would be stored without its own name and the answer would
    read "What breaks if — fails". The name is read through the walk's own
    tenant-fenced resolver.
    """
    from app.modules.intelligence.services.query_service import _resolve_elements_batch

    elements = dict(result.get("elements") or {})
    if str(element_id) in elements:
        return
    org_id = current_org_id()
    if org_id is None:
        return
    elements.update(_resolve_elements_batch([element_id], org_id))
    result["elements"] = elements


def _compute(kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if kind == KIND_DATA_FLOW:
        result = IntelligenceQueryService.data_flow(params["element_id"], max_depth=params["max_depth"])
        if ELEMENT_NOT_FOUND_REASON in result["reasons"]:
            raise AnswerNotAskable(ELEMENT_NOT_FOUND_REASON)
        _name_the_element_asked_about(result, params["element_id"])
        return result
    if kind == KIND_IMPACT:
        result = IntelligenceQueryService.cross_layer_impact(
            params["element_id"],
            include_derived=params["include_derived"],
            max_depth=params["max_depth"],
            direction=params["direction"],
            with_owner=True,
            page_size=SET_IMPACT_MAX_ROWS,
        )
        if ELEMENT_NOT_FOUND_REASON in result.get("reasons", []):
            raise AnswerNotAskable(ELEMENT_NOT_FOUND_REASON)
        _name_the_element_asked_about(result, params["element_id"])
        if result.get("next_cursor") is not None:
            result["truncated"] = True
            result["reasons"] = list(result.get("reasons") or []) + [ROWS_TRUNCATED_REASON]
        return result
    if kind == KIND_SET_IMPACT:
        result = IntelligenceQueryService.set_impact(
            params["element_ids"],
            element_filter=params.get("filter"),
            include_derived=params["include_derived"],
            max_depth=params["max_depth"],
            direction=params["direction"],
        )
        if NO_SEED_ELEMENTS_REASON in result["reasons"]:
            raise AnswerNotAskable(NO_SEED_ELEMENTS_REASON)
        if not result["summary"]["resolved_seed_count"]:
            raise AnswerNotAskable(ELEMENT_NOT_FOUND_REASON)
        return result
    result = IntelligenceQueryService.risk_propagation(
        params["element_id"],
        max_depth=params["max_depth"],
        include_derived=params["include_derived"],
    )
    if ELEMENT_NOT_FOUND_REASON in result["reasons"]:
        raise AnswerNotAskable(ELEMENT_NOT_FOUND_REASON)
    return result


def ask_and_store(kind: str, raw_params: Dict[str, Any], *, user_id: Optional[int] = None):
    """Compute the answer for the caller's active organisation, wrap it and
    store it at a new permanent address. Returns the stored ``AnswerRecord``.

    The organisation is the active one (``current_org_id``); with none the
    question is refused and nothing is written.
    """
    from app.models.answer_record import AnswerRecord

    org_id = current_org_id()
    if org_id is None:
        raise AnswerNotAskable(NO_TENANT_CONTEXT_REASON)
    params = normalise_params(kind, raw_params)
    envelope = build_envelope(kind, params, _compute(kind, params))
    record = AnswerRecord(
        organization_id=org_id,
        address=uuid.uuid4().hex,
        question_kind=kind,
        params=params,
        truth_class=envelope["truth_class"],
        confidence=envelope["confidence"],
        confidence_basis=envelope["confidence_basis"],
        rows=envelope["rows"],
        summary=envelope["summary"],
        reasons=envelope["reasons"],
        elements=envelope["elements"],
        citations=envelope["citations"],
        freshness=envelope["freshness"],
        truncated=envelope["truncated"],
        computed_at=envelope["computed_at"],
        created_by_id=user_id,
    )
    db.session.add(record)
    db.session.commit()
    return record


def open_answer(address: str):
    """The stored answer at *address* for the caller's active organisation, or
    ``None``. An address issued to another organisation, a malformed one and
    one never issued are the same ``None``."""
    from app.models.answer_record import AnswerRecord

    org_id = current_org_id()
    if org_id is None or not isinstance(address, str) or not ADDRESS_PATTERN.match(address):
        return None
    return db.session.execute(
        db.select(AnswerRecord).where(
            AnswerRecord.address == address,
            AnswerRecord.organization_id == org_id,
        )
    ).scalar_one_or_none()


def recent_answers(limit: int = 50) -> List[Any]:
    """The caller's organisation's most recent answers, newest first (an empty
    list with no active organisation)."""
    from app.models.answer_record import AnswerRecord

    org_id = current_org_id()
    if org_id is None:
        return []
    return list(
        db.session.execute(
            db.select(AnswerRecord)
            .where(AnswerRecord.organization_id == org_id)
            .order_by(AnswerRecord.created_at.desc(), AnswerRecord.id.desc())
            .limit(limit)
        ).scalars()
    )


def present(record, *, include_rows: bool = True) -> Dict[str, Any]:
    """The envelope as stored, with its address, for the API and the page."""
    body: Dict[str, Any] = {
        "address": record.address,
        "page_url": f"/intelligence/answers/{record.address}",
        "api_url": f"/api/v1/intelligence/answers/{record.address}",
        "question_kind": record.question_kind,
        "params": record.params,
        "truth_class": record.truth_class,
        "confidence": record.confidence,
        "confidence_basis": record.confidence_basis,
        "confidence_percent": (
            int(round(record.confidence * 100)) if record.confidence is not None else None
        ),
        "summary": record.summary,
        "reasons": record.reasons,
        "freshness": record.freshness,
        "truncated": record.truncated,
        "computed_at": record.computed_at.isoformat() + "Z",
    }
    if include_rows:
        body["rows"] = record.rows
        body["elements"] = record.elements
        body["citations"] = record.citations
    return body
