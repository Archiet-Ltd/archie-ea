"""DMN decision tables: the one writer and reader.

A decision is an ArchiMate element (the existing element store): a business function whose
``custom_properties["dmn"]`` holds the hit policy and the input and output columns. Its numbered
rule rows are ``DecisionRule`` rows. A decision is linked to the business process that applies it by
a ``serving`` relationship from the decision to the process, written by
``ArchiMateRelationshipService``, so the model graph and the impact pages see it like any other link.

Entries are written in FEEL unary tests, the way DMN modelers write them: ``-`` (anything), ``"gold"``,
``42``, ``<1000``, ``>=5``, ``[1..10]``, ``(1..10)``, ``"a","b"`` and ``not("x")``. ``evaluate`` runs a
test input against the table under the hit policy and says which rules matched.

Every function flushes and never commits; the route commits. Every read names the organisation.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import cast, String

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.decision_rule import DecisionRule

DECISION_TYPE = "BusinessFunction"
DECISION_LAYER = "business"
LINK_TYPE = "serving"

# letter -> (DMN hitPolicy attribute, name, one-line meaning)
HIT_POLICIES: Dict[str, Tuple[str, str, str]] = {
    "U": ("UNIQUE", "Unique", "Only one rule may match; two matching rules is an error."),
    "F": ("FIRST", "First", "The first matching rule, in rule order, gives the answer."),
    "A": ("ANY", "Any", "Several rules may match, but they must all give the same answer."),
    "C": ("COLLECT", "Collect", "Every matching rule's answer is collected into a list."),
    "R": ("RULE ORDER", "Rule order", "Every matching rule's answer, listed in rule order."),
}
TYPE_REFS = ("string", "number", "boolean")
MAX_RULES = 500


class DecisionError(ValueError):
    """A request the decision table refuses; the message is plain enough to show."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def hit_policy_choices() -> List[Dict[str, str]]:
    return [{"letter": k, "name": v[1], "meaning": v[2]} for k, v in HIT_POLICIES.items()]


def hit_policy_for_dmn(value: Optional[str]) -> Optional[str]:
    """The letter for a DMN ``hitPolicy`` attribute; ``None`` for one not supported."""
    wanted = (value or "UNIQUE").strip().upper()
    for letter, (dmn, _name, _meaning) in HIT_POLICIES.items():
        if dmn == wanted:
            return letter
    return None


# --- FEEL unary tests ------------------------------------------------------------------------

_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_COMPARE = re.compile(r"^(<=|>=|<|>|!=|=)\s*(.+)$")
_RANGE = re.compile(r"^([\[\(\]])\s*(.+?)\s*\.\.\s*(.+?)\s*([\]\)\[])$")


def _split_tests(text: str) -> List[str]:
    """Split on commas that sit outside quotes, brackets and parentheses."""
    parts: List[str] = []
    depth = 0
    quote = False
    current = ""
    for ch in text:
        if ch == '"':
            quote = not quote
        elif not quote:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth <= 0:
                parts.append(current.strip())
                current = ""
                continue
        current += ch
    parts.append(current.strip())
    return [p for p in parts if p != ""]


def _literal(token: str, type_ref: str) -> Any:
    token = token.strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    if token in ("true", "false"):
        return token == "true"
    if _NUMBER.match(token):
        return Decimal(token)
    if type_ref == "number":
        raise DecisionError('"%s" is not a number.' % token)
    return token


def _coerce_input(raw: Any, type_ref: str) -> Any:
    if isinstance(raw, bool):
        return raw
    text = "" if raw is None else str(raw).strip()
    if type_ref == "number":
        try:
            return Decimal(text)
        except InvalidOperation:
            raise DecisionError('"%s" is not a number.' % text)
    if type_ref == "boolean":
        if text.lower() in ("true", "yes", "1"):
            return True
        if text.lower() in ("false", "no", "0"):
            return False
        raise DecisionError('"%s" is not true or false.' % text)
    return text


