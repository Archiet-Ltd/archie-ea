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


NAMED_ADDRESSES = {"/opportunities": "Opportunities", "/recommendations": "Recommendations", "/simulations": "Simulation scenarios"}


@pytest.mark.parametrize("path,title", sorted(NAMED_ADDRESSES.items()))
def test_a_named_address_lands_on_its_early_access_page_in_wave_one_and_with_the_switch_off(app, _schema, monkeypatch, path, title):
    for wave in ("1", None):
        monkeypatch.setitem(app.config, "RELEASE_WAVE", wave)
        monkeypatch.delenv("RELEASE_WAVE", raising=False)
        rv = app.test_client().get(path)
        html = rv.get_data(as_text=True)
        assert rv.status_code == 200, (path, wave)
        assert 'data-testid="early-access-page"' in html and "<h1>%s</h1>" % title in html, (path, wave)
        assert "Join the waiting list" in html
    with app.test_request_context(path):
        assert entry_for(path, app.url_map.bind("").match(path)[0])[0] == "W3"


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
    rv = client.get("/adm-kanban/roadmap")  # the roadmap lens is open at wave 1, so its old address leads there
    assert rv.status_code == 302 and "/genome/roadmap/" in rv.headers["Location"]
    rv = client.get("/capability-map/archimate/review-queue")
    assert rv.status_code == 200 and b"early-access-page" in rv.data  # ships in wave 2
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "2")
    rv = client.get("/capability-map/archimate/review-queue")
    assert rv.status_code == 302 and "/ai-chat/approvals/inbox" in rv.headers["Location"]


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
                    # a capability's own page, or the hub for plumbing that has no page of its own
                    assert b"early-access-page" in rv.data or b"early-access-hub" in rv.data, rule.rule
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
    assert "Connectors and integrations" not in html  # Connections is open, so it is not listed as coming
    # every group a closed address can answer with reads the same way
    for code, info in wave_map()["groups"].items():
        assert not banned.search(info["title"]), (code, info["title"])
        assert not banned.search(info["blurb"] or ""), (code, info["blurb"])


def test_the_github_install_return_and_starter_removal_are_open_at_wave_one(wave1):
    """Connecting GitHub ends on the return address the App install sends the administrator back to, and a
    first-sit-down starter capability can be removed: neither may answer early access."""
    with wave1.test_request_context("/"):
        assert is_released_path("/admin/connectors/github")
        assert is_released_path("/admin/connectors/github/installed")
        assert is_released_path("/account/welcome/starter/remove")


SIGN_UP_LOADS = (
    "/capability-map/",
    "/capability-map/api/capabilities",
    "/capability-map/health",
    "/capability-map/maturity",
    "/capability-map/list",
    "/capability-map/hierarchy",
    "/enterprise/capability-map/mapping",
    "/enterprise/capabilities/7/fact-sheet",
    "/capability-maturity/frameworks",
    "/capability-maturity/framework/togaf",
    "/genome/roadmap/",
    "/business-model/",
    "/business-model/3",
    "/business-model/api/list",
    "/value-streams/",
    "/value-streams/4",
    "/value-streams/4/grid",
    "/frameworks/",
    "/industry-apqc/framework/banking",
    "/ea-workflows/definitions",
    "/dashboard/compliance",
    "/dashboard/api/compliance/frameworks",
    "/dashboard/api/compliance/frameworks/2/controls",
    "/safe/portfolio/",
    "/intelligence/answers/12",
)


@pytest.mark.parametrize("path", SIGN_UP_LOADS)
def test_what_sign_up_loads_is_open_at_wave_one(wave1, path):
    """The customer chooses canvases and frameworks at sign-up and they are loaded to match, so the capability
    map, canvases, value streams, framework catalogues and compliance pages must open."""
    with wave1.test_request_context("/"):
        assert is_released_path(path), f"{path} is closed at wave 1"


def test_every_route_an_assistant_tool_reads_is_open_at_wave_one(wave1):
    """The assistant connection stays on at wave 1: the read tools that are listed name rules that are open."""
    from app.modules.ai_chat.tools.route_read_tools import TOOL_ROUTE_PATHS, tool_released

    assert tool_released("ask_impact")
    assert tool_released("get_element") and tool_released("search_elements")
    for name in ("ask_strategy", "ask_portfolio", "ask_programme", "ask_risk", "ask_accountability"):
        assert tool_released(name), name
    assert set(TOOL_ROUTE_PATHS) >= {"ask_impact"}


