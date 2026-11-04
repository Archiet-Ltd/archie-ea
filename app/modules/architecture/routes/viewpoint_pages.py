"""Viewpoint pages: each ArchiMate 3.2 viewpoint drawn from the live model.

  GET /architecture/viewpoints/            the viewpoints, with what each one holds today
  GET /architecture/viewpoints/<code>      one viewpoint: diagram, elements, relationships, gaps

They live under /architecture, not /archimate: /archimate/viewpoints is an older address that
redirects to the composer, and the two must not share a prefix.

A viewpoint page is a reading of the organisation's own model at the moment it is
opened, never a stored copy. Three choices follow from that:

* Gaps are computed on every request (missing element types, unconnected elements,
  elements with no description), so they never wait for a save or a nightly sweep and
  a link change shows at once.
* Each element carries the enterprise genome's facts about it (capability support,
  work packages, security controls), each with its citation id, so a reader can trace
  a statement to the record behind it.
* The page states when the facts were read (as_of). The genome's spec hash and each
  fact's citation id are carried in data attributes for tooling, never shown as text.

Reads are scoped to the signed-in organisation by TenantMixin; the genome slices take
the organisation id explicitly.
"""
from __future__ import annotations

import logging
import uuid

from flask import Blueprint, abort, render_template
from flask_login import login_required

from app.utils.tenant import current_organization_id

logger = logging.getLogger(__name__)

viewpoint_pages_bp = Blueprint("viewpoint_pages", __name__, url_prefix="/architecture/viewpoints")

# The viewpoints these pages draw. Others follow; an address outside this list is a 404, not a blank page.
PAGE_VIEWPOINTS = ("motivation", "layered", "requirements_realization")

MAX_ELEMENTS = 100
GENOME_DOMAINS = ("business", "implementation", "security")


def _builder():
    from app.modules.architecture.services.viewpoint_builder import get_viewpoint_builder

    return get_viewpoint_builder()


def _definition(slug):
    """The viewpoint definition for an address slug (the VIEWPOINTS key, e.g. "motivation")."""
    from app.config.archimate_viewpoints import VIEWPOINTS

    return VIEWPOINTS[slug]


def _fact_text(domain, node):
    """One sentence a reader can act on, for one genome node."""
    kind = node.get("kind")
    if domain == "business" and kind == "cap":
        n = len(node.get("supported_by") or [])
        return ("Supported by %d application%s" % (n, "" if n == 1 else "s")) if n else "No application supports this capability"
    if domain == "business" and kind == "app":
        return "Application in the portfolio"
    if domain == "implementation" and kind == "wp":
        status = (node.get("status") or "").replace("_", " ")
        return "Work package" + (" (%s)" % status if status else "")
    if domain == "implementation" and kind == "plateau":
        return "Plateau on the roadmap"
    if domain == "security" and kind == "control":
        return "Security control"
    return "%s %s" % (domain.capitalize(), kind or "fact")


def genome_facts(organization_id):
    """Map ArchiMate element id -> list of cited facts, plus what each slice reported.

    A slice that cannot be read is reported by name, never dropped silently.
    """
    from app.modules.genome.slices.registry import get_slice

    facts, sources = {}, []
    for domain in GENOME_DOMAINS:
        try:
            env = get_slice(domain, organization_id)
        except Exception as exc:  # a broken slice must not take the page down; it is shown as unread
            logger.warning("genome slice %s unreadable for org %s: %s", domain, organization_id, exc)
            sources.append({"domain": domain, "ok": False, "nodes": 0, "spec_hash": None, "as_of": None})
            continue
        sources.append({
            "domain": domain, "ok": True, "nodes": len(env.get("nodes") or []),
            "spec_hash": env.get("spec_hash"), "as_of": env.get("as_of"), "reason": env.get("reason"),
        })
        for node in env.get("nodes") or []:
            element_id = (node.get("provenance") or {}).get("archimate_element_id")
            if not isinstance(element_id, int):
                continue
            facts.setdefault(element_id, []).append({
                "citation": node.get("id"), "text": _fact_text(domain, node), "href": node.get("link"),
                "warn": domain == "business" and node.get("kind") == "cap" and not node.get("supported"),
            })
    return facts, sources


def _drawable(relationship):
    """A relationship in the shape ComposerRenderer draws: it joins ends by source_id/target_id.

    ViewpointRelationship.to_dict names the ends source/target, which the renderer does not
    read, so every line was dropped. Both spellings are kept so other readers of to_dict still work.
    """
    out = relationship.to_dict()
    out["source_id"] = relationship.source_id
    out["target_id"] = relationship.target_id
    return out


def readiness(viewpoint, definition):
    """The viewpoint's gaps, each with why it matters and what to do next. Computed live."""
    gaps = []
    present = {e.element_type for e in viewpoint.elements}
    allowed = list(getattr(definition, "allowed_elements", None) or [])
    missing = [t for t in allowed if t not in present]
    if viewpoint.elements and missing:
        gaps.append({
            "key": "missing-types", "severity": "medium",
            "title": "%d element type%s this viewpoint expects %s not in the model" % (
                len(missing), "" if len(missing) == 1 else "s", "is" if len(missing) == 1 else "are"),
            "why": "Without them the viewpoint cannot answer its stakeholders' concerns in full: " + ", ".join(missing) + ".",
            "next": "Add them in the composer, or import them from your current tool.",
        })
    linked = set()
    for r in viewpoint.relationships:
        linked.add(r.source_id)
        linked.add(r.target_id)
    loose = [e for e in viewpoint.elements if e.id not in linked]
    if loose and len(viewpoint.elements) > 1:
        gaps.append({
            "key": "unconnected", "severity": "high",
            "title": "%d element%s with no relationship in this viewpoint" % (len(loose), "" if len(loose) == 1 else "s"),
            "why": "An element nothing points to cannot be traced; impact questions skip it.",
            "next": "Open each one and relate it to the element it realises, serves or influences.",
        })
    bare = [e for e in viewpoint.elements if not (e.description or "").strip()]
    if bare:
        gaps.append({
            "key": "no-description", "severity": "low",
            "title": "%d element%s without a description" % (len(bare), "" if len(bare) == 1 else "s"),
            "why": "A reviewer cannot tell what the element means from its name alone.",
            "next": "Add one sentence saying what it is and who owns it.",
        })
    if len(viewpoint.elements) >= MAX_ELEMENTS:
        gaps.append({
            "key": "truncated", "severity": "medium",
            "title": "Showing the first %d elements" % MAX_ELEMENTS,
            "why": "The diagram is limited so it stays readable.",
            "next": "Use the table filter to find the rest, or split the model into smaller views.",
        })
    return gaps, {e.id for e in loose}


