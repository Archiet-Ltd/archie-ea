"""Risk service for TPM-013 — risk heat map CRUD and grid data."""
from app.services.archimate_backbone import sync_archimate_element
from app import db
from app.models.risk import Risk, RiskStatus
from app.models.risk_entity_link import ENTITY_TYPES, RiskEntityLink
from app.models.risk_score_history import SCORE_KINDS, RiskScoreHistory


def get_heat_map_data(solution_id=None):
    """Return 5×5 grid with risks placed at (likelihood, impact) positions.

    Returns a dict: {grid: [[...5 rows of 5 cols...]], risks: [...all risk dicts...]}
    Each cell is a list of risk dicts for risks that fall at that position.
    Row index 0 = likelihood 5 (top), col index 0 = impact 1 (left).
    """
    q = Risk.query
    if solution_id is not None:
        q = q.filter_by(solution_id=solution_id)
    all_risks = q.all()

    # Build 5×5 grid indexed [likelihood 1-5][impact 1-5]
    grid = {item: {i: [] for i in range(1, 6)} for item in range(1, 6)}
    for risk in all_risks:
        item = max(1, min(5, risk.likelihood))
        i = max(1, min(5, risk.impact))
        grid[item][i].append(risk.to_dict())

    # Convert to list-of-lists (row 0 = likelihood 5 at top)
    grid_list = []
    for likelihood in range(5, 0, -1):
        row = []
        for impact in range(1, 6):
            row.append(grid[likelihood][impact])
        grid_list.append(row)

    return {
        "grid": grid_list,
        "risks": [r.to_dict() for r in all_risks],
    }


def create_risk(solution_id, title, description, likelihood, impact, owner, mitigation_plan):
    """Create and persist a new Risk. Returns the saved Risk instance."""
    risk = Risk(
        solution_id=solution_id,
        title=title,
        description=description,
        likelihood=int(likelihood),
        impact=int(impact),
        owner=owner,
        mitigation_plan=mitigation_plan,
        status=RiskStatus.OPEN,
    )
    db.session.add(risk)
    sync_archimate_element(risk)
    db.session.commit()
    return risk


def update_risk_status(risk_id, status):
    """Update status of an existing Risk. Returns the updated Risk."""
    risk = Risk.query.get_or_404(risk_id)
    risk.status = RiskStatus(status)
    db.session.commit()
    return risk


# H2: full edit/delete were missing entirely -- the register could create a
# risk and flip its status, nothing else. The Description and Mitigation
# plan captured at creation were consequently write-only: nothing after
# creation ever read them back.
_EDITABLE_FIELDS = ("title", "description", "likelihood", "impact", "owner", "mitigation_plan")


def update_risk(risk_id, **fields):
    """Full edit of an existing Risk. Only keys in _EDITABLE_FIELDS are applied;
    unknown keys are ignored rather than raising, so a caller can pass a whole
    form payload safely."""
    risk = Risk.query.get_or_404(risk_id)
    for key in _EDITABLE_FIELDS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key in ("likelihood", "impact"):
                value = int(value)
            setattr(risk, key, value)
    db.session.commit()
    return risk


def delete_risk(risk_id):
    """Delete a Risk and its entity links (cascade). Does not touch the
    ArchiMate mirror row -- archimate_element_id is SET NULL on delete per
    the FK's ondelete, consistent with every other motivation-entity delete
    in this codebase."""
    risk = Risk.query.get_or_404(risk_id)
    db.session.delete(risk)
    db.session.commit()


# --- H1: entity links -------------------------------------------------

def list_risk_links(risk_id):
    return RiskEntityLink.query.filter_by(risk_id=risk_id).order_by(RiskEntityLink.id).all()


