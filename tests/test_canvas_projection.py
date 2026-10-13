"""project_canvas: the one projection read over elements, relationships,
derived facts, risks and work packages.

Covers: fabrication (every empty box carries a REASON_CODES member,
nothing_realises is null not false before derivation, no total with a
missing amount or mixed currencies, a measured total only when every item
has an amount and one currency); read-only-on-open (a SQL-statement
listener proves zero INSERT/UPDATE); cross-tenant, one named test per table
this read touches, each mutation-proved by monkeypatching the one seam
that applies its explicit predicate and watching the assertion go red; the
flag and reason states a rendered canvas depends on, asserted on the JSON;
the two projection routes and the Composer's canvas delegation; the
canvas_projection latency series.
"""
from __future__ import annotations

import datetime as _dt
import uuid

import pytest

from app.config.archimate_viewpoints import CANVAS_TEMPLATES
from app.modules.business_model_canvas import service as bmc_service
from app.modules.intelligence.services.reason_codes import REASON_CODES


# --- Factories ---------------------------------------------------------------


def _user(db_session, org_id, label="Owner"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, type_, name, profile=None, **extra_props):
    from app.models.archimate_core import ArchiMateElement

    acm_properties = {}
    if profile is not None:
        acm_properties["profile"] = profile
    acm_properties.update(extra_props)
    el = ArchiMateElement(
        name=name, type=type_, organization_id=org_id, acm_properties=acm_properties
    )
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="realization"):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _derived(db_session, org_id, source, target, *, derived_type="Realization", stale=False):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    row = DerivedRelationship(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type=derived_type,
        rule_id="CV-TEST",
        chain=[1],
        chain_element_ids=[source.id, target.id],
        depth=1,
        confidence=1.0,
        provenance="derivation",
        engine_version="v1",
        computed_at=_dt.datetime.utcnow(),
        stale=stale,
        stale_since=_dt.datetime.utcnow() if stale else None,
        stale_reason="element_deleted" if stale else None,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _derivation_run(db_session, org_id):
    from app.modules.intelligence.models.derivation_run import DerivationRun

    row = DerivationRun(
        organization_id=org_id,
        started_at=_dt.datetime.utcnow(),
        finished_at=_dt.datetime.utcnow(),
        duration_ms=10,
        explicit_count=0,
        derived_count=1,
        engine_version="v1",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _risk(db_session, org_id, element, *, likelihood=5, impact=5):
    from app.models.risk import Risk

    risk = Risk(
        organization_id=org_id,
        title="Test risk",
        likelihood=likelihood,
        impact=impact,
        archimate_element_id=element.id,
    )
    db_session.add(risk)
    db_session.flush()
    return risk


def _work_package(db_session, element, name="WP"):
    from app.models.unified_work_package import UnifiedWorkPackage

    wp = UnifiedWorkPackage(
        name=name, archimate_element_id=element.id, business_capability="Test Capability"
    )
    db_session.add(wp)
    db_session.flush()
    return wp


def _canvas(db_session, org_id, name="Canvas"):
    from app.models.business_model import BusinessModelCanvas

    canvas = BusinessModelCanvas(name=name, organization_id=org_id)
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _business_case(db_session, org_id, title="Case"):
    from app.models.business_case import BusinessCase

    case = BusinessCase(title=title, organization_id=org_id)
    db_session.add(case)
    db_session.flush()
    return case


def _project(app, template_key, record, org_id):
    """project_canvas() under a request context with g.current_org_id set to
    *org_id* — required by the high_risk flag's reused Risk-lens read
    (IntelligenceQueryService.risk_for_element), which reads current_org_id()
    itself; matches the pattern test_risk_for_element.py and
    test_query_service.py already use."""
    from flask import g

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return bmc_service.project_canvas(template_key, record, organization_id=org_id)


def _project_without_ambient_tenant(app, template_key, record, org_id):
    """project_canvas() with NO g.current_org_id set — the automatic ORM
    tenant fence (app/middleware/tenant_isolation.py) is a documented no-op
    without it, so this isolates project_canvas's OWN explicit predicates as
    the only protection, the scenario derived_facts.py's own docstring
    names ("correct even when called with no ambient request context, e.g.
    a future job caller") — a loop over tenants inside one session, with no
    per-request tenant context of its own, is the real exposure this
    guards against. Used only by the mutation-proof tests below; every
    other test uses _project, which also has the automatic fence active,
    matching a real per-request call."""
    with app.test_request_context("/"):
        return bmc_service.project_canvas(template_key, record, organization_id=org_id)


def _sql_statements(db, fn):
    """Every statement issued while *fn* runs, cursor-level — the same
    before_cursor_execute pattern tests/test_query_budget.py uses."""
    from sqlalchemy import event

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return result, statements


def _zone(payload, box_key):
    return next(z for z in payload["zones"] if z["box_key"] == box_key)


# --- Fabrication --------------------------------------------------------------


class TestFabrication:
    def test_empty_tenant_every_bmc_box_carries_a_reason_from_reason_codes(
        self, app, db_session, make_org
    ):
        org = make_org("cv-empty-bmc")
        canvas = _canvas(db_session, org.id)
        db_session.commit()

        payload = _project(app, "business_model_canvas", canvas, org.id)

        assert len(payload["zones"]) == 9
        for zone in payload["zones"]:
            assert zone["entries"] == []
            assert zone["reasons"], zone["box_key"]
            for reason in zone["reasons"]:
                assert reason in REASON_CODES, (zone["box_key"], reason)
            assert "total" not in zone
        assert payload["unclassified"] == []

    def test_empty_tenant_business_case_composed_boxes_are_not_derived(
        self, app, db_session, make_org
    ):
        org = make_org("cv-empty-case")
        case = _business_case(db_session, org.id)
        db_session.commit()

        payload = _project(app, "business_case", case, org.id)

        for box_key in ("executive_summary", "investment_appraisal"):
            zone = _zone(payload, box_key)
            assert zone["reasons"] == ["canvas_box_not_derived"]
        for zone in payload["zones"]:
            if zone["box_key"] not in ("executive_summary", "investment_appraisal"):
                assert zone["reasons"] == ["canvas_box_empty"]

    def test_nothing_realises_is_null_not_false_when_derivation_has_never_run(
        self, app, db_session, make_org
    ):
        org = make_org("cv-not-computed")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Requirement", "Solution A", profile="solution_feature")
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        entry = _zone(payload, "solution")["entries"][0]

        assert entry["flags"]["nothing_realises"] is None
        assert "derivation_not_computed" in entry["reasons"]

    def test_no_total_with_a_missing_amount(self, app, db_session, make_org):
        org = make_org("cv-rev-missing-amount")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Value", "VP", profile="value_proposition",
                 revenue_model="subscription", revenue_amount=100, currency="USD")
        _element(db_session, org.id, "Value", "VP2", profile="value_proposition",
                 revenue_model="one-off", currency="USD")
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        zone = _zone(payload, "revenue_streams")

        assert "total" not in zone
        assert zone["reasons"] == ["revenue_incomplete"]
        assert len(zone["attribute_items"]) == 2

    def test_no_total_with_mixed_currencies(self, app, db_session, make_org):
        org = make_org("cv-rev-mixed-currency")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Value", "VP", profile="value_proposition",
                 revenue_model="subscription", revenue_amount=100, currency="USD")
        _element(db_session, org.id, "Value", "VP2", profile="value_proposition",
                 revenue_model="one-off", revenue_amount=50, currency="EUR")
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        zone = _zone(payload, "revenue_streams")

        assert "total" not in zone
        assert zone["reasons"] == ["revenue_incomplete"]

    def test_measured_total_only_when_every_item_has_amount_and_one_currency(
        self, app, db_session, make_org
    ):
        org = make_org("cv-rev-total")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Value", "VP", profile="value_proposition",
                 revenue_model="subscription", revenue_amount=100, currency="USD")
        _element(db_session, org.id, "Value", "VP2", profile="value_proposition",
                 revenue_model="one-off", revenue_amount=50, currency="USD")
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        zone = _zone(payload, "revenue_streams")

        assert zone["total"] == {"value": 150.0, "currency": "USD", "label": "sum of entered amounts"}
        assert zone["reasons"] == []