def test_the_hub_does_not_list_a_capability_that_already_has_a_page_open(wave1):
    from app.release_waves.routes import coming_groups

    titles = {g["title"] for g in coming_groups()}
    assert "Connectors and integrations" not in titles, "Connections is open in the sidebar, so it is not coming"
    assert "Capability map, maturity and capability records" not in titles
    assert titles, "later capabilities are still listed"
    html = wave1.test_client().get("/early-access").get_data(as_text=True)
    assert "Connectors and integrations" not in html


def test_the_waiting_list_form_labels_its_fields(wave1):
    html = wave1.test_client().get("/modules/diagrams").get_data(as_text=True)
    assert 'for="early-access-email"' in html and 'id="early-access-email"' in html
    assert 'for="early-access-consent"' in html and 'id="early-access-consent"' in html


def test_a_group_with_its_own_name_has_a_readable_address_and_the_old_one_moves_permanently(wave1):
    client = wave1.test_client()
    page = client.get("/early-access/business-cases")
    assert page.status_code == 200 and b"Business Cases" in page.data
    moved = client.get("/early-access/pg-bc")
    assert moved.status_code == 301 and moved.headers["Location"].endswith("/early-access/business-cases")
    assert client.get("/early-access/business-layer").status_code == 200
    assert client.get("/early-access/pg-bl").status_code == 301
    # a retired group is not a capability: its old address moves to the decisions address it stood for
    retired = client.get("/early-access/pg-rt")
    assert retired.status_code == 301 and retired.headers["Location"].endswith("/architecture/adrs/")
    assert client.get("/early-access/no-such-group").status_code == 404


def test_every_group_address_is_unique_and_the_hub_links_the_readable_ones(wave1):
    from app.release_waves import code_slug, group_by_slug, legacy_slug

    groups = {code: info for code, info in wave_map()["groups"].items() if info.get("page", True)}
    slugs = [code_slug(code) for code in groups]
    assert len(slugs) == len(set(slugs)), "two groups share one early-access address"
    for code in groups:
        assert group_by_slug(code_slug(code))[0] == code
        # the earlier code-based address is no page of its own: it only moves on
        assert legacy_slug(code) == code_slug(code) or group_by_slug(legacy_slug(code)) is None
    hub = wave1.test_client().get("/early-access").get_data(as_text=True)
    for code in groups:
        if code_slug(code) != legacy_slug(code):
            assert f"/early-access/{legacy_slug(code)}\"" not in hub, "the hub links an address that only moves on"


def test_the_early_access_page_does_not_say_twice_that_it_is_not_open(wave1):
    html = wave1.test_client().get("/early-access/business-cases").get_data(as_text=True)
    article = re.search(r'<article[^>]*data-testid="early-access-page".*?</article>', html, re.S).group(0)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", article))
    assert "later release" not in text.lower()
    assert "not available yet" not in text.lower()
    assert "Join the waiting list and we will email you when you can use it." in text


def _early_access_texts(client, slug):
    html = client.get(f"/early-access/{slug}").get_data(as_text=True)
    article = re.search(r'<article[^>]*data-testid="early-access-page".*?</article>', html, re.S).group(0)
    heading = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.search(r"<h1[^>]*>.*?</h1>", article, re.S).group(0))).strip()
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", article))
    return heading, text


def test_no_early_access_page_says_a_sentence_twice_or_is_headed_generically(wave1):
    from app.release_waves import code_slug

    client = wave1.test_client()
    groups = {code: info for code, info in wave_map()["groups"].items() if info.get("page", True)}
    assert len(groups) >= 70
    for code in groups:
        heading, text = _early_access_texts(client, code_slug(code))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        repeated = {s for s in sentences if sentences.count(s) > 1}
        assert not repeated, (code, repeated)
        assert heading and heading.lower() not in ("this capability", "capability", "this page"), (code, heading)
        assert "Join the waiting list and we will email you when you can use it." in text, code


