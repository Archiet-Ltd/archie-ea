"""Risk service for TPM-013 — risk heat map CRUD and grid data."""
from app.services.archimate_backbone import sync_archimate_element
from app import db
from app.services import result_footer_service
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
    all_risks = q.order_by(Risk.id).all()

    # Build 5×5 grid indexed [likelihood 1-5][impact 1-5]
    grid = {item: {i: [] for i in range(1, 6)} for item in range(1, 6)}
    for risk in all_risks:
        _kind, likelihood, impact = risk._score_basis  # the cell is the pair the score and band come from
        item = max(1, min(5, int(likelihood or 1)))
        i = max(1, min(5, int(impact or 1)))
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


def get_risk(organization_id, risk_id):
    """One risk of one organisation, or None. Names the organisation in the statement, so a caller
    with no request (the chat's worker, a command) is fenced the same as a request."""
    return db.session.execute(
        db.select(Risk).where(Risk.id == risk_id, Risk.organization_id == organization_id)
    ).scalar_one_or_none()


def _own_organization(organization_id):
    if organization_id is not None:
        return organization_id
    from app.middleware.tenant_context import current_org_id
    return current_org_id()


def create_risk(solution_id, title, description, likelihood, impact, owner, mitigation_plan,
                links=(), organization_id=None):
    """Create and persist a new Risk with what it touches, in one transaction.

    ``links`` is a list of ``(entity_type, entity_id)`` pairs (or dicts with those keys). A target
    outside the organisation raises ``ValueError`` and nothing is written. Returns the saved Risk.
    """
    organization_id = _own_organization(organization_id)
    pairs = []
    for link in links or ():
        if isinstance(link, dict):
            link = (link.get("entity_type"), link.get("entity_id"))
        pairs.append(tuple(link))
    for entity_type, entity_id in pairs:
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"entity_type must be one of {ENTITY_TYPES}, got {entity_type!r}")
        try:
            int(entity_id)
        except (TypeError, ValueError):
            raise ValueError(f"{entity_type} needs a numeric entity_id")
    if solution_id is not None:
        pairs.append(("solution", solution_id))
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
    if organization_id is not None:
        risk.organization_id = organization_id
    try:
        db.session.add(risk)
        db.session.flush()
        for entity_type, entity_id in pairs:
            add_risk_link(risk.id, entity_type, int(entity_id), organization_id=organization_id,
                          commit=False)
        sync_archimate_element(risk)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
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
    sync_archimate_element(risk)
    _rename_mirror(risk)
    db.session.commit()
    return risk


def _rename_mirror(risk):
    """Keep the risk's Assessment element carrying the risk's current title, so a search by the new
    title finds it and the old title no longer does. Fenced to the risk's own organisation."""
    if risk.archimate_element_id is None or risk.organization_id is None or not risk.title:
        return
    from app.models import ArchiMateElement
    from app.services.archimate_backbone import fit_name

    element = db.session.execute(
        db.select(ArchiMateElement).where(ArchiMateElement.id == risk.archimate_element_id,
                                          ArchiMateElement.organization_id == risk.organization_id)
    ).scalar_one_or_none()
    if element is not None:
        element.name = fit_name(risk.title)


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
    if entity_type == "capability":
        from app.models.business_capabilities import BusinessCapability
        return BusinessCapability.query.filter_by(id=entity_id).first()
    if entity_type == "archimate_element":
        from app.models import ArchiMateElement
        return ArchiMateElement.query.filter_by(id=entity_id).first()
    return None


