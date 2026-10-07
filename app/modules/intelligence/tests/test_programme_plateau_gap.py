"""Tests for the programme lens's plateau/gap block: where each work package
lands (``Plateau``), which gap it closes (``Gap``), its recorded
``risk_level``/``priority``/``risk_mitigation``, and the element's own
recorded plateau classification on every affected row.

Fixtures (app, db_session, make_org, client, login_as) are discovered via
app/modules/conftest.py's import of tests.conftest.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid

import pytest
from sqlalchemy import event

from app.extensions import db
from app.modules.intelligence.services import query_service
from app.modules.intelligence.services.query_service import IntelligenceQueryService

# --- fixture builders --------------------------------------------------------


def _element(db_session, org_id, name, layer="application", **extra):
    from app.models import ArchiMateElement

    el = ArchiMateElement(
        name=name, type="ApplicationComponent", layer=layer, organization_id=org_id, **extra
    )
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


def _work_package(
    db_session,
    element,
    *,
    name="Migrate to cloud",
    status="in_progress",
    progress_percentage=40.0,
    owner_id=None,
    estimated_cost=None,
    actual_cost=None,
    start_date=None,
    end_date=None,
    plateau_id=None,
    gap_id=None,
    risk_level=None,
    priority=None,
    risk_mitigation=None,
):
    """Keyword fields, same shape as test_programme_for_element.py's own
    helper. ``risk_level``/``priority`` are only passed to the constructor
    when a real value is given, so the column's own ``default="medium"``
    still applies for a caller that leaves them unset -- the two are not
    the same thing, and test (6) below pins that distinction."""
    from app.models.unified_work_package import UnifiedWorkPackage

    kwargs = dict(
        name=name,
        archimate_element_id=element.id,
        organization_id=element.organization_id,
        business_capability="Test Capability",
        status=status,
        progress_percentage=progress_percentage,
        owner_id=owner_id,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
        start_date=start_date,
        end_date=end_date,
        plateau_id=plateau_id,
        gap_id=gap_id,
        risk_mitigation=risk_mitigation,
    )
    if risk_level is not None:
        kwargs["risk_level"] = risk_level
    if priority is not None:
        kwargs["priority"] = priority
    wp = UnifiedWorkPackage(**kwargs)
    db_session.add(wp)
    db_session.flush()
    return wp


def _plateau(db_session, org_id, *, name="Plateau", sequence_order=1, target_date=None,
             baseline_plateau_id=None):
    from app.models.implementation_migration import Plateau

    p = Plateau(
        name=name,
        organization_id=org_id,
        sequence_order=sequence_order,
        target_date=target_date,
        baseline_plateau_id=baseline_plateau_id,
    )
    db_session.add(p)
    db_session.flush()
    return p


def _gap(
    db_session,
    org_id,
    *,
    name="Gap",
    gap_kind="capability_shortfall",
    gap_type=None,
    originating_plateau_id=None,
    target_plateau_id=None,
    owner=None,
    estimated_cost=None,
    resolution_status=None,
):
    from app.models.implementation_migration import Gap

    kwargs = dict(
        name=name,
        organization_id=org_id,
        gap_kind=gap_kind,
        gap_type=gap_type,
        originating_plateau_id=originating_plateau_id,
        target_plateau_id=target_plateau_id,
        owner=owner,
        estimated_cost=estimated_cost,
    )
    if resolution_status is not None:
        kwargs["resolution_status"] = resolution_status
    gap = Gap(**kwargs)
    db_session.add(gap)
    db_session.flush()
    return gap


def _make_user(db_session, org, *, enterprise_role=None):
    from app.models.user import Role, User

    # tenant-scoping-ok: Role is a global RBAC table with no organization_id column.
    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        # tenant-scoping-ok: Role is a global RBAC table with no organization_id column.
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"pgap-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _two_org_fixture(db_session, make_org):
    """Org A: ``app_a`` serving ``app_b`` (so ``app_b`` lands in ``app_a``'s
    own blast radius); a plateau ``p1`` (``Target``, a real ``target_date``);
    a plateau-transition gap ``g1`` naming ``p1``/an earlier plateau as its
    two ends; a work package on ``app_a`` pointing at both, with a recorded
    ``risk_level``/``priority``; and ``app_b`` itself classified ``Target``.
    Org B exists only so a later test can point a work package's FK at one
    of its rows.
    """
    org_a = make_org("plategap-two-org-a")
    org_b = make_org("plategap-two-org-b")
    app_a = _element(db_session, org_a.id, "AppA")
    app_b = _element(db_session, org_a.id, "AppB", togaf_plateau="Target")
    _relationship(db_session, org_a.id, app_a, app_b, type_="Serving")
    originating = _plateau(db_session, org_a.id, name="Current state", sequence_order=0)
    p1 = _plateau(
        db_session, org_a.id, name="Target state", sequence_order=1,
        target_date=_dt.date(2027, 6, 1),
    )
    g1 = _gap(
        db_session, org_a.id, name="Close the gap", gap_kind="plateau_transition",
        originating_plateau_id=originating.id, target_plateau_id=p1.id,
    )
    wp = _work_package(
        db_session, app_a, name="Migrate", plateau_id=p1.id, gap_id=g1.id,
        risk_level="high", priority="critical",
    )
    db_session.commit()
    return org_a, org_b, app_a, app_b, wp, p1, g1


class _StatementCounter:
    """Records every SQL statement and its bound parameters."""

    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append((statement, parameters))

    def matching(self, *substrings):
        return [(s, p) for s, p in self.statements if all(sub in s for sub in substrings)]


@pytest.fixture
def statement_counter():
    counter = _StatementCounter()
    event.listen(db.engine, "before_cursor_execute", counter)
    try:
        yield counter
    finally:
        event.remove(db.engine, "before_cursor_execute", counter)


def _param_values(parameters):
    if isinstance(parameters, dict):
        return list(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return list(parameters)
    return [parameters]


# --- (1) two-organisation: A sees its own plateau/gap/priority/row --------


def test_org_a_sees_its_own_plateau_gap_priority_and_row_classification(app, db_session, make_org):
    org_a, org_b, app_a, app_b, wp, p1, g1 = _two_org_fixture(db_session, make_org)

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = IntelligenceQueryService.programme_for_element(app_a.id)

    payload = result["work_packages"][0]
    assert payload["plateau"]["plateau_id"] == p1.id
    assert payload["plateau"]["name"] == "Target state"
    assert payload["plateau"]["reason"] is None
    assert payload["gap"]["gap_id"] == g1.id
    assert payload["gap"]["gap_kind"] == "plateau_transition"
    assert payload["gap"]["originating_plateau_id"] == g1.originating_plateau_id
    assert payload["gap"]["target_plateau_id"] == p1.id
    assert payload["gap"]["reason"] is None
    assert payload["risk_level"] == "high"
    assert payload["priority"] == "critical"

    b_row = next(r for r in payload["affected_rows"] if r["element_id"] == app_b.id)
    assert b_row["plateau"] == "Target"
    assert b_row["plateau_reason"] is None


# --- (2) two-organisation: a foreign-tenant plateau/gap is never shown, ---
# --- mutation-proved --------------------------------------------------------


def test_b_owned_plateau_is_never_shown_and_the_absence_is_not_vacuous(
    app, db_session, make_org, monkeypatch
):
    from flask import g

    from app.models.implementation_migration import Plateau

    org_a = make_org("plategap-plateau-mut-a")
    org_b = make_org("plategap-plateau-mut-b")
    app_a = _element(db_session, org_a.id, "AppA")
    p_b = _plateau(db_session, org_b.id, name="Org B's plateau", sequence_order=1)
    _work_package(db_session, app_a, name="Points at B", plateau_id=p_b.id)
    db_session.commit()

    monkeypatch.setattr(query_service, "current_org_id", lambda: org_a.id)
    with app.test_request_context("/"):
        # The ORM tenant listener keys off g.current_org_id; leaving it unset
        # makes it a no-op, so only this method's own explicit predicate can
        # keep org B's plateau out of org A's answer.
        g.current_org_id = None
        result = IntelligenceQueryService.programme_for_element(app_a.id)

    plateau_block = result["work_packages"][0]["plateau"]
    assert plateau_block["reason"] == "no_plateau_recorded"
    for key, value in plateau_block.items():
        if key != "reason":
            assert value is None

    # Mutation proof: under the SAME no-ambient-tenant conditions, a select
    # that dropped this method's own organization_id predicate DOES return
    # org B's row -- the fixture is real and reachable, not vacuous.
    with app.test_request_context("/"):
        g.current_org_id = None
        predicate_dropped = db.session.execute(
            db.select(Plateau.id, Plateau.name).where(Plateau.id.in_({p_b.id}))
        ).all()
    assert predicate_dropped == [(p_b.id, p_b.name)]
    with pytest.raises(AssertionError):
        assert predicate_dropped == []


def test_b_owned_gap_is_never_shown_and_the_absence_is_not_vacuous(
    app, db_session, make_org, monkeypatch
):
    from flask import g

    from app.models.implementation_migration import Gap

    org_a = make_org("plategap-gap-mut-a")
    org_b = make_org("plategap-gap-mut-b")
    app_a = _element(db_session, org_a.id, "AppA")
    g_b = _gap(db_session, org_b.id, name="Org B's gap", gap_kind="capability_shortfall")
    _work_package(db_session, app_a, name="Points at B", gap_id=g_b.id)
    db_session.commit()

    monkeypatch.setattr(query_service, "current_org_id", lambda: org_a.id)
    with app.test_request_context("/"):
        g.current_org_id = None
        result = IntelligenceQueryService.programme_for_element(app_a.id)

    gap_block = result["work_packages"][0]["gap"]
    assert gap_block["reason"] == "no_gap_recorded"
    for key, value in gap_block.items():
        if key not in ("reason", "resolution_status_default_possible"):
            assert value is None
    assert gap_block["resolution_status_default_possible"] is True

    with app.test_request_context("/"):
        g.current_org_id = None
        predicate_dropped = db.session.execute(
            db.select(Gap.id, Gap.name).where(Gap.id.in_({g_b.id}))
        ).all()
    assert predicate_dropped == [(g_b.id, g_b.name)]
    with pytest.raises(AssertionError):
        assert predicate_dropped == []


# --- (3) a foreign-tenant element's togaf_plateau is never even asked for --


def test_foreign_tenant_elements_are_never_asked_for_a_plateau(
    app, db_session, make_org, statement_counter
):
    from flask import g

    org_a = make_org("plategap-foreign-elem-a")
    org_b = make_org("plategap-foreign-elem-b")
    app_a = _element(db_session, org_a.id, "AppA")
    foreign = _element(db_session, org_b.id, "OrgBElement", togaf_plateau="Baseline")
    # Org A's own relationship happens to point at org B's element (the
    # dangling cross-tenant pointer the identity map's own fence exists for).
    _relationship(db_session, org_a.id, app_a, foreign)
    _work_package(db_session, app_a, name="WP")
    db_session.commit()

    statement_counter.statements.clear()
    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        result = IntelligenceQueryService.programme_for_element(app_a.id)

    row = next(
        (r for r in result["work_packages"][0]["affected_rows"] if r["element_id"] == foreign.id),
        None,
    )
    assert row is not None
    assert row["plateau"] is None
    assert row["plateau_reason"] == "no_plateau_recorded"

    element_plateau_selects = statement_counter.matching("FROM archimate_elements", "is_baseline", " IN (")
    assert len(element_plateau_selects) == 1
    _, params = element_plateau_selects[0]
    bound_values = _param_values(params)
    assert foreign.id not in bound_values
    assert app_a.id in bound_values  # not vacuous -- A's own id is legitimately asked for


# --- (4) cross-tenant element id is an honest not-found, not a leak --------


def test_cross_tenant_element_id_returns_element_not_found(app, db_session, make_org):
    from flask import g

    org_a = make_org("plategap-404-a")
    org_b = make_org("plategap-404-b")
    a = _element(db_session, org_a.id, "A")
    _work_package(db_session, a, name="Tenant A's WP")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    assert result["work_packages"] == []
    assert result["reasons"] == ["element_not_found"]


# --- (5) not recorded: unset FKs ---------------------------------------------


def test_plateau_id_and_gap_id_none_are_honestly_absent(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-unset-fk")
    a = _element(db_session, org.id, "A")
    _work_package(db_session, a, name="No plateau or gap")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    payload = result["work_packages"][0]
    assert payload["plateau"]["reason"] == "no_plateau_recorded"
    assert payload["plateau"]["plateau_id"] is None
    assert payload["gap"]["reason"] == "no_gap_recorded"
    assert payload["gap"]["gap_id"] is None


# --- (6) risk_level: default vs explicit None, disclosed either way -------


def test_risk_level_none_vs_default_disclosed(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-risk-default")
    a = _element(db_session, org.id, "A")
    default_wp = _work_package(db_session, a, name="Default risk")
    explicit_none_wp = _work_package(db_session, a, name="Explicit none")
    # Set AFTER construction so the column's own default does not fill it.
    explicit_none_wp.risk_level = None
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    by_name = {wp["name"]: wp for wp in result["work_packages"]}
    assert by_name["Default risk"]["risk_level"] == "medium"
    assert by_name["Default risk"]["risk_level_default_possible"] is True
    assert by_name["Explicit none"]["risk_level"] is None
    assert by_name["Explicit none"]["risk_level_default_possible"] is True
    assert default_wp.id != explicit_none_wp.id  # not vacuous


# --- (7) a null togaf_plateau column is an honest absence, not "Baseline" -


def test_row_with_null_togaf_plateau_is_honestly_absent(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-null-plateau")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B", togaf_plateau=None)
    _relationship(db_session, org.id, a, b)
    _work_package(db_session, a, name="WP")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    row = next(r for r in result["work_packages"][0]["affected_rows"] if r["element_id"] == b.id)
    assert row["plateau"] is None
    assert row["plateau_reason"] == "no_plateau_recorded"


# --- (8) a real gap with no estimated_cost carries None, not 0 -------------


def test_gap_with_no_estimated_cost_is_none(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-gap-uncosted")
    a = _element(db_session, org.id, "A")
    g1 = _gap(db_session, org.id, name="Real gap, no cost", estimated_cost=None)
    _work_package(db_session, a, name="WP", gap_id=g1.id)
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    payload = result["work_packages"][0]
    assert payload["gap"]["gap_id"] == g1.id
    assert payload["gap"]["estimated_cost"] is None
    assert payload["gap"]["reason"] is None


# --- (9) shape: six plateau keys, eleven gap keys, seven new payload keys -


def test_plateau_and_gap_block_shapes_and_payload_key_set(app, db_session, make_org):
    from flask import g

    org_a, org_b, app_a, app_b, wp, p1, g1 = _two_org_fixture(db_session, make_org)

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        result = IntelligenceQueryService.programme_for_element(app_a.id)

    payload = result["work_packages"][0]
    assert set(payload["plateau"].keys()) == {
        "plateau_id", "name", "target_date", "sequence_order", "baseline_plateau_id", "reason",
    }
    assert len(payload["plateau"]) == 6
    assert set(payload["gap"].keys()) == {
        "gap_id", "name", "gap_kind", "gap_type", "resolution_status",
        "resolution_status_default_possible",
        "originating_plateau_id", "target_plateau_id", "owner_text",
        "estimated_cost", "access_reason", "reason",
    }
    assert len(payload["gap"]) == 12

    base_keys = {
        "work_package_id", "name", "status", "progress_percentage", "start_date",
        "end_date", "is_overdue", "owner", "cost_variance_pct", "cost_reason",
        "affected_rows", "affected_summary",
    }
    new_keys = {
        "plateau", "gap", "risk_level", "priority", "risk_mitigation",
        "risk_level_default_possible", "priority_default_possible",
    }
    assert len(new_keys) == 7
    assert set(payload.keys()) == base_keys | new_keys

    assert payload["affected_rows"]  # not vacuous
    for row in payload["affected_rows"]:
        assert "plateau" in row
        assert "plateau_reason" in row


# --- (10) three selects regardless of package count, none with no packages -


def test_three_new_selects_regardless_of_package_count(app, db_session, make_org, statement_counter):
    from flask import g

    org = make_org("plategap-select-count")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")  # a non-empty blast radius, so the
    _relationship(db_session, org.id, a, b)  # plateau-classification select
    p1 = _plateau(db_session, org.id, name="P1", sequence_order=1)  # (3) has something to ask for
    g1 = _gap(db_session, org.id, name="G1")
    _work_package(db_session, a, name="WP1", plateau_id=p1.id, gap_id=g1.id)
    db_session.commit()

    def _counts():
        statement_counter.statements.clear()
        with app.test_request_context("/"):
            g.current_org_id = org.id
            IntelligenceQueryService.programme_for_element(a.id)
        return (
            len(statement_counter.matching("FROM plateaus")),
            len(statement_counter.matching("FROM gaps")),
            len(statement_counter.matching("FROM archimate_elements", "is_baseline", " IN (")),
        )

    assert _counts() == (1, 1, 1)

    for i in range(2, 6):
        _work_package(db_session, a, name=f"WP{i}", plateau_id=p1.id, gap_id=g1.id)
    db_session.commit()

    assert _counts() == (1, 1, 1)


def test_no_new_selects_for_an_element_with_no_work_packages(app, db_session, make_org, statement_counter):
    from flask import g

    org = make_org("plategap-no-packages")
    lonely = _element(db_session, org.id, "Lonely")
    db_session.commit()

    statement_counter.statements.clear()
    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(lonely.id)

    assert result["reasons"] == ["no_work_package_recorded"]
    assert statement_counter.matching("FROM plateaus") == []
    assert statement_counter.matching("FROM gaps") == []
    assert statement_counter.matching("FROM archimate_elements", "is_baseline", " IN (") == []


# --- (11) existing fields are identical with and without plateau/gap data -


def test_existing_fields_unchanged_by_plateau_and_gap_addition(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-unchanged")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    _work_package(db_session, a, name="WP", estimated_cost=1000.0, actual_cost=1100.0)
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        with_plateau = IntelligenceQueryService.programme_for_element(a.id)
        direct = IntelligenceQueryService.cross_layer_impact(a.id, with_owner=True)

    wp_payload = with_plateau["work_packages"][0]
    assert wp_payload["cost_variance_pct"] == 10.0
    assert wp_payload["is_overdue"] is False

    # ``latency_ms`` is a per-run measurement of the call that produced the
    # summary, so it necessarily differs between two separate runs; every
    # other summary field must be identical with and without the plateau/gap
    # data on the payload.
    def _without_latency(summary):
        return {k: v for k, v in summary.items() if k != "latency_ms"}

    assert _without_latency(wp_payload["affected_summary"]) == _without_latency(
        direct["summary"]
    )

    # Same position, same depth/relation and the same other fields on every
    # row; only the two new keys (``plateau``, ``plateau_reason``) differ.
    assert len(wp_payload["affected_rows"]) == len(direct["rows"]) == 1
    for row, direct_row in zip(wp_payload["affected_rows"], direct["rows"]):
        assert row["element_id"] == direct_row["element_id"]
        assert row["relation"] == direct_row["relation"]
        stripped = {k: v for k, v in row.items() if k not in ("plateau", "plateau_reason")}
        assert stripped == direct_row


# --- (12) fabrication: absent blocks are all-None, never a defaulted value -


def test_fabrication_every_absent_block_is_all_none_never_falsy(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-fabrication")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B", togaf_plateau=None)
    _relationship(db_session, org.id, a, b)
    wp = _work_package(db_session, a, name="Uncosted", estimated_cost=None, actual_cost=None)
    wp.risk_level = None
    wp.priority = None
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    payload = result["work_packages"][0]

    assert payload["plateau"]["reason"] == "no_plateau_recorded"
    for key, value in payload["plateau"].items():
        if key != "reason":
            assert value is None

    assert payload["gap"]["reason"] == "no_gap_recorded"
    for key, value in payload["gap"].items():
        if key not in ("reason", "resolution_status_default_possible"):
            assert value is None
    assert payload["gap"]["resolution_status_default_possible"] is True

    assert payload["risk_level"] is None
    assert payload["priority"] is None
    assert payload["risk_level_default_possible"] is True
    assert payload["priority_default_possible"] is True

    for row in payload["affected_rows"]:
        if row["plateau_reason"] == "no_plateau_recorded":
            assert row["plateau"] is None

    dumped = json.dumps(result)
    assert '"risk_level": "medium"' not in dumped
    assert '"priority": "medium"' not in dumped
    assert '"plateau": "Baseline"' not in dumped
    assert '"estimated_cost": 0' not in dumped


# --- (13) route and redaction ------------------------------------------------


def test_route_redacts_gap_estimated_cost_without_budget_authority(app, db_session, make_org, client, login_as):
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("plategap-route-redact")
    user = _make_user(db_session, org, enterprise_role="solution_architect")
    a = _element(db_session, org.id, "A")
    g1 = _gap(db_session, org.id, name="Costed gap", estimated_cost=5000.0)
    db_session.add(UnifiedWorkPackage(
        name="Migrate", archimate_element_id=a.id, organization_id=org.id,
        business_capability="Test", gap_id=g1.id,
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}")
    assert resp.status_code == 200
    row = resp.get_json()["data"]["work_packages"][0]
    assert row["gap"]["estimated_cost"] is None
    assert row["gap"]["access_reason"] == "financial_data_restricted"
    # Unrelated fields are not financial figures and stay visible.
    assert row["gap"]["gap_id"] == g1.id
    assert row["gap"]["name"] == "Costed gap"


def test_route_does_not_redact_gap_estimated_cost_for_cto(app, db_session, make_org, client, login_as):
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("plategap-route-no-redact")
    user = _make_user(db_session, org, enterprise_role="cto")
    a = _element(db_session, org.id, "A")
    g1 = _gap(db_session, org.id, name="Costed gap", estimated_cost=5000.0)
    db_session.add(UnifiedWorkPackage(
        name="Migrate", archimate_element_id=a.id, organization_id=org.id,
        business_capability="Test", gap_id=g1.id,
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}")
    assert resp.status_code == 200
    row = resp.get_json()["data"]["work_packages"][0]
    assert row["gap"]["estimated_cost"] == 5000.0
    assert row["gap"]["access_reason"] is None


# --- (14) resolution_status_default_possible disclosure --------------------


def test_resolution_status_default_possible_is_true_when_gap_present(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-res-status-present")
    a = _element(db_session, org.id, "A")
    g1 = _gap(db_session, org.id, name="Gap with default status")
    _work_package(db_session, a, name="WP", gap_id=g1.id)
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    payload = result["work_packages"][0]
    assert payload["gap"]["gap_id"] == g1.id
    assert payload["gap"]["resolution_status"] == "identified"
    assert payload["gap"]["resolution_status_default_possible"] is True


def test_resolution_status_default_possible_is_true_when_gap_absent(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-res-status-absent")
    a = _element(db_session, org.id, "A")
    _work_package(db_session, a, name="No gap")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    payload = result["work_packages"][0]
    assert payload["gap"]["reason"] == "no_gap_recorded"
    assert payload["gap"]["resolution_status"] is None
    assert payload["gap"]["resolution_status_default_possible"] is True


def test_resolution_status_explicit_is_still_ambiguous(app, db_session, make_org):
    from flask import g

    org = make_org("plategap-res-status-explicit")
    a = _element(db_session, org.id, "A")
    g1 = _gap(db_session, org.id, name="Explicit status", resolution_status="in_progress")
    _work_package(db_session, a, name="WP", gap_id=g1.id)
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.programme_for_element(a.id)

    payload = result["work_packages"][0]
    assert payload["gap"]["resolution_status"] == "in_progress"
    assert payload["gap"]["resolution_status_default_possible"] is True