def _earlier_addresses():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "app" / "release_waves" / "earlier_addresses.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_early_access_address_an_earlier_release_served_still_answers(wave1):
    """A shared link keeps working: each earlier address answers 200, or moves permanently to a page that answers 200."""
    client = wave1.test_client()
    earlier = _earlier_addresses()
    assert len(earlier) >= 200
    for slug in earlier:
        first = client.get(f"/early-access/{slug}")
        if first.status_code == 200:
            continue
        assert first.status_code == 301, (slug, first.status_code)
        landed = client.get(first.headers["Location"])
        assert landed.status_code == 200 and len(landed.data) > 2000, (slug, first.headers["Location"], landed.status_code)
        assert b"We couldn't find that page" not in landed.data, slug


def test_the_two_readable_addresses_a_blueprint_name_once_shadowed_are_pages_again(wave1):
    client = wave1.test_client()
    for slug, heading in (("connectors-and-integrations", "Connectors and integrations"), ("governance", "Governance")):
        page = client.get(f"/early-access/{slug}")
        assert page.status_code == 200, slug
        assert f"<h1>{heading}</h1>" in page.get_data(as_text=True)
    # the address of the blueprint that shared the name moves to the capability's page
    moved = client.get("/early-access/bp-connectors")
    assert moved.status_code == 301 and moved.headers["Location"].endswith("/early-access/connectors-and-integrations")
    moved = client.get("/early-access/bp-governance")
    assert moved.status_code == 301 and moved.headers["Location"].endswith("/early-access/governance")


def test_a_retired_group_a_public_group_and_plumbing_have_no_early_access_page(wave1):
    client = wave1.test_client()
    retired = client.get("/early-access/pg-rt")
    assert retired.status_code == 301 and retired.headers["Location"].endswith("/architecture/adrs/")
    followed = client.get("/architecture/adrs/")
    assert followed.status_code == 200 and b"early-access-page" in followed.data
    for slug in ("pg-pub", "infra", "bp-api-session-status", "bp-sidebar-api", "bp-csp-report", "bp-dashboard-pages"):
        moved = client.get(f"/early-access/{slug}")
        assert moved.status_code == 301 and moved.headers["Location"].endswith("/early-access"), slug
        assert b"early-access-hub" in client.get(moved.headers["Location"]).data
    # their names answer no page of their own: an earlier name moves to the hub, a name never served is not found
    for code, info in wave_map()["groups"].items():
        if info.get("page", True) is False:
            name = re.sub(r"[^a-z0-9]+", "-", info["title"].lower()).strip("-")
            reply = client.get(f"/early-access/{name}")
            assert reply.status_code in (301, 404), (code, reply.status_code)
            if reply.status_code == 301:
                assert reply.headers["Location"].rstrip("/").endswith(("/early-access", "/architecture/adrs")), code


def test_a_closed_internal_address_leads_to_the_hub_not_to_a_page_about_plumbing(wave1):
    client = wave1.test_client()
    html = client.get("/capabilities/").get_data(as_text=True)
    assert 'data-testid="early-access-hub"' in html and 'data-testid="early-access-page"' not in html
    api = client.get("/api/auth/session")
    assert api.status_code == 404 and api.get_json()["early_access"] == "/early-access"


def test_no_early_access_page_is_headed_this_page_and_no_two_share_a_description(wave1):
    pages = {code: info for code, info in wave_map()["groups"].items() if info.get("page", True)}
    assert len(pages) >= 70
    descriptions = Counter(info["blurb"] for info in pages.values())
    assert not [d for d, n in descriptions.items() if n > 1], "two early-access pages share one description"
    assert all(info["blurb"].strip() and info["blurb"] != "Entelim is building this." for info in pages.values())
    titles = Counter(info["title"].lower() for info in pages.values())
    assert not [t for t, n in titles.items() if n > 1]
    client = wave1.test_client()
    from app.release_waves import code_slug

    for code in pages:
        heading, text = _early_access_texts(client, code_slug(code))
        assert heading.lower() not in ("this page", "this capability", "capability"), (code, heading)