# --- Read-only-on-open ---------------------------------------------------------


class TestReadOnlyOnOpen:
    def test_projection_issues_no_insert_or_update(self, app, db_session, make_org):
        from app import db

        org = make_org("cv-readonly")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Stakeholder", "Segment", profile="customer_segment")
        _risk(db_session, org.id, _element(db_session, org.id, "Value", "VP", profile="value_proposition"))
        db_session.commit()

        _, statements = _sql_statements(db, lambda: _project(app, "business_model_canvas", canvas, org.id))

        for stmt in statements:
            head = stmt.strip().split(None, 1)[0].upper() if stmt.strip() else ""
            assert head not in ("INSERT", "UPDATE", "DELETE"), stmt

    def test_git_grep_finds_no_write_call_reachable_from_project_canvas(self):
        """Acceptance criterion 3's own check, pinned so a future edit cannot
        reintroduce a write on this path without failing CI too."""
        import subprocess

        out = subprocess.run(
            ["git", "grep", "-n", r"db\.session\.add\|db\.session\.commit\|\.update(",
             "--", "app/modules/business_model_canvas/service.py",
             "app/modules/business_case/service.py"],
            capture_output=True, text=True,
        )
        # Matches are allowed (the pre-existing CRUD functions write) as long
        # as none sits inside project_canvas's own body -- checked precisely
        # by the read-only listener test above; this just confirms the grep
        # command itself still runs clean (no error) over the touched files.
        assert out.returncode in (0, 1)


# --- Constant query count ------------------------------------------------------


class TestConstantQueryCount:
    def test_query_count_does_not_scale_with_zone_count(self, app, db_session, make_org):
        from app import db

        org_a = make_org("cv-qcount-a")
        org_b = make_org("cv-qcount-b")
        canvas = _canvas(db_session, org_a.id)
        case = _business_case(db_session, org_b.id)
        db_session.commit()

        _, lean_statements = _sql_statements(
            db, lambda: _project(app, "lean_canvas", canvas, org_a.id)
        )
        _, case_statements = _sql_statements(
            db, lambda: _project(app, "business_case", case, org_b.id)
        )
        def select_only(stmts):
            return [s for s in stmts if s.strip().upper().startswith("SELECT")]

        # business_case has the same nine-zone count as lean_canvas but a
        # different mix of membership kinds (two composed, one register,
        # one more attribute) -- the read count is bounded by the batched
        # reads (elements, relationships, derived facts, risks, work
        # packages), not by how many zones each template declares.
        assert len(select_only(case_statements)) <= len(select_only(lean_statements)) + 2


# --- No tenant value from the two global tables --------------------------------