def add_risk_link(risk_id, entity_type, entity_id, organization_id=None, commit=True):
    """Link a risk to one record. A caller with no request (the demo seed) names the organisation and the
    record must belong to it; ``commit=False`` leaves the transaction to that caller."""
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPES}, got {entity_type!r}")
    if organization_id is not None:
        if get_risk(organization_id, risk_id) is None:
            raise ValueError(f"risk {risk_id} was not found in this organisation")
    else:
        Risk.query.get_or_404(risk_id)  # 404s cleanly if the risk does not exist
    target = _load_linkable_entity(entity_type, entity_id)
    if target is None:
        # Covers both "no such row" and "that row belongs to another
        # organisation" -- refused identically, so a caller learns nothing
        # about what exists outside its own tenant.
        raise ValueError(
            f"{entity_type} {entity_id} was not found in this organisation"
        )
    if organization_id is not None and getattr(target, "organization_id", None) != organization_id:
        raise ValueError(
            f"{entity_type} {entity_id} was not found in this organisation"
        )
    existing = RiskEntityLink.query.filter_by(
        risk_id=risk_id, entity_type=entity_type, entity_id=entity_id
    ).first()
    if existing:
        return existing
    link = RiskEntityLink(risk_id=risk_id, entity_type=entity_type, entity_id=int(entity_id))
    if organization_id is not None:
        link.organization_id = organization_id
    db.session.add(link)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return link


def remove_risk_link(risk_id, link_id):
    link = RiskEntityLink.query.filter_by(id=link_id, risk_id=risk_id).first_or_404()
    db.session.delete(link)
    db.session.commit()


def risks_linked_to(entity_type, entity_id, organization_id=None):
    """The Risk rows linked to one Application/Solution/Programme, as ORM
    objects -- the shared query behind links_for_entity's dicts and any
    other reader (e.g. the solution risk tab's GET list) that needs the rows
    themselves rather than their serialised form. ``organization_id`` names the
    organisation in the statement for a caller with no request."""
    link_query = db.select(RiskEntityLink.risk_id).where(
        RiskEntityLink.entity_type == entity_type, RiskEntityLink.entity_id == entity_id)
    risk_query = db.select(Risk).where(Risk.id.in_(link_query)).order_by(Risk.id)
    if organization_id is not None:
        link_query = link_query.where(RiskEntityLink.organization_id == organization_id)
        risk_query = db.select(Risk).where(
            Risk.id.in_(link_query), Risk.organization_id == organization_id).order_by(Risk.id)
    return list(db.session.execute(risk_query).scalars().all())


