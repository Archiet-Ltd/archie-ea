"""Cost lines on the canvas: every row has the same keys, the real source, the full count, and a person the
cost rule withholds from gets the canvas without the amounts.

Two organisations in every test; the shared transactional fixtures leave nothing behind.
"""

import uuid

import pytest

from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.business_model import BusinessModelCanvas
from app.models.org_role import OrgRole
from app.models.user import Role, User
from app.modules.ai_chat.services import catalogue_answer
from app.modules.ai_chat.tools import route_read_tools
from app.modules.business_model_canvas import model_service as ms
from app.services import cost_fact_store, technology_cost
from config import CurrencyConfig

SENTENCE = "Cost figures are withheld for your role."
QUESTION = "what is in our cost structure?"


def _user(db_session, org, enterprise_role, admin=False):
    Role.insert_roles()
    user = User(email="cost.%s@example.test" % uuid.uuid4().hex[:8], first_name="Cost", last_name="Line",
                organization_id=org.id, enterprise_role=enterprise_role, confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    if admin:
        OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
        db_session.flush()
    return user


def _application(db_session, org, name):
    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
    db_session.add(row)
    db_session.flush()
    return row


def _costed_canvas(db_session, org, label, direct=2, shares=1):
    """A canvas whose organisation has ``direct`` applications with an annual cost and ``shares`` applications
    carrying a technology cost; the first application has both."""
    currency = CurrencyConfig.get_org_currency_code(organization_id=org.id)
    canvas = BusinessModelCanvas(name="%s model" % label, organization_id=org.id)
    db_session.add(canvas)
    db_session.flush()
    apps = [_application(db_session, org, "%s app %d" % (label, n)) for n in range(max(direct, shares))]
    for n, application in enumerate(apps[:direct]):
        cost_fact_store.upsert_fact(org.id, "application", application.id, 1000 + n, currency, "test")
    node = ArchiMateElement(name="%s cluster" % label, type="Node", layer="technology", organization_id=org.id)
    db_session.add(node)
    db_session.flush()
    for application in apps[:shares]:
        db_session.add(ArchiMateRelationship(source_id=node.id, target_id=application.archimate_element_id,
                                             type="realization", organization_id=org.id))
    db_session.flush()
    technology_cost.record_cost(org.id, node.id, "500", currency, "hosting")
    db_session.flush()
    return canvas, apps


@pytest.fixture
def world(db_session, make_org):
    one, other = make_org("canvas-cost-a"), make_org("canvas-cost-b")
    mine, apps = _costed_canvas(db_session, one, "Ours")
    theirs, _ = _costed_canvas(db_session, other, "Theirs")
    return {"one": one, "other": other, "mine": mine, "theirs": theirs, "apps": apps,
            "cto": _user(db_session, one, "cto", admin=True), "architect": _user(db_session, one, "solution_architect")}


def _through_the_route(client, login_as, user):
    def call(method, path, **_):
        login_as(client, user)
        reply = client.open(path, method=method)
        return reply.status_code, reply.get_json(silent=True) or {}

    return call


def _cost_box(contents):
    return next(b for b in contents["boxes"] if b["costs"] is not None)


def _chat(app, monkeypatch, org_id, user):
    monkeypatch.setattr(catalogue_answer, "current_user", user)
    monkeypatch.setattr(catalogue_answer, "interpret", lambda *a, **k: {
        "entry_id": "canvas_box_entries", "params": {"box": "cost_structure"}, "confidence": 1.0,
        "method": "keyword", "title": None})
    with app.test_request_context():
        return catalogue_answer.answer_catalogue_question(QUESTION, org_id)


def test_every_cost_row_carries_an_id_a_kind_and_its_real_source(app, world, tenant_ctx):
    with tenant_ctx(world["one"].id), app.test_request_context():
        lines = _cost_box(ms.canvas_contents(world["one"].id, world["mine"].id))["costs"]["lines"]
    assert len(lines) == 3
    assert {line["kind"] for line in lines} == {"cost", "technology_share"}
    assert all(isinstance(line["id"], int) and line["source_table"] == "cost_facts" for line in lines)
    assert {line["id"] for line in lines} <= {a.id for a in world["apps"]}


def test_the_chat_answers_a_canvas_with_cost_lines_and_technology_shares(app, world, tenant_ctx, monkeypatch):
    with tenant_ctx(world["one"].id):
        answer = _chat(app, monkeypatch, world["one"].id, world["cto"])
    assert answer is not None
    records = answer["records"]
    cited = [r for r in records if r["kind"] == "application"]
    assert len(cited) == 3, records
    assert sorted(r["source_line"].rsplit(" · ", 1)[1] for r in cited) == ["cost", "cost", "technology share"]
    assert all(r["url"] for r in cited)
    assert answer["total"] == 3
    assert "Theirs" not in str(answer)


def test_a_cost_row_is_labelled_with_the_store_it_comes_from(app, world, tenant_ctx):
    from app.modules.intelligence.services import query_catalogue as qc

    with tenant_ctx(world["one"].id), app.test_request_context():
        result = qc.run_entry("canvas_box_entries", world["one"].id, box="cost_structure", show_costs=True)
    assert {row["source_table"] for row in result["rows"] if row["kind"] == "application"} == {"cost_facts"}
    assert "saved_diagram_elements" not in {row["source_table"] for row in result["rows"]
                                            if row["kind"] == "application"}


def test_the_total_counts_every_cost_line_and_says_how_many_are_not_listed(app, db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services import query_catalogue as qc

    org = make_org("canvas-cost-many")
    canvas, _ = _costed_canvas(db_session, org, "Many", direct=ms.COST_LINES_SHOWN + 3, shares=1)
    with tenant_ctx(org.id), app.test_request_context():
        box = _cost_box(ms.canvas_contents(org.id, canvas.id))["costs"]
        result = qc.run_entry("canvas_box_entries", org.id, box="cost_structure", show_costs=True)
    assert box["count"] == ms.COST_LINES_SHOWN + 4 and box["not_listed"] == 3
    assert result["total"] == ms.COST_LINES_SHOWN + 4
    assert result["left_out"] == 3 and "3 cost lines not listed" in result["answer"]


def test_a_citation_does_not_borrow_another_rows_source_line(app, world, tenant_ctx, monkeypatch):
    with tenant_ctx(world["one"].id):
        answer = _chat(app, monkeypatch, world["one"].id, world["cto"])
    both = [r for r in answer["records"] if r["kind"] == "application" and r["name"].startswith("Ours app 0")]
    assert sorted(r["source_line"].rsplit(" · ", 1)[1] for r in both) == ["cost", "technology share"]


def test_the_api_and_get_canvas_leave_the_amounts_out_for_a_viewer_and_keep_them_for_a_cto(
        client, login_as, world, monkeypatch):
    monkeypatch.setattr(route_read_tools, "call_internal_api", _through_the_route(client, login_as, world["architect"]))
    withheld = route_read_tools._get_canvas({"canvas_type": "business_model", "canvas_id": world["mine"].id})
    costs = _cost_box(withheld["data"])
    assert costs["costs"]["lines"] == [] and costs["costs"]["withheld"] is True
    assert costs["empty_reason"] == SENTENCE
    assert "1,000" not in str(withheld) and "1000" not in str(withheld)
    login_as(client, world["architect"])
    assert client.get("/business-model/api/%d" % world["theirs"].id).status_code == 404

    monkeypatch.setattr(route_read_tools, "call_internal_api", _through_the_route(client, login_as, world["cto"]))
    shown = route_read_tools._get_canvas({"canvas_type": "business_model", "canvas_id": world["mine"].id})
    assert len(_cost_box(shown["data"])["costs"]["lines"]) == 3
    assert "Theirs" not in str(shown)


def test_the_chat_leaves_the_amounts_out_for_a_viewer_and_says_why(app, world, tenant_ctx, monkeypatch):
    with tenant_ctx(world["one"].id):
        answer = _chat(app, monkeypatch, world["one"].id, world["architect"])
    assert answer["records"] == [] and answer["costs_withheld"] is True
    assert SENTENCE in (answer["summary"] or "") or answer["reason"] == SENTENCE
    assert answer["next_step"] is None
    assert "1,000" not in str(answer) and "1000" not in str(answer)
