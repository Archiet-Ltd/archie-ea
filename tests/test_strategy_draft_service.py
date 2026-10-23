"""The strategy drafting engine — draft, critique, and accept.

Covers the engine's two guarantees the brief makes load-bearing:
- no point without a citation ever reaches the screen (the critique step),
  proven at the draft and enforced again at the write;
- an accepted point becomes an Assessment element carrying the box's profile
  value, so the canvas reads it straight back, and nothing is written until a
  person accepts.

The model call is stubbed so the test is deterministic and needs no provider;
the engine under test still parses, critiques and writes exactly as it would
against a real provider.
"""
from __future__ import annotations

import json

import pytest

from app.services import strategy_draft_service as engine


def _stub_llm(monkeypatch, responses):
    """Make LLMService.generate_from_prompt return queued responses in order.

    ``responses`` is a list of raw strings (the draft response, then the
    critique response). The engine calls the provider once to draft and once
    to critique.
    """
    calls = {"i": 0}

    def fake(prompt, *args, **kwargs):
        i = calls["i"]
        calls["i"] = i + 1
        return responses[min(i, len(responses) - 1)]

    monkeypatch.setattr(
        "app.modules.ai_chat.services.llm_service_impl.LLMService.generate_from_prompt",
        staticmethod(fake),
    )
    return calls


# -- The critique filter: a point with no resolvable citation is dropped ------


class TestCritiqueFilter:
    def test_a_point_with_no_citation_never_survives(self, monkeypatch):
        # The critique model pass returns the proposals unchanged; the
        # deterministic citation filter must still drop the uncited point.
        records = [{"id": "7", "kind": "ApplicationComponent", "name": "Payroll", "description": ""}]
        boxes = {
            "strengths": [
                {"point": "Grounded in a real record", "citations": ["7"]},
                {"point": "Floating, cites nothing", "citations": []},
                {"point": "Cites an id that is not ours", "citations": ["999"]},
            ],
            "weaknesses": [],
        }
        _stub_llm(monkeypatch, [json.dumps(boxes)])

        out = engine._critique("swot", boxes, records)

        strengths = out["strengths"]
        assert len(strengths) == 1
        assert strengths[0]["point"] == "Grounded in a real record"
        assert strengths[0]["citations"] == ["7"]

    def test_only_a_real_record_id_grounds_a_point(self, monkeypatch):
        # There is no ungrounded source: a point that cites only a word that is
        # not a record id in this organisation is dropped, so the footer never
        # offers a citation with no page to land on.
        records = [{"id": "3", "kind": "Driver", "name": "Growth", "description": ""}]
        boxes = {"opportunities": [
            {"point": "From nowhere real", "citations": ["website"]},
            {"point": "From the growth driver", "citations": ["3"]},
        ]}
        _stub_llm(monkeypatch, [json.dumps(boxes)])

        out = engine._critique("swot", boxes, records)
        assert [p["point"] for p in out["opportunities"]] == ["From the growth driver"]

    def test_critique_falls_back_to_the_citation_filter_when_the_model_pass_is_unparseable(
        self, monkeypatch
    ):
        records = [{"id": "5", "kind": "Risk", "name": "Outage", "description": ""}]
        boxes = {
            "threats": [
                {"point": "Real", "citations": ["5"]},
                {"point": "Uncited", "citations": []},
            ]
        }
        # The critique model returns junk; the hard citation filter still runs.
        _stub_llm(monkeypatch, ["not json at all"])
        out = engine._critique("swot", boxes, records)
        assert [p["point"] for p in out["threats"]] == ["Real"]


class TestParseBoxes:
    def test_a_non_object_response_is_a_parse_failure_not_an_empty_draft(self):
        with pytest.raises(engine.StrategyDraftError):
            engine._parse_boxes("[]", ["strengths"])

    def test_a_missing_box_is_tolerated_as_empty(self):
        out = engine._parse_boxes(json.dumps({"strengths": []}), ["strengths", "weaknesses"])
        assert out == {"strengths": [], "weaknesses": []}


# -- draft() end to end over a seeded organisation ----------------------------


def _seed_element(db_session, org_id, name, type_="ApplicationComponent"):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


