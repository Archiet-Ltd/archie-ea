"""The strategy drafting engine: competitor analysis, SWOT and PESTLE drafted from the public footprint.

Pins the rules a person relies on:
- every statement names sources in the ledger, and a statement with none (or an invented one) is removed;
- the second reading may only delete;
- drafts are proposals in the approvals store and change nothing in the model until a person accepts;
- accepting writes cited records, on the SWOT and PESTLE canvases and under the competitor;
- the route follows the plan (free: public input only; paid: the organisation's own provider);
- one organisation never sees, accepts or is read into another's drafts.
The model is a stand-in; no provider is called.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.strategy_seed import (
    COMPANY, FABRICATED, HARBOR, INVENTED_SHARE, NO_SOURCE, RIVALS, SIGNALS, SWIFTLINE, FakeStrategyModel, seed_company,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _person(org_id, label="founder"):
    from app import db
    from app.models.user import User

    user = User(email="%s-%s@example.test" % (label, uuid.uuid4().hex[:10]), first_name="Casey", last_name="Founder",
                organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def company(make_org):
    from app import db

    org = make_org("strategy")
    db.session.commit()
    user = _person(org.id)
    seed_company(org.id, user.id)
    return org.id, user.id


def _drafts(org_id, model=None):
    from app.services import strategy_draft_service as engine

    model = model or FakeStrategyModel()
    summary = engine.run_drafts(org_id, llm=model)
    return summary, model


def _accept(org_id, user_id, decisions=None):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import strategy_draft_service as engine

    return engine.accept_drafts(db.session.get(Organization, org_id), db.session.get(User, user_id), decisions)


def _texts(org_id, analysis=None):
    from app.services import strategy_draft_service as engine

    return [d["text"] for d in engine.pending_statements(org_id, analysis)]


def test_competitors_are_the_ones_the_person_named_then_the_profile_holds_at_most_five(make_org):
    from app import db
    from app.models.organization import Organization
    from app.services import strategy_draft_service as engine

    org = make_org("names")
    db.session.commit()
    user = _person(org.id)
    seed_company(org.id, user.id, rivals=["Harbor Logistics", "harbor logistics", COMPANY, "Beta (beta.example)"],
                 profile_rivals=["Swiftline Cargo", "Gamma", "Delta", "Epsilon", "Zeta"])
    got = engine.competitors_named(db.session.get(Organization, org.id))
    assert [c["name"] for c in got] == ["Harbor Logistics", "Beta", "Swiftline Cargo", "Gamma", "Delta"]
    assert got[1]["domain"] == "beta.example" and got[0]["domain"] is None


def test_ledger_numbers_every_source_with_its_page_publisher_and_date(company):
    from app import db
    from app.models.organization import Organization
    from app.services import strategy_draft_service as engine

    org = db.session.get(Organization, company[0])
    ledger = engine.build_ledger(org, engine.competitors_named(org), "public")
    assert [s["id"] for s in ledger] == ["S%d" % n for n in range(1, len(ledger) + 1)]
    pages = [s for s in ledger if s["kind"] == "company_page"]
    assert {p["url"] for p in pages} == {"https://northwind-freight.example/", "https://northwind-freight.example/products"}
    signals = [s for s in ledger if s["kind"] == "signal"]
    assert {s["subject"] for s in signals} == {COMPANY, HARBOR, SWIFTLINE}
    for source in ledger:
        assert source["url"] and source["title"] and source["publisher"] and source["read_on_label"] and source["percent"] > 0
    assert not [s for s in ledger if s["kind"] == "connected"], "a free plan is given public input only"


def test_the_pipeline_removes_the_unsourced_the_invented_and_the_unsupported(company):
    summary, model = _drafts(company[0])
    texts = _texts(company[0])
    assert texts, "nothing was drafted"
    for gone in (INVENTED_SHARE, NO_SOURCE, FABRICATED):
        assert not [t for t in texts if gone in t], gone
    removed = {r["text"]: r["why"] for r in summary["removed"]}
    assert removed[INVENTED_SHARE] == "no source"  # cites S99, which is not in the ledger
    assert removed[NO_SOURCE] == "no source"
    assert removed[FABRICATED] == "not supported by its source"  # cited a real source; the second reading deleted it
    assert "Large depot network across the region" in texts
    assert summary["analyses"]["competitors"]["state"] == "ok" and summary["analyses"]["competitors"]["removed"] == 3


def test_the_critique_only_deletes(company):
    from app.services import strategy_draft_service as engine

    class Meddler(FakeStrategyModel):
        def _critique(self, prompt):
            return json.dumps({"remove": [], "add": [{"text": "A new claim", "sources": ["S1"]}],
                               "rewrite": {"s1": "Something else entirely"}})

    _drafts(company[0], Meddler())
    texts = _texts(company[0])
    assert "A new claim" not in texts and "Something else entirely" not in texts
    # nothing was deleted either, so the unsupported-but-cited one the real critique removes is still here
    assert [t for t in texts if FABRICATED in t]


def test_a_critique_that_gives_no_answer_leaves_the_citation_filter_standing(company):
    class Silent(FakeStrategyModel):
        def _critique(self, prompt):
            raise RuntimeError("no answer")

    summary, _ = _drafts(company[0], Silent())
    texts = _texts(company[0])
    assert not [t for t in texts if INVENTED_SHARE in t or NO_SOURCE in t]
    assert [t for t in texts if FABRICATED in t], "only the second reading removes this one"
    assert summary["analyses"]["swot"]["state"] == "ok"


def test_each_statement_stores_its_sources_with_page_publisher_date_and_how_sure(company):
    from app.services import strategy_draft_service as engine

    _drafts(company[0])
    rows = engine.pending_statements(company[0], "competitors")
    assert rows
    for row in rows:
        assert row["sources"], row
        assert 0 < row["percent"] <= 100
        for source in row["sources"]:
            assert source["url"].startswith("http") and source["title"] and source["publisher"]
            assert source["read_on"] and source["read_on_label"] and source["percent"] > 0
    threat = {(r["group"], r["row"]): r["text"] for r in rows}
    assert threat[(HARBOR, "threat")] == "High" and threat[(SWIFTLINE, "threat")] == "Medium"


def test_drafts_are_proposals_and_change_nothing_in_the_model_until_accepted(company):
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    before = ArchiMateElement.query.filter_by(organization_id=company[0]).count()
    summary, _ = _drafts(company[0])
    assert ArchiMateElement.query.filter_by(organization_id=company[0]).count() == before
    assert BusinessModelCanvas.query.filter_by(organization_id=company[0]).count() == 0
    pending = AIChatCRUDApproval.query.filter_by(
        organization_id=company[0], source_table="strategy_draft", status=ApprovalStatus.PENDING).count()
    assert pending == summary["stored"] > 0


def test_a_second_run_replaces_the_pending_drafts_rather_than_piling_up(company):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    first, _ = _drafts(company[0])
    second, _ = _drafts(company[0])
    pending = AIChatCRUDApproval.query.filter_by(
        organization_id=company[0], source_table="strategy_draft", status=ApprovalStatus.PENDING).count()
    assert pending == second["stored"] == first["stored"]


def test_accepting_writes_cited_records_on_the_canvases_and_under_each_competitor(company):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_model import BusinessModelCanvas
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.business_model_canvas import model_service
    from app.services import strategy_draft_service as engine

    org_id, user_id = company
    _drafts(org_id)
    done = engine.accept_drafts(db.session.get(Organization, org_id), db.session.get(User, user_id))
    assert done["kept"] > 0 and done["left_out"] == 0
    assert engine.pending_statements(org_id) == []

    swot = BusinessModelCanvas.query.filter_by(organization_id=org_id, canvas_type="swot_analysis").one()
    strengths = [e.description for e in model_service.entries(swot, "strengths")]
    assert strengths == ["ColdRoute planning cuts empty running"]
    assert [e.description for e in model_service.entries(swot, "opportunities")] == ["Harbor customers want faster online booking"]
    pestle = BusinessModelCanvas.query.filter_by(organization_id=org_id, canvas_type="pestle_analysis").one()
    assert [e.type for e in model_service.entries(pestle, "technological")] == ["Driver"]
    assert model_service.entries(pestle, "political") == []

    harbor = ArchiMateElement.query.filter_by(organization_id=org_id, type="BusinessActor", name=HARBOR).one()
    assert harbor.custom_properties["external"] is True
    strengths = [e for e in engine.accepted_statements(org_id, "competitors") if e["group"] == HARBOR and e["row"] == "strength"]
    assert [s["text"] for s in strengths] == ["Large depot network across the region"]
    assert strengths[0]["competitor_element_id"] == harbor.id
    assert strengths[0]["confirmed_by_name"]
    element = db.session.get(ArchiMateElement, strengths[0]["element_id"])
    assert element.source_kind == "import" and element.source_recorded_at is not None
    assert json.loads(element.source_ref)["sources"] == [s["url"] for s in strengths[0]["sources"]]
    assert all(s["url"] and s["title"] and s["read_on"] and s["percent"] for s in strengths[0]["sources"])


def test_accepting_twice_changes_nothing_the_second_time(company):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import strategy_draft_service as engine

    org_id, user_id = company
    _drafts(org_id)
    _accept(org_id, user_id)
    count = ArchiMateElement.query.filter_by(organization_id=org_id).count()
    _drafts(org_id)  # the same drafts again
    _accept(org_id, user_id)
    assert ArchiMateElement.query.filter_by(organization_id=org_id).count() == count


def test_a_person_can_leave_a_draft_out_and_edit_another_before_it_is_recorded(company):
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import strategy_draft_service as engine

    org_id, user_id = company
    _drafts(org_id)
    swot = engine.pending_statements(org_id, "swot")
    leave, edit = swot[0], swot[1]
    others = [d for d in engine.pending_statements(org_id) if d["id"] not in (leave["id"], edit["id"])]
    decisions = [{"id": leave["id"], "accept": False}, {"id": edit["id"], "accept": True, "value": "Our own wording"}]
    decisions += [{"id": d["id"], "accept": True} for d in others]
    done = engine.accept_drafts(db.session.get(Organization, org_id), db.session.get(User, user_id), decisions)
    assert done["left_out"] == 1 and done["edited"] == 1
    recorded = {e["text"]: e for e in engine.accepted_statements(org_id, "swot")}
    assert "Our own wording" in recorded and leave["text"] not in recorded and edit["text"] not in recorded
    element = db.session.get(ArchiMateElement, recorded["Our own wording"]["element_id"])
    assert element.source_kind == "manual" and recorded["Our own wording"]["percent"] == 100
    # a left-out draft is discarded, not left waiting
    assert leave["id"] not in [d["id"] for d in engine.pending_statements(org_id)]


def test_a_new_accepted_reading_of_a_competitor_replaces_the_matrix_column(company):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import strategy_draft_service as engine

    org_id, user_id = company
    _drafts(org_id)
    _accept(org_id, user_id)
    first = {s["element_id"] for s in engine.accepted_statements(org_id, "competitors") if s["group"] == HARBOR}

    class Updated(FakeStrategyModel):
        def _competitors(self, prompt):
            data = json.loads(super()._competitors(prompt))
            for c in data["competitors"]:
                if c["name"] == HARBOR:
                    c["position"]["text"] = "Now the largest regional carrier"
            return json.dumps(data)

    _drafts(org_id, Updated())
    _accept(org_id, user_id)
    now = {s["element_id"]: s for s in engine.accepted_statements(org_id, "competitors") if s["group"] == HARBOR}
    positions = [s["text"] for s in now.values() if s["row"] == "position"]
    assert positions == ["Now the largest regional carrier"]
    assert not first & {k for k, s in now.items() if s["row"] == "position"}


def test_the_confirm_step_gets_the_drafts_grouped_in_the_orders_the_ruling_gives(company):
    from app.services import strategy_draft_service as engine

    _drafts(company[0])
    groups = engine.grouped_drafts(company[0])
    assert [g["label"] for g in groups] == ["Competitors", "SWOT", "PESTLE"]
    competitors, swot, pestle = groups
    assert [s["label"] for s in competitors["subgroups"]] == [HARBOR, SWIFTLINE]  # highest threat first
    harbor_rows = [r["label"] for r in competitors["subgroups"][0]["rows"]]
    assert harbor_rows[:2] == ["Market position", "Threat level"]
    assert [s["label"] for s in swot["subgroups"]] == ["Strengths", "Weaknesses", "Opportunities", "Threats"]
    assert [s["label"] for s in pestle["subgroups"]] == ["Economic", "Technological", "Legal", "Environmental"]
    assert competitors["count"] == sum(len(s["rows"]) for s in competitors["subgroups"])
    row = swot["subgroups"][0]["rows"][0]
    assert row["sources"] and row["percent"] and row["keep"] is True


def test_one_analysis_failing_is_reported_and_the_others_still_land(company):
    summary, _ = _drafts(company[0], FakeStrategyModel(fail=("pestle",)))
    assert summary["analyses"]["pestle"]["state"] == "failed"
    assert summary["analyses"]["swot"]["state"] == "ok" and summary["analyses"]["competitors"]["state"] == "ok"
    assert _texts(company[0], "swot") and not _texts(company[0], "pestle")


def test_a_company_with_no_competitors_named_still_gets_its_swot_and_pestle(make_org):
    from app import db

    org = make_org("alone")
    db.session.commit()
    org_id = org.id
    user = _person(org_id)
    seed_company(org_id, user.id, rivals=[], signals={COMPANY: SIGNALS[COMPANY]})
    summary, _ = _drafts(org_id)
    assert summary["analyses"]["competitors"]["state"] == "no_competitors"
    assert _texts(org_id, "swot")


def test_drafting_needs_a_confirmed_profile(make_org):
    from app import db
    from app.services import strategy_draft_service as engine

    org = make_org("unconfirmed")
    db.session.commit()
    org_id = org.id
    with pytest.raises(engine.DraftsRefused) as refused:
        engine.run_drafts(org_id, llm=FakeStrategyModel())
    assert refused.value.reason == "no_profile"
    with pytest.raises(engine.DraftsRefused):
        engine.queue_drafts(org_id)


def test_drafting_is_a_job_queued_for_the_organisation_never_run_inline(company):
    from app.models.job import Job
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.services import strategy_draft_service as engine

    org_id, user_id = company
    job = engine.queue_drafts(org_id, user_id)
    assert job.task == "strategy_drafts" and job.payload["organization_id"] == org_id and job.status == "pending"
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_id, source_table="strategy_draft").count() == 0
    assert Job.query.filter_by(task="strategy_drafts").count() == 1


def test_the_job_body_runs_the_drafting_and_returns_counts_only(company, monkeypatch):
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.services import strategy_draft_service as engine

    model = FakeStrategyModel()
    monkeypatch.setattr(LLMService, "generate_from_prompt", staticmethod(lambda prompt, **kw: model(prompt)))
    result = engine.run_drafts_job({"organization_id": company[0], "requested_by": company[1]})
    assert result["success"] is True and result["stored"] > 0 and result["route"] == "public"
    assert set(result) == {"success", "stored", "sources", "route", "analyses", "removed"}  # never what a source said
    assert engine.run_drafts_job({"organization_id": company[0] + 10 ** 6}) == {"success": False, "reason": "no_org"}


def _set_plan(org_id, plan):
    from app import db
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    row = Subscription.query.filter_by(organization_id=org_id).first() or Subscription(organization_id=org_id)
    row.plan, row.status, row.seats_purchased = SubscriptionPlan[plan], SubscriptionStatus.active, 5
    db.session.add(row)
    db.session.commit()


def _record_route(monkeypatch):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    calls = []
    model = FakeStrategyModel()

    def generate(prompt, **kw):
        calls.append(kw)
        return model(prompt)

    monkeypatch.setattr(LLMService, "generate_from_prompt", staticmethod(generate))
    return calls


def test_a_free_plan_uses_the_free_providers_and_is_given_public_input_only(company, monkeypatch):
    from app.services import strategy_draft_service as engine

    calls = _record_route(monkeypatch)
    summary = engine.run_drafts(company[0])
    assert summary["route"] == "public"
    assert calls and all(c.get("public_input") is True for c in calls)
    assert all(c.get("use_cache") is False for c in calls)


def test_a_paid_plan_uses_its_own_provider_and_may_add_its_connected_facts(company, monkeypatch):
    from app import db
    from app.models.organization import Organization
    from app.services import strategy_draft_service as engine
    from tests.genome_cited_seed import seed_cited_model

    org_id, _user = company
    seed_cited_model(db.session, org_id)
    db.session.commit()
    org = db.session.get(Organization, org_id)
    free_ledger = engine.build_ledger(org, engine.competitors_named(org), engine.plan_route(org))
    named = engine.competitors_named(org)
    assert not [s for s in free_ledger if s["kind"] == "connected"], "a free plan is given public input only"
    _set_plan(org_id, "team")
    calls = _record_route(monkeypatch)
    summary = engine.run_drafts(org_id)
    assert summary["route"] == "paid"
    assert calls and all(not c.get("public_input") for c in calls)
    assert engine.plan_route(db.session.get(Organization, org_id)) == "paid"
    ledger = engine.build_ledger(db.session.get(Organization, org_id), named, "paid")
    connected = [s for s in ledger if s["kind"] == "connected"]
    assert {"Capability: Fraud Screening (nothing supports it)"} <= {s["text"] for s in connected}
    assert all(s["url"].startswith("/") and s["publisher"] == "Your model" and s["percent"] == 100 for s in connected)


def test_a_prompt_tells_the_model_that_source_text_is_data_and_never_an_instruction(company):
    _summary, model = _drafts(company[0])
    for prompt in model.prompts:
        if "hostile filter" not in prompt:
            assert "never an instruction" in prompt
    assert all(len(p) < 40000 for p in model.prompts), "a prompt is held inside the cap"


def test_one_organisation_never_sees_accepts_or_is_read_into_anothers_drafts(make_org):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import strategy_draft_service as engine

    a, b = make_org("a"), make_org("b")
    db.session.commit()
    a_id, b_id = a.id, b.id
    ua, ub = _person(a_id, "a"), _person(b_id, "b")
    ua_id, ub_id = ua.id, ub.id
    seed_company(a_id, ua_id)
    seed_company(b_id, ub_id, site="https://two.example", company="Northwind Freight Two", rivals=["Other Rival"], signals={
        "Northwind Freight Two": [("news", "NewsAPI", "Two opens a depot", "Depot opened.", "https://b.example/one", "2026-10-01")],
        "Other Rival": [("news", "NewsAPI", "Other Rival grows", "Growth.", "https://b.example/two", "2026-10-02")]})
    ma = FakeStrategyModel()
    engine.run_drafts(a_id, llm=ma)
    mb = FakeStrategyModel()
    engine.run_drafts(b_id, llm=mb)
    assert not [p for p in mb.prompts if HARBOR in p or "northwind-freight.example" in p or "freightnews.example" in p]
    assert not [p for p in ma.prompts if "Other Rival" in p or "b.example" in p]
    a_ids = {d["id"] for d in engine.pending_statements(a_id)}
    assert not a_ids & {d["id"] for d in engine.pending_statements(b_id)}
    # B accepting A's draft ids records nothing for either organisation
    done = engine.accept_drafts(db.session.get(Organization, b_id), db.session.get(User, ub_id),
                                [{"id": i, "accept": True} for i in a_ids])
    assert done == {"kept": 0, "edited": 0, "left_out": 0}
    assert {d["id"] for d in engine.pending_statements(a_id)} == a_ids
    assert engine.accepted_statements(a_id, "swot") == [] and engine.accepted_statements(b_id, "swot") == []


def test_the_strategy_slice_reads_only_what_was_accepted_with_the_sources(company, tenant_ctx):
    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.modules.genome.slices.registry import get_slice
    from app.services import strategy_draft_service as engine

    org_id, user_id = company
    _drafts(org_id)
    empty = get_slice("strategy", org_id)
    assert empty["nodes"] == [] and "accepted" in empty["reason"]
    engine.accept_drafts(db.session.get(Organization, org_id), db.session.get(User, user_id))
    envelope = get_slice("strategy", org_id)
    competitors = [n for n in envelope["nodes"] if n["kind"] == "competitor"]
    assert [n["name"] for n in competitors] == [HARBOR, SWIFTLINE]
    harbor = competitors[0]
    assert harbor["id"].startswith("strategy:competitor:") and harbor["threat"] == "High"
    assert harbor["provenance"]["archimate_element_id"] and harbor["link"].startswith("/strategy/competitors#competitor-")
    assert [s["text"] for s in harbor["differences"]] == ["We plan routes around refrigeration needs"]
    assert harbor["sources"] and all(s["url"] and s["title"] for s in harbor["sources"])
    kinds = {n["kind"] for n in envelope["nodes"]}
    assert {"competitor", "swot", "pestle"} <= kinds
    swot = next(n for n in envelope["nodes"] if n["kind"] == "swot" and n["box"] == "strengths")
    assert swot["link"].startswith("/business-model/") and swot["sources"]
    again = get_slice("strategy", org_id)
    assert again["spec_hash"] == envelope["spec_hash"]
    assert INVENTED_SHARE not in json.dumps(envelope) and FABRICATED not in json.dumps(envelope)


def test_competitors_the_site_names_are_offered_at_sign_up_and_compared_once_confirmed(make_org):
    from app import db
    from app.models.organization import Organization
    from app.modules.organization.website_crawl import confirm, service
    from app.services import strategy_draft_service as engine

    org = make_org("sitenames")
    db.session.commit()
    org_id = org.id
    user = _person(org_id)
    facts = service._Facts()
    service._from_inference(facts, "https://acme.example/", "2026-10-26T09:00:00",
                            {"potential_competitors": ["Harbor Logistics", "Swiftline Cargo"],
                             "data_quality": {"reliability_score": 0.7}})
    rows = [r for r in facts.ordered() if r["field"] == "competitor"]
    assert [r["value"] for r in rows] == ["Harbor Logistics", "Swiftline Cargo"] and rows[0]["label"] == "Competitor"
    service._store(org_id, rows, "crawl-1")
    offered, _ = confirm.proposals(org_id)
    assert [f["text"] for f in offered if f["field"] == "competitor"] == ["Harbor Logistics", "Swiftline Cargo"]
    done = confirm.confirm(db.session.get(Organization, org_id), db.session.get(type(user), user.id),
                           [{"id": f["id"], "accept": f["text"] == "Harbor Logistics"} for f in offered])
    assert done["confirmed"] == 1 and done["left_out"] == 1
    named = engine.competitors_named(db.session.get(Organization, org_id))
    assert [c["name"] for c in named] == ["Harbor Logistics"]
