"""Consolidation candidates, opportunities and savings belong to one organisation.

Two organisations, each with candidates, opportunities and savings. No mocked tenant
context for the routes: each organisation signs in and reads the real pages and API.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from tests.test_compliance_policies_org_fence import _user

TABLES = ("consolidation_candidates", "consolidation_opportunities", "savings_realizations")


def test_the_three_models_are_tenant_models():
    from app.models.consolidation import (
        ConsolidationCandidate,
        ConsolidationOpportunity,
        SavingsRealization,
    )
    from app.models.mixins.core import TenantMixin

    for model in (ConsolidationCandidate, ConsolidationOpportunity, SavingsRealization):
        assert issubclass(model, TenantMixin), model.__name__


def test_the_three_tables_are_no_longer_listed_as_unfenced():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("unfenced_tables.txt", "unfenced_tables_baseline.txt"):
        listed = {
            line.strip()
            for line in (root / name).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        assert not listed & set(TABLES), name


def _app(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(db_session, make_org):
    from app.models.consolidation import (
        ConsolidationCandidate,
        ConsolidationOpportunity,
        SavingsRealization,
    )

    class World:
        pass

    w = World()
    tag = uuid.uuid4().hex[:6]
    w.org_a, w.org_b = make_org("csa"), make_org("csb")
    w.user_a, w.user_b = _user(db_session, w.org_a), _user(db_session, w.org_b)
    w.names = {}
    for key, org in (("a", w.org_a), ("b", w.org_b)):
        first = _app(db_session, org, f"Alpha {key} {tag}")
        second = _app(db_session, org, f"Beta {key} {tag}")
        cand = ConsolidationCandidate(
            organization_id=org.id,
            primary_application_id=first.id,
            duplicate_application_id=second.id,
            similarity_score=0.9,
            detection_method="name_match",
            status="pending_review",
        )
        opp = ConsolidationOpportunity(
            organization_id=org.id,
            name=f"Merge plan {key.upper()} {tag}",
            status="approved",
            target_application_id=first.id,
            estimated_annual_savings=111111.0 if key == "a" else 777777.0,
        )
        db_session.add_all([cand, opp])
        db_session.flush()
        sav = SavingsRealization(
            organization_id=org.id,
            opportunity_id=opp.id,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 3, 31),
            realized_savings=4242.0 if key == "a" else 9191.0,
            savings_category="license",
        )
        db_session.add(sav)
        setattr(w, f"cand_{key}", cand)
        setattr(w, f"opp_{key}", opp)
        setattr(w, f"sav_{key}", sav)
        w.names[key] = (f"Alpha {key} {tag}", f"Merge plan {key.upper()} {tag}")
    db_session.commit()
    return w


def test_the_service_returns_only_the_active_organisations_rows(world, tenant_ctx):
    from app.services.consolidation_service import ConsolidationService as S

    for key, org in (("a", world.org_a), ("b", world.org_b)):
        with tenant_ctx(org.id):
            assert {c["id"] for c in S.get_all_candidates()} == {getattr(world, f"cand_{key}").id}
            assert {o["id"] for o in S.get_all_opportunities()} == {getattr(world, f"opp_{key}").id}
            stats = S.get_consolidation_statistics()
            assert stats["candidates"]["total"] == 1
            assert stats["opportunities"]["total"] == 1
            assert S.get_total_realized_savings()["count"] == 1
            dash = S.get_consolidation_dashboard_data()
            assert [c["id"] for c in dash["pending_candidates"]] == [getattr(world, f"cand_{key}").id]
            assert [o["id"] for o in dash["top_opportunities"]] == [getattr(world, f"opp_{key}").id]
            assert dash["recent_savings"][0]["realized_savings"] == (4242.0 if key == "a" else 9191.0)


def test_one_organisation_cannot_read_or_change_the_others_rows(world, tenant_ctx):
    from app.models.consolidation import ConsolidationCandidate, ConsolidationOpportunity
    from app.services.consolidation_service import ConsolidationService as S

    with tenant_ctx(world.org_a.id):
        assert not S.get_opportunity(world.opp_b.id)
        assert not S.review_candidate(world.cand_b.id, "approve")["success"]
        assert not S.update_opportunity(world.opp_b.id, {"name": "taken over"})["success"]
        assert not S.calculate_savings(world.opp_b.id)["success"]
        planted = S.record_realized_savings(
            world.opp_b.id,
            {"period_start": "2026-01-01", "period_end": "2026-02-01", "realized_savings": 1.0},
        )
        assert not planted["success"]
        assert S.get_savings_by_opportunity(world.opp_b.id) == []
    with tenant_ctx(world.org_b.id):
        assert ConsolidationCandidate.query.get(world.cand_b.id).status == "pending_review"
        assert ConsolidationOpportunity.query.get(world.opp_b.id).name == world.names["b"][1]
        assert S.get_total_realized_savings()["total"] == 9191.0


def test_detection_and_new_opportunities_carry_the_active_organisation(
    world, db_session, tenant_ctx
):
    from app.models.consolidation import ConsolidationCandidate, ConsolidationOpportunity
    from app.services.consolidation_service import ConsolidationService as S

    twin = _app(db_session, world.org_a, world.names["a"][0])
    db_session.commit()
    with tenant_ctx(world.org_a.id):
        assert S.detect_duplicate_candidates()["success"]
        approved = S.review_candidate(world.cand_a.id, "approve")
        assert approved["success"]
        made = S.create_opportunity_from_candidates([world.cand_a.id], {"name": "Fresh A"})
        assert made["success"], made
        new_id = made["opportunity"]["id"]
        assert ConsolidationOpportunity.query.get(new_id).organization_id == world.org_a.id
        assert S.record_realized_savings(
            new_id, {"period_start": "2026-01-01", "period_end": "2026-02-01", "realized_savings": 5.0}
        )["success"]
    with tenant_ctx(world.org_b.id):
        assert {c.id for c in ConsolidationCandidate.query.all()} == {world.cand_b.id}
        assert {o.id for o in ConsolidationOpportunity.query.all()} == {world.opp_b.id}
    assert twin.id


def _seen(client, url, mine, theirs):
    body = client.get(url).get_data(as_text=True)
    for text in mine:
        assert text in body, (url, text)
    for text in theirs:
        assert text not in body, (url, text)
    return body


def test_the_pages_and_api_show_each_organisation_only_its_own(client, login_as, world):
    pages = ("/enterprise/consolidation/candidates", "/enterprise/consolidation/opportunities")
    for mine, theirs, user in (("a", "b", world.user_a), ("b", "a", world.user_b)):
        login_as(client, user)
        _seen(client, pages[0], [world.names[mine][0]], [world.names[theirs][0]])
        _seen(
            client, pages[1],
            [world.names[mine][1]], [world.names[theirs][1], "777,777" if mine == "a" else "111,111"],
        )
        login_as(client, user)
        stats = client.get("/enterprise/api/consolidation/statistics").get_json()
        assert stats["candidates"]["total"] == 1 and stats["opportunities"]["total"] == 1
        login_as(client, user)
        dash = client.get("/enterprise/consolidation")
        body = dash.get_data(as_text=True)
        assert world.names[theirs][1] not in body and world.names[theirs][0] not in body


def test_detection_through_the_api_only_writes_the_callers_organisation(
    client, login_as, world, db_session
):
    from app.models.consolidation import ConsolidationCandidate
    from sqlalchemy import text

    _app(db_session, world.org_a, world.names["a"][0])
    db_session.commit()
    before_b = db_session.execute(
        text("select count(*) from consolidation_candidates where organization_id = :o"),
        {"o": world.org_b.id},
    ).scalar()
    login_as(client, world.user_a)
    assert client.post("/enterprise/api/consolidation/detect").status_code == 200
    after_b = db_session.execute(
        text("select count(*) from consolidation_candidates where organization_id = :o"),
        {"o": world.org_b.id},
    ).scalar()
    assert before_b == after_b == 1
    assert ConsolidationCandidate is not None


def test_the_service_names_the_organisation_even_where_the_tenant_filter_does_not_apply(
    world, app, monkeypatch
):
    """The model's own tenant filter is off here (no tenant on the request); only the service's predicate guards.

    The other organisation's ids are asked for by the first organisation. This fails the moment a service
    read stops naming the organisation, whatever the model's own filter does.
    """
    from flask import g

    from app.services import consolidation_service
    from app.services.consolidation_service import ConsolidationService as S

    monkeypatch.setattr(consolidation_service, "current_organization_id", lambda: world.org_a.id)
    with app.test_request_context("/"):
        assert getattr(g, "current_org_id", None) is None
        assert not S.get_opportunity(world.opp_b.id)
        assert not S.review_candidate(world.cand_b.id, "approve")["success"]
        assert not S.update_opportunity(world.opp_b.id, {"name": "taken over"})["success"]
        assert S.get_savings_by_opportunity(world.opp_b.id) == []
        assert {o["id"] for o in S.get_all_opportunities()} == {world.opp_a.id}
        assert {c["id"] for c in S.get_all_candidates()} == {world.cand_a.id}
