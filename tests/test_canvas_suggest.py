"""Suggestions on the business model canvas: who gets the control, what the model is told, and what a
failure leaves behind. Two organisations throughout; the model provider is always stubbed."""

import json
import re
import uuid
from pathlib import Path

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.business_model import BusinessModelCanvas
from app.models.org_role import OrgRole
from app.models.user import Role, User
from app.modules.business_model_canvas import ai_service
from app.modules.business_model_canvas import model_service as ms
from app.services.feature_flag_service import FeatureFlagService

ROOT = Path(__file__).resolve().parent.parent
PAGES = [
    ROOT / "content/pages/function-per-segment/uc-s1-02-canvas-on-one-page.md",
    ROOT / "content/pages/function-per-segment/uc-s1-06-single-point-of-failure.md",
]


def _user(db_session, org, role_name, enterprise_role, admin=False):
    Role.insert_roles()
    user = User(email="suggest.%s@example.test" % uuid.uuid4().hex[:8], first_name="Sug", last_name="Gest",
                organization_id=org.id, enterprise_role=enterprise_role, confirmed=True)
    user.role = Role.query.filter_by(name=role_name).one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    if admin:
        OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
        db_session.flush()
    return user


def _canvas(db_session, org, label):
    """A canvas with two filled boxes and an empty Key Partners box, and one unplaced actor to propose."""
    canvas = BusinessModelCanvas(name="%s model" % label, organization_id=org.id)
    db_session.add(canvas)
    db_session.flush()
    for box, name, kind, layer in (
        ("customer_segments", "%s logistics firms" % label, "BusinessActor", "business"),
        ("value_propositions", "%s faster releases" % label, "Value", "motivation"),
    ):
        el = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id)
        db_session.add(el)
        db_session.flush()
        ms.add_entry(canvas, box, element_id=el.id)
    spare = ArchiMateElement(name="%s cloud host" % label, type="BusinessActor", layer="business",
                             organization_id=org.id)
    db_session.add(spare)
    db_session.flush()
    return canvas, spare


@pytest.fixture
def world(db_session, make_org, monkeypatch):
    one, other = make_org("suggest-a"), make_org("suggest-b")
    mine, spare = _canvas(db_session, one, "Ours")
    theirs, _ = _canvas(db_session, other, "Theirs")
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)
    return {
        "one": one, "other": other, "mine": mine, "theirs": theirs, "spare": spare,
        "architect": _user(db_session, one, "Architect", "solution_architect"),
        "viewer": _user(db_session, one, "Viewer", "viewer"),
        "stranger": _user(db_session, other, "Architect", "solution_architect"),
    }


def _stub_model(monkeypatch, reply=None, calls=None):
    def generate(prompt, **_):
        if calls is not None:
            calls.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(ai_service.LLMService, "generate_from_prompt", staticmethod(generate))


def _post(client, login_as, user, canvas, box="key_partners"):
    login_as(client, user)
    return client.post("/business-model/api/%d/ai-draft-block" % canvas.id, json={"block": box})


def test_a_viewer_has_no_suggest_control_and_the_route_refuses(client, login_as, world, monkeypatch):
    calls = []
    _stub_model(monkeypatch, "{}", calls)
    login_as(client, world["viewer"])
    page = client.get("/business-model/%d" % world["mine"].id)
    assert page.status_code == 200
    assert b"data-bmc-suggest" not in page.data
    assert _post(client, login_as, world["viewer"], world["mine"]).status_code == 403
    assert calls == []


def test_an_architect_gets_a_suggest_control_on_every_box_that_takes_entries(client, login_as, world):
    login_as(client, world["architect"])
    html = client.get("/business-model/%d" % world["mine"].id).get_data(as_text=True)
    keys = set(re.findall(r'data-bmc-suggest="([a-z-]+)"', html))
    assert keys == {k.replace("_", "-") for k in ms.element_boxes(world["mine"])}


def test_another_organisations_canvas_answers_404(client, login_as, world, monkeypatch):
    calls = []
    _stub_model(monkeypatch, "{}", calls)
    assert _post(client, login_as, world["stranger"], world["mine"]).status_code == 404
    assert _post(client, login_as, world["architect"], world["theirs"]).status_code == 404
    assert calls == []


