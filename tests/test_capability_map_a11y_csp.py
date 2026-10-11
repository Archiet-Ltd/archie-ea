"""Accessible names and CSP nonce compliance for capability map pages.

Defects found by whole-product audit:
1. An icon-only sidebar collapse button in admin_header.html had no accessible
   name — the <i> icon was not hidden from assistive technology.
2. <style> blocks in _head.html and admin_base.html lacked the CSP nonce,
   causing 'style-src-elem' violations on every page.

Each of the three capability map pages is rendered for a signed-in architect
and checked for:
- The sidebar collapse button's <i> icon carries aria-hidden="true"
- Every <style> element carries a nonce attribute
- No static inline style= attributes (which would violate style-src-elem)
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# Pages under audit: dashboard, hierarchy, simple
CAPMAP_PAGES = [
    "/capability-map/",
    "/capability-map/hierarchy",
    "/capability-map/simple",
]

STYLE_TAG_RE = re.compile(r"<style\b([^>]*)>", re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r"\bstyle\s*=", re.IGNORECASE)


def _login(client, user_id):
    """Standard Flask-Login test-client pattern (see test_sidebar_render.py)."""
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, make_org, label):
    from app.models.user import User

    org = make_org(f"capmap-a11y-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"capmap-a11y-{label}-{suffix}@example.com",
        first_name="Capmap",
        last_name="A11y",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user.id, org


def _get_style_tags_without_nonce(html):
    """Find all <style> tags that lack a nonce attribute."""
    nonceless = []
    for match in STYLE_TAG_RE.finditer(html):
        attrs = match.group(1)
        if "nonce" not in attrs:
            nonceless.append(match.group(0))
    return nonceless


def _get_inline_style_attributes(html):
    """Find all static inline style= attributes (excluding Alpine :style bindings
    and style attributes inside <script> blocks)."""
    # Strip <script> blocks first to avoid matching style= inside JS strings
    stripped = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)
    matches = []
    for match in STYLE_ATTR_RE.finditer(stripped):
        # Skip Alpine :style bindings (handled by JS, not static HTML)
        preceding = stripped[max(0, match.start() - 10):match.start()]
        if ":" in preceding:
            continue
        matches.append(match.group(0))
    return matches


def _sidebar_collapse_icon_has_aria_hidden(html):
    """Check that the sidebar collapse button's <i> icon has aria-hidden='true'."""
    # Find the sidebar collapse button pattern
    pattern = re.compile(
        r'<button\b[^>]*sidebar-collapse-btn[^>]*>.*?<i\b[^>]*data-lucide="panel-left"[^>]*>',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html)
    if not match:
        return False, "sidebar collapse button not found"
    icon_tag = match.group(0)
    if 'aria-hidden="true"' in icon_tag or "aria-hidden=true" in icon_tag:
        return True, ""
    return False, "sidebar collapse icon missing aria-hidden='true'"


@pytest.fixture
def _user_and_org(db_session, make_org):
    """Create a single enterprise_architect user."""
    return _make_user(db_session, make_org, "arch")


@pytest.fixture
def _logged_in_client(client, _user_and_org):
    """Log in as the enterprise_architect user."""
    user_id, _org = _user_and_org
    _login(client, user_id)
    return client


class TestCapabilityMapSidebarButtonA11y:
    """The sidebar collapse button's icon must be hidden from assistive tech."""

    @pytest.mark.parametrize("path", CAPMAP_PAGES, ids=["dashboard", "hierarchy", "simple"])
    def test_sidebar_collapse_icon_hidden_from_at(self, _logged_in_client, path):
        """The <i data-lucide="panel-left"> inside the sidebar collapse button
        must carry aria-hidden='true' so screen readers don't announce the icon."""
        resp = _logged_in_client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"

        ok, msg = _sidebar_collapse_icon_has_aria_hidden(resp.data.decode("utf-8"))
        assert ok, f"{path}: {msg}"


class TestCapabilityMapCspNonce:
    """All <style> elements must carry a nonce; no static inline style= attributes."""

    @pytest.mark.parametrize("path", CAPMAP_PAGES, ids=["dashboard", "hierarchy", "simple"])
    def test_all_style_tags_have_nonce(self, _logged_in_client, path):
        """Every <style> element must carry nonce="{{ csp_nonce }}" to satisfy
        the style-src-elem CSP directive."""
        resp = _logged_in_client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"

        nonceless = _get_style_tags_without_nonce(resp.data.decode("utf-8"))
        assert not nonceless, (
            f"{path}: {len(nonceless)} <style> tag(s) without a nonce attribute:\n"
            + "\n".join(nonceless[:10])
        )

    @pytest.mark.parametrize("path", CAPMAP_PAGES, ids=["dashboard", "hierarchy", "simple"])
    def test_no_static_inline_style_attributes(self, _logged_in_client, path):
        """Static inline style= attributes violate style-src-elem 'self' 'nonce-…'.
        All styling must use classes or Alpine :style bindings."""
        resp = _logged_in_client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"

        inline = _get_inline_style_attributes(resp.data.decode("utf-8"))
        assert not inline, (
            f"{path}: {len(inline)} static inline style= attribute(s) found:\n"
            + "\n".join(inline[:10])
        )