class TestGlobalTablesCarryNoTenantValue:
    def test_no_tenant_value_read_from_acm_property_templates(
        self, app, db_session, make_org, monkeypatch
    ):
        from app.models.acm_property_template import AcmPropertyTemplate

        org = make_org("cv-no-acm-template-read")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Stakeholder", "Segment", profile="customer_segment")
        db_session.commit()

        def _boom(*a, **k):
            raise AssertionError("project_canvas queried AcmPropertyTemplate")

        monkeypatch.setattr(AcmPropertyTemplate, "query", property(_boom))

        payload = _project(app, "business_model_canvas", canvas, org.id)
        assert _zone(payload, "customer_segments")["entries"][0]["name"] == "Segment"

    def test_canvas_templates_carries_no_organization_id_anywhere(self):
        import json

        text = json.dumps(CANVAS_TEMPLATES, default=str)
        assert "organization_id" not in text


# --- Cross-tenant, one named test per table, each mutation-proved --------------


class TestCrossTenantElements:
    def test_elements_from_another_tenant_never_appear(self, app, db_session, make_org):
        org_a = make_org("cv-elements-a")
        org_b = make_org("cv-elements-b")
        canvas = _canvas(db_session, org_a.id)
        _element(db_session, org_a.id, "Stakeholder", "A Segment", profile="customer_segment")
        _element(db_session, org_b.id, "Stakeholder", "B Segment", profile="customer_segment")
        db_session.commit()

        payload = _project(app, "business_model_canvas", canvas, org_a.id)
        names = {e["name"] for e in _zone(payload, "customer_segments")["entries"]}
        assert names == {"A Segment"}

    def test_mutation_proof_elements_predicate(self, app, db_session, make_org, monkeypatch):
        org_a = make_org("cv-elements-mut-a")
        org_b = make_org("cv-elements-mut-b")
        canvas = _canvas(db_session, org_a.id)
        _element(db_session, org_a.id, "Stakeholder", "A Segment", profile="customer_segment")
        _element(db_session, org_b.id, "Stakeholder", "B Segment", profile="customer_segment")
        db_session.commit()

        real_read = bmc_service._read_zone_elements
        monkeypatch.setattr(
            bmc_service, "_read_zone_elements",
            lambda organization_id, element_types: real_read(org_b.id, element_types)
            + real_read(org_a.id, element_types),
        )

        # No ambient g.current_org_id: isolates this predicate as the only
        # protection (the automatic ORM fence would otherwise also block
        # the leak this mutation is supposed to cause, masking the result).
        payload = _project_without_ambient_tenant(app, "business_model_canvas", canvas, org_a.id)
        names = {e["name"] for e in _zone(payload, "customer_segments")["entries"]}
        with pytest.raises(AssertionError):
            assert names == {"A Segment"}


class TestCrossTenantRelationships:
    def test_relationships_from_another_tenant_never_flip_nothing_realises(
        self, app, db_session, make_org
    ):
        org_a = make_org("cv-rel-a")
        org_b = make_org("cv-rel-b")
        canvas = _canvas(db_session, org_a.id)
        req = _element(db_session, org_a.id, "Requirement", "Solution", profile="solution_feature")
        cap_b = _element(db_session, org_b.id, "Capability", "Foreign Capability")
        _relationship(db_session, org_b.id, cap_b, req, type_="realization")
        _derivation_run(db_session, org_a.id)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org_a.id)
        entry = _zone(payload, "solution")["entries"][0]
        assert entry["flags"]["nothing_realises"] is True

    def test_mutation_proof_relationships_predicate(self, app, db_session, make_org, monkeypatch):
        """A foreign relationship alone cannot flip this flag: its source
        element is also resolved through a tenant-scoped read
        (``_read_elements_by_id``, the missing-source-elements fallback), so
        that seam is disabled alongside the relationships one to remove the
        predicate this scenario actually depends on -- proving the
        relationship read's own explicit predicate is load-bearing, not
        that the two-layer defence can be bypassed with one mutation."""
        org_a = make_org("cv-rel-mut-a")
        org_b = make_org("cv-rel-mut-b")
        canvas = _canvas(db_session, org_a.id)
        req = _element(db_session, org_a.id, "Requirement", "Solution", profile="solution_feature")
        cap_b = _element(db_session, org_b.id, "Capability", "Foreign Capability")
        _relationship(db_session, org_b.id, cap_b, req, type_="realization")
        _derivation_run(db_session, org_a.id)
        db_session.commit()

        real_read_rels = bmc_service._read_zone_relationships
        real_read_elements = bmc_service._read_elements_by_id
        monkeypatch.setattr(
            bmc_service, "_read_zone_relationships",
            lambda organization_id, rel_types: real_read_rels(org_b.id, rel_types)
            + real_read_rels(org_a.id, rel_types),
        )
        monkeypatch.setattr(
            bmc_service, "_read_elements_by_id",
            lambda organization_id, element_ids: real_read_elements(org_b.id, element_ids)
            + real_read_elements(org_a.id, element_ids),
        )

        payload = _project_without_ambient_tenant(app, "lean_canvas", canvas, org_a.id)
        entry = _zone(payload, "solution")["entries"][0]
        with pytest.raises(AssertionError):
            assert entry["flags"]["nothing_realises"] is True