def test_the_build_refuses_a_page_headed_generically_or_sharing_a_name_or_description():
    from app.release_waves.build import check_groups

    good = {"A": {"title": "Alpha", "blurb": "One."}, "B": {"title": "Beta", "blurb": "Two."}}
    check_groups(good)
    for bad in (
        {**good, "C": {"title": "This page", "blurb": "Three."}},
        {**good, "C": {"title": "alpha", "blurb": "Three."}},
        {**good, "C": {"title": "Gamma", "blurb": "One."}},
        {**good, "C": {"title": "Gamma", "blurb": ""}},
    ):
        with pytest.raises(ValueError):
            check_groups(bad)
    check_groups({**good, "C": {"title": "This page", "blurb": "", "page": False}})


TIMELINE_WORDS = re.compile(r"\b(release|releases|released|wave|waves|later|soon|quarter|quarters|(?:19|20)\d\d)\b", re.I)


def _timeline_offences(client):
    from app.release_waves import code_slug

    found = []
    groups = {code: info for code, info in wave_map()["groups"].items() if info.get("page", True)}
    assert len(groups) >= 70
    for code in groups:
        heading, text = _early_access_texts(client, code_slug(code))
        for match in TIMELINE_WORDS.finditer(text):
            found.append((code, match.group(0)))
    hub = client.get("/early-access").get_data(as_text=True)
    hub_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.search(r"<article[^>]*early-access-hub.*?</article>", hub, re.S).group(0)))
    found.extend(("hub", m.group(0)) for m in TIMELINE_WORDS.finditer(hub_text))
    return found


def test_no_early_access_page_says_when_something_opens(wave1):
    """The public text presents each capability as what Entelim offers: no release, wave, date or 'later'."""
    assert _timeline_offences(wave1.test_client()) == []


def test_the_timeline_check_refuses_a_page_that_says_when_it_opens(wave1, monkeypatch):
    from app.release_waves import wave_map as shipped

    doc = shipped()
    original = doc["groups"]["PG-BL"]["blurb"]
    monkeypatch.setitem(doc["groups"]["PG-BL"], "blurb", original + " It opens in a later release.")
    assert ("PG-BL", "release") in _timeline_offences(wave1.test_client())


CLOSED_ADDRESSES = {
    "/vendors": "vendors-contracts-licences-and-spend",
    "/vendors/": "vendors-contracts-licences-and-spend",
    "/roadmaps": "roadmaps-and-plateaus-in-the-shapes-people-know",
    "/technology-roadmap": "roadmaps-and-plateaus-in-the-shapes-people-know",
    "/hybrid-roadmap": "roadmaps-and-plateaus-in-the-shapes-people-know",
    "/integrations": "connectors-and-integrations",
}


@pytest.mark.parametrize("address", sorted(CLOSED_ADDRESSES))
def test_a_closed_address_a_buyer_knows_lands_on_its_own_early_access_page(wave1, address):
    client = wave1.test_client()
    reply = client.get(address)
    html = reply.get_data(as_text=True)
    assert reply.status_code in (200, 404), reply.status_code
    assert 'data-testid="early-access-page"' in html and 'data-testid="early-access-hub"' not in html
    heading, text = _early_access_texts(client, CLOSED_ADDRESSES[address])
    page_heading = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.search(r"<h1[^>]*>.*?</h1>", html, re.S).group(0))).strip()
    assert page_heading == heading and len(text) > 80


def test_a_closed_internal_address_treats_a_signed_out_visitor_like_every_other(wave1):
    client = wave1.test_client()
    reply = client.get("/usage-analytics/")
    assert reply.status_code in (200, 404), (reply.status_code, reply.headers.get("Location"))
    assert 'data-testid="early-access-hub"' in reply.get_data(as_text=True)
    other = client.get("/capabilities/")
    assert other.status_code == reply.status_code


def test_the_planning_timeline_data_route_ships_with_its_page(app):
    """The dashboard's roadmap loads from the timeline route, so the route sits in the same wave and page
    group as the dashboard: a data route left out of the map is closed on a page that is open."""
    page = entry_for("/implementation/", "implementation_planning.implementation_dashboard")
    data = entry_for("/implementation/api/roadmap-timeline", "implementation_planning.api_roadmap_timeline")
    assert page is not None and data is not None, (page, data)
    assert (data[0], data[1], data[4]) == (page[0], page[1], page[4]), (page, data)
