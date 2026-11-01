"""DMN decision tables as standard DMN 1.5 XML: export one, import any.

The file is the OMG DMN model interchange format that Camunda Modeler and other DMN tools open. Export
writes one ``decision`` with a ``decisionTable`` for a stored decision. Import reads every decision
table in a file (DMN 1.1 to 1.5) into the organisation, under the same three strategies as the
ArchiMate model import, and previews the result without writing anything.

A decision this tool cannot hold is refused with the reason, and the rest of the file still lands:
Priority and Output order hit policies, Collect with an aggregator, and entries it cannot read.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union
from xml.etree import ElementTree as ET

from app import db
from app.modules.architecture.services import decision_table_service as dts
from app.services.change_source import records_changes_as
from app.utils import safe_xml

DMN_NS = "https://www.omg.org/spec/DMN/20230324/MODEL/"
_DMN_NS_PREFIXES = ("https://www.omg.org/spec/DMN/", "http://www.omg.org/spec/DMN/")
TARGET_NAMESPACE = "https://entelim.app/dmn"
_RESERVED_POLICIES = {"PRIORITY": "Priority", "OUTPUT ORDER": "Output order"}


def _q(tag: str) -> str:
    return "{%s}%s" % (DMN_NS, tag)


def _ids(prefix: str, *parts: Any) -> str:
    return prefix + "_" + "_".join(str(p) for p in parts)


# --- export ----------------------------------------------------------------------------------

def export_dmn(element) -> str:
    """The decision as DMN 1.5 XML (UTF-8 text with its declaration)."""
    d = dts.definition(element)
    ET.register_namespace("", DMN_NS)
    root = ET.Element(_q("definitions"), {
        "id": _ids("Definitions", element.id), "name": element.name, "namespace": TARGET_NAMESPACE,
    })
    decision = ET.SubElement(root, _q("decision"), {"id": _ids("Decision", element.id), "name": element.name})
    if element.description:
        ET.SubElement(decision, _q("description")).text = element.description
    table = ET.SubElement(decision, _q("decisionTable"), {
        "id": _ids("DecisionTable", element.id), "hitPolicy": dts.HIT_POLICIES[d["hit_policy"]][0],
    })
    for i, column in enumerate(d["inputs"], start=1):
        node = ET.SubElement(table, _q("input"), {"id": _ids("Input", element.id, i), "label": column["label"]})
        expression = ET.SubElement(node, _q("inputExpression"), {
            "id": _ids("InputExpression", element.id, i), "typeRef": column["type"],
        })
        ET.SubElement(expression, _q("text")).text = column["label"]
    for i, column in enumerate(d["outputs"], start=1):
        ET.SubElement(table, _q("output"), {
            "id": _ids("Output", element.id, i), "label": column["label"], "name": column["label"],
            "typeRef": column["type"],
        })
    for rule in dts.rules_of(element):
        node = ET.SubElement(table, _q("rule"), {"id": _ids("Rule", element.id, rule.position)})
        if rule.annotation:
            ET.SubElement(node, _q("description")).text = rule.annotation
        for i, entry in enumerate(rule.input_entries, start=1):
            holder = ET.SubElement(node, _q("inputEntry"), {"id": _ids("InputEntry", element.id, rule.position, i)})
            ET.SubElement(holder, _q("text")).text = entry
        for i, entry in enumerate(rule.output_entries, start=1):
            holder = ET.SubElement(node, _q("outputEntry"), {"id": _ids("OutputEntry", element.id, rule.position, i)})
            ET.SubElement(holder, _q("text")).text = entry
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def export_filename(element) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", element.name or "decision").strip("-").lower() or "decision"
    return slug + ".dmn"


# --- import ----------------------------------------------------------------------------------

def is_dmn_document(content: Union[bytes, str, None]) -> bool:
    """True when the file's root element is a DMN ``definitions``."""
    if not content:
        return False
    head = content[:4096]
    text = head.decode("utf-8", "ignore") if isinstance(head, bytes) else head
    match = re.search(r"<(?:[\w.-]+:)?definitions\b[^>]*>", text)
    return bool(match and any(p in match.group(0) for p in _DMN_NS_PREFIXES))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(node, name: str) -> list:
    return [c for c in node if _local(c.tag) == name]