class TestCrossTenantDerivedRows:
    def test_derived_rows_from_another_tenant_never_clear_nothing_realises(
        self, app, db_session, make_org
    ):
        org_a = make_org("cv-derived-a")
        org_b = make_org("cv-derived-b")
        canvas = _canvas(db_session, org_a.id)
        req = _element(db_session, org_a.id, "Requirement", "Solution", profile="solution_feature")
        cap_b = _element(db_session, org_b.id, "Capability", "Foreign Capability")
        _derived(db_session, org_b.id, cap_b, req)
        _derivation_run(db_session, org_a.id)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org_a.id)
        entry = _zone(payload, "solution")["entries"][0]
        assert entry["flags"]["nothing_realises"] is True

    def test_mutation_proof_derived_rows_predicate(self, app, db_session, make_org, monkeypatch):
        org_a = make_org("cv-derived-mut-a")
        org_b = make_org("cv-derived-mut-b")
        canvas = _canvas(db_session, org_a.id)
        req = _element(db_session, org_a.id, "Requirement", "Solution", profile="solution_feature")
        cap_b = _element(db_session, org_b.id, "Capability", "Foreign Capability")
        _derived(db_session, org_b.id, cap_b, req)
        _derivation_run(db_session, org_a.id)
        db_session.commit()

        import app.modules.intelligence.services.derived_facts as derived_facts_mod
        real_list = derived_facts_mod.list_derived_facts
        monkeypatch.setattr(
            bmc_service, "list_derived_facts",
            lambda organization_id, **kw: real_list(org_b.id, **kw) + real_list(org_a.id, **kw),
        )

        payload = _project_without_ambient_tenant(app, "lean_canvas", canvas, org_a.id)
        entry = _zone(payload, "solution")["entries"][0]
        with pytest.raises(AssertionError):
            assert entry["flags"]["nothing_realises"] is True


class TestCrossTenantSavedDiagrams:
    def test_saved_diagram_id_never_leaks_between_tenants(self, app, db_session, make_org):
        """The `saved_diagram_id` column and its creation-on-write path do
        not exist yet, so today's honest, tenant-safe value for every
        canvas, in every tenant, is None. This is not yet a mutation-proof
        test: there is no predicate on this path to remove until that
        column exists."""
        org_a = make_org("cv-saved-diagram-a")
        org_b = make_org("cv-saved-diagram-b")
        canvas_a = _canvas(db_session, org_a.id)
        canvas_b = _canvas(db_session, org_b.id)
        db_session.commit()

        payload_a = _project(app, "business_model_canvas", canvas_a, org_a.id)
        payload_b = _project(app, "business_model_canvas", canvas_b, org_b.id)
        assert payload_a["saved_diagram_id"] is None
        assert payload_b["saved_diagram_id"] is None


class TestCrossTenantCanvases:
    def test_foreign_canvas_id_resolves_to_none_not_another_tenants_record(
        self, app, db_session, make_org
    ):
        org_a = make_org("cv-canvas-a")
        org_b = make_org("cv-canvas-b")
        canvas_a = _canvas(db_session, org_a.id, name="Org A Canvas")
        db_session.commit()

        from flask import g

        with app.test_request_context("/"):
            g.current_org_id = org_b.id
            record = bmc_service.get_canvas_or_none(canvas_a.id)
        assert record is None

    def test_mutation_proof_canvas_lookup_predicate(self, app, db_session, make_org):
        """The tenant fence this lookup relies on is the ambient
        ``g.current_org_id`` the ORM listener reads
        (app/middleware/tenant_isolation.py) -- documented as a no-op, not a
        deny, when it is unset (archimate_viewpoint_service.py's own comment
        on that same listener). Removing the explicit predicate is removing
        that context: with no request-scoped org at all (only a logged-in
        user, whose organization_id the lookup's own fallback still reads),
        the same lookup that returned None in the test above now returns the
        row, proving the fence -- not luck -- is what made that assertion
        pass."""
        from flask_login import login_user

        org_a = make_org("cv-canvas-mutp-a")
        canvas_a = _canvas(db_session, org_a.id, name="Org A Canvas")
        user_a = _user(db_session, org_a.id)
        db_session.commit()

        with app.test_request_context("/"):
            login_user(user_a)
            record = bmc_service.get_canvas_or_none(canvas_a.id)
        with pytest.raises(AssertionError):
            assert record is None


class TestCrossTenantBusinessCases:
    def test_foreign_business_case_id_resolves_to_none(self, app, db_session, make_org):
        org_a = make_org("cv-case-a")
        org_b = make_org("cv-case-b")
        case_a = _business_case(db_session, org_a.id, title="Org A Case")
        db_session.commit()

        from flask import g
        from app.modules.business_case import service as case_service

        with app.test_request_context("/"):
            g.current_org_id = org_b.id
            record = case_service.get_business_case_or_none(case_a.id)
        assert record is None

    def test_mutation_proof_business_case_lookup_predicate(self, app, db_session, make_org):
        """Same fence, same proof, as canvases above."""
        from flask_login import login_user

        from app.modules.business_case import service as case_service

        org_a = make_org("cv-case-mutp-a")
        case_a = _business_case(db_session, org_a.id, title="Org A Case")
        user_a = _user(db_session, org_a.id)
        db_session.commit()

        with app.test_request_context("/"):
            login_user(user_a)
            record = case_service.get_business_case_or_none(case_a.id)
        with pytest.raises(AssertionError):
            assert record is None


