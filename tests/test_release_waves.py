"""The wave switch: RELEASE_WAVE=N shows waves 1 to N and answers early access for the rest.

Unit level: the map covers every registered route rule, the switch off changes nothing for any
route, a closed page answers its early-access page, a write or data call answers 404 JSON, a retired
address redirects only once its destination ships, and every menu and list asks the same question.
The browser journey is tests/smoke/test_wave_switch_journey.py.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest
from flask import request

from app.release_waves import (
    CLOSED,
    OPEN,
    REDIRECT,
    configured_wave,
    entry_for,
    is_released_endpoint,
    is_released_path,
    state_for,
    wave_map,
)
from app.release_waves.build import build, dump, MAP_PATH


@pytest.fixture
def wave1(app, _schema, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    return app


@pytest.fixture
def switch_off(app, _schema, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", None)
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    return app


def _rules(app):
    return [(r.rule, r.endpoint) for r in app.url_map.iter_rules()]


def test_every_registered_route_rule_has_a_wave(app):
    missing = [(rule, endpoint) for rule, endpoint in _rules(app) if entry_for(rule, endpoint) is None]
    assert not missing, f"{len(missing)} route rules have no wave (an unmapped route is never open): {missing[:10]}"


def test_the_shipped_map_is_what_the_builder_makes(app):
    """Every rule of this app gets from the builder the entry the shipped map carries (the shipped map may
    also carry rules of optional modules this app did not load)."""
    import json

    shipped = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    built = build(_rules(app))
    stale = [k for k, v in built["rules"].items() if shipped["rules"].get(k) != v]
    assert not stale, f"wave_map.json is stale for {len(stale)} rules: run python scripts/build_wave_map.py ({stale[:5]})"
    assert shipped["groups"].keys() >= built["groups"].keys()
    assert MAP_PATH.read_text(encoding="utf-8") == dump(shipped), "wave_map.json is not in the builder's format"


def test_an_unmapped_rule_is_closed_never_open():
    assert entry_for("/a-route-the-map-does-not-know") is None
    assert state_for(None, 99) == CLOSED


def test_the_switch_off_changes_nothing_for_any_route(switch_off):
    app = switch_off
    with app.test_request_context("/"):
        assert configured_wave() is None
        from app.release_waves.hook import enforce

        for rule in app.url_map.iter_rules():
            request.url_rule = rule
            assert enforce() is None, f"{rule.rule} answered with the switch off"
            assert is_released_endpoint(rule.endpoint)
        assert is_released_path("/archimate/diagrams")
        assert is_released_path("/anything/at/all")


def test_wave_one_closes_a_later_waves_pages_and_keeps_its_own(wave1):
    with wave1.test_request_context("/"):
        assert configured_wave() == 1
        counts = Counter(state_for(entry_for(r.rule, r.endpoint), 1) for r in wave1.url_map.iter_rules())
        assert counts[CLOSED] > 100 and counts[OPEN] > 100, counts
        assert is_released_path("/account/login")
        assert not is_released_path("/archimate/diagrams")


def test_a_later_wave_page_answers_its_early_access_page(wave1):
    client = wave1.test_client()
    rv = client.get("/archimate/diagrams")
    html = rv.get_data(as_text=True)
    assert rv.status_code == 200
    assert 'data-testid="early-access-page"' in html
    assert "Join the waiting list" in html
    assert "Diagrams" in html or "Composer" in html or "Early access" in html
    assert rv.headers.get("X-Robots-Tag") == "noindex"


def test_a_write_to_a_later_wave_route_answers_404_json_and_writes_nothing(wave1):
    from app import db
    from app.models.waitlist_signup import WaitlistSignup

    from app.models.product_inquiry import ProductInquiry

    with wave1.app_context():
        before = (db.session.query(WaitlistSignup).count(), db.session.query(ProductInquiry).count())
    client = wave1.test_client()
    rv = client.post("/archimate/composer", data={"name": "x"})
    assert rv.status_code == 404
    body = rv.get_json()
    assert body["error"] == "early_access" and body["early_access"].startswith("/early-access/")
    with wave1.app_context():
        assert (db.session.query(WaitlistSignup).count(), db.session.query(ProductInquiry).count()) == before


def test_a_write_to_a_later_wave_route_that_takes_writes_answers_404_json(wave1):
    post_rules = [r for r in wave1.url_map.iter_rules()
                  if "POST" in r.methods and not r.arguments and not r.rule.startswith(("/api/", "/early-access"))
                  and state_for(entry_for(r.rule, r.endpoint), 1) == CLOSED]
    assert post_rules
    client = wave1.test_client()
    for rule in post_rules[:25]:
        rv = client.post(rule.rule, data={"x": "y"})
        assert rv.status_code == 404 and rv.get_json()["error"] == "early_access", (rule.rule, rv.status_code)


def test_an_api_call_to_a_later_wave_route_answers_404_json(wave1):
    api_rules = [r for r in wave1.url_map.iter_rules()
                 if r.rule.startswith("/api/") and "GET" in r.methods and "<" not in r.rule
                 and state_for(entry_for(r.rule, r.endpoint), 1) == CLOSED]
    assert api_rules
    rv = wave1.test_client().get(api_rules[0].rule)
    assert rv.status_code == 404 and rv.get_json()["error"] == "early_access"


def test_a_retired_address_redirects_only_when_its_destination_has_shipped(app, monkeypatch):
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    client = app.test_client()
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    rv = client.get("/dashboard/applications/5/edit")
    assert rv.status_code == 302 and rv.headers["Location"].endswith("/applications/5/edit")
    rv = client.get("/adm-kanban/roadmap")
    assert rv.status_code == 200 and b"early-access-page" in rv.data  # ships in wave 3
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "3")
    rv = client.get("/adm-kanban/roadmap")
    assert rv.status_code == 302 and "/genome/roadmap/" in rv.headers["Location"]


def test_a_malformed_switch_closes_rather_than_opens(app, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "soon")
    with app.test_request_context("/"):
        assert configured_wave() == 0
        assert not is_released_path("/account/login")


def test_staff_and_foundation_waves_pass_through(wave1):
    doc = wave_map()
    assert set(doc["pass_waves"]) == {"A", "W0", "W6"}
    assert state_for(["W6", "PG-OPS", "page", None, None], 1) == OPEN
    assert state_for(["A", "infra", "override", None, None], 1) == OPEN
    assert state_for(["W2", "PG-DIA", "page", None, None], 1) == CLOSED
    assert state_for(["W2", "PG-DIA", "page", None, None], 2) == OPEN
    assert state_for(["EA", "PG-EA", "page", None, None], 6) == CLOSED
    assert state_for(["RT", "PG-RT", "page", "/x", "W3"], 2) == CLOSED
    assert state_for(["RT", "PG-RT", "page", "/x", "W3"], 3) == REDIRECT


def test_early_access_join_stores_the_enquiry_once(wave1):
    from app import db
    from app.models.product_inquiry import ProductInquiry

    client = wave1.test_client()
    slug = "pg-gov"
    email = "waiting-list-unit@example.com"
    for _ in range(2):
        rv = client.post(f"/early-access/{slug}/join", data={"email": email, "consent": "1"})
        assert rv.status_code == 200 and b"early-access-thanks" in rv.data
    with wave1.app_context():
        rows = db.session.query(ProductInquiry).filter_by(email=email).all()
        assert len(rows) == 1 and rows[0].offer == f"early-access:{slug}"
        db.session.delete(rows[0])
        db.session.commit()


def test_early_access_join_needs_consent_and_a_real_address(wave1):
    client = wave1.test_client()
    assert b"agree" in client.post("/early-access/pg-gov/join", data={"email": "a@example.com"}).data
    assert b"valid email" in client.post("/early-access/pg-gov/join", data={"email": "nope", "consent": "1"}).data
    assert client.post("/early-access/no-such-group/join", data={"email": "a@example.com", "consent": "1"}).status_code == 404


def test_the_early_access_hub_lists_what_is_coming(wave1):
    rv = wave1.test_client().get("/early-access")
    html = rv.get_data(as_text=True)
    assert rv.status_code == 200 and "Coming to Entelim" in html
    links = re.findall(r'href="(/early-access/[a-z0-9-]+)"', html)
    assert len(links) >= 10
    for href in links[:5]:
        page = wave1.test_client().get(href)
        assert page.status_code == 200 and b"Join the waiting list" in page.data


def test_the_module_directory_and_search_leave_out_a_later_waves_pages(wave1):
    from app.modules.modules_directory.routes import all_module_links
    from app.utils.role_access import is_hidden_not_ready

    with wave1.test_request_context("/"):
        links = all_module_links()
        hidden = [link["endpoint"] for link in links if is_hidden_not_ready(link["endpoint"])]
        shown = [link["endpoint"] for link in links if not is_hidden_not_ready(link["endpoint"])]
        assert hidden and shown
        for endpoint in shown:
            assert is_released_endpoint(endpoint)


def test_the_sidebar_search_index_and_catalogue_list_only_released_pages(wave1):
    from app.models.user import User
    from app.navigation.registry import search_index

    with wave1.test_request_context("/"):
        user = User(email="nav-wave@example.com", first_name="N", last_name="W", confirmed=True,
                    enterprise_role="enterprise_architect")
        rows = search_index(user)
        assert rows
        bad = [r["url"] for r in rows if not is_released_path(r["url"])]
        assert not bad, bad


def test_the_public_menus_footer_and_sitemap_list_only_released_pages(wave1):
    from app.services.site_registry import registry_links

    with wave1.test_request_context("/"):
        coming = {"Coming soon", "Everything coming soon"}
        bad = [link for link in registry_links()
               if not is_released_path(link["href"].split("#")[0])
               and link["label"] not in coming and not link["href"].startswith("/early-access")]
        assert not bad, bad
    xml = wave1.test_client().get("/sitemap.xml").get_data(as_text=True)
    paths = re.findall(r"<loc>https://entelim.org([^<]*)</loc>", xml)
    assert paths
    assert [p for p in paths if not is_released_path(p)] == []


def test_a_module_page_whose_screen_opens_later_is_not_advertised(wave1):
    from app.services.public_pages import load_page

    with wave1.test_request_context("/"):
        page = load_page("module", slug="architecture-model")
        assert page is not None and page.wave_closed is True
        assert page.is_held
        open_page = load_page("module", slug="applications")
        assert open_page.wave_closed is False
    rv = wave1.test_client().get("/modules/architecture-model")
    assert b"early-access-page" in rv.data


def test_every_wave_one_page_of_the_plan_is_open_under_wave_one(app):
    import json

    from app.release_waves.build import PLAN_PATH, _plan_pages, norm

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    registered = {norm(r.rule): r for r in app.url_map.iter_rules()}
    checked = 0
    for key, item in _plan_pages(plan).items():
        if item["wave"] != "W1" or key not in registered:
            continue
        checked += 1
        assert state_for(entry_for(registered[key].rule, registered[key].endpoint), 1) == OPEN, item["title"]
    assert checked >= 40


def test_a_signed_out_crawl_of_every_plain_get_page_never_errors_under_wave_one(wave1):
    client = wave1.test_client()
    bad = []
    seen = 0
    for rule in wave1.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments or rule.rule.startswith(("/static", "/admin", "/health")):
            continue
        if rule.rule in ("/logout", "/account/logout") or "download" in rule.rule or "export" in rule.rule:
            continue
        seen += 1
        rv = client.get(rule.rule, follow_redirects=False)
        if rv.status_code >= 500:
            bad.append((rule.rule, rv.status_code))
        if state_for(entry_for(rule.rule, rule.endpoint), 1) == CLOSED:
            wants_html = not rule.rule.startswith("/api/")
            if wants_html:
                assert rv.status_code in (200, 404), (rule.rule, rv.status_code)
                if rv.status_code == 200:
                    assert b"early-access-page" in rv.data, rule.rule
    assert seen > 100
    assert not bad, bad[:10]


def test_every_public_content_page_names_its_wave():
    """Role, use-case, module, comparison, connector, site, legal and story pages each say which wave they
    belong to (wave: 1 to 5, or EA). A page that says nothing fails here; at runtime it is closed."""
    from app.release_waves import page_wave_code
    from app.services.public_pages import load_all_pages

    pages = load_all_pages()
    assert len(pages) >= 100
    missing = [p.url for p in pages if page_wave_code(p.front_matter.get("wave")) == "X"]
    assert not missing, f"{len(missing)} content pages name no wave: {missing[:10]}"


def test_a_use_case_page_of_a_later_wave_answers_early_access_and_a_first_wave_page_its_body(wave1):
    client = wave1.test_client()
    later = client.get("/use-cases/duplicate-software-spend")
    assert later.status_code == 200 and b"early-access-page" in later.data
    first = client.get("/use-cases/what-i-own")
    assert first.status_code == 200 and b"early-access-page" not in first.data
    role = client.get("/roles/chief-architects")
    assert b"early-access-page" in role.data
    assert b"early-access-page" not in client.get("/roles/startup-founder").data


def test_no_early_access_group_title_or_blurb_carries_plan_wording(wave1):
    """A customer reads plain capability names: no wave, screen, rebuild, redirect, operator or admin-pages wording."""
    banned = re.compile(r"wave|screen|rebuild|redirect|operator|admin pages", re.I)
    from app.release_waves.routes import coming_groups

    groups = coming_groups()
    assert len(groups) >= 10
    for g in groups:
        assert not banned.search(g["title"]), g["title"]
        assert not banned.search(g["blurb"] or ""), g["blurb"]
    html = wave1.test_client().get("/early-access").get_data(as_text=True)
    assert "Connectors and integrations" in html
    # every group a closed address can answer with reads the same way
    for code, info in wave_map()["groups"].items():
        assert not banned.search(info["title"]), (code, info["title"])
        assert not banned.search(info["blurb"] or ""), (code, info["blurb"])