def _text_of(node, name: str) -> str:
    child = next(iter(_kids(node, name)), None)
    return (child.text or "").strip() if child is not None and child.text else ""


def _type_ref(value: Optional[str]) -> str:
    base = (value or "string").split(":")[-1].strip().lower()
    return base if base in dts.TYPE_REFS else "string"


def _parse_decision(node) -> Dict[str, Any]:
    name = (node.get("name") or "").strip() or node.get("id") or "Decision"
    out: Dict[str, Any] = {
        "identifier": node.get("id") or name, "name": name, "description": _text_of(node, "description") or None,
        "error": None, "hit_policy": None, "inputs": [], "outputs": [], "rules": [],
    }
    table = next(iter(_kids(node, "decisionTable")), None)
    if table is None:
        out["error"] = "%s has no decision table (only decision tables are imported)." % name
        return out
    raw_policy = (table.get("hitPolicy") or "UNIQUE").strip().upper()
    letter = dts.hit_policy_for_dmn(raw_policy)
    if letter is None:
        label = _RESERVED_POLICIES.get(raw_policy, raw_policy.title())
        out["error"] = "%s uses the %s hit policy, which is not supported yet." % (name, label)
        return out
    if letter == "C" and table.get("aggregation"):
        out["error"] = "%s collects with a %s aggregator, which is not supported yet." % (name, table.get("aggregation"))
        return out
    out["hit_policy"] = letter
    for node_in in _kids(table, "input"):
        expression = next(iter(_kids(node_in, "inputExpression")), None)
        label = (node_in.get("label") or "").strip() or (_text_of(expression, "text") if expression is not None else "")
        out["inputs"].append({
            "label": label or "Input %d" % (len(out["inputs"]) + 1),
            "type": _type_ref(expression.get("typeRef") if expression is not None else None),
        })
    for node_out in _kids(table, "output"):
        label = (node_out.get("label") or node_out.get("name") or "").strip()
        out["outputs"].append({
            "label": label or "Output %d" % (len(out["outputs"]) + 1), "type": _type_ref(node_out.get("typeRef")),
        })
    if not out["inputs"] or not out["outputs"]:
        out["error"] = "%s needs at least one input and one output column." % name
        return out
    for number, rule in enumerate(_kids(table, "rule"), start=1):
        inputs = [_text_of(e, "text") or "-" for e in _kids(rule, "inputEntry")]
        outputs = [_text_of(e, "text") for e in _kids(rule, "outputEntry")]
        if len(inputs) != len(out["inputs"]) or len(outputs) != len(out["outputs"]):
            out["error"] = "%s rule %d does not have one entry for each column." % (name, number)
            return out
        for entry, column in zip(inputs, out["inputs"]):
            problem = dts.check_entry(entry, column["type"])
            if problem:
                out["error"] = "%s rule %d, %s: %s" % (name, number, column["label"], problem)
                return out
        out["rules"].append({"inputs": inputs, "outputs": outputs, "annotation": _text_of(rule, "description") or None})
    return out


def parse_dmn(content: Union[bytes, str]) -> Dict[str, Any]:
    from app.services.archimate_import_service import ArchiMateImportService, ImportRequestError

    text = ArchiMateImportService.decode_xml(content)
    try:
        root = safe_xml.fromstring(text)
    except Exception as exc:
        raise ImportRequestError("The file is not valid XML: %s" % exc)
    if _local(root.tag) != "definitions" or not any((root.tag or "").startswith("{" + p) for p in _DMN_NS_PREFIXES):
        raise ImportRequestError("The file is not a DMN model.")
    decisions = [_parse_decision(n) for n in _kids(root, "decision")]
    if not decisions:
        raise ImportRequestError("No decision found in the DMN file.")
    return {"model_name": (root.get("name") or "").strip(), "decisions": decisions}


