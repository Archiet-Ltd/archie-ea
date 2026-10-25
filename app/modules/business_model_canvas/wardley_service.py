"""The Wardley Map drawn as a map: where each component sits, and the lines between them.

The evolution stage of a component is the box it sits in. Its visibility is not typed
by anyone: it is the number of dependency steps between the component and the user's
need, read from the relationships the organisation already holds between the elements
on the map. A component with no recorded dependency path to the need is drawn on the
bottom row and listed as not yet linked.
"""

import textwrap

from app.models.archimate_core import ArchiMateRelationship

STAGES = [
    ("genesis", "Genesis"),
    ("custom-built", "Custom-built"),
    ("product", "Product (+rental)"),
    ("commodity", "Commodity (+utility)"),
]
_STAGE_INDEX = {key: i for i, (key, _title) in enumerate(STAGES)}

WIDTH = 1000
LEFT = 84
RIGHT = 16
TOP = 36
ROW = 92
BOTTOM = 58
STAGE_W = (WIDTH - LEFT - RIGHT) / len(STAGES)


def _lines(name, width):
    return textwrap.wrap(name, width=width, break_long_words=False) or [name]


def _node(note, kind, stage=None):
    return {
        "id": note["id"], "name": note["text"], "href": note["href"], "kind": kind,
        "stage": stage, "note": note, "deps": [], "depth": None,
    }