def _match_one(test: str, value: Any, type_ref: str) -> bool:
    test = test.strip()
    if test in ("-", ""):
        return True
    cmp = _COMPARE.match(test)
    if cmp:
        op, rest = cmp.group(1), _literal(cmp.group(2), type_ref)
        try:
            return {
                "<": lambda: value < rest, ">": lambda: value > rest, "<=": lambda: value <= rest,
                ">=": lambda: value >= rest, "!=": lambda: value != rest, "=": lambda: value == rest,
            }[op]()
        except TypeError:
            raise DecisionError('"%s" cannot compare with "%s".' % (value, rest))
    rng = _RANGE.match(test)
    if rng:
        lo, hi = _literal(rng.group(2), type_ref), _literal(rng.group(3), type_ref)
        low_ok = value >= lo if rng.group(1) == "[" else value > lo
        high_ok = value <= hi if rng.group(4) == "]" else value < hi
        return low_ok and high_ok
    return value == _literal(test, type_ref)


def entry_matches(entry: str, raw_value: Any, type_ref: str = "string") -> bool:
    """Whether a unary-test entry accepts the value. Raises ``DecisionError`` for text it cannot read."""
    value = _coerce_input(raw_value, type_ref)
    text = (entry or "").strip()
    if text in ("", "-"):
        return True
    negate = False
    inner = re.match(r"^not\s*\((.*)\)$", text, re.S)
    if inner:
        negate, text = True, inner.group(1)
    tests = _split_tests(text)
    if not tests:
        return True
    hit = any(_match_one(t, value, type_ref) for t in tests)
    return (not hit) if negate else hit


def check_entry(entry: str, type_ref: str = "string") -> Optional[str]:
    """``None`` when the entry is readable as a unary test of this type, else why not."""
    sample = {"number": "0", "boolean": "true"}.get(type_ref, "x")
    try:
        entry_matches(entry, sample, type_ref)
    except DecisionError as exc:
        return str(exc)
    return None


def display_value(entry: Any) -> str:
    """An output entry as a person reads it: a quoted string loses its quotes."""
    text = "" if entry is None else str(entry).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


# --- reads -----------------------------------------------------------------------------------

def current_org_id() -> int:
    from app.services.archimate_import_service import ArchiMateImportService

    return ArchiMateImportService._require_organization_id(None)


def _dmn(element: ArchiMateElement) -> Dict[str, Any]:
    return dict((element.custom_properties or {}).get("dmn") or {})


def is_decision(element: Optional[ArchiMateElement]) -> bool:
    return bool(element is not None and element.type == DECISION_TYPE and _dmn(element))


def _decision_filter(org_id: int):
    return [
        ArchiMateElement.organization_id == org_id,
        ArchiMateElement.type == DECISION_TYPE,
        cast(ArchiMateElement.custom_properties["dmn"], String).notin_(["null"]),
        ArchiMateElement.custom_properties["dmn"].isnot(None),
    ]


def list_decisions(org_id: Optional[int] = None) -> List[ArchiMateElement]:
    org_id = org_id or current_org_id()
    rows = db.session.execute(
        db.select(ArchiMateElement).where(*_decision_filter(org_id)).order_by(ArchiMateElement.name)
    ).scalars().all()
    return [r for r in rows if is_decision(r)]


def get_decision(decision_id: int, org_id: Optional[int] = None) -> ArchiMateElement:
    org_id = org_id or current_org_id()
    element = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id == decision_id, ArchiMateElement.organization_id == org_id
        )
    ).scalar_one_or_none()
    if not is_decision(element):
        raise DecisionError("That decision was not found.", 404)
    return element


def find_decision_by_name(name: str, org_id: int) -> Optional[ArchiMateElement]:
    for row in list_decisions(org_id):
        if (row.name or "").strip().lower() == name.strip().lower():
            return row
    return None


def definition(element: ArchiMateElement) -> Dict[str, Any]:
    d = _dmn(element)
    return {
        "hit_policy": d.get("hit_policy", "U"),
        "inputs": list(d.get("inputs") or []),
        "outputs": list(d.get("outputs") or []),
    }


def rules_of(element: ArchiMateElement) -> List[DecisionRule]:
    return db.session.execute(
        db.select(DecisionRule)
        .where(DecisionRule.element_id == element.id, DecisionRule.organization_id == element.organization_id)
        .order_by(DecisionRule.position)
    ).scalars().all()


def rule_counts(org_id: int) -> Dict[int, int]:
    rows = db.session.execute(
        db.select(DecisionRule.element_id, db.func.count(DecisionRule.id))
        .where(DecisionRule.organization_id == org_id)
        .group_by(DecisionRule.element_id)
    ).all()
    return {eid: n for eid, n in rows}


