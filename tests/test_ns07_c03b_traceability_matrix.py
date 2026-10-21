"""Traceability page: the d3 Sankey is gone and the matrix is the dense-table matrix variant.

Group C-03b of the tier 1 conformance burn-down. Rows are the source layer's elements
(application components), columns the target layers; every cell is the number of
relationships the page's data source (ArchiMateTraceabilityService.get_full_matrix)
returns for that row and layer, written with matrix_cell's four states. The page is
rendered through its real route with two organisations seeded.
"""
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.usefixtures("db_session")

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "archimate_views" / "traceability.html"
LAYERS = ["motivation", "strategy", "business", "technology", "data"]


def _el(db_session, org, name, etype, layer):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _rel(db_session, org, source, target, type_):
    from app.models import ArchiMateRelationship

    db_session.add(ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_,
                                         organization_id=org.id))
    db_session.flush()


def _seed_two_layers(app, db_session, make_org, tenant_ctx):
    """Org A: Alpha (two business neighbours, one technology neighbour) and Beta (none).
    Org B: Zulu with one business neighbour."""
    from app.models.user import User

    org_a, org_b = make_org("trc-a"), make_org("trc-b")
    with tenant_ctx(org_a.id):
        alpha = _el(db_session, org_a, "Alpha ledger", "ApplicationComponent", "application")
        _el(db_session, org_a, "Beta portal", "ApplicationComponent", "application")
        b1 = _el(db_session, org_a, "Billing process", "BusinessProcess", "business")
        b2 = _el(db_session, org_a, "Invoicing service", "BusinessService", "business")
        node = _el(db_session, org_a, "Core server", "Node", "technology")
        _rel(db_session, org_a, alpha, b1, "RealizationRelationship")
        _rel(db_session, org_a, b2, alpha, "ServingRelationship")
        _rel(db_session, org_a, node, alpha, "ServingRelationship")
        user = User(email="trc-a-tester@example.com", first_name="T", last_name="A",
                    organization_id=org_a.id, confirmed=True)
        user.password = "not-used-in-tests-123"
        db_session.add(user)
        db_session.flush()
    with tenant_ctx(org_b.id):
        zulu = _el(db_session, org_b, "Zulu foreign app", "ApplicationComponent", "application")
        bz = _el(db_session, org_b, "Foreign process", "BusinessProcess", "business")
        _rel(db_session, org_b, zulu, bz, "RealizationRelationship")
    return org_a, org_b, user


def _cells(html):
    page = BeautifulSoup(html, "html.parser")
    table = page.select_one("#matrix-view table")
    assert table is not None
    heads = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    rows = {}
    for tr in table.select("tbody tr"):
        th = tr.select_one('th[scope="row"]')
        if th is None:
            continue
        cells = {}
        for layer, td in zip(LAYERS, tr.select("td")):
            state = td.get("data-state")
            count = {"empty": 0, "single-value": 1, "not-recorded": None}.get(state)
            if state == "multiple-count":
                count = int(td.get_text(strip=True))
            cells[layer] = (state, count)
        rows[th.get_text(" ", strip=True).split("  ")[0]] = cells
    return page, table, heads, rows


def test_matrix_cells_equal_the_relationship_counts_the_data_source_returns(
        app, db_session, make_org, tenant_ctx, client, login_as):
    from app.services.archimate_traceability_service import ArchiMateTraceabilityService

    org_a, _org_b, user = _seed_two_layers(app, db_session, make_org, tenant_ctx)
    with tenant_ctx(org_a.id):
        source = {r["application"]["name"]: r
                  for r in ArchiMateTraceabilityService().get_full_matrix(limit=None)}
    login_as(client, user)
    resp = client.get("/architecture/traceability")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    page, table, heads, rows = _cells(resp.get_data(as_text=True))

    assert set(rows) == {"Alpha ledger", "Beta portal"} == set(source)
    for name, cells in rows.items():
        for layer in LAYERS:
            state, count = cells[layer]
            assert count == len(source[name][layer]), (name, layer, cells[layer])
    # the seeded shape, so the equality above cannot be met by two empty sides
    assert rows["Alpha ledger"]["business"] == ("multiple-count", 2)
    assert rows["Alpha ledger"]["technology"] == ("single-value", 1)
    assert rows["Alpha ledger"]["strategy"] == ("empty", 0)
    assert all(state == "empty" for state, _ in rows["Beta portal"].values())

    # matrix variant: sticky header and first column, a legend, no pagination
    assert "matrix" in table.get("class") and "sticky-header" in table.get("class")
    assert "sticky-col" in table.get("class")
    assert page.select_one("#matrix-view .matrix-legend") is not None
    assert len(heads) == 6 and heads[0] == "Application Component"
    assert "Showing" not in page.select_one("#matrix-view").get_text()
    assert page.select_one('#matrix-view a[href*="page="]') is None