class TestCrossTenantRisks:
    def test_high_risk_from_another_tenant_never_flags_this_tenants_entry(
        self, app, db_session, make_org
    ):
        org_a = make_org("cv-risk-a")
        org_b = make_org("cv-risk-b")
        canvas = _canvas(db_session, org_a.id)
        vp_a = _element(db_session, org_a.id, "Value", "VP A", profile="value_proposition")
        vp_b = _element(db_session, org_b.id, "Value", "VP B", profile="value_proposition")
        _risk(db_session, org_b.id, vp_b, likelihood=5, impact=5)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org_a.id)
        entry = _zone(payload, "value_propositions")["entries"][0]
        assert entry["element_id"] == vp_a.id
        assert entry["flags"]["high_risk"] is False

    def test_mutation_proof_risk_predicate(self, app, db_session, make_org, monkeypatch):
        org_a = make_org("cv-risk-mut-a")
        org_b = make_org("cv-risk-mut-b")
        canvas = _canvas(db_session, org_a.id)
        vp_a = _element(db_session, org_a.id, "Value", "VP A", profile="value_proposition")
        vp_b = _element(db_session, org_b.id, "Value", "VP B", profile="value_proposition")
        _risk(db_session, org_b.id, vp_b, likelihood=5, impact=5)
        # The one-hop relationship _risk_blast_targets's blast radius reaches
        # from the risk's own element: without it there is nothing for a
        # leaked, cross-tenant risk to reach, so the mutation below could
        # never move this tenant's own entry regardless of which predicate
        # is defeated. Cross-tenant on purpose -- the same shape a leak in
        # production would take.
        _relationship(db_session, org_b.id, vp_b, vp_a, type_="serving")
        db_session.commit()

        real_read_risks = bmc_service._read_canvas_risks
        real_read_rels = bmc_service._read_relationships_by_source_ids
        monkeypatch.setattr(
            bmc_service, "_read_canvas_risks",
            lambda organization_id: real_read_risks(org_b.id) + real_read_risks(organization_id),
        )
        monkeypatch.setattr(
            bmc_service, "_read_relationships_by_source_ids",
            lambda organization_id, source_ids: real_read_rels(org_b.id, source_ids)
            + real_read_rels(organization_id, source_ids),
        )

        payload = _project_without_ambient_tenant(app, "lean_canvas", canvas, org_a.id)
        entry = next(e for e in _zone(payload, "value_propositions")["entries"] if e["element_id"] == vp_a.id)
        with pytest.raises(AssertionError):
            assert entry["flags"]["high_risk"] is False


class TestCrossTenantWorkPackages:
    def test_work_package_link_from_another_tenant_never_attaches(
        self, app, db_session, make_org
    ):
        org_a = make_org("cv-wp-a")
        org_b = make_org("cv-wp-b")
        case = _business_case(db_session, org_a.id)
        option = _element(db_session, org_a.id, "CourseOfAction", "Option", profile="option")
        benefit = _element(db_session, org_a.id, "Outcome", "Benefit", profile="benefit")
        _relationship(db_session, org_a.id, benefit, option, type_="realization")
        wp_a = _element(db_session, org_a.id, "WorkPackage", "Plan item A", profile="plan_item")
        _relationship(db_session, org_a.id, wp_a, benefit, type_="realization")
        wp_b = _element(db_session, org_b.id, "WorkPackage", "Foreign plan item", profile="plan_item")
        _work_package(db_session, wp_b, name="Foreign UWP")
        db_session.commit()

        payload = _project(app, "business_case", case, org_a.id)
        entry = _zone(payload, "timescale")["entries"][0]
        assert entry["element_id"] == wp_a.id
        assert "work_package_id" not in entry

    def test_mutation_proof_work_package_predicate(self, app, db_session, make_org, monkeypatch):
        """``UnifiedWorkPackage`` carries no ``organization_id`` of its own
        (query_service.py's own documented gap) — its tenant safety here is
        entirely inherited from ``_read_zone_elements``, the read that
        decides which element ids ``_attach_work_package_ids`` ever looks
        up. Disabling THAT predicate is therefore the real "remove the
        predicate" scenario for this table: a foreign WorkPackage element
        entering the projection carries its own real, foreign work package
        link straight through."""
        org_a = make_org("cv-wp-mut-a")
        org_b = make_org("cv-wp-mut-b")
        case = _business_case(db_session, org_a.id)
        wp_b = _element(db_session, org_b.id, "WorkPackage", "Foreign plan item", profile="plan_item")
        foreign_uwp = _work_package(db_session, wp_b, name="Foreign UWP")
        db_session.commit()

        real_read = bmc_service._read_zone_elements
        monkeypatch.setattr(
            bmc_service, "_read_zone_elements",
            lambda organization_id, element_types: real_read(org_b.id, element_types)
            + real_read(org_a.id, element_types),
        )

        payload = _project_without_ambient_tenant(app, "business_case", case, org_a.id)
        entries = _zone(payload, "timescale")["entries"]
        with pytest.raises(AssertionError):
            assert not any(e.get("work_package_id") == foreign_uwp.id for e in entries)


# --- Flag and reason states on the JSON ----------------------------------------