# --- writes ----------------------------------------------------------------------------------

def _clean_name(name: Any) -> str:
    text = (name or "").strip()
    if not text:
        raise DecisionError("Give the decision a name.")
    if len(text) > 200:
        raise DecisionError("The name can be at most 200 characters.")
    return text


def _clean_column(column: Dict[str, Any], kind: str) -> Dict[str, str]:
    label = (column.get("label") or "").strip()
    if not label:
        raise DecisionError("Give the %s column a name." % kind)
    if len(label) > 120:
        raise DecisionError("A column name can be at most 120 characters.")
    type_ref = (column.get("type") or "string").strip().lower()
    if type_ref not in TYPE_REFS:
        raise DecisionError("A column type is text, number or true/false.")
    return {"label": label, "type": type_ref}


def _store_definition(element: ArchiMateElement, hit_policy: str, inputs, outputs) -> None:
    props = dict(element.custom_properties or {})
    props["dmn"] = {"hit_policy": hit_policy, "inputs": inputs, "outputs": outputs}
    element.custom_properties = props  # a new dict, so the JSON column registers the change
    db.session.flush()


def create_decision(
    name: Any,
    hit_policy: str = "U",
    inputs: Optional[List[Dict[str, Any]]] = None,
    outputs: Optional[List[Dict[str, Any]]] = None,
    description: Optional[str] = None,
    org_id: Optional[int] = None,
    architecture_id: Optional[int] = None,
    allow_duplicate_name: bool = False,
) -> ArchiMateElement:
    org_id = org_id or current_org_id()
    name = _clean_name(name)
    if hit_policy not in HIT_POLICIES:
        raise DecisionError("Choose a hit policy: %s." % ", ".join(HIT_POLICIES))
    if not allow_duplicate_name and find_decision_by_name(name, org_id) is not None:
        raise DecisionError("A decision called %s already exists." % name, 409)
    element = ArchiMateElement(
        name=name, type=DECISION_TYPE, layer=DECISION_LAYER,
        description=(description or "").strip() or None,
        architecture_id=architecture_id, organization_id=org_id,
    )
    db.session.add(element)
    db.session.flush()
    _store_definition(
        element, hit_policy,
        [_clean_column(c, "input") for c in (inputs or [])],
        [_clean_column(c, "output") for c in (outputs or [])],
    )
    return element


