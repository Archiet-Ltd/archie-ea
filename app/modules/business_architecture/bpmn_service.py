"""The drawn flow of a business process, and its BPMN 2.0 XML.

The flow is stored on the business process itself (``BusinessProcess.bpmn_flow``); there is no second
process store. A lane is a business role of the model (an ArchiMate BusinessRole element), a sub-process
is a child BusinessProcess, and saving a flow records the ArchiMate link from each lane's role to the process.

Flow shape::

    {"pools": [{"id", "name", "lanes": [{"id", "name", "roleId"}]}],
     "nodes": [{"id", "type", "label", "pool", "lane", "x", "y", "w", "h", "processId"}],
     "flows": [{"id", "type": "sequence" | "message", "source", "target", "label"}]}

Pools are stacked top to bottom from ``POOL_X`` and all as wide as the furthest shape needs; a lane is ``LANE_HEIGHT`` tall, so a node's lane follows
from where it is drawn and the drawing and the XML agree on geometry.
"""
from __future__ import annotations

import re
from xml.sax.saxutils import quoteattr

from defusedxml import ElementTree as SafeET

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.process_data import BusinessProcess

POOL_X = 0
POOL_GAP = 40
POOL_MIN_WIDTH = 700
POOL_PAD = 40
POOL_HEADER = 30
LANE_HEIGHT = 160

NODE_SIZES = {
    "start": (36, 36), "intermediate": (36, 36), "end": (36, 36),
    "task": (110, 64), "subprocess": (110, 64),
    "xor": (50, 50), "and": (50, 50), "event": (50, 50),
}
NODE_LABELS = {
    "start": "Start event", "intermediate": "Intermediate event", "end": "End event",
    "task": "Task", "subprocess": "Sub-process",
    "xor": "Exclusive gateway", "and": "Parallel gateway", "event": "Event-based gateway",
}
FLOW_TYPES = ("sequence", "message")

NS_BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
NS_DI = "http://www.omg.org/spec/BPMN/20100524/DI"
NS_DC = "http://www.omg.org/spec/DD/20100524/DC"
NS_DI2 = "http://www.omg.org/spec/DD/20100524/DI"
NS_ENT = "https://entelim.com/schema/bpmn"

_XML_TAG = {
    "start": "startEvent", "intermediate": "intermediateCatchEvent", "end": "endEvent",
    "task": "task", "subprocess": "subProcess",
    "xor": "exclusiveGateway", "and": "parallelGateway", "event": "eventBasedGateway",
}
_TAG_TYPE = {v: k for k, v in _XML_TAG.items()}
_TAG_TYPE.update({
    "intermediateThrowEvent": "intermediate", "boundaryEvent": "intermediate",
    "userTask": "task", "serviceTask": "task", "manualTask": "task", "scriptTask": "task",
    "sendTask": "task", "receiveTask": "task", "businessRuleTask": "task",
    "callActivity": "subprocess", "transaction": "subprocess", "adHocSubProcess": "subprocess",
})
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class FlowError(ValueError):
    """The flow cannot be saved; the message says why in plain words."""


def empty_flow(name: str = "Process") -> dict:
    return {"pools": [{"id": "pool1", "name": name, "lanes": [{"id": "lane1", "name": "Lane 1", "roleId": None}]}],
            "nodes": [], "flows": []}


def pool_boxes(flow: dict) -> dict:
    """Pool and lane rectangles: {pool_id: {'x','y','w','h','lanes': {lane_id: {'y','h'}}}}."""
    boxes, y = {}, 0
    width = max([POOL_MIN_WIDTH] + [n["x"] + n.get("w", 0) + POOL_PAD for n in flow.get("nodes", [])])
    for pool in flow["pools"]:
        h = LANE_HEIGHT * len(pool["lanes"])
        lanes = {lane["id"]: {"y": y + i * LANE_HEIGHT, "h": LANE_HEIGHT} for i, lane in enumerate(pool["lanes"])}
        boxes[pool["id"]] = {"x": POOL_X, "y": y, "w": width, "h": h, "lanes": lanes}
        y += h + POOL_GAP
    return boxes


def _text(value, limit=300) -> str:
    return (str(value) if value is not None else "").strip()[:limit]