def risk_ids_touching_elements(organization_id, element_ids):
    """Which risks touch which ArchiMate elements: ``{risk_id: [element_id, ...]}``.

    A risk touches an element when a link points at the record that element mirrors (an application,
    a capability, a solution or a programme) or at the element itself. The risk's own Assessment
    mirror is not a touch and is never returned here.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative

    wanted = {int(e) for e in element_ids or ()}
    if not wanted or organization_id is None:
        return {}
    found = {}

    def note(risk_id, element_id):
        bucket = found.setdefault(risk_id, [])
        if element_id not in bucket:
            bucket.append(element_id)

    for link_type, model in (("application", ApplicationComponent), ("capability", BusinessCapability),
                             ("solution", Solution), ("programme", StrategicInitiative)):
        mirror = getattr(model, "archimate_element_id", None)
        if mirror is None:
            continue
        mirrored = {
            record_id: element_id for record_id, element_id in db.session.execute(
                db.select(model.id, mirror).where(model.organization_id == organization_id,
                                                  mirror.in_(sorted(wanted)))
            ).all()
        }
        if not mirrored:
            continue
        for risk_id, record_id in db.session.execute(
            db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_id).where(
                RiskEntityLink.organization_id == organization_id,
                RiskEntityLink.entity_type == link_type,
                RiskEntityLink.entity_id.in_(sorted(mirrored)),
            ).order_by(RiskEntityLink.id)
        ).all():
            note(risk_id, mirrored[record_id])
    for risk_id, element_id in db.session.execute(
        db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_id).where(
            RiskEntityLink.organization_id == organization_id,
            RiskEntityLink.entity_type == "archimate_element",
            RiskEntityLink.entity_id.in_(sorted(wanted)),
        ).order_by(RiskEntityLink.id)
    ).all():
        note(risk_id, element_id)
    owned = set(db.session.execute(
        db.select(Risk.id).where(Risk.organization_id == organization_id, Risk.id.in_(list(found) or [-1]))
    ).scalars().all())
    return {risk_id: elements for risk_id, elements in found.items() if risk_id in owned}


def solution_risk_sources(solution_id):
    """Every risk a solution lists, as ``(canonical, legacy)``.

    ``canonical`` are the register's Risk rows linked to the solution;
    ``legacy`` are the superseded solution_risks rows the merge backfill has
    not yet retired (a retired row is excluded: its replacement is already in
    ``canonical``). The solution's risk list and its heat map both read this,
    so they cannot disagree about which risks the solution has.
    """
    from app.models.solution_lifecycle_models import SolutionRisk

    canonical = risks_linked_to("solution", solution_id)
    legacy = (
        SolutionRisk.query.filter_by(solution_id=solution_id)
        .filter(SolutionRisk.retired_into_risk_id.is_(None))
        .all()
    )
    return canonical, legacy


# Score bands for a probability x impact cell. Same thresholds as
# Risk.risk_level, so a cell and the badge on a risk in it use one scale.
RISK_BANDS = (
    ("critical", "Critical", 15),
    ("high", "High", 9),
    ("medium", "Medium", 5),
    ("low", "Low", 1),
)


def risk_band(score):
    """The band key for a probability x impact score."""
    for key, _label, floor in RISK_BANDS:
        if score >= floor:
            return key
    return "low"


def probability_impact_grid(placements):
    """The cells of the five by five risk matrix, the shape ``components/risk_matrix.html`` draws.

    ``placements`` is (probability, impact, title) for each risk. Five rows, impact 5 down to 1, each of five
    cells, probability 1 to 5, each a dict of likelihood, impact, count, band and titles.
    """
    cells = {}
    for likelihood, impact, title in placements:
        cells.setdefault((likelihood, impact), []).append(title)
    return [[{"likelihood": lk, "impact": im, "count": len(cells.get((lk, im), [])),
              "band": risk_band(lk * im), "titles": cells.get((lk, im), [])}
             for lk in range(1, 6)] for im in range(5, 0, -1)]


def register_matrix():
    """The risk matrix for every risk in the register, in the shape ``probability_impact_grid`` gives. The risks are
    placed by ``get_heat_map_data``, the one place a risk's cell is worked out."""
    placements = []
    for row_number, row in enumerate(get_heat_map_data()["grid"]):  # row 0 is likelihood 5
        for column_number, cell in enumerate(row):  # column 0 is impact 1
            placements.extend((5 - row_number, column_number + 1, risk["title"]) for risk in cell)
    return probability_impact_grid(placements)


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


_LINK_KINDS = ("capability", "solution", "programme")


def _link_url(kind, entity_id):
    """Where a linked capability, solution or programme opens, or None when the screen that
    owns it is not registered."""
    from flask import current_app, url_for

    endpoint, argument = {
        "capability": ("enterprise_crud.capability_fact_sheet", "capability_id"),
        "solution": ("solution_design.view_solution", "solution_id"),
        "programme": ("solution_design.programme_cockpit", "initiative_id"),
    }[kind]
    if endpoint not in current_app.view_functions:
        return None
    return url_for(endpoint, **{argument: entity_id})


def _other_links_by_risk(org_id):
    """For each risk, the capabilities, solutions and programmes it is linked to, as touches.

    A link whose target no longer exists, or belongs to another organisation, is left out:
    a risk is shown linked only to something a reader can open.
    """
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative

    rows = db.session.execute(
        db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_type, RiskEntityLink.entity_id).where(
            RiskEntityLink.organization_id == org_id, RiskEntityLink.entity_type.in_(_LINK_KINDS)
        ).order_by(RiskEntityLink.id)
    ).all()
    models = {"capability": BusinessCapability, "solution": Solution, "programme": StrategicInitiative}
    named = {}
    mirrors = {}   # (kind, id) -> the ArchiMate element that mirrors the linked record, if it has one
    for kind, model in models.items():
        ids = sorted({entity_id for _, k, entity_id in rows if k == kind})
        if ids:
            columns = [model.id, model.name]
            if hasattr(model, "archimate_element_id"):
                columns.append(model.archimate_element_id)
            for found in db.session.execute(db.select(*columns).where(model.organization_id == org_id, model.id.in_(ids))).all():
                named.setdefault(kind, {})[found[0]] = found[1]
                if len(found) > 2 and found[2] is not None:
                    mirrors[(kind, found[0])] = found[2]
    out = {}
    for risk_id, kind, entity_id in rows:
        name = named.get(kind, {}).get(entity_id)
        if name is not None:
            out.setdefault(risk_id, []).append({
                "name": name, "kind": kind, "id": entity_id, "element_id": None,
                "url": _link_url(kind, entity_id), "mirror_element_id": mirrors.get((kind, entity_id)),
            })
    return out