def update_decision(
    element: ArchiMateElement, name: Any = None, hit_policy: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    d = definition(element)
    if name is not None:
        new_name = _clean_name(name)
        other = find_decision_by_name(new_name, element.organization_id)
        if other is not None and other.id != element.id:
            raise DecisionError("A decision called %s already exists." % new_name, 409)
        element.name = new_name
    if description is not None:
        element.description = description.strip() or None
    if hit_policy is not None:
        if hit_policy not in HIT_POLICIES:
            raise DecisionError("Choose a hit policy: %s." % ", ".join(HIT_POLICIES))
        d["hit_policy"] = hit_policy
    _store_definition(element, d["hit_policy"], d["inputs"], d["outputs"])


def add_column(element: ArchiMateElement, kind: str, label: Any, type_ref: str = "string") -> None:
    if kind not in ("input", "output"):
        raise DecisionError("A column is an input or an output.")
    d = definition(element)
    column = _clean_column({"label": label, "type": type_ref}, kind)
    key = kind + "s"
    d[key].append(column)
    for rule in rules_of(element):
        entries = list(rule.input_entries if kind == "input" else rule.output_entries)
        entries.append("-" if kind == "input" else "")
        if kind == "input":
            rule.input_entries = entries
        else:
            rule.output_entries = entries
    _store_definition(element, d["hit_policy"], d["inputs"], d["outputs"])


def remove_column(element: ArchiMateElement, kind: str, index: int) -> None:
    if kind not in ("input", "output"):
        raise DecisionError("A column is an input or an output.")
    d = definition(element)
    key = kind + "s"
    if not 0 <= index < len(d[key]):
        raise DecisionError("That column was not found.", 404)
    del d[key][index]
    for rule in rules_of(element):
        entries = list(rule.input_entries if kind == "input" else rule.output_entries)
        if index < len(entries):
            del entries[index]
        if kind == "input":
            rule.input_entries = entries
        else:
            rule.output_entries = entries
    _store_definition(element, d["hit_policy"], d["inputs"], d["outputs"])


def _clean_entries(values: Any, columns: List[Dict[str, str]], kind: str) -> List[str]:
    values = list(values or [])
    if len(values) != len(columns):
        raise DecisionError("Give one entry for each %s column." % kind)
    out = []
    for value, column in zip(values, columns):
        text = ("" if value is None else str(value)).strip()
        if kind == "input":
            text = text or "-"
            problem = check_entry(text, column["type"])
            if problem:
                raise DecisionError("%s: %s" % (column["label"], problem))
        out.append(text)
    return out


def save_rule(
    element: ArchiMateElement, input_entries, output_entries, annotation: Optional[str] = None,
    position: Optional[int] = None,
) -> DecisionRule:
    """Add a rule at the end, or replace the entries of the rule at ``position``."""
    d = definition(element)
    if not d["inputs"] or not d["outputs"]:
        raise DecisionError("Add at least one input and one output column before adding rules.")
    inputs = _clean_entries(input_entries, d["inputs"], "input")
    outputs = _clean_entries(output_entries, d["outputs"], "output")
    rules = rules_of(element)
    if position is None:
        if len(rules) >= MAX_RULES:
            raise DecisionError("A decision table can have at most %d rules." % MAX_RULES)
        rule = DecisionRule(
            element_id=element.id, organization_id=element.organization_id,
            position=(rules[-1].position + 1) if rules else 1,
        )
        db.session.add(rule)
    else:
        rule = next((r for r in rules if r.position == position), None)
        if rule is None:
            raise DecisionError("That rule was not found.", 404)
    rule.input_entries = inputs
    rule.output_entries = outputs
    rule.annotation = (annotation or "").strip() or None
    db.session.flush()
    return rule


def delete_rule(element: ArchiMateElement, position: int) -> None:
    rules = rules_of(element)
    target = next((r for r in rules if r.position == position), None)
    if target is None:
        raise DecisionError("That rule was not found.", 404)
    db.session.delete(target)
    db.session.flush()
    for number, rule in enumerate([r for r in rules if r is not target], start=1):
        rule.position = number
    db.session.flush()


def replace_rules(element: ArchiMateElement, rows: List[Dict[str, Any]]) -> None:
    """Replace every rule (an import that updates a stored decision)."""
    for rule in rules_of(element):
        db.session.delete(rule)
    db.session.flush()
    for number, row in enumerate(rows, start=1):
        db.session.add(DecisionRule(
            element_id=element.id, organization_id=element.organization_id, position=number,
            input_entries=list(row["inputs"]), output_entries=list(row["outputs"]),
            annotation=(row.get("annotation") or None),
        ))
    db.session.flush()


def delete_decision(element: ArchiMateElement) -> None:
    for rel in _links(element, as_source=True) + _links(element, as_source=False):
        db.session.delete(rel)
    for rule in rules_of(element):
        db.session.delete(rule)
    db.session.flush()
    db.session.delete(element)
    db.session.flush()


# --- the link to the business process ---------------------------------------------------------

def _links(element: ArchiMateElement, as_source: bool) -> List[ArchiMateRelationship]:
    column = ArchiMateRelationship.source_id if as_source else ArchiMateRelationship.target_id
    return db.session.execute(
        db.select(ArchiMateRelationship).where(
            column == element.id, ArchiMateRelationship.type == LINK_TYPE,
            ArchiMateRelationship.organization_id == element.organization_id,
        )
    ).scalars().all()


def processes_of(decision: ArchiMateElement) -> List[ArchiMateElement]:
    """The business processes that apply this decision."""
    ids = [r.target_id for r in _links(decision, as_source=True)]
    if not ids:
        return []
    return db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id.in_(ids), ArchiMateElement.type == "BusinessProcess",
            ArchiMateElement.organization_id == decision.organization_id,
        ).order_by(ArchiMateElement.name)
    ).scalars().all()


def decisions_of(process: ArchiMateElement) -> List[ArchiMateElement]:
    """The decisions this business process applies."""
    ids = [r.source_id for r in _links(process, as_source=False)]
    if not ids:
        return []
    rows = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id.in_(ids), ArchiMateElement.type == DECISION_TYPE,
            ArchiMateElement.organization_id == process.organization_id,
        ).order_by(ArchiMateElement.name)
    ).scalars().all()
    return [r for r in rows if is_decision(r)]


