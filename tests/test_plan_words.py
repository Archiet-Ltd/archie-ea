"""The pricing page and the home page describe each plan in the plan record's words."""
import re
import uuid

import pytest

from app.services.billing_plans import PLANS

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "fifty": 50}
NUMBER = re.compile(r"\b(\d+|" + "|".join(WORDS) + r")\b(?:-| )people", re.I)


def _plan(key):
    return next(p for p in PLANS if p.key == key)


def _body(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _text(fragment):
    from html import unescape

    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _card(html, key):
    start = html.index(f'data-testid="plan-card-{key}"')
    return html[start:html.index("</article>", start)]


def _cell(html, testid):
    start = html.index(f'data-testid="{testid}"')
    return _text(html[html.index(">", start) + 1:html.index("</td>", start)])


def _head_counts(text):
    return {int(m) if m.isdigit() else WORDS[m.lower()] for m in NUMBER.findall(text)}


def test_startup_and_team_read_the_same_words_on_both_pages(client):
    pricing = _body(client, "/pricing")
    home = _body(client, "/")
    for key in ("startup", "team"):
        plan = _plan(key)
        card = _card(pricing, key)
        assert plan.audience and plan.features
        assert _text(re.search(r'<p class="pp-for">(.*?)</p>', card, re.S).group(1)) == plan.audience
        assert _cell(home, f"home-plan-for-{key}") == plan.audience
        items = [_text(i) for i in re.findall(r"<li[^>]*>(.*?)</li>", card, re.S)]
        assert items == ["Everything in Community, plus", *plan.features]
        assert _cell(home, f"home-plan-includes-{key}") == ". ".join(plan.features) + "."


def test_startup_says_ten_people_from_its_limit_and_no_other_head_count(client):
    startup = _plan("startup")
    assert startup.user_limit == 10
    assert startup.people_words == "ten"
    pricing = _body(client, "/pricing")
    home = _body(client, "/")
    texts = [
        _text(_card(pricing, "startup")),
        _cell(home, "home-plan-for-startup"),
        _cell(home, "home-plan-includes-startup"),
    ]
    for text in texts:
        assert _head_counts(text) == {10}, text
    assert "ten people" in _cell(home, "home-plan-for-startup").lower()


def test_no_page_still_carries_the_old_audience_ranges(client):
    for path in ("/pricing", "/"):
        html = _body(client, path)
        assert "5 to 50" not in html and "50 to 500" not in html


def test_structured_data_states_each_plans_audience_from_the_plan_record(client):
    import json

    # The home page's structured data is its Organization record only; the plan
    # offers ride on the pricing page.
    for path in ("/pricing",):
        html = _body(client, path)
        blocks = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S)
        found = {}

        def walk(node):
            if isinstance(node, dict):
                if node.get("@type") == "Audience":
                    found.setdefault("audiences", set()).add(node["audienceType"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        for b in blocks:
            walk(json.loads(b))
        assert {p.audience for p in PLANS} <= found.get("audiences", set()), path


def _org_with_people(db_session, label, plan_key, count):
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Words {label} {suffix}", slug=f"words-{label}-{suffix}", plan=plan_key)
    db_session.add(org)
    db_session.flush()
    role = Role.query.filter_by(name="User").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="User").first()
    for _ in range(count):
        db_session.add(User(email=f"words-{uuid.uuid4().hex[:10]}@example.com", first_name="A", last_name="B",
                            organization_id=org.id, role=role, confirmed=True))
        db_session.flush()
    return org


def test_a_startup_organisation_holds_as_many_people_as_the_page_says(client, db_session):
    from app.models.user import Role, User
    from app.services.billing_plans import PlanLimitReached

    startup = _org_with_people(db_session, "startup", "startup", 0)
    other = _org_with_people(db_session, "community", "free", 0)
    role = Role.query.filter_by(name="User").first()
    held = 0
    try:
        while True:
            with db_session.begin_nested():
                db_session.add(User(email=f"words-{uuid.uuid4().hex[:10]}@example.com", first_name="A",
                                    last_name="B", organization_id=startup.id, role=role, confirmed=True))
                db_session.flush()
            held += 1
            assert held < 100
    except PlanLimitReached:
        pass
    shown = _head_counts(_cell(_body(client, "/"), "home-plan-for-startup"))
    assert shown == {10}
    assert held == next(iter(shown))
    # Another organisation on Community is held to its own, smaller limit.
    assert _plan("free").user_limit == 3 and other.id != startup.id