def test_matrix_page_has_no_sankey_and_no_view_toggle(app, db_session, make_org, tenant_ctx, client, login_as):
    org_a, _org_b, user = _seed_two_layers(app, db_session, make_org, tenant_ctx)
    login_as(client, user)
    html = client.get("/architecture/traceability").get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")
    assert page.select_one("#sankey-view") is None and page.select_one("#sankey-container") is None
    assert "d3-sankey" not in html and "d3.min.js" not in html and "createTraceabilitySankey" not in html
    assert not re.search(r"d3[.-]", html)
    assert not [b for b in page.select("button") if b.get_text(strip=True) in ("Sankey", "Matrix")]
    src = TEMPLATE.read_text(encoding="utf-8")
    assert "d3" not in re.sub(r"\{#.*?#\}", "", src, flags=re.S).lower().replace("d3-", "d3-") or "d3" not in src.lower()
    # every control the rest of the page uses is still there
    for needle in ('id="matrix-view"', 'id="direction"', 'id="pivot_type"', 'name="search"',
                   'name="scope"', "/api/archimate/traceability/export"):
        assert needle in html or needle in src, needle


def test_matrix_shows_only_the_signed_in_organisations_elements(
        app, db_session, make_org, tenant_ctx, client, login_as):
    org_a, org_b, user = _seed_two_layers(app, db_session, make_org, tenant_ctx)
    login_as(client, user)
    html = client.get("/architecture/traceability").get_data(as_text=True)
    assert "Alpha ledger" in html and "Billing process" in html
    assert "Zulu foreign app" not in html and "Foreign process" not in html


def test_matrix_shows_the_true_count_when_there_are_more_than_three(
        app, db_session, make_org, tenant_ctx, client, login_as):
    """The data source's per-layer lists are capped at three for display; the cell is the real number."""
    org_a, _org_b, user = _seed_two_layers(app, db_session, make_org, tenant_ctx)
    with tenant_ctx(org_a.id):
        from app.models import ArchiMateElement
        hub = ArchiMateElement.query.filter_by(name="Beta portal").one()
        for i in range(6):
            _rel(db_session, org_a, hub, _el(db_session, org_a, f"Extra process {i}", "BusinessProcess", "business"),
                 "RealizationRelationship")
        for i in range(4):
            _rel(db_session, org_a, _el(db_session, org_a, f"Extra node {i}", "Node", "technology"), hub,
                 "ServingRelationship")
    login_as(client, user)
    resp = client.get("/architecture/traceability")
    _page, _table, _heads, rows = _cells(resp.get_data(as_text=True))
    assert rows["Beta portal"]["business"] == ("multiple-count", 6)
    assert rows["Beta portal"]["technology"] == ("multiple-count", 4)
    assert rows["Alpha ledger"]["business"] == ("multiple-count", 2)


def test_single_relationship_cell_shows_one_and_title_names_the_type(
        app, db_session, make_org, tenant_ctx, client, login_as):
    """A matrix shows one kind of value per grid: a lone relationship is 1, never an abbreviation."""
    org_a, _org_b, user = _seed_two_layers(app, db_session, make_org, tenant_ctx)
    login_as(client, user)
    html = client.get("/architecture/traceability").get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")
    for tr in page.select("#matrix-view tbody tr"):
        if "Alpha ledger" not in tr.get_text():
            continue
        tech = tr.select("td")[3]
        assert tech.get("data-state") == "single-value"
        badge = tech.select_one(".badge")
        assert badge.get_text(strip=True) == "1"
        assert "Serving" in badge.get("title") and "Core server" in badge.get("title")
        break
    else:
        raise AssertionError("Alpha ledger row missing")
    assert not re.search(r">\s*(Srv|Rlz|Asgn|Cmp|Agg|Asc|Acc)\s*<", html)