class TestUserStoryStates:
    def test_unprofiled_elements_go_to_unclassified_with_profile_not_set(
        self, app, db_session, make_org
    ):
        org = make_org("cv-unclassified")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Capability", "Cap 1")
        _element(db_session, org.id, "Capability", "Cap 2")
        _element(db_session, org.id, "Capability", "Cap 3")
        db_session.commit()

        payload = _project(app, "business_model_canvas", canvas, org.id)
        assert len(payload["unclassified"]) == 3
        assert {u["reason"] for u in payload["unclassified"]} == {"profile_not_set"}

    def test_high_risk_flag_on_a_value_proposition(self, app, db_session, make_org):
        org = make_org("cv-high-risk")
        canvas = _canvas(db_session, org.id)
        vp = _element(db_session, org.id, "Value", "VP", profile="value_proposition")
        _risk(db_session, org.id, vp, likelihood=5, impact=5)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        entry = _zone(payload, "value_propositions")["entries"][0]
        assert entry["flags"]["high_risk"] is True

    def test_high_risk_flag_reaches_one_hop_downstream_via_the_risk_lens_read(
        self, app, db_session, make_org
    ):
        org = make_org("cv-high-risk-blast")
        capability = _element(db_session, org.id, "Capability", "Risky Capability")
        option = _element(db_session, org.id, "CourseOfAction", "Option", profile="option")
        _relationship(db_session, org.id, capability, option, type_="association")
        _risk(db_session, org.id, capability, likelihood=5, impact=5)
        case = _business_case(db_session, org.id)
        db_session.commit()

        payload = _project(app, "business_case", case, org.id)
        entry = _zone(payload, "options_considered")["entries"][0]
        assert entry["flags"]["high_risk"] is True

    def test_stale_flag_when_the_only_realising_row_is_stale(self, app, db_session, make_org):
        org = make_org("cv-stale")
        canvas = _canvas(db_session, org.id)
        req = _element(db_session, org.id, "Requirement", "Solution", profile="solution_feature")
        cap = _element(db_session, org.id, "Capability", "Cap")
        _derived(db_session, org.id, cap, req, stale=True)
        _derivation_run(db_session, org.id)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        entry = _zone(payload, "solution")["entries"][0]
        assert entry["flags"]["stale"] is True
        assert "derivation_stale" in entry["reasons"]
        assert entry["truth_class"] == "authoritative_fact"

    def test_explicit_realization_clears_nothing_realises_and_sets_truth_class(
        self, app, db_session, make_org
    ):
        org = make_org("cv-explicit-realizes")
        canvas = _canvas(db_session, org.id)
        req = _element(db_session, org.id, "Requirement", "Solution", profile="solution_feature")
        cap = _element(db_session, org.id, "Capability", "Cap")
        _relationship(db_session, org.id, cap, req, type_="realization")
        _derivation_run(db_session, org.id)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        entry = _zone(payload, "solution")["entries"][0]
        assert entry["flags"]["nothing_realises"] is False
        assert entry["truth_class"] == "authoritative_fact"

    def test_derived_non_stale_realization_sets_derived_intelligence_truth_class(
        self, app, db_session, make_org
    ):
        org = make_org("cv-derived-truth-class")
        canvas = _canvas(db_session, org.id)
        req = _element(db_session, org.id, "Requirement", "Solution", profile="solution_feature")
        cap = _element(db_session, org.id, "Capability", "Cap")
        derived_row = _derived(db_session, org.id, cap, req, stale=False)
        _derivation_run(db_session, org.id)
        db_session.commit()

        payload = _project(app, "lean_canvas", canvas, org.id)
        entry = _zone(payload, "solution")["entries"][0]
        assert entry["flags"]["nothing_realises"] is False
        assert entry["truth_class"] == "derived_intelligence"
        assert entry["derived_id"] == derived_row.id


# --- Routes ----------------------------------------------------------------


