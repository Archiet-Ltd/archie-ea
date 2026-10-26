"""SAFe Kind filter on the layer elements API, from the profile registry.

PR 903 added the SAFe kind profile (kind, WSJF) and a Kind dropdown. The
elements endpoint applied the ``kind`` query parameter AFTER slicing the page
and never recalculated ``total`` or ``pages``: with 55 work packages across
three pages and 12 marked Epic, ``?kind=Epic`` returned only the Epics that
happened to sit on page 1, told the caller the total was 55, and reported the
unfiltered page count. Each element's profile was read with a per-row
``session.get`` and the relationship count with a per-row ``count()``, so a
page of 25 elements issued more than sixty statements.

These tests pin the fixed behaviour:

* the kind filter matches on the property the profile writer stores and runs
  before pagination, so every matching element across every page comes back
  and ``total``/``pages`` describe the filtered set;
* a page of 25 elements costs at most three data queries (one to load the
  archimate rows with the filter, one for the batched relationship count, one
  for the batched dedicated-row lookup) — never one per row;
* the Kind options come from the profile registry, not a hard-coded list;
* org A's Epics are invisible to org B and vice versa.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event

from app.extensions import db

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------------- seed


def _seed_work_packages(db_session, org_id, count, epic_indices, prefix="WP"):
    """Seed ``count`` work packages as mirrored ArchiMate elements, marking the
    indices in ``epic_indices`` (1-based) as Epic and the rest as Feature, and
    writing the kind through the canonical profile writer (the same property
    the list endpoint filters on). Returns {name: archimate_element}."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage
    from app.services.element_profile_service import set_kind

    epic = set(epic_indices)
    mapping = {}
    for i in range(1, count + 1):
        name = "%s %02d" % (prefix, i)
        ae = ArchiMateElement(
            name=name,
            type="WorkPackage",
            layer="Implementation",
            organization_id=org_id,
        )
        db_session.add(ae)
        db_session.flush()
        wp = WorkPackage(
            name=name,
            organization_id=org_id,
            element_type="WorkPackage",
            archimate_element_id=ae.id,
        )
        db_session.add(wp)
        db_session.flush()
        set_kind(ae, "Epic" if i in epic else "Feature")
        mapping[name] = ae
    db_session.commit()
    return mapping


def _make_architect(db_session, org_id, label):
    from app.models.organization import Organization
    from app.models.user import Role, User

    Role.insert_roles()
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email="%s-%s@example.com" % (label, suffix),
        first_name="Kind",
        last_name="Architect",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.commit()
    # Organization is referenced lazily by the tenant middleware; keep it local.
    org = Organization.query.filter_by(id=org_id).one()
    return user, org


def _elements_path(layer="implementation", **params):
    from urllib.parse import urlencode

    return "/architecture/api/layer/%s/elements?%s" % (layer, urlencode(params))


# ------------------------------------------------------------------ kind filter


def test_kind_filter_returns_every_match_across_pages(app, db_session, make_org, login_as):
    """12 Epics spread over three pages (55 work packages at 25 rows/page) are
    all returned by ``?kind=Epic`` with total and page count describing the
    filtered set — not the filter applied to page 1 only."""
    org = make_org("kind-across-pages")
    epic_indices = [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]
    seeded = _seed_work_packages(db_session, org.id, 55, epic_indices)
    user, org_row = _make_architect(db_session, org.id, "kf-ap")

    c = app.test_client()
    login_as(c, user)

    # The unfiltered list really does span three pages, and the Epics live on
    # more than one of them — page 2 alone holds four of the twelve.
    resp = c.get(_elements_path(per_page=25, page=2))
    body = resp.get_json()
    assert body["pagination"]["total"] == 55
    assert body["pagination"]["pages"] == 3
    page2_names = {e["name"] for e in body["elements"]}
    assert len(page2_names & set(seeded)) == 25  # a full page

    # Filtering must find all twelve, wherever they sit.
    resp = c.get(_elements_path(kind="Epic", per_page=25, page=1))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["pagination"]["total"] == 12, (
        "kind filter ran after pagination: total is not recalculated"
    )
    assert body["pagination"]["pages"] == 1, (
        "kind filter ran after pagination: page count is not recalculated"
    )
    names = [e["name"] for e in body["elements"]]
    expected = {"WP %02d" % i for i in epic_indices}
    assert len(names) == 12
    assert set(names) == expected
    assert all(e["kind"] == "Epic" for e in body["elements"])
    # A second page must not exist: requesting it yields no elements.
    resp = c.get(_elements_path(kind="Epic", per_page=25, page=2))
    assert resp.get_json()["elements"] == []


