"""At wave 1 a person never meets a link to a page the wave closes.

A visible link to a closed page is a defect: it lands on an early-access page the person did not ask for.
Two checks, both reading the wave map:
* every persona's sidebar holds only links whose page is open at wave 1;
* every link on every page a signed-in organisation administrator reaches at wave 1 either lands on a page
  with its own content or points at the early-access pages on purpose.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlparse

import pytest

from app.release_waves import is_released_endpoint, is_released_path
from app.utils.role_access import SIDEBAR_ZONES, get_sidebar_zones

PAGES = [
    "/dashboard/overview", "/applications/", "/dashboard/health", "/account/manage", "/account/manage/info",
    "/admin/", "/admin/team", "/admin/billing/", "/docs", "/early-access",
]
SKIP = re.compile(r"^(#|mailto:|tel:|javascript:|https?://|//)|^/(static|api|account/logout|logout|oauth|mcp)(/|$)")


@pytest.fixture
def wave1(app, _schema, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    return app


def _admin(db_session, org, label):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name="Wave", last_name="Links",
                organization_id=org.id, role=role, is_org_admin=True, confirmed=True)
    user.password = "pw-original"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.mark.parametrize("persona", sorted(SIDEBAR_ZONES))
def test_every_persona_sidebar_holds_only_links_open_at_wave_one(wave1, db_session, persona):
    from app.models.user import User

    user = User(email=f"{persona}@example.com", enterprise_role=persona, is_platform_admin=True, is_org_admin=True)
    with wave1.test_request_context("/"):
        zones = get_sidebar_zones(user)
    links = [link for zone in zones for link in zone["links"]]
    closed = sorted(link["endpoint"] for link in links if not is_released_endpoint(link["endpoint"]))
    assert not closed, f"{persona} sees links to pages the wave closes: {closed}"


def _hrefs(html):
    found = set()
    for href in re.findall(r'href="([^"]+)"', html):
        href = href.replace("&amp;", "&")
        if SKIP.search(href) or not href.startswith("/"):
            continue
        found.add(urlparse(href).path.rstrip("/") or "/")
    return found


def test_every_link_a_signed_in_person_sees_at_wave_one_lands_on_a_page_with_content(
    wave1, db_session, make_org, login_as
):
    from app.models import ArchiMateElement

    org = make_org("wavelinks")
    user = _admin(db_session, org, "wavelinks")
    db_session.add(ArchiMateElement(name="Billing service", type="ApplicationComponent", layer="application",
                                    organization_id=org.id))
    db_session.flush()
    client = wave1.test_client()
    login_as(client, user)

    pages = list(PAGES)
    listing = client.get("/applications/").get_data(as_text=True)
    pages += sorted(p for p in _hrefs(listing) if re.fullmatch(r"/applications/\d+", p))[:1]

    sources: dict[str, set[str]] = {}
    where: dict[str, str] = {}
    for page in pages:
        reply = client.get(page, follow_redirects=True)
        assert reply.status_code < 400, (page, reply.status_code)
        html = reply.get_data(as_text=True)
        for path in _hrefs(html):
            sources.setdefault(path, set()).add(page)
            at = html.find('href="' + path)
            where.setdefault(path, " ".join(html[max(0, at - 120): at + 40].split()))

    closed = {p: (sorted(s)[:3], where.get(p)) for p, s in sources.items()
              if not p.startswith("/early-access") and not is_released_path(p)}
    assert not closed, f"links to pages the wave closes, with the pages that show them: {closed}"

    empty = {}
    for path in sorted(sources):
        if path.startswith("/early-access"):
            continue
        reply = client.get(path, follow_redirects=True)
        body = reply.get_data()
        if reply.status_code == 404 or reply.status_code >= 500 or b'data-testid="early-access-page"' in body:
            empty[path] = (reply.status_code, sorted(sources[path])[:3])
    assert not empty, f"links that land on a missing page, a crash or an early-access page (a 403 is the person lacking a platform role, not the wave): {empty}"