def test_a_failing_provider_gives_a_plain_error_and_keeps_the_entries(app, client, login_as, world, monkeypatch):
    with app.test_request_context():
        before = ms.canvas_contents(world["one"].id, world["mine"].id)
    _stub_model(monkeypatch, RuntimeError("boom: provider returned 500 secret-token"))
    reply = _post(client, login_as, world["architect"], world["mine"])
    assert reply.status_code == 502
    message = reply.get_json()["error"]
    assert "Your entries are unchanged" in message and "boom" not in message
    with app.test_request_context():
        assert ms.canvas_contents(world["one"].id, world["mine"].id)["boxes"] == before["boxes"]


def test_proposals_each_cite_a_source_and_only_real_elements_keep_their_id(client, login_as, world, monkeypatch):
    spare = world["spare"]
    _stub_model(monkeypatch, json.dumps({"proposals": [
        {"name": spare.name, "source": "Your model: an actor not on this canvas", "element_id": spare.id},
        {"name": "Payment provider", "source": "Customer Segments: Ours logistics firms", "element_id": 987654321},
        {"name": "No source given", "source": "", "element_id": None},
    ]}))
    reply = _post(client, login_as, world["architect"], world["mine"])
    assert reply.status_code == 200
    body = reply.get_json()
    proposals = body["draft"]["proposals"]
    assert [p["name"] for p in proposals] == [spare.name, "Payment provider"]
    assert [p["element_id"] for p in proposals] == [spare.id, None]
    assert all(p["source"] for p in proposals)
    assert body["notice"] == ai_service.SUGGESTION_NOTICE == "Suggested by AI from this canvas and your model"
    assert "Answer written" not in body["notice"]


def test_the_context_for_a_person_without_cost_visibility_has_no_cost_line(db_session, make_org, app, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.services import cost_fact_store
    from config import CurrencyConfig

    org = make_org("suggest-cost")
    canvas, _ = _canvas(db_session, org, "Costed")
    element = ArchiMateElement(name="Billing app", type="ApplicationComponent", layer="application",
                               organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(name="Billing app", organization_id=org.id, archimate_element_id=element.id)
    db_session.add(row)
    db_session.flush()
    currency = CurrencyConfig.get_org_currency_code(organization_id=org.id)
    cost_fact_store.upsert_fact(org.id, "application", row.id, 123456, currency, "test")
    db_session.flush()
    with tenant_ctx(org.id), app.test_request_context():
        shown = ai_service._build_context(canvas, "key_partners", show_costs=True)
        withheld = ai_service._build_context(canvas, "key_partners", show_costs=False)
    assert "Billing app" in json.dumps(shown["other_blocks"]["cost_structure"])
    assert "cost_structure" not in withheld["other_blocks"]
    assert "123,456" not in json.dumps(withheld) and "Billing app" not in json.dumps(withheld["other_blocks"])


def test_the_pages_name_the_control_as_the_canvas_labels_it(client, login_as, world):
    login_as(client, world["architect"])
    html = client.get("/business-model/%d" % world["mine"].id).get_data(as_text=True)
    labels = set(re.findall(r'aria-label="(Suggest for [^"]+)"', html))
    assert "Suggest for Key Partners" in labels
    prefix = {label.split(" for ")[0] for label in labels}
    assert prefix == {"Suggest"}
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        assert "“Suggest for" in text or '"Suggest for' in text, page.name
        assert "Draft Revenue Streams with AI" not in text, page.name


def test_the_prompt_holds_none_of_another_organisations_names(app, world, monkeypatch):
    """Built with no request organisation, so only the context reader's own organisation filter keeps names apart."""
    prompts = []
    _stub_model(monkeypatch, json.dumps({"proposals": []}), calls=prompts)
    with app.test_request_context():
        ai_service.generate_block_draft(world["mine"], "key_partners")
        seen = json.dumps(ms.canvas_contents(world["one"].id, world["mine"].id), default=str)
    assert len(prompts) == 1
    assert "Ours logistics firms" in prompts[0] and "Ours cloud host" in prompts[0]
    assert "Theirs" not in prompts[0]
    assert "Theirs" not in seen