def test_kind_filter_matches_the_stored_property(app, db_session, make_org, login_as):
    """The filter reads the property the profile writer stores, so the Epics
    are all found and the Features are not returned when filtering for Epic,
    across the whole three-page list at per_page=25."""
    org = make_org("kind-stored")
    epic_indices = [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]
    _seed_work_packages(db_session, org.id, 55, epic_indices)
    user, org_row = _make_architect(db_session, org.id, "kf-sp")

    c = app.test_client()
    login_as(c, user)

    resp = c.get(_elements_path(kind="Feature"))
    body = resp.get_json()
    assert body["pagination"]["total"] == 43
    assert body["pagination"]["pages"] == 2
    assert all(e["kind"] == "Feature" for e in body["elements"])
    epic_names = {"WP %02d" % i for i in epic_indices}
    assert not ({e["name"] for e in body["elements"]} & epic_names)

    # Round-trip: the Feature view's second page still excludes the Epics, and
    # requesting the next page of the Epic view returns nothing.
    resp = c.get(_elements_path(kind="Feature", per_page=25, page=2))
    body = resp.get_json()
    assert body["pagination"]["total"] == 43
    assert body["pagination"]["pages"] == 2
    assert not ({e["name"] for e in body["elements"]} & epic_names)
    resp = c.get(_elements_path(kind="Epic", per_page=25, page=2))
    assert resp.get_json()["elements"] == []


def test_thirty_work_packages_twelve_epics_across_three_pages(app, db_session, make_org, login_as):
    """The brief's exact scenario at per_page=10: 30 work packages, 12 marked
    Epic, spread across three pages; filtering to Epic shows 12 with the right
    page count."""
    org = make_org("kind-thirty")
    epic_indices = [1, 2, 3, 13, 14, 15, 16, 17, 18, 27, 28, 29]  # one on each page
    _seed_work_packages(db_session, org.id, 30, epic_indices)
    user, org_row = _make_architect(db_session, org.id, "kf-30")

    c = app.test_client()
    login_as(c, user)

    # 30 work packages at 10 rows/page = three pages.
    resp = c.get(_elements_path(per_page=10, page=3))
    body = resp.get_json()
    assert body["pagination"]["total"] == 30, body["pagination"]
    assert body["pagination"]["pages"] == 3, body["pagination"]

    # Filtering to Epic finds every one of the twelve, wherever it sits, and
    # the page count describes the filtered set: 12 items, 2 pages at 10.
    resp = c.get(_elements_path(kind="Epic", per_page=10, page=1))
    body = resp.get_json()
    assert body["pagination"]["total"] == 12
    assert body["pagination"]["pages"] == 2
    assert len(body["elements"]) == 10
    assert all(e["kind"] == "Epic" for e in body["elements"])
    resp = c.get(_elements_path(kind="Epic", per_page=10, page=2))
    body = resp.get_json()
    assert body["pagination"]["total"] == 12
    assert body["pagination"]["pages"] == 2
    assert len(body["elements"]) == 2
    assert all(e["kind"] == "Epic" for e in body["elements"])
    resp = c.get(_elements_path(kind="Epic", per_page=10, page=3))
    assert resp.get_json()["elements"] == []


# ----------------------------------------------------------------- kind options