def _load_linkable_entity(entity_type, entity_id):
    """Load an Application/Solution/Programme/Constraint row, tenant-scoped.

    Returns ``None`` both when ``entity_id`` does not exist at all and when
    it belongs to another organisation: the tenant-isolation ORM filter
    (app/middleware/tenant_isolation.py's do_orm_execute listener) makes
    those two cases indistinguishable by construction, which is the point --
    a caller must not learn that a row exists in an organisation it cannot
    see. ``.filter_by(...).first()`` rather than ``.get()``: ``Query.get()``
    can be satisfied from the identity map without re-issuing a SELECT, which
    would bypass the tenant filter for an entity already loaded elsewhere in
    the same session.
    """
    if entity_type == "application":
        from app.models.application_portfolio import ApplicationComponent
        return ApplicationComponent.query.filter_by(id=entity_id).first()
    if entity_type == "solution":
        from app.models.solution_models import Solution
        return Solution.query.filter_by(id=entity_id).first()
    if entity_type == "programme":
        from app.models.strategic import StrategicInitiative
        return StrategicInitiative.query.filter_by(id=entity_id).first()
    if entity_type == "constraint":
        from app.models.solution_architect_models import SolutionConstraint
        return SolutionConstraint.query.filter_by(id=entity_id).first()
    return None


def add_risk_link(risk_id, entity_type, entity_id):
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPES}, got {entity_type!r}")
    Risk.query.get_or_404(risk_id)  # 404s cleanly if the risk does not exist
    if _load_linkable_entity(entity_type, entity_id) is None:
        # Covers both "no such row" and "that row belongs to another
        # organisation" -- refused identically, so a caller learns nothing
        # about what exists outside its own tenant.
        raise ValueError(
            f"{entity_type} {entity_id} was not found in this organisation"
        )
    existing = RiskEntityLink.query.filter_by(
        risk_id=risk_id, entity_type=entity_type, entity_id=entity_id
    ).first()
    if existing:
        return existing
    link = RiskEntityLink(risk_id=risk_id, entity_type=entity_type, entity_id=int(entity_id))
    db.session.add(link)
    db.session.commit()
    return link


def remove_risk_link(risk_id, link_id):
    link = RiskEntityLink.query.filter_by(id=link_id, risk_id=risk_id).first_or_404()
    db.session.delete(link)
    db.session.commit()


def risks_linked_to(entity_type, entity_id):
    """The Risk rows linked to one Application/Solution/Programme, as ORM
    objects -- the shared query behind links_for_entity's dicts and any
    other reader (e.g. the solution risk tab's GET list) that needs the rows
    themselves rather than their serialised form."""
    links = RiskEntityLink.query.filter_by(entity_type=entity_type, entity_id=entity_id).all()
    if not links:
        return []
    risk_ids = [link.risk_id for link in links]
    return Risk.query.filter(Risk.id.in_(risk_ids)).order_by(Risk.id).all()


def links_for_entity(entity_type, entity_id):
    """Risks linked to one Application/Solution/Programme -- the parent-side
    read for "Linked risks" sections on those entities' own detail pages."""
    return [r.to_dict() for r in risks_linked_to(entity_type, entity_id)]


# --- Inherent/residual scores, stored with history ------------------------

def set_risk_score(risk_id, score_kind, likelihood, impact, recorded_by_id=None):
    """Record an inherent or residual likelihood/impact score for a risk.

    Stores the score on the Risk row itself -- the score is stored, not only
    displayed -- and appends one RiskScoreHistory row per actual
    change. Calling this again with the same (likelihood, impact) updates
    nothing and adds no history row, matching the idempotent-on-no-change
    shape the rest of this service already uses (add_risk_link above).
    """
    if score_kind not in SCORE_KINDS:
        raise ValueError(f"score_kind must be one of {SCORE_KINDS}, got {score_kind!r}")
    # .filter_by(...).first_or_404() rather than .get_or_404(risk_id): Query.get()
    # can be satisfied from the identity map without re-issuing a SELECT, which
    # would bypass the tenant filter for a Risk already loaded (by another
    # organisation's request/session) -- the same reasoning as
    # _load_linkable_entity above.
    risk = Risk.query.filter_by(id=risk_id).first_or_404()
    likelihood, impact = int(likelihood), int(impact)
    likelihood_attr, impact_attr = f"{score_kind}_likelihood", f"{score_kind}_impact"
    changed = (
        getattr(risk, likelihood_attr) != likelihood
        or getattr(risk, impact_attr) != impact
    )
    setattr(risk, likelihood_attr, likelihood)
    setattr(risk, impact_attr, impact)
    if changed:
        db.session.add(RiskScoreHistory(
            risk_id=risk.id,
            score_kind=score_kind,
            likelihood=likelihood,
            impact=impact,
            recorded_by_id=recorded_by_id,
        ))
    db.session.commit()
    return risk


