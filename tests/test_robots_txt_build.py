"""How robots.txt is built: from the URL map and the decorators on each view, never by calling a view.

The behavioural test at the top is the safety net: it asks every GET route as a signed-out visitor, through the
test client, and fails when a route that refuses a visitor is not disallowed or a public page is. Production does
no such probing, so what is checked here is that the static reading and the real answers agree.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from urllib.parse import urlsplit

import pytest
from sqlalchemy import event

from test_public_content_pages import _parse_robots_rules, _robots_allows

LOGIN = "/account/login"


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


@pytest.fixture(scope="module")
def robots_text(client):
    return client.get("/robots.txt").get_data(as_text=True)


def _path(rule) -> str:
    return re.sub(r"<[^>]+>", "1", rule.rule)


def _refused(client, path: str, rules, hops: int = 5) -> str | None:
    """Why a signed-out GET of ``path`` ends in a refusal (401, 403, sign-in or a disallowed address), else None."""
    for _ in range(hops):
        response = client.get(path)
        if response.status_code in (401, 403):
            return f"{path} answers {response.status_code}"
        if response.status_code not in (301, 302, 303, 307, 308):
            return None
        target = urlsplit(response.headers.get("Location", "")).path
        if target == LOGIN:
            return f"{path} sends the visitor to sign-in"
        if not _robots_allows(rules, target):
            return f"{path} redirects to {target}, which robots.txt disallows"
        path = target
    return None


def test_no_route_that_refuses_a_signed_out_visitor_is_allowed_to_crawlers(app, client, robots_text):
    rules = _parse_robots_rules(robots_text, "Googlebot")
    leaks = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint.endswith("static") or rule.rule == LOGIN:
            continue
        for path in (_path(rule), _path(rule) + "?x=1"):
            if _robots_allows(rules, path):
                reason = _refused(client, path.split("?")[0], rules)
                if reason:
                    leaks.append(reason)
                break
    assert not leaks, f"{len(leaks)} private routes a crawler may read: {sorted(set(leaks))[:20]}"


def test_no_public_page_is_disallowed(client, robots_text):
    rules = _parse_robots_rules(robots_text, "Googlebot")
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    pages = set(re.findall(r"<loc>https://entelim\.org([^<]*)</loc>", sitemap)) | {"/", "/early-access"}
    hub = client.get("/early-access").get_data(as_text=True)
    pages |= {urlsplit(h).path for h in re.findall(r'href="(/(?:early-access|vs)/[^"]*)"', hub)}
    pages |= {"/vs/avolution", "/vs/orbus"}
    blocked = sorted(p for p in pages if not _robots_allows(rules, p))
    assert not blocked, f"public pages a crawler may not read: {blocked}"
    assert len(pages) > 20


def test_robots_txt_is_the_same_bytes_for_a_signed_out_crawler_and_a_signed_in_person(
    app, db_session, make_org, login_as
):
    from app.models.user import User
    from app.services import crawl_rules

    org = make_org("crawl")
    user = User(email=f"crawl-{uuid.uuid4().hex[:8]}@example.com", first_name="Crawl", organization_id=org.id,
                confirmed=True)
    db_session.add(user)
    db_session.flush()
    signed_out_first = app.test_client()
    app.extensions.pop("crawl_rules", None)
    login_as(signed_in := app.test_client(), user)
    # the signed-in person asks first, on a cold cache: nothing about them may reach the file
    mine = signed_in.get("/robots.txt")
    assert mine.status_code == 200
    app.extensions.pop("crawl_rules", None)
    from flask import g
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)
    theirs = signed_out_first.get("/robots.txt")
    assert mine.get_data() == theirs.get_data()
    assert crawl_rules.robots_text().encode() == theirs.get_data()


def test_share_links_are_disallowed_and_carry_noindex(app, client, robots_text, db_session, make_org, tenant_ctx):
    from app.models.artefact_share import ArtefactShareLink, generate_share_token
    from app.modules.codegen.models import CodegenGeneration
    from app.models.solution_models import Solution

    rules = _parse_robots_rules(robots_text, "Googlebot")
    for path in ("/shared/abc", "/shared/abc?x=1", "/share/code/abc", "/share/code/abc/download"):
        assert not _robots_allows(rules, path), path

    org = make_org("share")
    link = ArtefactShareLink(token=generate_share_token(), artefact_type="maturity_heatmap",
                             organization_id=org.id, view_count=0)
    db_session.add(link)
    with tenant_ctx(org.id):
        solution = Solution(name=f"Share {uuid.uuid4().hex[:6]}", organization_id=org.id)
        db_session.add(solution)
        db_session.flush()
        token = generate_share_token()
        db_session.add(CodegenGeneration(solution_id=solution.id, config={"share_token": token, "language": "python"},
                                         generated_files={"README.md": "hello"}))
    db_session.commit()
    try:
        for path in (f"/shared/{link.token}", f"/share/code/{token}", f"/shared/{generate_share_token()}"):
            response = app.test_client().get(path)
            assert "noindex" in response.headers.get("X-Robots-Tag", ""), (path, response.status_code)
        real = app.test_client().get(f"/shared/{link.token}")
        assert real.status_code == 200
        real = app.test_client().get(f"/share/code/{token}")
        assert real.status_code == 200
    finally:
        db_session.rollback()


def test_building_robots_txt_writes_nothing_and_opens_no_connection_or_thread(app, monkeypatch):
    import redis

    from app import db
    from app.services import crawl_rules

    started, writes, commits, connects = [], [], [], []
    monkeypatch.setattr(threading.Thread, "start", lambda self, *a, **k: started.append(self.name))
    monkeypatch.setattr(redis.Connection, "connect", lambda self: connects.append("redis"), raising=True)
    with app.app_context():
        engine = db.engine

    def statement(conn, cursor, text, *rest):
        if re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER)", text, re.I):
            writes.append(text[:60])

    event.listen(engine, "before_cursor_execute", statement)

    def on_commit(conn):
        commits.append(1)

    event.listen(engine, "commit", on_commit)
    try:
        app.extensions.pop("crawl_rules", None)
        with app.test_request_context("/robots.txt"):
            text = crawl_rules.robots_text()
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", on_commit)
    assert "Disallow:" in text
    assert (started, writes, commits, connects) == ([], [], [], [])


def test_the_first_robots_txt_of_a_cold_worker_builds_in_under_a_second(app):
    from app.services import crawl_rules

    with app.test_request_context("/robots.txt"):
        crawl_rules.robots_text()  # imports and the content index are warm in a running worker
        app.extensions.pop("crawl_rules", None)
        started = time.perf_counter()
        crawl_rules.robots_text()
        elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"robots.txt took {elapsed:.2f}s to build"


def _token_rules(app):
    """GET routes whose address has a segment named like a secret (token, *_token, secret), found independently of
    the module under test."""
    return [r for r in app.url_map.iter_rules()
            if "GET" in r.methods and re.search(r"<(?:[^>:]+:)?[^>]*(?:token|secret)[^>]*>", r.rule)]


def test_every_address_that_carries_a_secret_token_is_disallowed_and_never_indexed(app, robots_text):
    rules = _parse_robots_rules(robots_text, "Googlebot")
    token_rules = _token_rules(app)
    assert {r.rule for r in token_rules} >= {
        "/account/reset-password/<token>", "/account/confirm-account/<token>", "/account/join/<token>",
        "/account/join-from-invite/<int:user_id>/<token>", "/shared/<token>", "/share/code/<token>",
    }
    problems = []
    for rule in token_rules:
        path = re.sub(r"<[^>]+>", "1", rule.rule)
        if _robots_allows(rules, path) or _robots_allows(rules, path + "?x=1"):
            problems.append(f"{rule.rule} is open to crawlers")
        response = app.test_client().get(path)
        if "noindex" not in response.headers.get("X-Robots-Tag", ""):
            problems.append(f"{rule.rule} answers {response.status_code} without noindex")
    assert not problems, problems


def test_a_new_token_route_is_covered_the_day_it_exists(app):
    from flask import Flask

    from app.services import crawl_rules

    scratch = Flask("scratch")
    scratch.add_url_rule("/unlock/<reset_token>", "unlock", lambda reset_token: "ok")
    scratch.add_url_rule("/pages/<slug>", "page", lambda slug: "ok")
    with scratch.app_context():
        found = {r.rule: crawl_rules.carries_secret_token(r) for r in scratch.url_map.iter_rules()
                 if r.endpoint != "static"}
    assert found == {"/unlock/<reset_token>": True, "/pages/<slug>": False}


def test_tracking_addresses_are_disallowed_and_never_indexed(app, robots_text):
    rules = _parse_robots_rules(robots_text, "Googlebot")
    tracking = [r for r in app.url_map.iter_rules() if r.rule.startswith("/t/")]
    assert any(r.rule == "/t/plan-click" for r in tracking)
    for rule in tracking:
        assert not _robots_allows(rules, rule.rule), rule.rule
        assert not _robots_allows(rules, rule.rule + "?plan=team&next=/contact"), rule.rule
    response = app.test_client().get("/t/plan-click?plan=team&next=/contact")
    assert "noindex" in response.headers.get("X-Robots-Tag", "")


def test_pricing_links_that_record_a_click_are_not_followed_by_crawlers(client):
    for page in ("/pricing", "/"):
        html = client.get(page).get_data(as_text=True)
        anchors = re.findall(r"<a\b[^>]*/t/plan-click[^>]*>", html)
        if page == "/pricing":
            assert len(anchors) >= 5, anchors
        assert all('rel="nofollow"' in a for a in anchors), [a for a in anchors if 'nofollow' not in a]


def _public_but_disallowed(client, rules, url_map, skip=()):
    """Routes that answer a signed-out GET with 200 (no redirect) while robots.txt disallows them."""
    found = []
    for rule in url_map.iter_rules():
        if ("GET" not in rule.methods or "<" in rule.rule or rule.endpoint.endswith("static")
                or rule.rule.startswith(skip)):
            continue
        if not _robots_allows(rules, rule.rule) and client.get(rule.rule).status_code == 200:
            found.append(rule.rule)
    return sorted(found)


def test_no_public_page_that_answers_200_is_disallowed(app, client, robots_text):
    from app.services.crawl_rules import TOOLING_PREFIXES, TRACKING_PREFIXES

    rules = _parse_robots_rules(robots_text, "Googlebot")
    found = _public_but_disallowed(client, rules, app.url_map, skip=TOOLING_PREFIXES + TRACKING_PREFIXES)
    assert not found, f"pages that answer 200 but are hidden from crawlers: {found}"


def test_the_public_page_check_sees_a_page_hidden_by_an_unknown_wrapper(app):
    import functools

    from flask import Flask

    from app.services import crawl_rules

    def unknown(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            return view(*args, **kwargs)
        return wrapper

    scratch = Flask("scratch")
    scratch.add_url_rule("/open-page", "open_page", unknown(lambda: "content"))
    with scratch.app_context():
        text = "User-agent: *\nAllow: /\n" + "".join(
            f"Disallow: {p}\n" for p in ("/open-page$", "/open-page?") if crawl_rules._is_private(
                next(r for r in scratch.url_map.iter_rules() if r.rule == "/open-page")))
    assert "Disallow: /open-page$" in text  # an unreadable wrapper hides the page, so the check below must see it
    found = _public_but_disallowed(scratch.test_client(), _parse_robots_rules(text, "Googlebot"), scratch.url_map)
    assert found == ["/open-page"]


def test_when_ready_builds_robots_and_the_sitemap_before_any_request(app, monkeypatch):
    import importlib.util
    import logging
    from types import SimpleNamespace

    from app.services import crawl_rules

    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", "gunicorn.conf.py")
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    app.extensions.pop("crawl_rules", None)  # a cold process: nothing built, nothing held
    server = SimpleNamespace(app=SimpleNamespace(wsgi=lambda: app), log=logging.getLogger("when_ready_test"))
    conf.when_ready(server)
    assert set(app.extensions["crawl_rules"]) == {"robots", "sitemap"}

    built = []
    monkeypatch.setattr(crawl_rules, "_build_robots", lambda: built.append("robots") or "")
    monkeypatch.setattr(crawl_rules, "_build_sitemap", lambda: built.append("sitemap") or "")
    started = time.perf_counter()
    first = app.test_client().get("/robots.txt")
    app.test_client().get("/sitemap.xml")
    assert built == [], "the first request after start-up rebuilt a file the server had already built"
    assert "Disallow:" in first.get_data(as_text=True)
    assert time.perf_counter() - started < 1.0


def test_when_ready_failing_to_build_does_not_stop_the_server(monkeypatch):
    import importlib.util
    import logging
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", "gunicorn.conf.py")
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)

    def broken():
        raise RuntimeError("no application")

    conf.when_ready(SimpleNamespace(app=SimpleNamespace(wsgi=broken), log=logging.getLogger("when_ready_test")))


def test_a_page_that_sends_visitors_to_sign_in_only_once_released_is_disallowed_only_then(app):
    original = app.config.get("RELEASE_WAVE")
    try:
        app.config["RELEASE_WAVE"] = "1"  # the simulation screen is still closed: everyone is offered the waiting list
        closed = app.test_client().get("/robots.txt").get_data(as_text=True)
        app.config["RELEASE_WAVE"] = "3"  # released: a visitor is sent to sign-in
        released = app.test_client().get("/robots.txt").get_data(as_text=True)
        app.config["RELEASE_WAVE"] = "1"
        closed_again = app.test_client().get("/robots.txt").get_data(as_text=True)
    finally:
        app.config["RELEASE_WAVE"] = original
        app.extensions.pop("crawl_rules", None)
    assert "Disallow: /simulations$" not in closed
    assert "Disallow: /simulations$" in released
    assert closed_again == closed


def test_a_new_page_that_checks_sign_in_in_its_own_body_is_covered_by_marking_it(app):
    from flask import Flask

    from app.services import crawl_rules

    scratch = Flask("scratch")
    state = {"released": False}
    scratch.add_url_rule("/later", "later", crawl_rules.refuses_visitors_when(lambda: state["released"])(lambda: "ok"))
    scratch.add_url_rule("/plain", "plain", lambda: "ok")
    with scratch.app_context():
        rules = {r.rule: r for r in scratch.url_map.iter_rules() if r.endpoint != "static"}
        before = {path: crawl_rules._is_private(rule) for path, rule in rules.items()}
        state["released"] = True
        after = {path: crawl_rules._is_private(rule) for path, rule in rules.items()}
    assert before == {"/later": False, "/plain": False}
    assert after == {"/later": True, "/plain": False}
