"""The chat answers about the company itself from the facts its owner confirmed from the company's own website.

Each answer cites the page the fact was read from, a fact the person corrected is cited as theirs, nothing
unconfirmed is used, and one organisation never sees another's facts.
"""
import json
from types import SimpleNamespace

import pytest
import requests

from tests.test_website_crawl import fake_model, internal_hosts_allowed, other_site, site  # noqa: F401
from tests.test_website_reading_progress import _person, _run, _signed_up

pytestmark = pytest.mark.usefixtures("internal_hosts_allowed")

ASK = "/ai-chat/estate-answer"


def _ask(client, message):
    return client.post(ASK, json={"message": message}).get_json()


def _read_whole(org, monkeypatch):
    """The reading is two jobs: the page facts at once, then the model pass as its own job. Run both."""
    from app.modules.organization.website_crawl import WEBSITE_RETRY_TASK
    from app.services.job_queue_service import get_job_queue_service

    result = _run(org, monkeypatch)
    queue = get_job_queue_service()
    waiting = queue.list_jobs(org.id, limit=5, task=WEBSITE_RETRY_TASK, statuses=["pending"])
    assert waiting, "the reading should have queued the model pass"
    for job_id, task, payload in [(j.id, j.task, dict(j.payload)) for j in waiting]:
        queue._execute_task(task, payload, job_id)
        queue.update_job_status(job_id, "completed", result={})
    return result


def _read_and_confirm(client, login_as, make_org, monkeypatch, site, email, correct=None, leave_out=()):
    """A person signs up with a website, it is read, and they confirm the facts on the confirm screen."""
    org, user_id = _signed_up(make_org, site.url, email)
    _read_whole(org, monkeypatch)
    login_as(client, _person(user_id))
    page = client.get("/account/welcome?step=3").get_data(as_text=True)
    assert "What we read on " + site.url in page
    from app.modules.organization.website_crawl import confirm

    facts, _ = confirm.proposals(org.id)
    decisions = []
    for fact in facts:
        text = fact["text"]
        if correct and fact["field"] == correct[0]:
            text = correct[1]
        decisions.append({"id": fact["id"], "accept": fact["field"] not in leave_out, "value": text})
    body = client.post("/account/welcome/profile", json={"facts": decisions}).get_json()
    assert body["ok"] and body["confirmed"] > 0
    return org, user_id


CASES = [
    ("What do we sell?", "products", "Product: PickBot", "/products"),
    ("Who are our customers?", "customers", "Target market: Warehouse operators", ""),
    ("Where are we based?", "location", "Headquarters city: Bristol", "/about"),
    ("What technology does our website use?", "technology", "Technology: React", None),
    ("Who leads the company?", "leaders", "Key person: Dana Whitfield, Chief Executive Officer", "/about"),
    ("What did Entelim learn about our company?", "overview", "Company name: Acme Robotics Ltd", None),
]


@pytest.mark.parametrize("question,topic,expected,page", CASES, ids=[c[1] for c in CASES])
def test_each_question_is_answered_from_confirmed_facts_and_cites_its_page(
        question, topic, expected, page, app, client, login_as, make_org, monkeypatch, site):
    _read_and_confirm(client, login_as, make_org, monkeypatch, site, "chat-%s@example.com" % topic)
    body = _ask(client, question)
    assert body["found"] is True and body["entry_id"] == "company_profile", body
    html = body["html"]
    assert 'data-catalogue-entry="company_profile"' in html
    assert expected in html, html
    assert "Sam Founder" in html and "confirmed by" in html
    # every record is a link to the company's own page, and the line beside it names that page and when it was read
    import re
    links = re.findall(r'data-testid="catalogue-open"[^>]*href="([^"]+)"|href="([^"]+)"[^>]*data-testid="catalogue-open"', html)
    hrefs = [a or b for a, b in links]
    assert hrefs and all(h.startswith(site.url) for h in hrefs), hrefs
    assert "as of " in html
    if page is not None:
        assert site.url + page in hrefs
        # the page the answer cites really states the fact (where the fact is stated, not inferred from the page)
        if topic in ("products", "leaders", "location"):
            fact_value = expected.split(": ", 1)[1].split(",")[0]
            assert fact_value in requests.get(site.url + page, timeout=10).text
    assert "Your records cannot answer this yet" not in html


