"""The impact answer's lifecycle block: a ``lifecycle`` block attached to
every row whose element resolves to a guarded ``ApplicationComponent`` or to
a ``Node``/``Device``/``SystemSoftware`` row, and one whole-answer
``lifecycle_flags`` block naming which of the elements reached are already
scheduled to stop.

Sixteen groups, matching the brief's own numbering: two-organisation (1-4),
not-recorded (5-9), shape and batching (10-13), fabrication (14), route (15),
latency (16).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import event

# Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
# discovered via app/modules/intelligence/tests/conftest.py's own import of
# tests.conftest -- pytest resolves fixtures by name without this module
# importing them itself (same pattern as test_query_service.py /
# test_impact_route.py).

LIFECYCLE_BLOCK_KEYS = {
    "status",
    "planned_retirement_date",
    "end_of_life_date",
    "support_expiry",
    "licence_expiry",
    "vendor_end_of_life_date",
    "vendor_reason",
    "retiring",
    "reason",
    "source",
}
LIFECYCLE_FLAGS_KEYS = {"retiring_element_ids", "reason", "window_days"}


# --- fixture helpers ----------------------------------------------------------


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"wire1-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Wire1",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, layer="application", type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _application_component(db_session, org_id, element_id, name="App", **fields):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(
        name=name, organization_id=org_id, archimate_element_id=element_id, **fields
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _node(db_session, org_id, element_id, name="Node", **fields):
    from app.models.technology_layer import Node

    node = Node(name=name, organization_id=org_id, archimate_element_id=element_id, **fields)
    db_session.add(node)
    db_session.flush()
    return node


def _vendor_product(db_session, name="Vendor Product", end_of_life_date=None):
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    vendor_org = VendorOrganization(name=f"ZZ-WIRE1 Vendor {uuid.uuid4().hex[:8]}")
    db_session.add(vendor_org)
    db_session.flush()
    product = VendorProduct(
        vendor_organization_id=vendor_org.id, name=name, end_of_life_date=end_of_life_date
    )
    db_session.add(product)
    db_session.flush()
    return product


def _row_for(rows, element_id):
    for row in rows:
        if row["element_id"] == element_id:
            return row
    raise AssertionError(f"no row for element {element_id}")


def _base_fixture(db_session, make_org):
    """Org A: ``app_a`` (component ``comp_a``) --Serving--> ``app_b``
    (component ``comp_b``); ``app_a`` --Serving--> ``node_a`` (technology,
    ``Node`` row). Org B alongside, empty. Every lifecycle field on
    ``comp_b``/the ``Node`` row starts unset -- individual tests record
    what they need.
    """
    org_a = make_org("wire1-a")
    org_b = make_org("wire1-b")
    app_a = _element(db_session, org_a.id, "App A")
    comp_a = _application_component(db_session, org_a.id, app_a.id, name="Comp A")
    app_b = _element(db_session, org_a.id, "App B")
    comp_b = _application_component(db_session, org_a.id, app_b.id, name="Comp B")
    _relationship(db_session, org_a.id, app_a, app_b)
    node_a = _element(db_session, org_a.id, "Node A", layer="technology", type_="Node")
    node_row = _node(db_session, org_a.id, node_a.id, name="Node A Row")
    _relationship(db_session, org_a.id, app_a, node_a)
    db_session.commit()
    return org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row


def _impact(app, org_id, element_id, **kwargs):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.cross_layer_impact(element_id, **kwargs)


# ===========================================================================
# Two-organisation (1)-(4)
# ===========================================================================


def test_1_own_component_end_of_life_within_window_flows_into_row_and_flags(app, db_session, make_org):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    comp_b.end_of_life_date = date.today() + timedelta(days=30)
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, with_owner=False)

    row = _row_for(result["rows"], app_b.id)
    assert row["lifecycle"]["retiring"] is True
    assert row["lifecycle"]["source"] == "application_components"
    assert result["lifecycle_flags"]["retiring_element_ids"] == [app_b.id]
    assert result["lifecycle_flags"]["reason"] is None
    assert result["lifecycle_flags"]["window_days"] == 90


def test_2_foreign_component_status_never_leaks_own_row_reads_no_lifecycle_recorded(
    app, db_session, make_org
):
    """comp_b (org A's own component on app_b) has nothing recorded. A
    second, org-B-owned component also points at app_b's element id (the FK
    is not tenant-checked) with ``lifecycle_status = "retired"``. Org A's
    answer must never show it.
    """
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    _application_component(
        db_session, org_b.id, app_b.id, name="Foreign Comp", lifecycle_status="retired"
    )
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, with_owner=False)

    row = _row_for(result["rows"], app_b.id)
    assert row["lifecycle"]["reason"] == "no_lifecycle_recorded"
    assert row["lifecycle"]["retiring"] is None
    assert row["lifecycle"]["status"] is None
    assert "retired" not in json.dumps(result)


def test_2_mutation_proof_disabling_sec09_leaks_the_foreign_status(app, db_session, make_org, monkeypatch):
    """Companion mutation proof for test 2, in the same shape
    ``test_mutation_proof_sec09_real_path`` (test_query_service.py) uses: a
    genuine ``g``/``org_id`` divergence reaches
    ``_resolve_component_lifecycle_batch`` (which shares the extracted
    ``_resolve_components_batch`` call) through unmodified
    production control flow, not a contrived same-request scenario the ORM's
    own tenant listener would already block regardless of SEC-09.
    """
    from flask import g

    from app.modules.intelligence.services import query_service

    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    foreign_comp = _application_component(
        db_session, org_b.id, app_b.id, name="Foreign Comp", lifecycle_status="retired"
    )
    db_session.commit()

    today = date.today()
    window = timedelta(days=90)

    # SEC-09 intact: resolving on behalf of org A, even while g has drifted
    # to org B (so org B's own component is genuinely, ORM-returnably
    # visible), never surfaces org B's component.
    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        blocks = query_service._resolve_component_lifecycle_batch(
            [app_b.id], org_a.id, today, window
        )
    assert app_b.id not in blocks

    # Disable ONLY _sec09_tenant_check -- the same real scenario now leaks.
    monkeypatch.setattr(
        query_service, "_sec09_tenant_check", lambda component_org_id, org_id: True
    )
    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        blocks2 = query_service._resolve_component_lifecycle_batch(
            [app_b.id], org_a.id, today, window
        )

    assert app_b.id in blocks2
    assert blocks2[app_b.id]["status"] == "retired"
    with pytest.raises(AssertionError):
        assert app_b.id not in blocks2


def test_3_foreign_technology_row_absent_from_own_answer(app, db_session, make_org):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    _node(
        db_session,
        org_b.id,
        node_a.id,
        name="Foreign Node",
        decommission_date=date.today() - timedelta(days=1),
    )
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, with_owner=False)

    row = _row_for(result["rows"], node_a.id)
    # node_a's OWN row has nothing recorded -- the foreign row's eol_date
    # never substitutes for it.
    assert row["lifecycle"]["reason"] == "no_lifecycle_recorded"
    assert row["lifecycle"]["retiring"] is None


def test_3_mutation_proof_disabling_the_technology_predicate_leaks_the_foreign_row(
    app, db_session, make_org, monkeypatch
):
    """Companion mutation proof for test 3: the technology select's explicit
    ``organization_id`` predicate is isolated in ``_technology_org_predicate``
    (same seam pattern as ``_sec09_tenant_check``). A genuine ``g``/``org_id``
    divergence reaches it through unmodified production control flow.
    """
    from flask import g

    from app.modules.intelligence.services import query_service

    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    foreign_node = _node(
        db_session,
        org_b.id,
        node_a.id,
        name="Foreign Node",
        decommission_date=date.today() - timedelta(days=1),
    )
    db_session.commit()

    today = date.today()
    window = timedelta(days=90)

    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        blocks = query_service._resolve_technology_lifecycle_batch(
            [node_a.id], org_a.id, today, window
        )
    assert node_a.id not in blocks

    monkeypatch.setattr(
        query_service, "_technology_org_predicate", lambda org_column, org_id: True
    )
    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        blocks2 = query_service._resolve_technology_lifecycle_batch(
            [node_a.id], org_a.id, today, window
        )

    assert node_a.id in blocks2
    assert blocks2[node_a.id]["retiring"] is True
    with pytest.raises(AssertionError):
        assert node_a.id not in blocks2


def test_4_foreign_element_id_is_the_absent_id_404_and_service_flags(
    app, db_session, make_org, client, login_as
):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    user_b = _user(db_session, org_b.id)
    db_session.commit()

    login_as(client, user_b)
    resp_cross_tenant = client.get(f"/api/v1/intelligence/impact/{app_a.id}")
    login_as(client, user_b)
    resp_nonexistent = client.get("/api/v1/intelligence/impact/999999999")

    assert resp_cross_tenant.status_code == 404 == resp_nonexistent.status_code
    body_a = resp_cross_tenant.get_json()
    body_b = resp_nonexistent.get_json()
    body_a.pop("meta", None)
    body_b.pop("meta", None)
    assert body_a == body_b

    result = _impact(app, org_b.id, app_a.id, with_owner=False)
    assert result["lifecycle_flags"] == {
        "retiring_element_ids": None,
        "reason": "element_not_found",
        "window_days": 90,
    }


# ===========================================================================
# Not recorded (5)-(9)
# ===========================================================================


def test_5_nothing_recorded_on_the_component_is_the_honest_absence(app, db_session, make_org):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    # comp_b already carries no lifecycle fields (base fixture default).

    result = _impact(app, org_a.id, app_a.id, with_owner=False)
    block = _row_for(result["rows"], app_b.id)["lifecycle"]

    assert block["status"] is None
    assert block["planned_retirement_date"] is None
    assert block["end_of_life_date"] is None
    assert block["retiring"] is None
    assert block["reason"] == "no_lifecycle_recorded"


def test_6_deprecated_status_alone_is_retiring_with_no_dates(app, db_session, make_org):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    comp_b.lifecycle_status = "deprecated"
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, with_owner=False)
    block = _row_for(result["rows"], app_b.id)["lifecycle"]

    assert block["retiring"] is True
    assert block["reason"] is None


def test_7_window_parameter_moves_the_retiring_boundary_and_echoes_in_the_block(
    app, db_session, make_org
):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    comp_b.end_of_life_date = date.today() + timedelta(days=200)
    db_session.commit()

    default_window = _impact(app, org_a.id, app_a.id, with_owner=False)
    wide_window = _impact(
        app, org_a.id, app_a.id, with_owner=False, retirement_window_days=365
    )

    default_block = _row_for(default_window["rows"], app_b.id)["lifecycle"]
    wide_block = _row_for(wide_window["rows"], app_b.id)["lifecycle"]

    assert default_block["retiring"] is False
    assert wide_block["retiring"] is True
    assert default_window["lifecycle_flags"]["window_days"] == 90
    assert wide_window["lifecycle_flags"]["window_days"] == 365


def test_8_vendor_mapping_absence_and_presence(app, db_session, make_org):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)
    comp_b.lifecycle_status = "deprecated"  # keep the block "recorded" throughout
    db_session.commit()

    no_vendor = _impact(app, org_a.id, app_a.id, with_owner=False)
    block = _row_for(no_vendor["rows"], app_b.id)["lifecycle"]
    assert block["vendor_end_of_life_date"] is None
    assert block["vendor_reason"] == "no_vendor_mapping_recorded"

    vendor_eol = date.today() + timedelta(days=45)
    from datetime import datetime as _dt

    product = _vendor_product(db_session, end_of_life_date=_dt.combine(vendor_eol, _dt.min.time()))
    comp_b.vendor_product_id = product.id
    db_session.commit()

    with_vendor = _impact(app, org_a.id, app_a.id, with_owner=False)
    block2 = _row_for(with_vendor["rows"], app_b.id)["lifecycle"]
    assert block2["vendor_end_of_life_date"] == vendor_eol.isoformat()
    assert block2["vendor_reason"] is None


def test_9_node_decommission_date_and_node_with_nothing(app, db_session, make_org):
    org_a, org_b, app_a, comp_a, app_b, comp_b, node_a, node_row = _base_fixture(db_session, make_org)

    empty = _impact(app, org_a.id, app_a.id, with_owner=False)
    empty_block = _row_for(empty["rows"], node_a.id)["lifecycle"]
    assert empty_block["reason"] == "no_lifecycle_recorded"

    node_row.decommission_date = date.today() - timedelta(days=1)
    db_session.commit()

    result = _impact(app, org_a.id, app_a.id, with_owner=False)
    block = _row_for(result["rows"], node_a.id)["lifecycle"]
    assert block["source"] == "technology_nodes"
    assert block["end_of_life_date"] is None
    assert block["retiring"] is True


# ===========================================================================
# Shape and batching (10)-(13)
# ===========================================================================


def test_10_block_and_flags_shape_on_every_branch(app, db_session, make_org):
    org = make_org("wire1-shape")
    root = _element(db_session, org.id, "Root")
    bystander = _element(db_session, org.id, "Bystander")  # no relationship reaches it
    one = _element(db_session, org.id, "One")
    plain = _element(db_session, org.id, "Plain", layer="business", type_="BusinessProcess")
    _relationship(db_session, org.id, root, one)
    _relationship(db_session, org.id, root, plain)
    _application_component(db_session, org.id, one.id, name="One", lifecycle_status="deprecated")
    db_session.commit()

    computed = _impact(app, org.id, root.id, with_owner=False)
    row = _row_for(computed["rows"], one.id)
    assert set(row["lifecycle"].keys()) == LIFECYCLE_BLOCK_KEYS
    assert set(computed["lifecycle_flags"].keys()) == LIFECYCLE_FLAGS_KEYS

    plain_row = _row_for(computed["rows"], plain.id)
    assert "lifecycle" not in plain_row

    computed_empty = _impact(app, org.id, bystander.id, with_owner=False)
    assert computed_empty["rows"] == []
    assert set(computed_empty["lifecycle_flags"].keys()) == LIFECYCLE_FLAGS_KEYS
    assert computed_empty["lifecycle_flags"]["reason"] == "no_lifecycle_recorded"
    assert computed_empty["lifecycle_flags"]["retiring_element_ids"] is None

    tenantless = _impact(app, None, root.id, with_owner=False)
    assert set(tenantless["lifecycle_flags"].keys()) == LIFECYCLE_FLAGS_KEYS
    assert tenantless["lifecycle_flags"]["reason"] == "no_tenant_context"
    assert tenantless["lifecycle_flags"]["retiring_element_ids"] is None

    not_found = _impact(app, org.id, 999999999, with_owner=False)
    assert set(not_found["lifecycle_flags"].keys()) == LIFECYCLE_FLAGS_KEYS
    assert not_found["lifecycle_flags"]["reason"] == "element_not_found"
    assert not_found["lifecycle_flags"]["retiring_element_ids"] is None


class _LifecycleStatementCounter:
    """Records every SELECT this task's own reads issue (application
    components, vendor products, the three technology tables)."""

    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if any(
            marker in statement
            for marker in (
                "FROM application_components",
                "FROM vendor_products",
                "FROM technology_nodes",
                "FROM technology_devices",
                "FROM technology_system_software",
            )
        ):
            self.statements.append(statement)


@pytest.fixture
def lifecycle_counter(app):
    from app.extensions import db

    counter = _LifecycleStatementCounter()
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        yield counter
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)


def test_11_selects_are_constant_regardless_of_row_count_and_zero_when_empty(
    app, db_session, make_org, lifecycle_counter
):
    org = make_org("wire1-count")
    root = _element(db_session, org.id, "Root")
    db_session.commit()

    lifecycle_counter.statements.clear()
    empty = _impact(app, org.id, root.id, with_owner=False)
    assert empty["rows"] == []
    assert lifecycle_counter.statements == []

    one = _element(db_session, org.id, "One")
    _relationship(db_session, org.id, root, one)
    _application_component(db_session, org.id, one.id, name="One", lifecycle_status="deprecated")
    db_session.commit()

    lifecycle_counter.statements.clear()
    _impact(app, org.id, root.id, with_owner=False)
    count_one = len(lifecycle_counter.statements)
    assert count_one <= 5

    for i in range(20):
        target = _element(db_session, org.id, f"Many-{i}")
        _relationship(db_session, org.id, root, target)
        _application_component(
            db_session, org.id, target.id, name=f"Many-{i}", lifecycle_status="deprecated"
        )
    db_session.commit()

    lifecycle_counter.statements.clear()
    _impact(app, org.id, root.id, with_owner=False)
    count_many = len(lifecycle_counter.statements)

    assert count_many == count_one


def test_12_with_owner_false_leaves_the_block_in_place(app, db_session, make_org):
    org = make_org("wire1-with-owner-false")
    root = _element(db_session, org.id, "Root")
    one = _element(db_session, org.id, "One")
    _relationship(db_session, org.id, root, one)
    _application_component(db_session, org.id, one.id, name="One", lifecycle_status="deprecated")
    db_session.commit()

    result = _impact(app, org.id, root.id, with_owner=False)
    row = _row_for(result["rows"], one.id)
    assert row["owner"] is None
    assert row["lifecycle"]["retiring"] is True


def test_13_summary_and_row_identity_unchanged_with_and_without_dates(app, db_session, make_org):
    org = make_org("wire1-identity")
    root = _element(db_session, org.id, "Root")
    one = _element(db_session, org.id, "One")
    _relationship(db_session, org.id, root, one)
    comp = _application_component(db_session, org.id, one.id, name="One")
    db_session.commit()

    without_dates = _impact(app, org.id, root.id, with_owner=False)

    comp.lifecycle_status = "deprecated"
    db_session.commit()

    with_dates = _impact(app, org.id, root.id, with_owner=False)

    assert with_dates["summary"] == without_dates["summary"]
    assert len(with_dates["rows"]) == len(without_dates["rows"])
    for row_a, row_b in zip(without_dates["rows"], with_dates["rows"]):
        assert row_a["element_id"] == row_b["element_id"]
        assert row_a["relation"] == row_b["relation"]


# ===========================================================================
# Fabrication (14)
# ===========================================================================


def test_14_fabrication_all_unrecorded_fixture_has_no_synthetic_values(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("wire1-fabrication")
    root = _element(db_session, org.id, "Root")
    comp_el = _element(db_session, org.id, "Comp")
    node_el = _element(db_session, org.id, "Node El", layer="technology", type_="Node")
    _relationship(db_session, org.id, root, comp_el)
    _relationship(db_session, org.id, root, node_el)
    _application_component(db_session, org.id, comp_el.id, name="Comp")  # nothing recorded
    _node(db_session, org.id, node_el.id, name="Node Row")  # nothing recorded
    db_session.commit()

    result = _impact(app, org.id, root.id, with_owner=False)

    value_fields = (
        "status",
        "planned_retirement_date",
        "end_of_life_date",
        "support_expiry",
        "licence_expiry",
        "vendor_end_of_life_date",
        "retiring",
    )
    saw_a_reasoned_block = False
    for row in result["rows"]:
        block = row.get("lifecycle")
        if block is None:
            continue
        if block["reason"] in REASON_CODES:
            saw_a_reasoned_block = True
            for field in value_fields:
                assert block[field] is None
        if block["vendor_reason"] in REASON_CODES:
            assert block["vendor_end_of_life_date"] is None
    assert saw_a_reasoned_block

    payload_text = json.dumps(result)
    assert '"status": ""' not in payload_text
    assert '"retiring": false' not in payload_text
    assert "1970-01-01" not in payload_text
    today_iso = date.today().isoformat()
    assert f'"end_of_life_date": "{today_iso}"' not in payload_text
    assert f'"planned_retirement_date": "{today_iso}"' not in payload_text


# ===========================================================================
# Route (15)
# ===========================================================================


def test_15_route_carries_lifecycle_flags_and_validates_the_window_parameter(
    app, db_session, make_org, client, login_as
):
    org = make_org("wire1-route")
    user = _user(db_session, org.id)
    root = _element(db_session, org.id, "Root")
    one = _element(db_session, org.id, "One")
    _relationship(db_session, org.id, root, one)
    _application_component(db_session, org.id, one.id, name="One", lifecycle_status="deprecated")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/impact/{root.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert set(data["lifecycle_flags"].keys()) == LIFECYCLE_FLAGS_KEYS
    row = next(r for r in data["rows"] if r["element_id"] == one.id)
    assert row["lifecycle"]["retiring"] is True

    for bad in ("0", "abc", "4000"):
        login_as(client, user)
        bad_resp = client.get(
            f"/api/v1/intelligence/impact/{root.id}?retirement_window_days={bad}"
        )
        assert bad_resp.status_code == 400
        assert bad_resp.get_json()["error"]["code"] == "INVALID_PARAMETER"


# ===========================================================================
# Latency (16)
# ===========================================================================


def test_16_latency_label_unaffected_by_the_lifecycle_block(app, db_session, make_org):
    from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

    org = make_org("wire1-latency")
    root = _element(db_session, org.id, "Root")
    one = _element(db_session, org.id, "One")
    _relationship(db_session, org.id, root, one)
    _application_component(db_session, org.id, one.id, name="One", lifecycle_status="deprecated")
    db_session.commit()

    before = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="3", include_derived="false"
    )._sum.get()

    result = _impact(app, org.id, root.id, include_derived=False, with_owner=False)

    after = INTELLIGENCE_QUERY_DURATION.labels(
        query="cross_layer_impact", depth="3", include_derived="false"
    )._sum.get()
    assert after > before
    row = _row_for(result["rows"], one.id)
    assert row["lifecycle"]["retiring"] is True
