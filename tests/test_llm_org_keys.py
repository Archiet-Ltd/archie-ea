"""An organisation's own AI key is only ever sent for that organisation.

Two organisations hold their own DeepSeek keys. Outside a request only the platform's environment key may be
sent; inside a request only that organisation's rows count; at the spend ceiling the pause is final.
Calls go to a stub on loopback that records every key it receives; no real provider is reached.
"""
from __future__ import annotations

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner, build_provider_chain
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, LLMService
from tests.deepseek_stub import KEY
from tests.test_llm_deepseek import _clear_ledger, _ledger, production_shape, stub  # noqa: F401

KEY_A = "sk-org-a-own-key-never-real-0003"
KEY_B = "sk-org-b-own-key-never-real-0002"


@pytest.fixture
def two_orgs(app):
    """Organisations A and B, each with its own enabled DeepSeek row; removed afterwards."""
    from app import db
    from app.models.models import APISettings
    from app.models.organization import Organization

    ids = []
    with app.app_context():
        for label, key in (("a", KEY_A), ("b", KEY_B)):
            org = Organization(name="Keys %s %s" % (label, KEY[-4:]), slug="keys-%s-%d" % (label, id(key)))
            db.session.add(org)
            db.session.flush()
            row = APISettings(provider="deepseek", enabled=True, default_model="deepseek-chat", organization_id=org.id)
            row.api_key = key
            db.session.add(row)
            ids.append(org.id)
        db.session.commit()
    yield ids
    from app.models.models import APISettings as Settings

    with app.app_context():
        db.session.rollback()
        Settings.query.filter(Settings.organization_id.in_(ids)).delete(synchronize_session=False)
        Organization.query.filter(Organization.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def test_p8_outside_a_request_a_platform_prompt_never_sends_an_organisations_key(app, production_shape, two_orgs):
    production_shape.accepted = {KEY_A, KEY_B}  # the platform key is refused; the organisations' would work
    with app.app_context():
        assert LLMService._get_all_api_keys("deepseek") == [KEY]
        with pytest.raises(Exception):
            LLMService.generate_from_prompt("platform job prompt", use_cache=False)
    assert KEY_A not in production_shape.keys_seen and KEY_B not in production_shape.keys_seen
    assert set(production_shape.keys_seen) <= {KEY}


def test_p8_outside_a_request_a_platform_prompt_is_recorded_on_the_platform_ledger(app, production_shape, two_orgs):
    with app.app_context():
        assert LLMService.generate_from_prompt("platform job prompt", use_cache=False) == "deepseek says ready"
    assert production_shape.keys_seen == [KEY]
    assert _ledger(app, "deepseek")[1] == 1


def test_p1_the_chain_outside_a_request_holds_only_the_platform_key(app, production_shape, two_orgs):
    with app.app_context():
        chain = build_provider_chain("deepseek", "deepseek-chat")
    assert chain and {entry[2] for entry in chain} == {KEY}


def test_inside_a_request_the_other_organisations_key_is_never_sent(app, production_shape, two_orgs):
    org_a, org_b = two_orgs
    production_shape.accepted = {KEY_B}
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_b
        assert LLMService._get_all_api_keys("deepseek") == [KEY_B]
        assert LLMService.platform_pays("deepseek", KEY_B) is False
        assert LLMService.generate_from_prompt("b's own prompt", use_cache=False) == "deepseek says ready"
    assert production_shape.keys_seen and set(production_shape.keys_seen) == {KEY_B}
    assert _ledger(app, "deepseek") is None  # B's own money, not the platform's


def test_p6_at_the_ceiling_the_runner_pauses_and_makes_no_further_provider_call(
    app, production_shape, two_orgs, monkeypatch
):
    production_shape.accepted = {KEY_A, KEY_B}
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "0.0001")
    with app.app_context():
        LLMService.record_spend("deepseek", "deepseek-chat", 1000, 500)  # over the ceiling
        result = AgentRunner(user_id=1).run("what breaks if payments fails")
    assert PAUSED_MESSAGE.split(".")[0].lower()[:12] in str(result).lower() or "paused" in str(result).lower()
    assert production_shape.requests == [] and production_shape.keys_seen == []


def test_a_stream_that_drops_part_way_still_records_an_estimate(app, production_shape):
    production_shape.usage = None
    production_shape.fail_stream_after_first_chunk = True
    runner = AgentRunner(user_id=1)
    with app.app_context():
        try:
            runner._call_llm("deepseek", "deepseek-chat", KEY, "system prompt", [{"role": "user", "content": "hi"}],
                             [], stream=True, base_url=production_shape.base_url)
        except Exception:  # noqa: BLE001 - the dropped stream is the case under test
            pass
    spent, requests = _ledger(app, "deepseek")
    assert requests == 1 and spent > 0


def test_p6_inside_a_request_an_organisation_with_no_row_at_the_ceiling_is_paused(
    app, production_shape, two_orgs, monkeypatch
):
    from app import db
    from app.models.models import APISettings
    from app.models.organization import Organization

    production_shape.accepted = {KEY, KEY_A, KEY_B}
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "0.0001")
    with app.app_context():
        org_c = Organization(name="Keys c %s" % KEY[-4:], slug="keys-c-%d" % id(monkeypatch))
        db.session.add(org_c)
        db.session.commit()
        org_c_id = org_c.id
    try:
        with app.test_request_context("/"):
            from flask import g

            g.current_org_id = org_c_id
            assert LLMService._get_all_api_keys("deepseek") == [KEY]
            LLMService.record_spend("deepseek", "deepseek-chat", 1000, 500)  # over the ceiling
            result = AgentRunner(user_id=1).run("what breaks if payments fails")
        assert PAUSED_MESSAGE in str(result)
        assert production_shape.requests == [] and production_shape.keys_seen == []
    finally:
        with app.app_context():
            db.session.rollback()
            APISettings.query.filter(APISettings.organization_id == org_c_id).delete(synchronize_session=False)
            Organization.query.filter(Organization.id == org_c_id).delete(synchronize_session=False)
            db.session.commit()