def test_kind_options_come_from_the_registry(app, db_session, make_org, login_as):
    """The endpoint's Kind options are the registry's allowed kinds for the
    layer's element types — not a hard-coded list."""
    org = make_org("kind-options")
    user, org_row = _make_architect(db_session, org.id, "kf-op")

    c = app.test_client()
    login_as(c, user)

    from app.services.element_profile_service import allowed_kinds

    expected = allowed_kinds("WorkPackage", "safe")
    assert expected  # Epic, Feature, Capability (SAFe), Enabler

    resp = c.get(_elements_path())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kind_options"] == expected


# --------------------------------------------------------------- tenant isolation


def test_kind_filter_is_isolated_between_organisations(app, db_session, make_org, login_as):
    """Two real organisations: org A's Epics never appear in org B's filtered
    list and vice versa, even though both store the same kinds. Both orgs'
    Epics span more than one page, so the cross-page fix and the tenancy hold
    at the same time."""
    org_a = make_org("kind-org-a")
    org_b = make_org("kind-org-b")
    # A: 55 work packages, 9 Epics spread over all three pages.
    # B: 30 work packages, 2 Epics with one on page 2.
    a_epic = {"A %02d" % i for i in [3, 9, 15, 21, 27, 33, 39, 45, 51]}
    b_epic = {"B 02", "B 27"}
    _seed_work_packages(db_session, org_a.id, 55, [3, 9, 15, 21, 27, 33, 39, 45, 51], prefix="A")
    _seed_work_packages(db_session, org_b.id, 30, [2, 27], prefix="B")
    user_a, org_row_a = _make_architect(db_session, org_a.id, "kf-a")
    user_b, org_row_b = _make_architect(db_session, org_b.id, "kf-b")

    c = app.test_client()
    login_as(c, user_a)
    body = c.get(_elements_path(kind="Epic")).get_json()
    names = {e["name"] for e in body["elements"]}
    assert names == a_epic, (
        "org A's Epic list is wrong: %s (expected %s) — leaked a foreign name "
        "or dropped a cross-page match" % (sorted(names), sorted(a_epic))
    )
    assert not (names & b_epic), "org A listed org B's Epics: %s" % (names & b_epic)

    login_as(c, user_b)
    body = c.get(_elements_path(kind="Epic")).get_json()
    names = {e["name"] for e in body["elements"]}
    assert names == b_epic, (
        "org B's Epic list is wrong: %s (expected %s) — leaked a foreign name "
        "or dropped a cross-page match" % (sorted(names), sorted(b_epic))
    )
    assert not (names & a_epic), "org B listed org A's Epics: %s" % (names & a_epic)


# ------------------------------------------------------------------- query budget


def test_page_of_25_elements_costs_at_most_three_queries(app, db_session, make_org, login_as):
    """One page of 25 elements loads through three data queries: the archimate
    rows themselves, a batched relationship count, and a batched dedicated-row
    lookup. No per-row ``session.get``, per-row ``count()`` or per-row
    dedicated lookup — the old code fired three per element."""
    org = make_org("kind-query-budget")
    _seed_work_packages(db_session, org.id, 25, list(range(1, 26)))
    user, org_row = _make_architect(db_session, org.id, "kf-qb")

    c = app.test_client()
    login_as(c, user)

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        text = " ".join(statement.split())
        if text.startswith("SELECT") and (
            "archimate_elements" in text
            or "archimate_relationships" in text
            or "work_packages" in text
        ):
            statements.append(text)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        c.get(_elements_path(per_page=25, source="architecture"))
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(statements) <= 3, (
        "page of 25 elements issued %d data queries (expected <= 3); "
        "the profile/relationship reads must be batched:\n%s"
        % (len(statements), "\n".join(s[:120] for s in statements))
    )


# ------------------------------------------------------ detail link resolution