def _type_counts(organization_id):
    """Elements per ArchiMate type for one organisation, in one grouped query.

    None when the model cannot be read, so every card says so instead of showing 0.
    """
    from sqlalchemy import func

    from app import db
    from app.models.archimate_core import ArchiMateElement

    try:
        rows = (
            db.session.query(ArchiMateElement.type, func.count(ArchiMateElement.id))
            .filter(ArchiMateElement.organization_id == organization_id)
            .group_by(ArchiMateElement.type)
            .all()
        )
    except Exception as exc:  # an unreadable model must not take the index down; the cards say so
        logger.warning("viewpoint counts unreadable for org %s: %s", organization_id, exc)
        return None
    return {t: n for t, n in rows}


def _count(type_counts, definition):
    """Elements the viewpoint can show today: the same rule the builder applies, from the grouped counts."""
    if type_counts is None:
        return None
    allowed = list(definition.allowed_elements or [])
    total = sum(type_counts.values()) if "*" in allowed else sum(type_counts.get(t, 0) for t in allowed)
    return ("%d+" % MAX_ELEMENTS) if total >= MAX_ELEMENTS else total


def _composer_href(slug):
    """The composer opened on this viewpoint when the composer knows it, else the composer itself."""
    from flask import url_for

    from app.services.archimate_viewpoint_service import STANDARD_VIEWPOINTS

    if slug in STANDARD_VIEWPOINTS:
        return url_for("archimate.composer_page", viewpoint=slug)
    return url_for("archimate.composer_page")


@viewpoint_pages_bp.route("/", methods=["GET"])
@login_required
def index():
    """Every ArchiMate viewpoint, each card with what it holds in the model today."""
    if current_organization_id() is None:
        abort(403)
    from app.config.archimate_viewpoints import VIEWPOINTS

    type_counts = _type_counts(current_organization_id())
    cards = []
    for slug, definition in VIEWPOINTS.items():
        cards.append({
            "slug": slug, "name": definition.name, "purpose": definition.purpose,
            "layer": (definition.layer or "").replace("_", " ").capitalize(),
            "stakeholders": ", ".join((definition.typical_stakeholders or [])[:3]),
            "elements": _count(type_counts, definition),
            "open": slug in PAGE_VIEWPOINTS,
            "composer_href": _composer_href(slug),
        })
    cards.sort(key=lambda c: (not c["open"], c["name"]))
    return render_template("archimate/viewpoints/index.html", cards=cards)


@viewpoint_pages_bp.route("/<code>", methods=["GET"])
@login_required
def detail(code):
    if code not in PAGE_VIEWPOINTS:
        abort(404)
    organization_id = current_organization_id()
    if organization_id is None:
        abort(403)
    from app.modules.genome.slices.registry import element_page

    definition = _definition(code)
    try:
        vp = _builder().build_viewpoint(definition.code, max_elements=MAX_ELEMENTS)
    except Exception as exc:  # shown as a stated error with a next step, never a bare server error
        reference = uuid.uuid4().hex[:8]
        logger.exception("viewpoint %s could not be built for org %s (reference %s)", code, organization_id, reference)
        return render_template("archimate/viewpoints/error.html", code=code, definition=definition,
                               reference=reference), 500
    facts, sources = genome_facts(organization_id)
    gaps, loose_ids = readiness(vp, definition)

    names = {e.id: e.name for e in vp.elements}
    degree = {}
    for r in vp.relationships:
        degree[r.source_id] = degree.get(r.source_id, 0) + 1
        degree[r.target_id] = degree.get(r.target_id, 0) + 1
    elements = [{
        "id": e.id, "name": e.name, "href": element_page(e.id), "description": e.description,
        "type": e.element_type, "layer": (e.layer or "").capitalize(), "relationships": degree.get(e.id, 0),
        "facts": facts.get(e.id, []), "unconnected": e.id in loose_ids,
    } for e in vp.elements]
    relationships = [{
        "id": r.id, "type": r.relationship_type, "name": r.name,
        "source": names.get(r.source_id), "source_href": element_page(r.source_id),
        "target": names.get(r.target_id), "target_href": element_page(r.target_id),
    } for r in vp.relationships]
    cited = sum(1 for e in elements if e["facts"])
    read = [s for s in sources if s["ok"]]
    return render_template(
        "archimate/viewpoints/detail.html",
        code=code, definition=definition, elements=elements, relationships=relationships, gaps=gaps,
        diagram={"elements": [e.to_dict() for e in vp.elements], "relationships": [_drawable(r) for r in vp.relationships]},
        stats={"elements": len(elements), "relationships": len(relationships), "unconnected": len(loose_ids), "cited": cited},
        sources=sources, as_of=(read[0]["as_of"] if read else None), warnings=list(vp.warnings or []),
    )