class TestDraftAndAccept:
    def test_draft_returns_only_cited_points_grouped_by_box(
        self, app, db_session, make_org, tenant_ctx, monkeypatch
    ):
        org = make_org("swot-draft")
        el = _seed_element(db_session, org.id, "Payroll System")

        draft_response = {
            "strengths": [
                {"point": "Payroll system is a core asset", "citations": [str(el.id)]},
                {"point": "Generic unbacked claim", "citations": []},
            ],
            "weaknesses": [],
            "opportunities": [],
            "threats": [],
        }
        # Draft call, then critique call returns the same shape.
        _stub_llm(monkeypatch, [json.dumps(draft_response), json.dumps(draft_response)])

        with tenant_ctx(org.id):
            result = engine.draft("swot", org.id)

        assert result["framework"] == "swot"
        assert set(result["boxes"]) == {"strengths", "weaknesses", "opportunities", "threats"}
        strengths = result["boxes"]["strengths"]
        assert len(strengths) == 1
        assert strengths[0]["citations"] == [str(el.id)]

    def test_accept_writes_an_assessment_element_the_canvas_reads_back(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models.archimate_core import ArchiMateElement

        org = make_org("swot-accept")
        el = _seed_element(db_session, org.id, "Payroll System")

        with tenant_ctx(org.id):
            saved = engine.accept_point(
                "swot", "strengths", "Payroll system is a core asset",
                [str(el.id)], org.id,
            )
            db_session.flush()

            written = ArchiMateElement.query.filter_by(id=saved["element_id"]).one()
            assert written.type == "Assessment"
            assert written.organization_id == org.id
            assert (written.acm_properties or {}).get("profile") == "swot_strength"
            assert (written.acm_properties or {}).get("citations") == [str(el.id)]
            # The framework's assessment_type is set, so a report or filter
            # keyed on it finds the accepted point.
            assert (written.acm_properties or {}).get("assessment_type") == "swot"
            assert written.name == "Payroll system is a core asset"

    def test_accept_is_idempotent_on_the_same_proposal(
        self, app, db_session, make_org, tenant_ctx
    ):
        # A double-click or a retry of the same accept lands on one element,
        # never two.
        from app.models.archimate_core import ArchiMateElement

        org = make_org("swot-idempotent")
        el = _seed_element(db_session, org.id, "Payroll System")

        with tenant_ctx(org.id):
            first = engine.accept_point(
                "swot", "strengths", "A core asset", [str(el.id)], org.id
            )
            db_session.flush()
            second = engine.accept_point(
                "swot", "strengths", "A core asset", [str(el.id)], org.id
            )
            db_session.flush()

            assert first["element_id"] == second["element_id"]
            count = ArchiMateElement.query.filter_by(
                organization_id=org.id, type="Assessment"
            ).count()
            assert count == 1

    def test_a_previously_accepted_point_is_not_a_citable_record(
        self, app, db_session, make_org, tenant_ctx
    ):
        # An accepted strength is itself an Assessment element; it must not
        # appear in the records a later draft may cite, so a draft cannot cite
        # a past SWOT point as the evidence for a new one.
        org = make_org("swot-no-circular")
        el = _seed_element(db_session, org.id, "Payroll System")

        with tenant_ctx(org.id):
            saved = engine.accept_point(
                "swot", "strengths", "A core asset", [str(el.id)], org.id
            )
            db_session.flush()

            record_ids = {r["id"] for r in engine._gather_records(org.id)}
            assert str(el.id) in record_ids
            assert str(saved["element_id"]) not in record_ids

    def test_accept_refuses_a_point_with_no_resolvable_citation(
        self, app, db_session, make_org, tenant_ctx
    ):
        org = make_org("swot-refuse")
        _seed_element(db_session, org.id, "Payroll System")

        with tenant_ctx(org.id):
            with pytest.raises(ValueError, match="citation"):
                engine.accept_point("swot", "strengths", "Floating claim", [], org.id)
            with pytest.raises(ValueError, match="citation"):
                engine.accept_point(
                    "swot", "strengths", "Cites a foreign id", ["999999"], org.id
                )

    def test_a_second_organisation_sees_none_of_the_firsts_records_when_drafting(
        self, app, db_session, make_org, tenant_ctx, monkeypatch
    ):
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")
        el_a = _seed_element(db_session, org_a.id, "Org A Payroll")

        # The model is asked to ground a point in org A's element id while org
        # B is the active tenant; org B has no such record, so the citation
        # does not resolve and the point is dropped.
        response = {
            "strengths": [{"point": "Leaked from org A", "citations": [str(el_a.id)]}],
            "weaknesses": [], "opportunities": [], "threats": [],
        }
        _stub_llm(monkeypatch, [json.dumps(response), json.dumps(response)])

        with tenant_ctx(org_b.id):
            result = engine.draft("swot", org_b.id)
        assert result["boxes"]["strengths"] == []

    def test_pestle_uses_the_same_engine_with_six_boxes(
        self, app, db_session, make_org, tenant_ctx, monkeypatch
    ):
        org = make_org("pestle-draft")
        el = _seed_element(db_session, org.id, "Data centre", type_="Node")

        response = {
            "political": [], "economic": [], "social": [],
            "technological": [{"point": "On-premise data centre", "citations": [str(el.id)]}],
            "legal": [], "environmental": [],
        }
        _stub_llm(monkeypatch, [json.dumps(response), json.dumps(response)])

        with tenant_ctx(org.id):
            result = engine.draft("pestle", org.id)

        assert set(result["boxes"]) == {
            "political", "economic", "social", "technological", "legal", "environmental"
        }
        assert len(result["boxes"]["technological"]) == 1