def plot(canvas, boxes):
    """The geometry of the map for one canvas, or None when there is nothing to draw.

    ``boxes`` is the page's notes by box key. Returns nodes (anchors and components, with x, y
    and label lines), edges, movement arrows, inertia bars, axis positions, and the rows of the
    text alternative.
    """
    users = [_node(n, "user") for n in boxes.get("user", [])]
    needs = [_node(n, "need") for n in boxes.get("user-need", [])]
    components = [_node(n, "component", key) for key, _t in STAGES for n in boxes.get(key, [])]
    movers = [(n, "movement") for n in boxes.get("movement", [])]
    resisters = [(n, "inertia") for n in boxes.get("inertia", [])]
    if not (users or needs or components):
        return None

    by_id = {n["id"]: n for n in users + needs + components}
    ids = set(by_id) | {n["id"] for n, _k in movers + resisters}
    links = set()
    if ids:
        rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.organization_id == canvas.organization_id,
            ArchiMateRelationship.source_id.in_(ids), ArchiMateRelationship.target_id.in_(ids),
        ).all()
        for r in rels:
            if r.source_id != r.target_id:
                links.add(tuple(sorted((r.source_id, r.target_id))))

    # Depth: dependency steps from the need, counting the need as the first row under the user.
    graph = {i: set() for i in ids}
    for a, b in links:
        graph[a].add(b)
        graph[b].add(a)
    frontier = [n["id"] for n in needs]
    seen = set(frontier)
    for n in needs:
        n["depth"] = 0
    level = 0
    while frontier:
        level += 1
        nxt = []
        for i in frontier:
            for j in sorted(graph[i]):
                if j in seen or j not in by_id or by_id[j]["kind"] != "component":
                    continue
                seen.add(j)
                by_id[j]["depth"] = level
                nxt.append(j)
        frontier = nxt
    for c in components:
        c["linked"] = c["depth"] is not None
    reached = [c["depth"] for c in components if c["linked"]]
    unlinked_row = (max(reached) if reached else 0) + 1
    for c in components:
        if not c["linked"]:
            c["depth"] = unlinked_row
    # Rows: 0 user, 1 need, a component at depth d on row d + 1.
    row_count = max(2, (max([c["depth"] for c in components] or [0]) + 2))

    def y_of(row):
        return TOP + row * ROW

    # Anchors: users on row 0, needs on row 1, spread across the plot.
    span = WIDTH - LEFT - RIGHT
    for group, row in ((users, 0), (needs, 1)):
        for k, n in enumerate(group):
            n["x"] = LEFT + span * (k + 1) / (len(group) + 1)
            n["y"] = y_of(row)
            n["label"] = _lines(n["name"], 30)

    # Components: the column of their stage; several in one cell share it side by side.
    cell = {}
    for c in components:
        cell.setdefault((c["stage"], c["depth"]), []).append(c)
    for (stage, depth), group in cell.items():
        x0 = LEFT + _STAGE_INDEX[stage] * STAGE_W
        width = int(STAGE_W / len(group) / 6.6)
        for k, c in enumerate(group):
            c["x"] = x0 + STAGE_W * (k + 1) / (len(group) + 1)
            c["y"] = y_of(depth + 1)
            c["label"] = _lines(c["name"], max(12, min(30, width)))

    edges = []
    for a, b in sorted(links):
        na, nb = by_id.get(a), by_id.get(b)
        if not na or not nb:
            continue
        top, bottom = (na, nb) if (na["depth"] or 0) <= (nb["depth"] or 0) else (nb, na)
        if na["kind"] == "user" or nb["kind"] == "user":
            continue
        edges.append({"x1": top["x"], "y1": top["y"], "x2": bottom["x"], "y2": bottom["y"], "from": top["name"], "to": bottom["name"]})
        top["deps"].append(bottom["name"])
    for u in users:
        for n in needs:
            edges.append({"x1": u["x"], "y1": u["y"], "x2": n["x"], "y2": n["y"], "from": u["name"], "to": n["name"]})
            u["deps"].append(n["name"])

    def attached(note):
        for c in components:
            if tuple(sorted((c["id"], note["id"]))) in links:
                return c
        return None

    arrows, bars, loose = [], [], []
    for note, kind in movers + resisters:
        c = attached(note)
        if c is None:
            loose.append({"kind": kind, "name": note["text"], "href": note["href"], "note": note})
            continue
        if kind == "movement":
            if c["stage"] == STAGES[-1][0]:
                loose.append({"kind": kind, "name": note["text"], "href": note["href"], "note": note, "component": c["name"]})
                continue
            arrows.append({"x1": c["x"] + 12, "y": c["y"], "x2": c["x"] + STAGE_W * 0.62, "name": note["text"], "href": note["href"], "component": c["name"], "note": note})
        else:
            bars.append({"x": c["x"] + STAGE_W * 0.2, "y1": c["y"] - 16, "y2": c["y"] + 16, "name": note["text"], "href": note["href"], "component": c["name"], "note": note})

    height = y_of(row_count - 1) + BOTTOM
    table = []
    for n in users:
        table.append({"stage": "User", "visibility": "Top of the map", "deps": ", ".join(n["deps"]) or "—", "name": n["name"], "href": n["href"], "note": n["note"], "box": "user"})
    for n in needs:
        table.append({"stage": "User need", "visibility": "Visible to the user", "deps": ", ".join(sorted(n["deps"])) or "No component depends on it yet", "name": n["name"], "href": n["href"], "note": n["note"], "box": "user-need"})
    titles = dict(STAGES)
    for c in sorted(components, key=lambda c: (c["depth"], _STAGE_INDEX[c["stage"]], c["name"])):
        vis = "Not yet linked to the need" if not c["linked"] else ("Directly serves the need" if c["depth"] == 1 else f"{c['depth']} steps from the need")
        table.append({"stage": titles[c["stage"]], "visibility": vis, "deps": ", ".join(sorted(c["deps"])) or "Depends on nothing else recorded", "name": c["name"], "href": c["href"], "note": c["note"], "box": c["stage"]})
    for a in arrows:
        table.append({"stage": "Movement", "visibility": "—", "deps": f"Moves {a['component']} one stage towards commodity", "name": a["name"], "href": a["href"], "note": a["note"], "box": "movement"})
    for b in bars:
        table.append({"stage": "Inertia", "visibility": "—", "deps": f"Resists change to {b['component']}", "name": b["name"], "href": b["href"], "note": b["note"], "box": "inertia"})
    for l in loose:
        text = f"{l['component']} is already a commodity" if l.get("component") else "Not tied to a component yet. Link it to one in the model."
        table.append({"stage": l["kind"].capitalize(), "visibility": "—", "deps": text, "name": l["name"], "href": l["href"], "note": l["note"], "box": l["kind"]})

    return {
        "width": WIDTH, "height": height, "left": LEFT, "top": TOP, "stage_w": STAGE_W, "plot_bottom": y_of(row_count - 1) + 40,
        "stages": [{"key": k, "title": t, "x": LEFT + i * STAGE_W, "mid": LEFT + (i + 0.5) * STAGE_W} for i, (k, t) in enumerate(STAGES)],
        "users": users, "needs": needs, "components": components,
        "edges": edges, "arrows": arrows, "bars": bars, "loose": loose, "table": table,
    }
