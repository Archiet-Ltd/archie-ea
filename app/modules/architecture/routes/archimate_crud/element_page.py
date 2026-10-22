"""What the element detail page says about one element, worked out once in the view.

The page answers "what is this, and what depends on it?" on its first screen. Every figure here comes from the
one impact walk the Impact analysis page also reads (``ArchiMateImpactService.analyze_impact``, which delegates
to ``IntelligenceQueryService.cross_layer_impact``), so the two pages cannot disagree. Nothing in this module
runs its own graph query.

Everything a person reads is built here as plain words, so the template never prints a model object, a list or
a database value.
"""

from __future__ import annotations

# Relationship, in words, as seen from the element on the page. (outgoing, incoming)
RELATIONSHIP_WORDS = {
    "Composition": ("Is made up of", "Is part of"),
    "Aggregation": ("Groups", "Is grouped in"),
    "Assignment": ("Is assigned to", "Has assigned"),
    "Realization": ("Realizes", "Is realized by"),
    "Serving": ("Serves", "Is served by"),
    "Access": ("Accesses", "Is accessed by"),
    "Influence": ("Influences", "Is influenced by"),
    "Triggering": ("Triggers", "Is triggered by"),
    "Flow": ("Sends to", "Receives from"),
    "Specialization": ("Is a kind of", "Has kinds"),
    "Association": ("Is associated with", "Is associated with"),
}

# One plain sentence per type, then the groups the "what this answers" card builds its lines from.
# A group is (neighbour types, lead-in). "*" matches any type still unclaimed. Only types the
# person already sees in the connections table are named; nothing is invented.
_GOAL_TYPES = ("Goal", "Outcome", "Driver", "Requirement", "Principle")
PROFILES = {
    "ApplicationComponent": {
        "subtitle": "An application the business runs.",
        "groups": [
            (("Node", "Device", "SystemSoftware", "TechnologyService"), "Runs on"),
            (("Capability", "BusinessFunction", "BusinessProcess"), "Supports"),
            (("ApplicationComponent", "ApplicationService", "ApplicationInterface"), "Connects to"),
            (_GOAL_TYPES, "Works towards"),
        ],
    },
    "Capability": {
        "subtitle": "Something the business can do.",
        "groups": [
            (("ApplicationComponent", "ApplicationService"), "Delivered by"),
            (_GOAL_TYPES, "Serves"),
            (("WorkPackage", "Gap", "Deliverable"), "Changed by"),
        ],
    },
    "Node": {
        "subtitle": "Infrastructure that runs software.",
        "groups": [
            (("ApplicationComponent", "SystemSoftware", "Artifact"), "Runs"),
            (("Node", "Device", "TechnologyService", "CommunicationNetwork"), "Linked to"),
        ],
    },
    "WorkPackage": {
        "subtitle": "Work that delivers change.",
        "groups": [
            (("Plateau",), "Sits in"),
            (_GOAL_TYPES, "Works towards"),
            (("*",), "Delivers"),
        ],
    },
    "Plateau": {
        "subtitle": "A stage on the roadmap.",
        "groups": [
            (("WorkPackage", "Deliverable", "Gap"), "Contains"),
            (("Plateau",), "Sits alongside"),
        ],
    },
    "Goal": {
        "subtitle": "An outcome the business wants.",
        "groups": [
            (("Capability",), "Served by"),
            (("WorkPackage", "Deliverable"), "Served by"),
            (("Driver", "Stakeholder", "Assessment"), "Driven by"),
        ],
    },
}

_SOURCE_RELIES_ON_TARGET = {"Composition", "Aggregation", "Access", "Specialization"}
_STATE_WORDS = {"Baseline": "As-is", "Target": "To-be", "Transition": "Transition"}
_SHOWN_PER_LINE = 5


def _layer_key(layer, layer_aliases):
    """The URL layer key for a stored layer spelling, or None when the page would not exist."""
    stored = (layer or "").strip().lower()
    for key, spellings in layer_aliases.items():
        if stored == key or stored in spellings:
            return key
    return None


def _noun(element_type, user, plain_name, many):
    word = (plain_name(element_type, user) or element_type or "element").lower()
    if not many:
        return word
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    return word + ("es" if word.endswith(("s", "x")) else "s")


def subtitle_for(element_type, user, plain_name):
    profile = PROFILES.get(element_type)
    if profile:
        return profile["subtitle"]
    return "%s in your architecture model." % _noun(element_type, user, plain_name, False).capitalize()


def relationship_words(rel_type, direction):
    key = (rel_type or "").strip().capitalize()
    words = RELATIONSHIP_WORDS.get(key)
    if not words:
        return (rel_type or "Is linked to").strip()
    return words[0] if direction == "outgoing" else words[1]


def _row(item, direction, *, user, plain_name, plain_layer, layer_aliases, href_for):
    layer_key = _layer_key(item.get("layer"), layer_aliases)
    kind = item.get("type") or ""
    return {
        "id": item.get("id"),
        "name": item.get("name") or "Unnamed",
        "type": kind,
        "type_label": plain_name(kind, user) if kind else "Element",
        "layer_label": plain_layer(layer_key, user) if layer_key else (item.get("layer") or "Other"),
        "direction": direction,
        "relationship": relationship_words(item.get("via_relationship"), direction),
        "relationship_raw": item.get("via_relationship") or "",
        "href": href_for(layer_key, kind, item.get("id")) if layer_key and kind and item.get("id") else None,
    }