def test_detail_link_opens_the_clicked_work_package_not_a_number_twin(
    app, db_session, make_org, login_as
):
    """The list links a mirrored work package by its ArchiMate element id, and
    the dedicated work_packages table keeps its own primary-key sequence. When
    those two numbers collide the detail route must still open the work package
    that was clicked — not a different one that merely shares the number.

    This builds the collision deliberately: the target work package's ArchiMate
    element id is reused as a DECOY work package's dedicated primary key. The
    detail route is then asked for that id. Resolving by the dedicated primary
    key first (the old behaviour) opens the decoy; resolving by the mirror
    (archimate_element_id) opens the target.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage
    from app.services.element_profile_service import set_kind

    org = make_org("detail-collision")

    # The target: a mirrored work package. Its ArchiMate element id is the id the
    # element list hands to the detail link.
    target_ae = ArchiMateElement(
        name="Target work package",
        type="WorkPackage",
        layer="Implementation",
        organization_id=org.id,
    )
    db_session.add(target_ae)
    db_session.flush()
    collision_id = target_ae.id
    target_wp = WorkPackage(
        name="Target work package",
        organization_id=org.id,
        element_type="WorkPackage",
        archimate_element_id=target_ae.id,
    )
    db_session.add(target_wp)
    db_session.flush()
    set_kind(target_ae, "Epic")

    # The decoy: a different work package whose dedicated primary key is forced
    # to equal the target's ArchiMate element id, so the two collide.
    decoy_ae = ArchiMateElement(
        name="Decoy work package",
        type="WorkPackage",
        layer="Implementation",
        organization_id=org.id,
    )
    db_session.add(decoy_ae)
    db_session.flush()
    decoy_wp = WorkPackage(
        id=collision_id,
        name="Decoy work package",
        organization_id=org.id,
        element_type="WorkPackage",
        archimate_element_id=decoy_ae.id,
    )
    db_session.add(decoy_wp)
    db_session.flush()
    set_kind(decoy_ae, "Feature")
    db_session.commit()

    assert decoy_wp.id == collision_id
    assert target_wp.id != collision_id, (
        "test setup invalid: target and collision ids must differ for the twin "
        "to be a real twin"
    )

    user, _org = _make_architect(db_session, org.id, "detail-c")
    c = app.test_client()
    login_as(c, user)

    resp = c.get("/architecture/implementation/WorkPackage/%d?ae=1" % collision_id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_data(as_text=True)
    assert "Target work package" in body, (
        "the detail link opened the wrong element: the clicked work package's "
        "name is not on the page"
    )
    assert "Decoy work package" not in body, (
        "the detail link opened the number-twin work package instead of the one "
        "that was clicked"
    )


def test_detail_link_is_isolated_between_organisations(
    app, db_session, make_org, login_as
):
    """Two real organisations: org A's work package, reached by its ArchiMate
    element id, is not served to org B. B gets a 404, never A's element."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage

    org_a = make_org("detail-iso-a")
    org_b = make_org("detail-iso-b")

    ae = ArchiMateElement(
        name="Org A only work package",
        type="WorkPackage",
        layer="Implementation",
        organization_id=org_a.id,
    )
    db_session.add(ae)
    db_session.flush()
    wp = WorkPackage(
        name="Org A only work package",
        organization_id=org_a.id,
        element_type="WorkPackage",
        archimate_element_id=ae.id,
    )
    db_session.add(wp)
    db_session.commit()

    user_a, _a = _make_architect(db_session, org_a.id, "detail-a")
    user_b, _b = _make_architect(db_session, org_b.id, "detail-b")

    c = app.test_client()

    login_as(c, user_a)
    resp = c.get("/architecture/implementation/WorkPackage/%d?ae=1" % ae.id)
    assert resp.status_code == 200
    assert "Org A only work package" in resp.get_data(as_text=True)

    login_as(c, user_b)
    resp = c.get("/architecture/implementation/WorkPackage/%d?ae=1" % ae.id)
    assert resp.status_code == 404, (
        "org B reached org A's work package through the detail link"
    )
    assert "Org A only work package" not in resp.get_data(as_text=True)