def preview_dmn(content: Union[bytes, str], organization_id: Optional[int] = None) -> Dict[str, Any]:
    """What importing this file would do, writing nothing (the shape the import screen's preview reads)."""
    parsed = parse_dmn(content)
    org_id = dts.current_org_id() if organization_id is None else organization_id
    elements, summary = [], {"total": 0, "new": 0, "exists": 0, "conflict": 0, "invalid": 0}
    for dec in parsed["decisions"]:
        if dec["error"]:
            status, why = "invalid", dec["error"]
        else:
            stored = dts.find_decision_by_name(dec["name"], org_id)
            if stored is None:
                status, why = "new", "%d rules" % len(dec["rules"])
            else:
                same = _same_as_stored(stored, dec)
                status, why = ("exists", "Identical to the stored decision") if same else (
                    "conflict", "A decision with this name is stored with different rules")
        summary["total"] += 1
        summary[status] += 1
        elements.append({
            "identifier": dec["identifier"], "name": dec["name"], "type": "Decision",
            "status": status, "application_kind_decision": why,
        })
    summary.update({"relationships_valid": 0, "relationships_invalid": 0})
    return {
        "model_name": parsed["model_name"], "kind": "dmn", "summary": summary, "elements": elements,
        "relationships": [],
    }


def _same_as_stored(stored, dec: Dict[str, Any]) -> bool:
    d = dts.definition(stored)
    rules = [{"inputs": list(r.input_entries), "outputs": list(r.output_entries)} for r in dts.rules_of(stored)]
    wanted = [{"inputs": r["inputs"], "outputs": r["outputs"]} for r in dec["rules"]]
    return d["hit_policy"] == dec["hit_policy"] and d["inputs"] == dec["inputs"] and d["outputs"] == dec["outputs"] and rules == wanted


@records_changes_as("import")
def import_dmn(
    content: Union[bytes, str], strategy: str = "skip_duplicates", organization_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Import every decision table in the file; the result carries the counts the import screen shows."""
    from app.services.archimate_import_service import STRATEGIES, ImportRequestError

    if strategy not in STRATEGIES:
        raise ImportRequestError("Invalid strategy: %s. Must be one of: %s" % (strategy, ", ".join(STRATEGIES)))
    parsed = parse_dmn(content)
    org_id = dts.current_org_id() if organization_id is None else organization_id
    created = updated = skipped = failed = 0
    errors: List[str] = []
    created_ids: List[int] = []
    results: List[Dict[str, Any]] = []
    for dec in parsed["decisions"]:
        if dec["error"]:
            failed += 1
            errors.append(dec["error"])
            continue
        stored = dts.find_decision_by_name(dec["name"], org_id)
        status = "created"
        if stored is not None and strategy == "skip_duplicates":
            skipped += 1
            results.append({"id": stored.id, "name": stored.name, "status": "skipped"})
            continue
        try:
            with db.session.begin_nested():
                if stored is not None and strategy == "update_existing":
                    element = stored
                    _write_definition(element, dec)
                    status = "updated"
                else:
                    element = dts.create_decision(
                        dec["name"], dec["hit_policy"], dec["inputs"], dec["outputs"], dec["description"],
                        org_id=org_id, allow_duplicate_name=stored is not None,
                    )
                dts.replace_rules(element, dec["rules"])
        except dts.DecisionError as exc:
            failed += 1
            errors.append("%s: %s" % (dec["name"], exc))
            continue
        if status == "updated":
            updated += 1
        else:
            created += 1
            created_ids.append(element.id)
        results.append({"id": element.id, "name": element.name, "status": status})
    return {
        "model_name": parsed["model_name"], "created": created, "updated": updated, "skipped": skipped,
        "failed": failed, "errors": errors, "relationships_created": 0, "relationships_skipped": 0,
        "relationships_failed": [], "created_ids": created_ids, "element_results": results,
        "relationship_results": [], "imported_kind": "dmn",
    }


def _write_definition(element, dec: Dict[str, Any]) -> None:
    props = dict(element.custom_properties or {})
    props["dmn"] = {"hit_policy": dec["hit_policy"], "inputs": dec["inputs"], "outputs": dec["outputs"]}
    element.custom_properties = props
    if dec["description"]:
        element.description = dec["description"]
    db.session.flush()