def plateau_facts(element, element_type, ae_row, *, href_for):
    """The state badge and the stage facts, as words. Never the plateau object itself.

    ``ArchiMateElement.plateau`` is not the TOGAF state string: ``Plateau.archimate_element`` hangs a list of
    Plateau rows on it. The state string is ``togaf_plateau``.
    """
    state = None
    if ae_row is not None:
        raw = getattr(ae_row, "togaf_plateau", None)
        if isinstance(raw, str):
            state = _STATE_WORDS.get(raw, raw)

    stage_row = None
    plateau_link = None
    if element_type == "Plateau":
        if element.__class__.__name__ == "Plateau":
            stage_row = element
        else:
            linked = getattr(element, "plateau", None)
            if isinstance(linked, (list, tuple)) and linked:
                stage_row = linked[0]
    else:
        linked = getattr(element, "plateau", None)
        if linked is not None and not isinstance(linked, (str, list, tuple)) and getattr(linked, "name", None):
            ae_target = getattr(linked, "archimate_element_id", None)
            plateau_link = (linked.name, href_for("implementation", "Plateau", ae_target) if ae_target else None)

    stage = None
    if stage_row is not None:
        follows = getattr(stage_row, "baseline_plateau", None)
        follows_link = None
        if follows is not None and getattr(follows, "name", None):
            ae_target = getattr(follows, "archimate_element_id", None)
            follows_link = (follows.name, href_for("implementation", "Plateau", ae_target) if ae_target else None)
        stage = {
            "order": getattr(stage_row, "sequence_order", None),
            "target_date": getattr(stage_row, "target_date", None),
            "follows": follows_link,
        }
    return {"state": state, "plateau_link": plateau_link, "stage": stage}


def build_connections(ae_id, relationships, *, user, plain_name, plain_layer, layer_aliases, href_for):
    """Depends-on, used-by and the rest, from the one impact walk the Impact analysis page reads."""
    from app.services.archimate_impact_service import ArchiMateImpactService

    out = {"available": False, "depends_on": [], "used_by": [], "other": [], "beyond": 0, "total": 0,
           "by_layer": []}
    if not ae_id:
        return out
    analysis = ArchiMateImpactService().analyze_impact(ae_id)
    if analysis.get("error"):
        return out
    out["available"] = True
    kw = dict(user=user, plain_name=plain_name, plain_layer=plain_layer, layer_aliases=layer_aliases,
              href_for=href_for)
    seen = set()
    for item in analysis.get("direct_impacts") or []:
        direction = item.get("direction") or "outgoing"
        # The walk's "outgoing" side is what is affected when this element changes or fails (a server serves,
        # a component realizes): those are the elements that USE this one. Its "incoming" side is what this
        # element relies on. A person asking "what does it depend on" wants the incoming side.
        # Containing, grouping, accessing and specialising run the other way: the source relies on the target.
        source_relies = (item.get("via_relationship") or "").strip().capitalize() in _SOURCE_RELIES_ON_TARGET
        goes_to_used_by = (direction == "outgoing") != source_relies
        out["used_by" if goes_to_used_by else "depends_on"].append(_row(item, direction, **kw))
        seen.add(item.get("id"))
    out["beyond"] = len(analysis.get("indirect_impacts") or [])
    out["total"] = analysis.get("total_impacted", 0)
    by_layer = sorted((analysis.get("by_layer") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    for layer, count in by_layer:
        key = _layer_key(layer, layer_aliases)
        out["by_layer"].append((plain_layer(key, user) if key else layer, count))
    # Links the impact walk does not follow still belong on the page: nothing is dropped.
    for rel in relationships or []:
        direction = rel.get("direction") or "outgoing"
        other = rel.get("target") if direction == "outgoing" else rel.get("source")
        if not other or other.get("id") in seen:
            continue
        seen.add(other.get("id"))
        item = dict(other, via_relationship=rel.get("type"))
        out["other"].append(_row(item, direction, **kw))
    return out


def answer_lines(element_type, connections, *, user, plain_name):
    """Lines for the "what this answers" card: lead-in, count, noun, then the names as links."""
    rows = connections["depends_on"] + connections["used_by"] + connections["other"]
    profile = PROFILES.get(element_type, {"groups": []})
    claimed = set()
    lines = []
    for types, lead in profile["groups"]:
        match = [r for r in rows if r["id"] not in claimed and ("*" in types or r["type"] in types)]
        if not match:
            continue
        claimed.update(r["id"] for r in match)
        lines.append(_line(lead, match, user, plain_name))
    rest = [r for r in rows if r["id"] not in claimed]
    by_type = {}
    for r in rest:
        by_type.setdefault(r["type"], []).append(r)
    for kind, match in by_type.items():
        lines.append(_line("Also connected to", match, user, plain_name))
    return lines


def _line(lead, match, user, plain_name):
    kinds = {r["type"] for r in match}
    noun = _noun(next(iter(kinds)), user, plain_name, len(match) != 1) if len(kinds) == 1 else (
        "element" if len(match) == 1 else "elements")
    return {
        "lead": lead,
        "count": len(match),
        "noun": noun,
        "items": match[:_SHOWN_PER_LINE],
        "more": max(0, len(match) - _SHOWN_PER_LINE),
    }
