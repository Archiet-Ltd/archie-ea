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
    _resolve_application_owners_batch,
    _resolve_elements_batch,
)

KIND_IMPACT = "impact"
KIND_SET_IMPACT = "set_impact"
KIND_RISK_PROPAGATION = "risk_propagation"
KIND_ELEMENT_PROFILE = "element_profile"
ANSWER_KINDS = (KIND_IMPACT, KIND_SET_IMPACT, KIND_RISK_PROPAGATION, KIND_ELEMENT_PROFILE)

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
    (KIND_ELEMENT_PROFILE, re.compile(r"\beverything (you know )?about\b|\b(full )?profile of\b|\b360 (view|profile)\b", re.I)),
    (KIND_RISK_PROPAGATION, re.compile(r"\b(risks?)\b.*\b(reach|affect|touch|expos|propagat)", re.I)),
    (KIND_SET_IMPACT, re.compile(r"\b(fail|fails|go down|are down|outage)\b.*\btogether\b|\btogether\b.*\b(fail|down)", re.I)),
    (
        KIND_IMPACT,
        re.compile(
            r"what\s+(breaks|stops|fails|is affected|would break|depends)|"
            r"\bblast radius\b|\bimpact (of|if)\b|\bif\b.+\b(fails?|goes down|is retired|is decommissioned)\b|"
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

    if kind == KIND_ELEMENT_PROFILE:
        # Everything connected to one element: one element, no walk depth to choose.
        return {"element_id": _as_int(raw.get("element_id"), "element_id")}

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
    if kind == KIND_IMPACT:
        return [row["relation"]]
    if kind == KIND_SET_IMPACT:
        return list(row["relations"])
    return []


def _row_truth_class(kind: str, row: Dict[str, Any]) -> str:
    """A row is drawn when any drawn relationship reaches it; derived when only
    derived ones do."""
    if kind == KIND_RISK_PROPAGATION:
        return TRUTH_CLASS_DERIVED if row.get("path_kind") == "derived" else TRUTH_CLASS_DRAWN
    if kind == KIND_ELEMENT_PROFILE:
        return TRUTH_CLASS_DERIVED if row.get("basis") == "worked_out" else TRUTH_CLASS_DRAWN
    kinds = {rel.get("kind") for rel in _relations_of(kind, row)}
    return TRUTH_CLASS_DRAWN if "explicit" in kinds else TRUTH_CLASS_DERIVED


def _citations(kind: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cited: Dict[Tuple[str, int], None] = {}

    def cite(source: str, ident: Any) -> None:
        if isinstance(ident, int):
            cited.setdefault((source, ident), None)

    for row in rows:
        if kind == KIND_ELEMENT_PROFILE:
            cite(row["source"]["type"], row["source"]["id"])
            continue
        if kind == KIND_RISK_PROPAGATION:
            cite("risk", row.get("risk_id"))
            for relationship_id in row.get("path_relationships") or []:
                cite("relationship", relationship_id)
            cite("derived_relationship", row.get("path_derived_id"))
            for control in row.get("controls") or []:
                cite("compliance_control", control.get("control_id"))
            continue
        for relation in _relations_of(kind, row):
            for relationship_id in relation.get("chain") or []:
                cite("relationship", relationship_id)
            cite("derived_relationship", relation.get("derived_id"))
    return [{"type": t, "id": i} for (t, i) in sorted(cited)][:MAX_CITATIONS]


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
        if kind == KIND_ELEMENT_PROFILE and original.get("basis") == "worked_out":
            if original.get("confidence") is not None:
                derived_confidences.append(float(original["confidence"]))
            if original.get("as_of"):
                derived_computed.append(_jsonable(original["as_of"]))
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



# --------------------------------------------------------------------------- #
# The profile of one element: everything connected to it, in groups
# --------------------------------------------------------------------------- #

# What a person unlocks from each element type's connections. The groups come
# first, in this order; the connections no group claims follow in "connections".
PROFILE_GROUPS: Dict[str, List[Tuple[str, str]]] = {
    "ApplicationComponent": [
        ("owner", "Owner"),
        ("cost", "Cost"),
        ("hosting_node", "Hosting node"),
        ("end_of_support", "End of support"),
        ("processes", "Processes and value streams served"),
        ("interfaces", "Interfaces"),
        ("risks", "Risks"),
        ("gaps", "Open gaps"),
    ],
    "Capability": [
        ("delivering_applications", "Delivering applications"),
        ("owner", "Owner"),
        ("maturity", "Maturity"),
        ("cost", "Cost"),
        ("gaps", "Gaps"),
    ],
    "WorkPackage": [
        ("gap_closed", "Gap closed"),
        ("plateau", "Plateau"),
        ("applications_touched", "Applications touched"),
        ("owner", "Owner"),
    ],
}
PROFILE_OTHER_GROUP = ("connections", "Other connections")

# (group, side the element is on or None for either, connected element types)
_PROFILE_CLAIMS: Dict[str, List[Tuple[str, Optional[str], frozenset]]] = {
    "ApplicationComponent": [
        ("hosting_node", None, frozenset({"Node", "Device", "SystemSoftware"})),
        ("processes", "out", frozenset({"BusinessProcess", "ValueStream"})),
        ("interfaces", None, frozenset({"ApplicationInterface"})),
        ("gaps", None, frozenset({"Gap"})),
    ],
    "Capability": [
        ("delivering_applications", None, frozenset({"ApplicationComponent"})),
        ("gaps", None, frozenset({"Gap"})),
    ],
    "WorkPackage": [
        ("applications_touched", None, frozenset({"ApplicationComponent"})),
    ],
}
# Shown from the work package's own record instead, so they are not listed twice.
_PROFILE_SHOWN_ELSEWHERE: Dict[str, frozenset] = {"WorkPackage": frozenset({"Gap", "Plateau"})}

# The reason code a group reports when it has nothing recorded, where the
# vocabulary has one.
_PROFILE_ABSENCE: Dict[str, Optional[str]] = {
    "owner": "no_ownership_recorded",
    "cost": "no_cost_recorded",
    "risks": "no_risk_recorded",
    "gaps": "no_gap_recorded",
    "gap_closed": "no_gap_recorded",
    "plateau": "no_plateau_recorded",
    "maturity": "no_maturity_recorded",
    "end_of_support": None,
    "hosting_node": "relationship_not_recorded",
    "processes": "relationship_not_recorded",
    "interfaces": "relationship_not_recorded",
    "delivering_applications": "relationship_not_recorded",
    "applications_touched": "relationship_not_recorded",
    "connections": "relationship_not_recorded",
}

_RELATION_OUT = {
    "Serving": "serves", "Realization": "realises", "Assignment": "is assigned to",
    "Access": "accesses", "Triggering": "triggers", "Flow": "flows to",
    "Association": "is associated with", "Composition": "is composed of",
    "Aggregation": "aggregates", "Specialization": "specialises", "Influence": "influences",
}
_RELATION_IN = {
    "Serving": "is served by", "Realization": "is realised by", "Assignment": "has assigned",
    "Access": "is accessed by", "Triggering": "is triggered by", "Flow": "receives flows from",
    "Association": "is associated with", "Composition": "is part of",
    "Aggregation": "is aggregated by", "Specialization": "is specialised by",
    "Influence": "is influenced by",
}

# Applications whose cost is read for a capability; past it the answer says so.
PROFILE_MAX_COSTED_APPLICATIONS = 25


def _relation_words(relation_type: Optional[str], side: str) -> str:
    table = _RELATION_OUT if side == "out" else _RELATION_IN
    return table.get((relation_type or "").capitalize(), (relation_type or "is connected to").lower())


def _recorded_on(model: Any, ids: List[Any], org_id: int) -> Dict[int, Optional[str]]:
    """The date each record was last written, as ISO text, in one select. The readers
    the profile composes return no timestamps, and every fact carries a date."""
    wanted = sorted({i for i in ids if isinstance(i, int)})
    if not wanted:
        return {}
    updated_col = getattr(model, "updated_at", None)
    created_col = getattr(model, "created_at", None)
    if updated_col is None and created_col is None:
        return {}
    stmt = db.select(
        model.id,
        updated_col if updated_col is not None else created_col,
        created_col if created_col is not None else updated_col,
    ).where(model.id.in_(wanted), model.organization_id == org_id)
    out: Dict[int, Optional[str]] = {}
    for ident, updated, made in db.session.execute(stmt).all():
        stamp = updated or made
        out[ident] = stamp.isoformat() if stamp is not None else None
    return out


def _profile_connections(element_id: int) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """The element's direct connections, outgoing then incoming, from the one
    canonical walk. Recorded connections come from a walk of depth one, so longer
    chains never use up its page; connections worked out from longer chains come from
    a second walk and keep their confidence. Returns the connections, whether the
    recorded walk was cut short, and whether the worked-out walk was."""
    found: List[Dict[str, Any]] = []
    recorded_cut = derived_cut = False
    for direction, side in (("downstream", "out"), ("upstream", "in")):
        for with_derived in (False, True):
            result = IntelligenceQueryService.cross_layer_impact(
                element_id,
                include_derived=with_derived,
                max_depth=3 if with_derived else 1,
                direction=direction,
                with_owner=False,
                page_size=SET_IMPACT_MAX_ROWS,
            )
            if result.get("next_cursor") is not None:
                if with_derived:
                    derived_cut = True
                else:
                    recorded_cut = True
            for row in result.get("rows") or []:
                relation = row["relation"]
                derived = relation.get("kind") == "derived"
                if derived != with_derived:
                    continue
                found.append({
                    "element_id": row["element_id"],
                    "side": side,
                    "relation_type": relation.get("type"),
                    "derived": derived,
                    "relationship_id": (relation.get("chain") or [None])[0],
                    "derived_id": relation.get("derived_id"),
                    "computed_at": relation.get("computed_at"),
                    "confidence": relation.get("confidence"),
                })
    return found, recorded_cut, derived_cut


def _fact(group: str, label: str, name: str, source: Tuple[str, Any], as_of: Optional[str],
          *, value: Any = None, basis: str = "recorded", **extra: Any) -> Dict[str, Any]:
    row = {
        "group": group, "group_label": label, "name": name, "value": value,
        "source": {"type": source[0], "id": source[1]}, "as_of": as_of, "basis": basis,
    }
    row.update(extra)
    return row


def _application_cost_and_support_rows(portfolio: Dict[str, Any], labels: Dict[str, str],
                                       only: Optional[str] = None) -> List[Dict[str, Any]]:
    """The cost and end-of-support facts of a resolved application component, read from
    the portfolio reader's block. Cost is the entered annual figure; nothing is summed."""
    component = portfolio.get("component")
    if not component:
        return []
    source = ("application_component", portfolio["application_component_id"])
    as_of = component.get("updated_at")
    out: List[Dict[str, Any]] = []
    cost = component.get("cost") or {}
    figures = {
        key: cost[key] for key in (
            "total_cost_of_ownership", "license_cost_annual", "maintenance_cost", "infrastructure_cost",
            "support_cost", "implementation_cost", "development_cost_annual",
        ) if cost.get(key) is not None
    }
    if figures:
        out.append(_fact(
            "cost", labels.get("cost", "Cost"), "Annual cost as entered", source, as_of,
            value=figures.get("total_cost_of_ownership"), figures=figures,
        ))
    period = component.get("cost_by_period") or {}
    if period.get("total_cost") is not None:
        out.append(_fact(
            "cost", labels.get("cost", "Cost"), f"Cost for fiscal year {period['fiscal_year']}", source, as_of,
            value=period["total_cost"], budget=period.get("total_budget"),
        ))
    if only != "cost" and component.get("end_of_life_date"):
        out.append(_fact(
            "end_of_support", labels.get("end_of_support", "End of support"), "End of support", source, as_of,
            value=component["end_of_life_date"],
        ))
    return out


def _compute_element_profile(params: Dict[str, Any]) -> Dict[str, Any]:
    """Everything connected to one element, composed from the readers that already
    exist: the canonical walk for connections, the owner reader, the portfolio reader
    for cost and end of support, the risk and programme readers, and the capability
    maturity reader. A group with nothing recorded says so; nothing is invented."""
    from app.models import ArchiMateRelationship
    from app.models.implementation_migration import Gap, Plateau
    from app.models.risk import Risk
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.modules.capabilities.services.capability_heatmap_service import CapabilityHeatmapService

    element_id = params["element_id"]
    org_id = current_org_id()
    subject = _resolve_elements_batch([element_id], org_id)
    if str(element_id) not in subject:
        raise AnswerNotAskable(ELEMENT_NOT_FOUND_REASON)
    element_type = subject[str(element_id)]["type"]
    groups = PROFILE_GROUPS.get(element_type, [])
    labels = dict(groups)
    labels[PROFILE_OTHER_GROUP[0]] = PROFILE_OTHER_GROUP[1]
    claims = _PROFILE_CLAIMS.get(element_type, [])
    elsewhere = _PROFILE_SHOWN_ELSEWHERE.get(element_type, frozenset())
    rows: List[Dict[str, Any]] = []
    reasons: List[str] = []

    connections, truncated, derived_cut = _profile_connections(element_id)
    if truncated or derived_cut:
        reasons.append(ROWS_TRUNCATED_REASON)
    elements = _resolve_elements_batch([c["element_id"] for c in connections] + [element_id], org_id)
    dates = _recorded_on(
        ArchiMateRelationship, [c["relationship_id"] for c in connections if not c["derived"]], org_id
    )
    seen = set()
    for conn in connections:
        other = elements.get(str(conn["element_id"]))
        if other is None or other["type"] in elsewhere:
            continue
        group = PROFILE_OTHER_GROUP[0]
        for claimed, side, types in claims:
            if other["type"] in types and (side is None or side == conn["side"]):
                group = claimed
                break
        key = (group, conn["element_id"], conn["relation_type"], conn["side"], conn["derived"])
        if key in seen:
            continue
        seen.add(key)
        words = _relation_words(conn["relation_type"], conn["side"])
        if conn["derived"]:
            source = ("derived_relationship", conn["derived_id"])
            as_of, basis = _jsonable(conn["computed_at"]), "worked_out"
        else:
            source = ("relationship", conn["relationship_id"])
            as_of, basis = dates.get(conn["relationship_id"]), "recorded"
        rows.append(_fact(
            group, labels[group], other["name"], source, as_of, basis=basis,
            element_id=conn["element_id"], type=other["type"], relation=words,
            relation_type=conn["relation_type"], direction=conn["side"],
            confidence=conn["confidence"] if conn["derived"] else None,
        ))

    if element_type in ("ApplicationComponent", "Capability"):
        owners = _resolve_application_owners_batch([element_id], org_id, subject).get(element_id, [])
        for owner in owners:
            rows.append(_fact(
                "owner", labels["owner"], owner["name"], ("application_owner", owner.get("owner_record_id")),
                owner.get("assigned_at"), value=owner["role"],
            ))

    if element_type == "ApplicationComponent":
        portfolio = IntelligenceQueryService.portfolio_component_for_element(element_id)
        rows.extend(_application_cost_and_support_rows(portfolio, labels))
        risks = IntelligenceQueryService.risk_for_element(element_id, max_depth=1, include_derived=False)
        risk_dates = _recorded_on(Risk, [r["risk_id"] for r in risks.get("risks") or []], org_id)
        for risk in risks.get("risks") or []:
            rows.append(_fact(
                "risks", labels["risks"], risk["title"], ("risk", risk["risk_id"]),
                risk_dates.get(risk["risk_id"]),
                value={"score": risk.get("risk_score"), "level": risk.get("risk_level"),
                       "status": risk.get("status")},
            ))

    if element_type == "Capability":
        block = CapabilityHeatmapService().maturity_for_elements(
            [element_id], organization_id=org_id).get(element_id)
        if block and block.get("current") is not None:
            rows.append(_fact(
                "maturity", labels["maturity"], subject[str(element_id)]["name"],
                ("capability", block.get("capability_id")), block.get("assessed_on"),
                value={"current": block["current"], "target": block.get("target")},
            ))
        delivering: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            if row["group"] != "delivering_applications":
                continue
            kept = delivering.get(row["element_id"])
            if kept is None or (kept["basis"] == "worked_out" and row["basis"] == "recorded"):
                delivering[row["element_id"]] = row
        for row in list(delivering.values())[:PROFILE_MAX_COSTED_APPLICATIONS]:
            portfolio = IntelligenceQueryService.portfolio_component_for_element(row["element_id"])
            for cost in _application_cost_and_support_rows(portfolio, labels, only="cost"):
                cost["name"] = row["name"]
                if row["basis"] == "worked_out":
                    cost["basis"] = "worked_out"
                    cost["reached_by"] = "worked_out_link"
                    cost["confidence"] = row.get("confidence")
                rows.append(cost)
        if len(delivering) > PROFILE_MAX_COSTED_APPLICATIONS and ROWS_TRUNCATED_REASON not in reasons:
            reasons.append(ROWS_TRUNCATED_REASON)

    if element_type == "WorkPackage":
        programme = IntelligenceQueryService.programme_for_element(
            element_id, max_depth=1, include_derived=False)
        own = set(db.session.execute(
            db.select(UnifiedWorkPackage.id).where(
                UnifiedWorkPackage.archimate_element_id == element_id,
                UnifiedWorkPackage.organization_id == org_id,
            )
        ).scalars())
        packages = [p for p in programme.get("work_packages") or [] if p["work_package_id"] in own]
        wp_dates = _recorded_on(UnifiedWorkPackage, [p["work_package_id"] for p in packages], org_id)
        gap_dates = _recorded_on(
            Gap, [(p.get("gap") or {}).get("gap_id") for p in packages], org_id)
        plateau_dates = _recorded_on(
            Plateau, [(p.get("plateau") or {}).get("plateau_id") for p in packages], org_id)
        for package in packages:
            wp_id = package["work_package_id"]
            if package.get("owner"):
                rows.append(_fact(
                    "owner", labels["owner"], package["owner"], ("work_package", wp_id), wp_dates.get(wp_id),
                ))
            gap, plateau = package.get("gap") or {}, package.get("plateau") or {}
            if gap.get("gap_id") is not None:
                rows.append(_fact(
                    "gap_closed", labels["gap_closed"], gap["name"], ("gap", gap["gap_id"]),
                    gap_dates.get(gap["gap_id"]), value=gap.get("resolution_status"),
                ))
            if plateau.get("plateau_id") is not None:
                rows.append(_fact(
                    "plateau", labels["plateau"], plateau["name"], ("plateau", plateau["plateau_id"]),
                    plateau_dates.get(plateau["plateau_id"]), value=plateau.get("target_date"),
                ))

    order = {name: index for index, (name, _label) in enumerate(groups + [PROFILE_OTHER_GROUP])}
    rows.sort(key=lambda r: (order[r["group"]], r["name"] or ""))
    group_status = []
    # Groups read from the recorded walk: when it was cut short, one with no rows is
    # not shown, never "not recorded".
    walk_groups = {claimed for claimed, _side, _types in claims} | {PROFILE_OTHER_GROUP[0]}
    if element_type == "Capability":
        walk_groups.add("cost")
    for name, label in groups + [PROFILE_OTHER_GROUP]:
        count = sum(1 for r in rows if r["group"] == name)
        if name == PROFILE_OTHER_GROUP[0] and not count:
            continue
        cut_short = truncated and name in walk_groups
        group_status.append({
            "group": name, "label": label,
            "status": "recorded" if count else ("not_shown" if cut_short else "not_recorded"),
            "count": count,
            "reason": None if count else (ROWS_TRUNCATED_REASON if cut_short else _PROFILE_ABSENCE.get(name)),
        })
    return {
        "rows": rows,
        "total": len(rows),
        "summary": {"element_type": element_type, "groups": group_status},
        "reasons": reasons,
        "truncated": bool(truncated),
        "elements": elements,
    }


def _compute(kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if kind == KIND_ELEMENT_PROFILE:
        return _compute_element_profile(params)
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