def decisions_applied_by(process_element_id: int, org_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """The decisions a business process (by its element id) applies, as rows a page can list."""
    org_id = org_id or current_org_id()
    process = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id == process_element_id, ArchiMateElement.organization_id == org_id
        )
    ).scalar_one_or_none()
    if process is None:
        return []
    counts = rule_counts(org_id)
    return [
        {
            "id": d.id, "name": d.name, "policy": definition(d)["hit_policy"],
            "policy_name": HIT_POLICIES[definition(d)["hit_policy"]][1], "rules": counts.get(d.id, 0),
        }
        for d in decisions_of(process)
    ]


def linkable_processes(decision: ArchiMateElement) -> List[ArchiMateElement]:
    """The organisation's business processes this decision is not yet linked to."""
    linked = {p.id for p in processes_of(decision)}
    rows = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.type == "BusinessProcess",
            ArchiMateElement.organization_id == decision.organization_id,
        ).order_by(ArchiMateElement.name)
    ).scalars().all()
    return [p for p in rows if p.id not in linked]


def link_process(decision: ArchiMateElement, process_id: int) -> ArchiMateElement:
    from app.modules.architecture.services.archimate_relationship_service import (
        ArchiMateRelationshipService,
    )

    process = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id == process_id, ArchiMateElement.type == "BusinessProcess",
            ArchiMateElement.organization_id == decision.organization_id,
        )
    ).scalar_one_or_none()
    if process is None:
        raise DecisionError("That business process was not found.", 404)
    if process.id in {p.id for p in processes_of(decision)}:
        raise DecisionError("%s already applies this decision." % process.name, 409)
    created = ArchiMateRelationshipService.create_relationship(
        decision, process, LINK_TYPE,
        architecture_id=process.architecture_id or decision.architecture_id,
        organization_id=decision.organization_id,
    )
    if created is None:
        raise DecisionError("The link could not be saved.", 500)
    return process


def unlink_process(decision: ArchiMateElement, process_id: int) -> None:
    for rel in _links(decision, as_source=True):
        if rel.target_id == process_id:
            db.session.delete(rel)
    db.session.flush()


# --- evaluation ------------------------------------------------------------------------------

def evaluate(element: ArchiMateElement, values: List[Any]) -> Dict[str, Any]:
    """Run a test input against the table.

    Returns ``{"policy", "matched": [rule numbers], "outputs": [...], "error", "message"}``. ``outputs`` is
    a list of ``{label: value}`` mappings: one for the single-answer policies, one per rule for Collect
    and Rule order. A problem with the input or with the policy (two rules matching under Unique) is in
    ``error``, with the rules that matched still listed.
    """
    d = definition(element)
    rules = rules_of(element)
    policy = d["hit_policy"]
    result: Dict[str, Any] = {"policy": policy, "matched": [], "outputs": [], "error": None, "message": ""}
    if len(values) != len(d["inputs"]):
        raise DecisionError("Give one value for each input column.")
    labels = [o["label"] for o in d["outputs"]]
    matched: List[DecisionRule] = []
    for rule in rules:
        hit = True
        for entry, value, column in zip(rule.input_entries, values, d["inputs"]):
            if not entry_matches(entry, value, column["type"]):
                hit = False
                break
        if hit:
            matched.append(rule)
    result["matched"] = [r.position for r in matched]

    def answer(rule: DecisionRule) -> Dict[str, str]:
        return {label: display_value(v) for label, v in zip(labels, rule.output_entries)}

    numbers = ", ".join(str(r.position) for r in matched)
    if not matched:
        result["message"] = "No rule matched this input."
    elif policy == "U" and len(matched) > 1:
        result["error"] = "Rules %s all match, and the Unique hit policy allows only one." % numbers
    elif policy == "A" and len({tuple(sorted(answer(r).items())) for r in matched}) > 1:
        result["error"] = "Rules %s match with different answers, and the Any hit policy needs them to agree." % numbers
    elif policy in ("C", "R"):
        result["outputs"] = [answer(r) for r in matched]
        result["message"] = "Rule %s matched." % numbers if len(matched) == 1 else "Rules %s matched." % numbers
    else:
        first = matched[0]
        result["matched"] = [first.position] if policy == "F" else result["matched"]
        result["outputs"] = [answer(first)]
        result["message"] = "Rule %d matched." % first.position
    return result
