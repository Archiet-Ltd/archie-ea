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
    assert "Entelim does not do this yet" in body["html"] and "Recorded as not proven" in body["html"]
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