def _num(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FlowError("A shape has no position.") from None
    if number != number or abs(number) > 100000:
        raise FlowError("A shape is outside the drawing.")
    return int(round(number))


def normalise(flow: dict, org_id: int, process: BusinessProcess | None = None, strict: bool = True) -> dict:
    """Validate a flow against the model and return it in canonical form.

    Lanes must name a business role of this organisation; sub-process ids must be this process's own children.
    Sequence flows stay inside one pool, message flows join two pools.
    """
    if not isinstance(flow, dict):
        raise FlowError("The flow is not a drawing.")
    pools_in = flow.get("pools") or []
    if not pools_in:
        raise FlowError("A process needs at least one pool with one lane.")
    if len(pools_in) > 20 or len(flow.get("nodes") or []) > 2000 or len(flow.get("flows") or []) > 4000:
        raise FlowError("This drawing is larger than the editor holds.")
    ids = set()

    def take(value, what):
        value = _text(value, 64)
        if not _ID.match(value):
            raise FlowError("%s has an identifier that is not valid." % what)
        if value in ids:
            raise FlowError("Two shapes share the identifier %s." % value)
        ids.add(value)
        return value

    role_ids = {r for r in (lane.get("roleId") for p in pools_in for lane in (p.get("lanes") or [])) if r}
    roles = {}
    if role_ids:
        roles = {
            e.id: e for e in ArchiMateElement.query.filter(
                ArchiMateElement.id.in_([int(r) for r in role_ids if str(r).isdigit()]),
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.type == "BusinessRole",
            )
        }
    pools = []
    lane_pool = {}
    for p in pools_in:
        pid = take(p.get("id"), "A pool")
        lanes = []
        for lane in p.get("lanes") or []:
            lid = take(lane.get("id"), "A lane")
            role = lane.get("roleId")
            if role not in (None, ""):
                try:
                    role = int(role)
                except (TypeError, ValueError):
                    raise FlowError("A lane names a role that is not in the model.") from None
                if role not in roles:
                    raise FlowError("A lane names a role that is not a business role in your model.")
            else:
                role = None
            lane_name = _text(lane.get("name"), 200) or (roles[role].name if role else "Lane")
            lanes.append({"id": lid, "name": lane_name, "roleId": role})
            lane_pool[lid] = pid
        if not lanes:
            raise FlowError("Every pool needs at least one lane.")
        pools.append({"id": pid, "name": _text(p.get("name"), 200) or "Pool", "lanes": lanes})
    out = {"pools": pools, "nodes": [], "flows": []}
    boxes = pool_boxes(out)
    children = set()
    if process is not None and process.id:
        children = {c.id for c in BusinessProcess.query.filter_by(parent_process_id=process.id)}
    node_pool = {}
    for n in flow.get("nodes") or []:
        nid = take(n.get("id"), "A shape")
        kind = n.get("type")
        if kind not in NODE_SIZES:
            raise FlowError("A shape has a type this editor does not draw.")
        w, h = NODE_SIZES[kind]
        x, y = _num(n.get("x"), "x"), _num(n.get("y"), "y")
        lane = n.get("lane")
        if lane not in lane_pool:
            lane = next((lid for pid, b in boxes.items() for lid, lb in b["lanes"].items()
                         if lb["y"] <= y + h / 2 < lb["y"] + lb["h"]), None)
        if lane is None:
            raise FlowError("Put “%s” inside a lane." % (_text(n.get("label")) or NODE_LABELS[kind]))
        process_id = n.get("processId") if kind == "subprocess" else None
        if process_id is not None:
            try:
                process_id = int(process_id)
            except (TypeError, ValueError):
                process_id = None
            if strict and process_id not in children:
                process_id = None
        out["nodes"].append({"id": nid, "type": kind, "label": _text(n.get("label")), "pool": lane_pool[lane],
                             "lane": lane, "x": x, "y": y, "w": w, "h": h, "processId": process_id})
        node_pool[nid] = lane_pool[lane]
    for f in flow.get("flows") or []:
        fid = take(f.get("id"), "A flow")
        kind = f.get("type") or "sequence"
        if kind not in FLOW_TYPES:
            raise FlowError("A flow has a type this editor does not draw.")
        s, t = f.get("source"), f.get("target")
        if s not in node_pool or t not in node_pool:
            raise FlowError("A flow ends at a shape that is not on the drawing.")
        if s == t:
            raise FlowError("A flow cannot start and end at the same shape.")
        if kind == "sequence" and node_pool[s] != node_pool[t]:
            raise FlowError("A sequence flow stays inside one pool; use a message flow between pools.")
        if kind == "message" and node_pool[s] == node_pool[t]:
            raise FlowError("A message flow joins two pools; use a sequence flow inside one pool.")
        out["flows"].append({"id": fid, "type": kind, "source": s, "target": t, "label": _text(f.get("label"), 200)})
    # One canonical order (pool by pool, sequence flows before message flows), so a file written and read back is the same drawing.
    order = {p["id"]: i for i, p in enumerate(out["pools"])}
    out["nodes"].sort(key=lambda n: order[n["pool"]])
    out["flows"].sort(key=lambda f: (f["type"] == "message", order[node_pool[f["source"]]] if f["type"] == "sequence" else 0))
    return out


def _element_of(process: BusinessProcess, org_id: int) -> ArchiMateElement:
    """The process's ArchiMate element, made and attributed to the organisation when it is missing."""
    element = process.ensure_archimate_element()
    if element.organization_id is None:
        element.organization_id = org_id
    return element


def save_flow(process: BusinessProcess, flow: dict, org_id: int, user_id: int | None = None) -> dict:
    """Store the flow on the process, give each sub-process shape its child process and record the role links."""
    out = normalise(flow, org_id, process)
    element = _element_of(process, org_id)
    for node in out["nodes"]:
        if node["type"] != "subprocess" or node["processId"]:
            continue
        # A sub-process drawn again (an import, a redraw) finds its child by name rather than making a second one.
        taken = {n["processId"] for n in out["nodes"] if n["processId"]}
        same = BusinessProcess.query.filter_by(parent_process_id=process.id, name=node["label"] or NODE_LABELS["subprocess"]).all()
        reuse = next((c for c in same if c.id not in taken), None)
        if reuse is not None:
            node["processId"] = reuse.id
            continue
        child = BusinessProcess(
            name=node["label"] or NODE_LABELS["subprocess"],
            parent_process_id=process.id,
            level=(process.level if process.level is not None else 2) + 1,
            bpmn_type="subprocess",
            process_type=process.process_type,
            primary_capability_id=process.primary_capability_id,
            created_by_id=user_id,
            organization_id=org_id,
        )
        db.session.add(child)
        db.session.flush()
        _element_of(child, org_id)
        node["processId"] = child.id
    # A renamed sub-process shape renames its child, so the shape and the process are one record.
    names = {n["processId"]: n["label"] for n in out["nodes"] if n["type"] == "subprocess" and n["label"]}
    if names:
        for child in BusinessProcess.query.filter(BusinessProcess.id.in_(list(names))):
            if child.parent_process_id == process.id and child.name != names[child.id]:
                child.name = names[child.id]
                if child.archimate_element_id:
                    el = db.session.get(ArchiMateElement, child.archimate_element_id)
                    if el is not None and el.organization_id == org_id:
                        el.name = names[child.id]
    process.bpmn_flow = out
    for lane in (lane for p in out["pools"] for lane in p["lanes"] if lane["roleId"]):
        exists = ArchiMateRelationship.query.filter_by(
            source_id=lane["roleId"], target_id=element.id, type="Assignment", organization_id=org_id,
        ).first()
        if exists is None:
            db.session.add(ArchiMateRelationship(
                source_id=lane["roleId"], target_id=element.id, type="Assignment", created_by_id=user_id,
                organization_id=org_id,
            ))
    db.session.flush()
    return out


def roles_in_org(org_id: int) -> list:
    return ArchiMateElement.query.filter_by(organization_id=org_id, type="BusinessRole").order_by(ArchiMateElement.name).all()


# ── XML ──────────────────────────────────────────────────────────────────────────────────────────

def _attr(value) -> str:
    return quoteattr(str(value))


def _edge_points(a, b):
    """A straight line between two boxes' borders, as waypoints."""
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2

    def border(box, cx, cy, tx, ty):
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        sx = (box["w"] / 2) / abs(dx) if dx else float("inf")
        sy = (box["h"] / 2) / abs(dy) if dy else float("inf")
        s = min(sx, sy)
        return cx + dx * s, cy + dy * s

    return [border(a, ax, ay, bx, by), border(b, bx, by, ax, ay)]


def to_xml(flow: dict, name: str = "Process") -> str:
    """BPMN 2.0 XML with diagram interchange, so any BPMN 2.0 tool draws it as it is drawn here."""
    boxes = pool_boxes(flow)
    nodes = {n["id"]: n for n in flow["nodes"]}
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(
        '<definitions xmlns="%s" xmlns:bpmndi="%s" xmlns:dc="%s" xmlns:di="%s" xmlns:entelim="%s" '
        'id="Definitions_1" name=%s targetNamespace="%s/definitions">' % (NS_BPMN, NS_DI, NS_DC, NS_DI2, NS_ENT, _attr(name), NS_ENT))
    out.append('  <collaboration id="Collaboration_1">')
    for pool in flow["pools"]:
        out.append('    <participant id="%s" name=%s processRef="Process_%s"/>' % (pool["id"], _attr(pool["name"]), pool["id"]))
    for f in flow["flows"]:
        if f["type"] == "message":
            out.append('    <messageFlow id="%s" name=%s sourceRef="%s" targetRef="%s"/>' % (f["id"], _attr(f["label"]), f["source"], f["target"]))
    out.append('  </collaboration>')
    for pool in flow["pools"]:
        pid = pool["id"]
        out.append('  <process id="Process_%s" name=%s isExecutable="false">' % (pid, _attr(pool["name"])))
        out.append('    <laneSet id="LaneSet_%s">' % pid)
        for lane in pool["lanes"]:
            role = ' entelim:roleId="%s"' % (lane["roleId"] or "")
            out.append('      <lane id="%s" name=%s%s>' % (lane["id"], _attr(lane["name"]), role))
            for n in flow["nodes"]:
                if n["lane"] == lane["id"]:
                    out.append('        <flowNodeRef>%s</flowNodeRef>' % n["id"])
            out.append('      </lane>')
        out.append('    </laneSet>')
        for n in flow["nodes"]:
            if n["pool"] != pid:
                continue
            extra = ' entelim:processId="%s"' % n["processId"] if n.get("processId") else ""
            out.append('    <%s id="%s" name=%s%s/>' % (_XML_TAG[n["type"]], n["id"], _attr(n["label"]), extra))
        for f in flow["flows"]:
            if f["type"] == "sequence" and nodes[f["source"]]["pool"] == pid:
                out.append('    <sequenceFlow id="%s" name=%s sourceRef="%s" targetRef="%s"/>' % (f["id"], _attr(f["label"]), f["source"], f["target"]))
        out.append('  </process>')
    out.append('  <bpmndi:BPMNDiagram id="Diagram_1">')
    out.append('    <bpmndi:BPMNPlane id="Plane_1" bpmnElement="Collaboration_1">')
    for pool in flow["pools"]:
        b = boxes[pool["id"]]
        out.append('      <bpmndi:BPMNShape id="Shape_%s" bpmnElement="%s" isHorizontal="true"><dc:Bounds x="%d" y="%d" width="%d" height="%d"/></bpmndi:BPMNShape>'
                   % (pool["id"], pool["id"], b["x"], b["y"], b["w"], b["h"]))
        for lane in pool["lanes"]:
            lb = b["lanes"][lane["id"]]
            out.append('      <bpmndi:BPMNShape id="Shape_%s" bpmnElement="%s" isHorizontal="true"><dc:Bounds x="%d" y="%d" width="%d" height="%d"/></bpmndi:BPMNShape>'
                       % (lane["id"], lane["id"], b["x"] + POOL_HEADER, lb["y"], b["w"] - POOL_HEADER, lb["h"]))
    for n in flow["nodes"]:
        expanded = ' isExpanded="false"' if n["type"] == "subprocess" else ""
        out.append('      <bpmndi:BPMNShape id="Shape_%s" bpmnElement="%s"%s><dc:Bounds x="%d" y="%d" width="%d" height="%d"/></bpmndi:BPMNShape>'
                   % (n["id"], n["id"], expanded, n["x"], n["y"], n["w"], n["h"]))
    for f in flow["flows"]:
        pts = _edge_points(nodes[f["source"]], nodes[f["target"]])
        way = "".join('<di:waypoint x="%d" y="%d"/>' % (round(x), round(y)) for x, y in pts)
        out.append('      <bpmndi:BPMNEdge id="Edge_%s" bpmnElement="%s">%s</bpmndi:BPMNEdge>' % (f["id"], f["id"], way))
    out.append('    </bpmndi:BPMNPlane>')
    out.append('  </bpmndi:BPMNDiagram>')
    out.append('</definitions>')
    return "\n".join(out) + "\n"


def _q(ns, tag):
    return "{%s}%s" % (ns, tag)


def from_xml(xml: bytes | str, org_id: int, process: BusinessProcess | None = None) -> tuple[dict, list]:
    """Read BPMN 2.0 XML into a flow. Returns (flow, notes); a file this editor cannot draw raises FlowError."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    if len(xml) > 5_000_000:
        raise FlowError("This file is larger than the editor reads.")
    try:
        root = SafeET.fromstring(xml)
    except Exception:  # noqa: BLE001 - any parser refusal is "not a readable BPMN file"
        raise FlowError("This file is not BPMN 2.0 XML the editor can read.") from None
    if root.tag != _q(NS_BPMN, "definitions"):
        raise FlowError("This file is not BPMN 2.0 XML: it has no definitions element.")
    notes = []
    processes = {p.get("id"): p for p in root.findall(_q(NS_BPMN, "process"))}
    if not processes:
        raise FlowError("This file holds no process.")
    participants = []
    collab = root.find(_q(NS_BPMN, "collaboration"))
    if collab is not None:
        for part in collab.findall(_q(NS_BPMN, "participant")):
            if part.get("processRef") in processes:
                participants.append((part.get("id"), part.get("name") or "", processes[part.get("processRef")]))
    if not participants:
        pid, proc = next(iter(processes.items()))
        participants.append((pid, proc.get("name") or "", proc))

    shapes, edges = {}, {}
    for shape in root.iter(_q(NS_DI, "BPMNShape")):
        b = shape.find(_q(NS_DC, "Bounds"))
        if b is not None:
            shapes[shape.get("bpmnElement")] = {k: float(b.get(k, 0)) for k in ("x", "y", "width", "height")}
    for edge in root.iter(_q(NS_DI, "BPMNEdge")):
        edges[edge.get("bpmnElement")] = edge

    roles = {e.id: e for e in roles_in_org(org_id)}
    by_name = {}
    for e in roles.values():
        by_name.setdefault(e.name.strip().lower(), e.id)
    flow = {"pools": [], "nodes": [], "flows": []}
    unsupported = set()
    for index, (pool_id, pool_name, proc) in enumerate(participants):
        lanes = []
        lane_nodes = {}
        for lane in proc.iter(_q(NS_BPMN, "lane")):
            declared = lane.get("{%s}roleId" % NS_ENT)
            role = int(declared) if declared and declared.isdigit() and int(declared) in roles else None
            name = lane.get("name") or ""
            if role is None and name.strip().lower() in by_name:
                role = by_name[name.strip().lower()]
            if role is None and declared != "":
                notes.append("The lane “%s” is not linked to a business role in your model." % (name or "unnamed"))
            lanes.append({"id": lane.get("id"), "name": name, "roleId": role, "box": shapes.get(lane.get("id"))})
            for ref in lane.findall(_q(NS_BPMN, "flowNodeRef")):
                lane_nodes[(ref.text or "").strip()] = lane.get("id")
        if not lanes:
            lanes = [{"id": "lane_" + str(pool_id), "name": pool_name or "Lane", "roleId": None, "box": None}]
        lanes.sort(key=lambda ln: (ln["box"] or {}).get("y", 0))
        pool = {"id": pool_id, "name": pool_name, "lanes": [{k: ln[k] for k in ("id", "name", "roleId")} for ln in lanes]}
        flow["pools"].append(pool)
        # A file drawn by another tool places its pools its own way; shift this pool onto the editor's grid.
        theirs = shapes.get(pool_id)
        mine_top = sum(LANE_HEIGHT * len(p["lanes"]) + POOL_GAP for p in flow["pools"][:-1])
        pool_dx = (theirs["x"] - POOL_X) if theirs else 0
        pool_dy = (theirs["y"] - mine_top) if theirs else 0
        count = 0
        for el in proc:
            tag = el.tag.split("}")[-1]
            if tag in ("laneSet", "sequenceFlow", "documentation", "extensionElements", "incomingflow", "ioSpecification"):
                continue
            if tag in ("textAnnotation", "association", "dataObject", "dataObjectReference", "dataStoreReference", "group"):
                continue
            kind = _TAG_TYPE.get(tag)
            if kind is None:
                unsupported.add(tag)
                continue
            box = shapes.get(el.get("id"))
            w, h = NODE_SIZES[kind]
            lane_id = lane_nodes.get(el.get("id")) or lanes[0]["id"]
            lane_index = next(i for i, ln in enumerate(lanes) if ln["id"] == lane_id)
            if box:
                x = int(round(box["x"] - pool_dx))
                y = int(round(box["y"] - pool_dy))
            else:
                top = sum(LANE_HEIGHT * len(p["lanes"]) + POOL_GAP for p in flow["pools"][:-1]) + lane_index * LANE_HEIGHT
                x, y = 80 + count * 150, top + (LANE_HEIGHT - h) // 2
            count += 1
            pid = el.get("{%s}processId" % NS_ENT)
            flow["nodes"].append({"id": el.get("id"), "type": kind, "label": el.get("name") or "", "pool": pool_id,
                                  "lane": lane_id, "x": x, "y": y, "w": w, "h": h,
                                  "processId": int(pid) if pid and pid.isdigit() else None})
        for sf in proc.iter(_q(NS_BPMN, "sequenceFlow")):
            flow["flows"].append({"id": sf.get("id"), "type": "sequence", "source": sf.get("sourceRef"),
                                  "target": sf.get("targetRef"), "label": sf.get("name") or ""})
    if collab is not None:
        for mf in collab.findall(_q(NS_BPMN, "messageFlow")):
            flow["flows"].append({"id": mf.get("id"), "type": "message", "source": mf.get("sourceRef"),
                                  "target": mf.get("targetRef"), "label": mf.get("name") or ""})
    if unsupported:
        raise FlowError("This file uses shapes the editor does not draw yet: %s." % ", ".join(sorted(unsupported)))
    return normalise(flow, org_id, process), notes


def order_to_delivery_flow(roles: list[tuple[int, str]]) -> dict:
    """The demonstration order-to-delivery flow. ``roles`` are three (element id, name) business roles, top lane first."""
    (r1, n1), (r2, n2), (r3, n3) = roles
    flow = {"pools": [
        {"id": "pool1", "name": "Order to delivery", "lanes": [
            {"id": "lane1", "name": n1, "roleId": r1}, {"id": "lane2", "name": n2, "roleId": r2},
            {"id": "lane3", "name": n3, "roleId": r3}]},
        {"id": "pool2", "name": "Customer", "lanes": [{"id": "lane4", "name": "Customer", "roleId": None}]},
    ], "nodes": [], "flows": []}
    tops = {k: v["lanes"] for k, v in pool_boxes(flow).items()}

    def put(nid, kind, lane, x, label="", dy=None, pool="pool1", process_id=None):
        w, h = NODE_SIZES[kind]
        top = tops[pool][lane]["y"]
        flow["nodes"].append({"id": nid, "type": kind, "label": label, "pool": pool, "lane": lane, "x": x,
                              "y": top + ((LANE_HEIGHT - h) // 2 if dy is None else dy), "w": w, "h": h,
                              "processId": process_id})

    def link(fid, a, b, label="", kind="sequence"):
        flow["flows"].append({"id": fid, "type": kind, "source": a, "target": b, "label": label})

    put("order", "task", "lane4", 150, "Place order", pool="pool2")
    put("received", "intermediate", "lane4", 1000, "Delivery received", pool="pool2")
    put("start", "start", "lane1", 80)
    put("receive", "task", "lane1", 150, "Receive customer order")
    put("stock", "xor", "lane2", 290, "Stock available?")
    put("reserve", "task", "lane2", 390, "Reserve replacement stock", dy=10)
    put("replenish", "task", "lane2", 390, "Replenish component stock", dy=86)
    put("merge", "xor", "lane2", 520)
    put("split", "and", "lane2", 600)
    put("pack", "subprocess", "lane3", 690, "Pack and inspect order")
    put("schedule", "task", "lane2", 690, "Schedule dispatch")
    put("join", "and", "lane2", 830)
    put("ship", "task", "lane2", 910, "Ship customer order")
    put("wait", "event", "lane2", 1050)
    put("delivered", "intermediate", "lane1", 1130, "Delivery confirmed")
    put("returned", "intermediate", "lane3", 1130, "Return requested")
    put("done", "end", "lane1", 1250, "Order delivered")
    put("back", "end", "lane3", 1250, "Order returned")
    link("f1", "start", "receive")
    link("f2", "receive", "stock")
    link("f3", "stock", "reserve", "Yes")
    link("f4", "stock", "replenish", "No")
    link("f5", "reserve", "merge")
    link("f6", "replenish", "merge")
    link("f7", "merge", "split")
    link("f8", "split", "pack")
    link("f9", "split", "schedule")
    link("f10", "pack", "join")
    link("f11", "schedule", "join")
    link("f12", "join", "ship")
    link("f13", "ship", "wait")
    link("f14", "wait", "delivered")
    link("f15", "wait", "returned")
    link("f16", "delivered", "done")
    link("f17", "returned", "back")
    link("m1", "order", "receive", "Order", "message")
    link("m2", "ship", "received", "Goods", "message")
    return flow