def risk_score_history(risk_id, score_kind=None):
    """History rows for one risk, oldest first. Optionally filtered by kind."""
    # See set_risk_score above for why this is .filter_by(...).first_or_404()
    # rather than .get_or_404(risk_id).
    Risk.query.filter_by(id=risk_id).first_or_404()
    query = RiskScoreHistory.query.filter_by(risk_id=risk_id)
    if score_kind is not None:
        query = query.filter_by(score_kind=score_kind)
    return query.order_by(RiskScoreHistory.recorded_at, RiskScoreHistory.id).all()


# --- Ranked risk list: exposure comes from the one impact engine ------------

RANKING_RULE = (
    "Ranked by the elements each risk reaches, then by recorded risk score; "
    "risks with no linked elements follow, ordered by recorded score."
)
_TOUCHES_SHOWN = 5


def _plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def ranked_risks():
    """Every risk of the caller's organisation, ranked by exposure.

    Exposure is how many elements the risk reaches, read from
    ``IntelligenceQueryService.set_impact`` (the one blast-radius answer) in
    one call per 25 distinct seed elements, never one per risk: a risk's seeds
    are its own ArchiMate element plus the element of every application it is
    linked to. Ties are broken by recorded score, then title, then id. A risk
    with no seed, or whose seeds connect to nothing, is ranked by its recorded
    score and says so; a risk whose
    exposure cannot be worked out carries ``exposure`` None and a reason.
    """
    from app.middleware.tenant_context import current_org_id
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.intelligence.services.query_service import (
        SET_IMPACT_MAX_SEEDS,
        IntelligenceQueryService,
    )

    org_id = current_org_id()
    if org_id is None:
        return {"rows": [], "total": 0, "rule": RANKING_RULE}
    risks = db.session.execute(
        db.select(Risk).where(Risk.organization_id == org_id).order_by(Risk.id)
    ).scalars().all()
    links = db.session.execute(
        db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_id).where(
            RiskEntityLink.organization_id == org_id, RiskEntityLink.entity_type == "application"
        )
    ).all()
    components = {}
    if links:
        components = {
            cid: (name, element_id)
            for cid, name, element_id in db.session.execute(
                db.select(
                    ApplicationComponent.id, ApplicationComponent.name,
                    ApplicationComponent.archimate_element_id,
                ).where(
                    ApplicationComponent.organization_id == org_id,
                    ApplicationComponent.id.in_(sorted({entity_id for _, entity_id in links})),
                )
            ).all()
        }
    apps_by_risk = {}
    for risk_id, component_id in links:
        if component_id in components:
            apps_by_risk.setdefault(risk_id, []).append(component_id)

    seeds_by_risk = {}
    unlinked_apps = set()   # risks with an application whose element pointer is empty
    for risk in risks:
        seeds = []
        if risk.archimate_element_id is not None:
            seeds.append(risk.archimate_element_id)
        for component_id in sorted(apps_by_risk.get(risk.id, [])):
            element_id = components[component_id][1]
            if element_id is None:
                unlinked_apps.add(risk.id)
            elif element_id not in seeds:
                seeds.append(element_id)
        seeds_by_risk[risk.id] = seeds

    distinct = sorted({s for seeds in seeds_by_risk.values() for s in seeds})
    reach = {}          # seed -> {element id: depth} of elements that seed reaches
    names = {}
    unresolved, truncated = set(), set()
    for start in range(0, len(distinct), SET_IMPACT_MAX_SEEDS):
        chunk = distinct[start:start + SET_IMPACT_MAX_SEEDS]
        answer = IntelligenceQueryService.set_impact(chunk, with_owner=False)
        unresolved.update(answer["unresolved_seed_ids"])
        names.update({int(k): v["name"] for k, v in answer["elements"].items()})
        if answer["truncated"]:
            truncated.update(chunk)
        for row in answer["rows"]:
            # Distance from each seed separately: row["depth"] is the nearest
            # over every seed, which would understate a far seed's distance.
            for relation in row["relations"]:
                seed_reach = reach.setdefault(relation["from_seed"], {})
                seed_reach[row["element_id"]] = min(
                    relation["depth"], seed_reach.get(row["element_id"], relation["depth"]))

    entries = []
    for risk in risks:
        seeds = seeds_by_risk[risk.id]
        reached = {}
        for seed in seeds:
            for element_id, depth in reach.get(seed, {}).items():
                # Only the risk's own element is left out: another application
                # mapped to the risk still counts when the risk already reached it.
                if element_id != risk.archimate_element_id:
                    reached[element_id] = min(depth, reached.get(element_id, depth))
        exposure, why = None, None
        if risk.id in unlinked_apps:
            why = "not_computed"
        elif not seeds:
            why = "no_links"
        elif any(s in unresolved for s in seeds) or any(s in truncated for s in seeds):
            why = "not_computed"
        elif reached:
            exposure = len(reached)
        else:
            # Its own element (every risk is mirrored as one) connects to nothing.
            why = "no_links"
        touches = [
            {"name": components[c][0], "kind": "application", "id": c, "element_id": components[c][1]}
            for c in sorted(apps_by_risk.get(risk.id, []))
        ]
        app_elements = {t["element_id"] for t in touches}
        # The risk's own mirrored element is never listed: it is the risk itself,
        # not something the risk touches (its page has no relationships to show).
        app_elements.add(risk.archimate_element_id)
        nearest = sorted(reached, key=lambda e: (reached[e], names.get(e, ""), e))
        touches += [{"name": names[e], "kind": "element", "id": e, "element_id": e}
                    for e in nearest if e in names and e not in app_elements][:_TOUCHES_SHOWN]
        entries.append({
            "risk": risk, "exposure": exposure, "why": why, "touches": touches,
            "score": risk.risk_score,
        })

    entries.sort(key=lambda e: (
        e["exposure"] is None,
        -(e["exposure"] or 0),
        -e["score"],
        (e["risk"].title or "").lower(),
        e["risk"].id,
    ))
    total = len(entries)
    rows = []
    for position, entry in enumerate(entries, start=1):
        risk = entry["risk"]
        scores = "likelihood %d of 5, impact %d of 5" % (risk.likelihood, risk.impact)
        if entry["exposure"] is not None:
            reason = "Ranked %d of %d: reaches %s, %s" % (
                position, total, _plural(entry["exposure"], "element"), scores)
        elif entry["why"] == "no_links":
            reason = "Ranked %d of %d: no linked elements recorded, ranked by its recorded score of %d (%s)" % (
                position, total, entry["score"], scores)
        else:
            reason = ("Ranked %d of %d: how far it reaches could not be worked out, "
                      "ranked by its recorded score of %d (%s)") % (position, total, entry["score"], scores)
        rows.append({
            "rank": position, "risk_id": risk.id, "title": risk.title,
            "status": risk.status.value, "risk_level": risk.risk_level,
            "likelihood": risk.likelihood, "impact": risk.impact, "risk_score": entry["score"],
            "exposure": entry["exposure"], "reason": reason, "touches": entry["touches"],
            "worked_out": entry["why"] != "not_computed",
        })
    return {"rows": rows, "total": total, "rule": RANKING_RULE}