def _touch_link(touch):
    if touch["kind"] in _LINK_KINDS:
        return touch.get("url")
    if touch["kind"] == "application":
        return result_footer_service.page_link("unified_applications.application_fact_sheet", id=touch["id"])
    return result_footer_service.page_link("archimate.element_impact_graph_page", element_id=touch["element_id"])


_LINK_KINDS = ("capability", "solution", "programme")


def _link_url(kind, entity_id):
    """Where a linked capability, solution or programme opens, or None when the screen that
    owns it is not registered."""
    from flask import current_app, url_for

    endpoint, argument = {
        "capability": ("enterprise_crud.capability_fact_sheet", "capability_id"),
        "solution": ("solution_design.view_solution", "solution_id"),
        "programme": ("solution_design.programme_cockpit", "initiative_id"),
    }[kind]
    if endpoint not in current_app.view_functions:
        return None
    return url_for(endpoint, **{argument: entity_id})


def _other_links_by_risk(org_id):
    """For each risk, the capabilities, solutions and programmes it is linked to, as touches.

    A link whose target no longer exists, or belongs to another organisation, is left out:
    a risk is shown linked only to something a reader can open.
    """
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_models import Solution
    from app.models.strategic import StrategicInitiative

    rows = db.session.execute(
        db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_type, RiskEntityLink.entity_id).where(
            RiskEntityLink.organization_id == org_id, RiskEntityLink.entity_type.in_(_LINK_KINDS)
        ).order_by(RiskEntityLink.id)
    ).all()
    models = {"capability": BusinessCapability, "solution": Solution, "programme": StrategicInitiative}
    named = {}
    mirrors = {}   # (kind, id) -> the ArchiMate element that mirrors the linked record, if it has one
    for kind, model in models.items():
        ids = sorted({entity_id for _, k, entity_id in rows if k == kind})
        if ids:
            columns = [model.id, model.name]
            if hasattr(model, "archimate_element_id"):
                columns.append(model.archimate_element_id)
            for found in db.session.execute(db.select(*columns).where(model.organization_id == org_id, model.id.in_(ids))).all():
                named.setdefault(kind, {})[found[0]] = found[1]
                if len(found) > 2 and found[2] is not None:
                    mirrors[(kind, found[0])] = found[2]
    out = {}
    for risk_id, kind, entity_id in rows:
        name = named.get(kind, {}).get(entity_id)
        if name is not None:
            out.setdefault(risk_id, []).append({
                "name": name, "kind": kind, "id": entity_id, "element_id": None,
                "url": _link_url(kind, entity_id), "mirror_element_id": mirrors.get((kind, entity_id)),
            })
    return out


def footers_by_risk(organization_id=None):
    """Each risk's footer as the register shows it, by risk id: the one confidence a risk has.

    Every screen that lists a risk beside its own footer (the prioritised actions, the recommendations)
    takes the footer from here, so a risk reads the same confidence, with the same reason, wherever it
    is shown.
    """
    return {row["risk_id"]: row["footer"] for row in ranked_risks(organization_id)["rows"]}


