"""Non-browser regression: a cache hit on the capability-map pages must
never serve a stale CSP nonce, and the sidebar button must carry a static
accessible name.

app/modules/capabilities/routes/map_views.py's hierarchy()/simple_view()/
dashboard() used to be decorated directly with @cached, which cached the
*rendered HTML* — a nonce baked in at render time by CspNonceExtension
(app/_bootstrap/security.py). A cache hit on a later request then served
that stale nonce inside a response whose Content-Security-Policy header
carried a different, freshly generated one (a new nonce is generated per
request regardless of caching), and the browser refused every nonce'd tag
on the page.

The browser smoke test (tests/smoke/test_capability_map_csp_and_button_names.py)
proves this end to end, but only where a real browser is available, and it
needs a genuine cache hit to reproduce the bug at all. CI's smoke job has
Postgres but no Redis, and app/extensions/cache.py's CacheManager disables
itself — get()/set() become no-ops — when the Redis connection fails at
startup, so a cache hit never happens there and that test cannot go red on
a regression in CI. This test forces a real cache hit with an in-memory
dict standing in for Redis: the same monkeypatch already used by
tests/test_capability_map_shell.py's cross-org cache-isolation test (reused
here, not duplicated), applied to app/extensions/cache.py's existing
cache_manager rather than a second cache implementation.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

CAPMAP_PAGES = [
    "/capability-map/hierarchy",
    "/capability-map/simple",
    "/capability-map/dashboard",
]

_HEADER_NONCE_RE = re.compile(r"script-src[^;]*'nonce-([A-Za-z0-9_\-]+)'")
_BODY_NONCE_RE = re.compile(r'<script\s+nonce="([^"]+)"')


def _force_in_memory_cache(monkeypatch):
    """Stand in for Redis with a plain dict, so @cached genuinely caches.

    Monkeypatches app/extensions/cache.py's existing cache_manager.get/set
    only — no second cache is added. Identical to the pattern
    tests/test_capability_map_shell.py's
    test_cross_org_hierarchy_cache_isolation-style test already uses.
    """
    from app.extensions import cache as cache_module

    store: dict[str, object] = {}
    monkeypatch.setattr(
        cache_module.cache_manager,
        "get",
        lambda key, default=None: store.get(key, default),
    )
    monkeypatch.setattr(
        cache_module.cache_manager,
        "set",
        lambda key, value, ttl=300: (store.__setitem__(key, value), True)[1],
    )
    return store


def _make_user(db_session, org):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Architect").first()

    user = User(
        email=f"nonce-regress-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Nonce",
        last_name="Regress",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex  # generated, not a real credential
    db_session.add(user)
    db_session.flush()
    return user


def _header_nonce(resp):
    csp = resp.headers.get("Content-Security-Policy", "")
    match = _HEADER_NONCE_RE.search(csp)
    assert match, "response carried no script-src nonce in its CSP header: %r" % csp
    return match.group(1)


def _body_nonce(resp):
    body = resp.get_data(as_text=True)
    match = _BODY_NONCE_RE.search(body)
    assert match, "response body carried no nonce'd <script> tag"
    return match.group(1)


@pytest.fixture
def _nonce_regress_client(app, db_session, make_org, login_as, monkeypatch):
    _force_in_memory_cache(monkeypatch)
    org = make_org("nonce-regress")
    user = _make_user(db_session, org)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)
    return client


@pytest.mark.parametrize("path", CAPMAP_PAGES, ids=["hierarchy", "simple", "dashboard"])
def test_second_request_body_nonce_matches_its_own_header_nonce(
    _nonce_regress_client, path
):
    """The second (cache-hit) request's body and header nonces must agree.

    The first request is always a cache MISS and is trivially consistent
    (the view just rendered that exact response). The second request is
    where the bug lived: with the un-fixed view, the cached HTML still
    carries the FIRST request's nonce while this response's own CSP header
    carries a freshly generated one.
    """
    first = _nonce_regress_client.get(path)
    assert first.status_code == 200, f"{path} -> {first.status_code}"

    second = _nonce_regress_client.get(path)
    assert second.status_code == 200, f"{path} -> {second.status_code}"

    header_nonce = _header_nonce(second)
    body_nonce = _body_nonce(second)
    assert body_nonce == header_nonce, (
        f"{path}: on the second (cache-hit) request, the body's <script> nonce "
        f"({body_nonce!r}) does not match the Content-Security-Policy header's "
        f"nonce ({header_nonce!r}) — the cached response served a stale nonce"
    )


@pytest.mark.parametrize("path", CAPMAP_PAGES, ids=["hierarchy", "simple", "dashboard"])
def test_sidebar_button_carries_a_static_aria_label(_nonce_regress_client, path):
    """The sidebar-collapse button's accessible name must not depend on
    Alpine having run: a static aria-label must be present in the HTML
    Flask itself served, before any script executes."""
    resp = _nonce_regress_client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"

    body = resp.get_data(as_text=True)
    assert 'class="hidden lg:flex h-9 w-9 items-center justify-center rounded-md' in body, (
        f"{path}: the sidebar-collapse button itself is missing from the response"
    )
    assert 'aria-label="Toggle sidebar"' in body, (
        f"{path}: the sidebar-collapse button has no static aria-label in the "
        "server-rendered HTML"
    )