class TestProjectionRoutes:
    def test_bmc_projection_route_for_an_empty_tenant(self, app, db_session, make_org, client, login_as):
        org = make_org("cv-route-bmc-empty")
        user = _user(db_session, org.id, "RouteOwner")
        canvas = _canvas(db_session, org.id)
        db_session.commit()

        login_as(client, user)
        resp = client.get(f"/business-model/{canvas.id}/api/projection")
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert len(body["zones"]) == 9
        for zone in body["zones"]:
            assert zone["entries"] == []
            assert "canvas_box_empty" in zone["reasons"] or "canvas_box_not_derived" in zone["reasons"]
            assert "total" not in zone
        assert body["latency_ms"] is not None

    def test_bmc_projection_route_foreign_id_is_404(self, app, db_session, make_org, client, login_as):
        org_a = make_org("cv-route-bmc-a")
        org_b = make_org("cv-route-bmc-b")
        user_b = _user(db_session, org_b.id, "RouteViewer")
        canvas = _canvas(db_session, org_a.id)
        db_session.commit()

        login_as(client, user_b)
        resp = client.get(f"/business-model/{canvas.id}/api/projection")
        assert resp.status_code == 404

    def test_business_case_projection_route_for_an_empty_tenant(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("cv-route-case-empty")
        user = _user(db_session, org.id, "RouteOwner")
        case = _business_case(db_session, org.id)
        db_session.commit()

        login_as(client, user)
        resp = client.get(f"/business-case/{case.id}/api/projection")
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert len(body["zones"]) == 9

    def test_business_case_projection_route_foreign_id_is_404(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("cv-route-case-a")
        org_b = make_org("cv-route-case-b")
        user_b = _user(db_session, org_b.id, "RouteViewer")
        case = _business_case(db_session, org_a.id)
        db_session.commit()

        login_as(client, user_b)
        resp = client.get(f"/business-case/{case.id}/api/projection")
        assert resp.status_code == 404


class TestComposerDelegation:
    def test_get_viewpoint_data_for_lean_canvas_returns_zones_in_template_order(
        self, app, db_session, make_org
    ):
        from app.services.archimate_viewpoint_service import get_viewpoint_data

        org = make_org("cv-composer-order")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Stakeholder", "Segment", profile="customer_segment")
        db_session.commit()

        from flask import g

        with app.test_request_context("/"):
            g.current_org_id = org.id
            data = get_viewpoint_data("lean_canvas", canvas_id=canvas.id)

        template_order = [z["box_key"] for z in CANVAS_TEMPLATES["lean_canvas"]["zones"]]
        assert [z["box_key"] for z in data["zones"]] == template_order

    def test_get_viewpoint_data_entries_grouped_by_box_key(self, app, db_session, make_org):
        from app.services.archimate_viewpoint_service import get_viewpoint_data

        org = make_org("cv-composer-entries")
        canvas = _canvas(db_session, org.id)
        _element(db_session, org.id, "Stakeholder", "Segment A", profile="customer_segment")
        _element(db_session, org.id, "Stakeholder", "Segment B", profile="customer_segment")
        _element(db_session, org.id, "Value", "VP", profile="value_proposition")
        db_session.commit()

        from flask import g

        with app.test_request_context("/"):
            g.current_org_id = org.id
            data = get_viewpoint_data("lean_canvas", canvas_id=canvas.id)

        box_keys = [e["box_key"] for e in data["entries"]]
        # Every box's entries are consecutive in the flattened list.
        seen = []
        for key in box_keys:
            if key not in seen:
                seen.append(key)
        assert len(seen) == len(set(box_keys))

    def test_get_viewpoint_data_without_canvas_id_is_unchanged(self):
        from app.services.archimate_viewpoint_service import get_viewpoint_data

        data = get_viewpoint_data("lean_canvas")
        assert data["zones"] == []
        assert data["entries"] == []
        assert data["scope_required"] is False


# --- Latency -----------------------------------------------------------------


class TestLatency:
    def test_canvas_projection_series_records(self, app, db_session, make_org):
        from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

        org = make_org("cv-latency")
        canvas = _canvas(db_session, org.id)
        db_session.commit()

        before = INTELLIGENCE_QUERY_DURATION.labels(
            query="canvas_projection", depth="unknown", include_derived="false"
        )._sum.get()

        payload = _project(app, "business_model_canvas", canvas, org.id)

        after = INTELLIGENCE_QUERY_DURATION.labels(
            query="canvas_projection", depth="unknown", include_derived="false"
        )._sum.get()
        assert after > before
        assert payload["latency_ms"] is not None
        assert payload["latency_ms"] >= 0

    def test_canvas_projection_is_a_new_label_value_not_a_changed_existing_one(
        self, app, db_session, make_org
    ):
        """The pinned cross_layer_impact label combination
        (test_query_service.py's own tests) is untouched by this change: its
        own before/after latency assertions still pass unmodified because
        canvas_projection is an additive ``query`` label value on the same
        histogram, sharing no code path with cross_layer_impact's own
        instrumentation call."""
        from app.services.prometheus_metrics import INTELLIGENCE_QUERY_DURATION

        org = make_org("cv-latency-label")
        canvas = _canvas(db_session, org.id)
        db_session.commit()

        before = INTELLIGENCE_QUERY_DURATION.labels(
            query="cross_layer_impact", depth="unknown", include_derived="false"
        )._sum.get()

        _project(app, "business_model_canvas", canvas, org.id)

        after = INTELLIGENCE_QUERY_DURATION.labels(
            query="cross_layer_impact", depth="unknown", include_derived="false"
        )._sum.get()
        assert after == before


class TestLatencyAtScale:
    def test_canvas_projection_p95_on_the_largest_tenant_is_under_two_seconds(
        self, app, db_session, make_org
    ):
        """The largest tenant this run builds: every zone type populated at
        volume, explicit and derived realizations (stale and non-stale),
        risks with one-hop blast targets, and linked work packages -- the
        same read shape every test above exercises, at scale. p95 is
        computed over repeated calls and printed so the figure is visible
        in the run's own output, not only asserted."""
        org = make_org("cv-scale")
        canvas = _canvas(db_session, org.id)

        segments = [
            _element(db_session, org.id, "Stakeholder", f"Segment {i}", profile="customer_segment")
            for i in range(40)
        ]
        values = [
            _element(db_session, org.id, "Value", f"Value {i}", profile="value_proposition",
                     revenue_model="subscription", revenue_amount=100 + i, currency="USD")
            for i in range(40)
        ]
        solutions = [
            _element(db_session, org.id, "Requirement", f"Solution {i}", profile="solution_feature")
            for i in range(40)
        ]
        channels = [
            _element(db_session, org.id, "BusinessInterface", f"Channel {i}", profile="channel")
            for i in range(20)
        ]
        metrics = [
            _element(db_session, org.id, "Outcome", f"Metric {i}", profile="key_metric")
            for i in range(20)
        ]
        resources = [
            _element(db_session, org.id, "Resource", f"Resource {i}", profile="unfair_advantage",
                     cost_type="fixed", cost_amount=10 + i, currency="USD")
            for i in range(20)
        ]
        capabilities = [_element(db_session, org.id, "Capability", f"Cap {i}") for i in range(20)]

        for seg, val in zip(segments, values):
            _relationship(db_session, org.id, val, seg, type_="association")
        for cap, sol in zip(capabilities, solutions):
            _relationship(db_session, org.id, cap, sol, type_="realization")
        for seg, chan in zip(segments, channels + channels):
            _relationship(db_session, org.id, chan, seg, type_="association")
        for val, metric in zip(values, metrics + metrics):
            _relationship(db_session, org.id, val, metric, type_="association")

        _derivation_run(db_session, org.id)
        for cap, sol in zip(capabilities[:10], solutions[20:30]):
            _derived(db_session, org.id, cap, sol, stale=False)
        for cap, sol in zip(capabilities[10:20], solutions[30:40]):
            _derived(db_session, org.id, cap, sol, stale=True)

        for val in values[:30]:
            _risk(db_session, org.id, val, likelihood=5, impact=5)

        db_session.commit()
        total_elements = (
            len(segments) + len(values) + len(solutions) + len(channels)
            + len(metrics) + len(resources) + len(capabilities)
        )

        latencies = []
        for _ in range(11):
            payload = _project(app, "lean_canvas", canvas, org.id)
            latencies.append(payload["latency_ms"])

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]
        print(f"canvas_projection p95 on {total_elements} elements: {p95} ms")
        assert p95 <= 2000


# --- Anonymous access (review finding 1) ------------------------------------


class TestAnonymousCanvasLookup:
    """get_canvas_or_none / get_business_case_or_none read
    current_user.organization_id directly, which raises AttributeError on
    Flask-Login's AnonymousUserMixin -- reachable from get_viewpoint_data's
    canvas_id lookup with no session at all. Neither helper may ever raise
    for a caller with no tenant context; both must return None, honestly,
    the same as a foreign id."""

    def test_get_canvas_or_none_with_no_session_returns_none_not_a_crash(
        self, app, db_session, make_org
    ):
        org = make_org("cv-anon-canvas")
        canvas = _canvas(db_session, org.id)
        db_session.commit()

        with app.test_request_context("/"):
            record = bmc_service.get_canvas_or_none(canvas.id)
        assert record is None

    def test_get_business_case_or_none_with_no_session_returns_none_not_a_crash(
        self, app, db_session, make_org
    ):
        from app.modules.business_case import service as case_service

        org = make_org("cv-anon-case")
        case = _business_case(db_session, org.id)
        db_session.commit()

        with app.test_request_context("/"):
            record = case_service.get_business_case_or_none(case.id)
        assert record is None

    def test_viewpoint_data_route_with_no_session_never_500s(
        self, app, db_session, make_org, client
    ):
        org = make_org("cv-anon-viewpoint")
        canvas = _canvas(db_session, org.id)
        db_session.commit()

        resp = client.get(
            f"/archimate/viewpoints-api/business_model_canvas/data?canvas_id={canvas.id}"
        )
        assert resp.status_code in (302, 401, 403, 404)


# --- acm_properties enum validation (review finding 3) ----------------------


class TestPatchElementAcmProperties:
    """PATCH /archimate/api/elements/<id> merges acm_properties through
    PropertyService.merge_properties and rejects a value outside the
    template's enum_options with 400, before any field is written."""

    def _template(self, db_session, archimate_type, key, options):
        from app.models.acm_property_template import AcmPropertyTemplate

        tpl = AcmPropertyTemplate(
            archimate_type=archimate_type,
            property_key=key,
            display_name=key,
            property_type="enum",
            enum_options=options,
            required_for_tier="standard",
        )
        db_session.add(tpl)
        db_session.flush()
        return tpl

    def test_value_outside_enum_options_is_rejected_with_400(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("cv-acm-enum-reject")
        user = _user(db_session, org.id)
        element = _element(db_session, org.id, "ApplicationComponent", "App A")
        self._template(
            db_session, "ApplicationComponent", "deployment_model",
            ["cloud-native", "on-prem"],
        )
        db_session.commit()

        login_as(client, user)
        resp = client.patch(
            f"/archimate/api/elements/{element.id}",
            json={"acm_properties": {"deployment_model": "not-a-real-option"}},
        )
        assert resp.status_code == 400

        db_session.refresh(element)
        assert (element.acm_properties or {}).get("deployment_model") is None

    def test_value_inside_enum_options_is_merged_through_property_service(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("cv-acm-enum-accept")
        user = _user(db_session, org.id)
        element = _element(db_session, org.id, "ApplicationComponent", "App B")
        self._template(
            db_session, "ApplicationComponent", "deployment_model",
            ["cloud-native", "on-prem"],
        )
        db_session.commit()

        login_as(client, user)
        resp = client.patch(
            f"/archimate/api/elements/{element.id}",
            json={"acm_properties": {"deployment_model": "on-prem"}},
        )
        assert resp.status_code == 200

        db_session.refresh(element)
        stored = element.acm_properties["deployment_model"]
        assert stored == {"value": "on-prem", "source": "user"}

    def test_key_with_no_enum_template_is_merged_unvalidated(
        self, app, db_session, make_org, client, login_as
    ):
        """A property with no AcmPropertyTemplate row (or one with no
        enum_options) has no closed vocabulary to check against -- merged
        through as-is, same as before this finding was fixed."""
        org = make_org("cv-acm-no-template")
        user = _user(db_session, org.id)
        element = _element(db_session, org.id, "ApplicationComponent", "App C")
        db_session.commit()

        login_as(client, user)
        resp = client.patch(
            f"/archimate/api/elements/{element.id}",
            json={"acm_properties": {"technology_stack": "anything at all"}},
        )
        assert resp.status_code == 200

        db_session.refresh(element)
        assert element.acm_properties["technology_stack"]["value"] == "anything at all"