def ranked_risks(organization_id=None):
    """Every risk of the caller's organisation (or of ``organization_id``), ranked by exposure.

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

    org_id = organization_id if organization_id is not None else current_org_id()
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
    others_by_risk = _other_links_by_risk(org_id)

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
        if risk.archimate_element_id is not None:
            app_elements.add(risk.archimate_element_id)
        # A linked capability, solution or programme is listed once, as the link: the element that
        # mirrors it is not listed again as something the risk reaches.
        app_elements.update(
            t["mirror_element_id"] for t in others_by_risk.get(risk.id, []) if t.get("mirror_element_id") is not None)
        nearest = sorted(reached, key=lambda e: (reached[e], names.get(e, ""), e))
        named = [e for e in nearest if e in names and e not in app_elements]
        touches += others_by_risk.get(risk.id, [])
        touches += [{"name": names[e], "kind": "element", "id": e, "element_id": e}
                    for e in named[:_TOUCHES_SHOWN]]
        # The ranking sentence states the reach figure only when the elements
        # named beside it are exactly that many; otherwise it says nothing a
        # reader could count and find different.
        figure_matches = exposure is not None and len(named) == exposure
        entries.append({
            "risk": risk, "exposure": exposure, "why": why, "touches": touches,
            "score": risk._effective_score, "reached_total": len(named),
            "figure_matches": figure_matches,
        })

    entries.sort(key=lambda e: (
        e["exposure"] is None,
        -(e["exposure"] or 0),
        -e["score"],
        (e["risk"].title or "").lower(),
        e["risk"].id,
    ))
    total = len(entries)
    known = result_footer_service.element_facts(
        org_id, {t["element_id"] for e in entries for t in e["touches"]})
    rows = []
    for position, entry in enumerate(entries, start=1):
        risk = entry["risk"]
        score_word, scores = risk.score_word, risk.score_pair_text
        _kind, likelihood, impact = risk._score_basis
        if entry["exposure"] is not None and entry["figure_matches"]:
            reason = "Ranked %d of %d: reaches %s, %s" % (
                position, total, _plural(entry["exposure"], "element"), scores)
        elif entry["exposure"] is not None:
            reason = "Ranked %d of %d by how far it reaches, then by score (%s)" % (position, total, scores)
        elif entry["why"] == "no_links" and entry["touches"]:
            reason = "Ranked %d of %d: how far it reaches is not recorded, ranked by its %s of %d (%s)" % (
                position, total, score_word, entry["score"], scores)
        elif entry["why"] == "no_links":
            reason = "Ranked %d of %d: no linked elements recorded, ranked by its %s of %d (%s)" % (
                position, total, score_word, entry["score"], scores)
        else:
            reason = ("Ranked %d of %d: how far it reaches could not be worked out, "
                      "ranked by its %s of %d (%s)") % (position, total, score_word, entry["score"], scores)
        rows.append({
            "rank": position, "risk_id": risk.id, "title": risk.title,
            "status": risk.status.value, "risk_level": risk.risk_level,
            "likelihood": likelihood, "impact": impact, "risk_score": entry["score"],
            "score_text": risk.score_text, "exposure": entry["exposure"], "reason": reason, "touches": entry["touches"],
            "reached_total": entry["reached_total"],
            "worked_out": entry["why"] != "not_computed",
            "footer": result_footer_service.risk_footer(
                risk, entry["touches"], entry["why"], known,
                None,  # the score is the risk's own: its title already links to the risk
                _touch_link, score_line="%s: %s" % (risk.score_word.capitalize(), risk.score_pair_text)),
        })
    return {"rows": rows, "total": total, "rule": RANKING_RULE}


def level_counts():
    """How many of the organisation's open risks sit at each level.

    The register's headline figures and the risk assessment both read this: a closed or
    mitigated risk is not current exposure, so only open risks are counted.
    """
    from app.middleware.tenant_context import current_org_id

    org_id = current_org_id()
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "open": 0}
    if org_id is None:
        return counts
    for risk in db.session.execute(
        db.select(Risk).where(Risk.organization_id == org_id, Risk.status == RiskStatus.OPEN)
    ).scalars():
        counts[risk.risk_level] += 1
        counts["open"] += 1
    return counts


def capability_owner_names(unified_capability_ids, org_id):
    """The owner's name for each unified capability that has one recorded, from the owner record.

    A capability with no owner recorded is absent from the answer (never a stand-in name), so the
    risk assessment and the capability map's lenses name the same owner for the same capability.
    """
    from app.models.application_owner import ApplicationOwner

    ids = sorted(set(unified_capability_ids))
    if not ids:
        return {}
    by_unified = ApplicationOwner.get_display_rows_for_elements_batch("capability", ids, org_id)
    names = {}
    for unified_id in ids:
        named = [o["user_name"] for o in by_unified.get(unified_id, []) if o["user_name"] != "\u2014"]
        if named:
            names[unified_id] = named[0]
    return names


def risk_assessment():
    """Every risk in the register with the capabilities it threatens, and the open-risk counts.

    A risk threatens a capability when it is linked to it directly, or to an application
    that is mapped to it. A capability's owner is read from the owner record (recorded
    against the unified capability the business capability is projected into); none
    recorded is None, never a stand-in name.
    """
    from app.middleware.tenant_context import current_org_id
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    org_id = current_org_id()
    if org_id is None:
        return {"rows": [], "counts": level_counts()}
    risks = db.session.execute(
        db.select(Risk).where(Risk.organization_id == org_id).order_by(Risk.id)
    ).scalars().all()
    links = db.session.execute(
        db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_type, RiskEntityLink.entity_id).where(
            RiskEntityLink.organization_id == org_id, RiskEntityLink.entity_type.in_(("capability", "application"))
        ).order_by(RiskEntityLink.id)
    ).all()
    app_ids = sorted({entity_id for _, kind, entity_id in links if kind == "application"})
    caps_of_app = {}
    if app_ids:
        for app_id, cap_id in db.session.execute(
            db.select(
                ApplicationCapabilityMapping.application_component_id,
                ApplicationCapabilityMapping.business_capability_id,
            ).where(
                ApplicationCapabilityMapping.organization_id == org_id,
                ApplicationCapabilityMapping.application_component_id.in_(app_ids),
            ).order_by(ApplicationCapabilityMapping.id)
        ).all():
            caps_of_app.setdefault(app_id, []).append(cap_id)
    caps_of_risk = {}
    for risk_id, kind, entity_id in links:
        for cap_id in ([entity_id] if kind == "capability" else caps_of_app.get(entity_id, [])):
            if cap_id not in caps_of_risk.setdefault(risk_id, []):
                caps_of_risk[risk_id].append(cap_id)
    cap_ids = sorted({c for ids in caps_of_risk.values() for c in ids})
    names, owners = {}, {}
    if cap_ids:
        names = dict(db.session.execute(
            db.select(BusinessCapability.id, BusinessCapability.name).where(
                BusinessCapability.organization_id == org_id, BusinessCapability.id.in_(cap_ids))
        ).all())
        projected = {
            int(source_id): unified_id
            for unified_id, source_id in db.session.execute(
                db.select(UnifiedCapability.id, UnifiedCapability.source_id).where(
                    UnifiedCapability.organization_id == org_id,
                    UnifiedCapability.source_table == "business_capability",
                    UnifiedCapability.source_id.in_([str(c) for c in names]),
                )
            ).all()
        }
        by_unified = capability_owner_names(projected.values(), org_id)
        for cap_id, unified_id in projected.items():
            owners[cap_id] = by_unified.get(unified_id)
    rows = []
    for risk in risks:
        rows.append({
            "risk_id": risk.id, "title": risk.title, "risk_level": risk.risk_level,
            "risk_score": risk._effective_score, "score_text": risk.score_text, "status": risk.status.value,
            "capabilities": [
                {"id": c, "name": names[c], "owner": owners.get(c), "url": _link_url("capability", c)}
                for c in caps_of_risk.get(risk.id, []) if c in names
            ],
        })
    rows.sort(key=lambda r: (-r["risk_score"], r["title"].lower(), r["risk_id"]))  # the score each row shows
    return {"rows": rows, "counts": level_counts()}