def test_the_overview_figure_matches_the_confirm_screen(app, client, login_as, make_org, monkeypatch, site):
    org, _ = _read_and_confirm(client, login_as, make_org, monkeypatch, site, "chat-count@example.com")
    html = _ask(client, "What did Entelim learn about our company?")["html"]
    import re
    said = int(re.search(r"(\d+) in your records", html).group(1))
    assert said == html.count("data-catalogue-record")
    with app.test_request_context():
        from app.modules.organization.website_crawl import confirm
        from app import db
        from app.models.organization import Organization

        held = confirm.profile_entries(db.session.get(Organization, org.id))
    assert said == len(held)


def test_a_fact_the_person_corrected_is_cited_as_theirs(app, client, login_as, make_org, monkeypatch, site):
    _read_and_confirm(client, login_as, make_org, monkeypatch, site, "chat-fix@example.com",
                      correct=("hq_city", "Bath"))
    html = _ask(client, "Where are we based?")["html"]
    assert "Headquarters city: Bath" in html and "Headquarters city: Bristol" not in html
    assert "corrected by Sam Founder" in html


def test_a_fact_left_out_is_not_in_the_answer(app, client, login_as, make_org, monkeypatch, site):
    _read_and_confirm(client, login_as, make_org, monkeypatch, site, "chat-out@example.com", leave_out=("hq_city", "hq_address"))
    html = _ask(client, "Where are we based?")["html"]
    assert "Bristol" not in html


def test_nothing_unconfirmed_is_used_and_the_answer_says_where_to_confirm(app, client, login_as, make_org, monkeypatch, site):
    org, user_id = _signed_up(make_org, site.url, "chat-pending@example.com")
    _read_whole(org, monkeypatch)
    login_as(client, _person(user_id))
    body = _ask(client, "What do we sell?")
    html = body["html"]
    assert "PickBot" not in html and "data-catalogue-record" not in html
    assert "What was read from your website has not been confirmed yet" in html
    assert 'href="/account/welcome?step=3"' in html and "Confirm what we read" in html
    # the link lands on the confirm screen with the facts to confirm
    page = client.get("/account/welcome?step=3").get_data(as_text=True)
    assert "What we read on " in page and "PickBot" in page


def test_without_a_website_the_answer_says_so_and_offers_to_give_one(app, client, login_as, make_org):
    from app import db
    from tests.test_website_reading_progress import _user

    org = make_org("nosite")
    db.session.flush()
    user = _user(org, "chat-nosite@example.com")
    login_as(client, user)
    html = _ask(client, "What do we sell?")["html"]
    assert "No company website has been read for this organisation" in html
    assert 'href="/account/welcome"' in html and "Give your website" in html
    assert "data-catalogue-record" not in html
    assert client.get("/account/welcome").status_code == 200


def test_one_organisation_never_gets_another_organisations_company_facts(app, client, login_as, make_org, monkeypatch, site):
    _read_and_confirm(client, login_as, make_org, monkeypatch, site, "chat-iso-a@example.com")
    mine = _ask(client, "What do we sell?")["html"]
    assert "PickBot" in mine

    from app import db
    from tests.test_website_reading_progress import _user

    other = make_org("other")
    db.session.flush()
    outsider = _user(other, "chat-iso-b@example.com")
    login_as(client, outsider)
    for question in ("What do we sell?", "Who leads the company?", "What did Entelim learn about our company?"):
        html = _ask(client, question)["html"]
        assert "PickBot" not in html and "Dana Whitfield" not in html and site.url not in html, question
        assert "data-catalogue-record" not in html


def test_questions_about_the_estate_still_go_where_they_went(app, client, login_as, make_org, monkeypatch, site):
    _read_and_confirm(client, login_as, make_org, monkeypatch, site, "chat-keep@example.com")
    for message in ("what is the meaning of life", "What if the board says no to the plan?",
                    "What breaks for our customers if the email provider is down?"):
        body = _ask(client, message)
        assert body.get("entry_id") != "company_profile", message
