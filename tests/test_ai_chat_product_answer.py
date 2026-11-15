"""A question about an organisation's own product is answered from that organisation's model only."""
import json

from app.services import customer_zero_model as model
from tests.test_sample_company_working_data import _architect


def _ask(client, message):
    return client.post("/ai-chat/estate-answer", json={"message": message}).get_json()


def _load(tmp_path, org_id, product="Entelim"):
    path = tmp_path / ("%s.json" % product)
    path.write_text(json.dumps({"product": product, "capabilities": [
        {"id": "x", "name": "Export a business model canvas", "status": "not proven", "what_exists": "A menu line."},
        {"id": "g", "name": "Connect GitHub", "status": "shipped", "what_a_person_can_do": "Reads repositories.",
         "limits": ["GitLab"]}]}))
    model.load_product(path, org_id)


def test_the_answer_cites_the_capability_and_another_organisation_gets_none(client, login_as, make_org, tmp_path):
    org, other = make_org(), make_org()
    _load(tmp_path, org.id)
    mine = _architect(org, "product-answer-a@example.com")
    theirs = _architect(other, "product-answer-b@example.com")
    login_as(client, mine)
    body = _ask(client, "Can Entelim export a canvas?")
    assert body["found"] is True and body["product_answer"] == "capability"
    assert "not recorded as shipped" in body["html"] and "does not do this yet" not in body["html"]
    assert "Export a business model canvas" in body["html"]
    assert 'data-testid="product-source"' in body["html"]
    unknown = _ask(client, "Can Entelim do blockchain?")
    assert unknown["product_answer"] == "not_recorded" and "Not recorded in your model" in unknown["html"]
    login_as(client, theirs)
    assert _ask(client, "Can Entelim export a canvas?").get("product_answer") is None


def test_a_withdrawn_capability_is_not_claimed(client, login_as, make_org, tmp_path):
    org = make_org()
    _load(tmp_path, org.id)
    only_github = tmp_path / "only.json"
    only_github.write_text(json.dumps({"product": "Entelim", "capabilities": [
        {"id": "g", "name": "Connect GitHub", "status": "shipped", "what_a_person_can_do": "Reads repositories."}]}))
    model.load_product(only_github, org.id)
    login_as(client, _architect(org, "product-answer-c@example.com"))
    html = _ask(client, "Can Entelim export a canvas?")["html"]
    assert "no longer recorded" in html and "does this" not in html
    assert "Recorded as" not in html


def _answer_html(app, org_id, message):
    from app.modules.ai_chat.services import product_answer

    answer = product_answer.answer_product_question(message, org_id)
    with app.test_request_context("/"):
        from flask import render_template
        return answer, render_template("ai_chat/_product_answer.html", answer=answer, page_url="/x", links={})


def test_nothing_internal_reaches_the_customer_from_the_shipped_file(app, db_session, make_org):
    import re

    from app.modules.ai_chat.services import product_answer

    org = make_org()
    model.load_product(model.DATA_DIRECTORY / "entelim.json", org.id)
    product = product_answer.products_named("Entelim", org.id)[0]
    leaks = re.compile(
        r"(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])|\b[A-Za-z]+_[A-Za-z_]+\b|sweep|livejourney|"
        r"/\S*\s+[1-5][0-9]{2}\b|\bHTTP\b|\b[1-5][0-9]{2}\b", re.I)
    shown = 0
    for capability in product_answer._capabilities_of(product):
        facts = product_answer._facts(capability)
        with app.test_request_context("/"):
            from flask import render_template
            html = render_template("ai_chat/_product_answer.html", answer={"kind": "capability", "product": product, **facts},
                                   page_url="/x", links={})
        text = re.sub(r"<[^>]+>", " ", html) + " " + capability.name
        text = re.sub(r"href=\S+", "", text)
        assert not leaks.search(text), (capability.name, leaks.search(text).group(0))
        shown += 1
    assert shown == 48


def test_a_longer_element_name_is_answered_as_that_element_not_as_the_product(app, db_session, make_org):
    from app.modules.ai_chat.services import product_answer
    from app.services.archimate_backbone import create_backbone_element

    org = make_org()
    model.load_product(model.DATA_DIRECTORY / "entelim.json", org.id)
    create_backbone_element(element_type="ApplicationComponent", layer="Application", name="Entelim Billing",
                            organization_id=org.id)
    db_session.flush()
    assert product_answer.answer_product_question("What does Entelim Billing do?", org.id) is None
    assert product_answer.answer_product_question("Can Entelim connect GitHub?", org.id)["kind"] == "capability"


def test_a_not_proven_row_with_a_page_is_not_said_to_be_missing(app, db_session, make_org):
    org = make_org()
    model.load_product(model.DATA_DIRECTORY / "entelim.json", org.id)
    answer, html = _answer_html(app, org.id, "Does Entelim have an Architecture Review Board?")
    assert answer["kind"] == "capability" and answer["status_word"] == "Not proven"
    assert "does not do this yet" not in html and 'href="/arb/"' in html
    answer, html = _answer_html(app, org.id, "Can Entelim do Software discovery?")
    assert "does not do this yet" in html


def test_a_certification_question_is_not_answered_with_a_feature(app, db_session, make_org):
    org = make_org()
    model.load_product(model.DATA_DIRECTORY / "entelim.json", org.id)
    answer, html = _answer_html(app, org.id, "Is Entelim SOC 2 certified?")
    assert answer["kind"] == "certification"
    assert "No certification is recorded in your model" in html
    assert "Compliance tracking" in html and "not a certificate" in html
