"""The page footer ends the page content of every signed-in page.

``admin_base.html`` renders the footer as the last child of the padded block
inside ``<main>``. A template that closes one ``</div>`` too many inside its
content block closes that block, or the content column, early in a browser, and
one that leaves a ``<div>`` open nests the footer inside its own markup. Either
moves the footer (and, when the column is closed, squeezes the page sideways).
Before the footer existed nothing followed those closers, so they did no harm.
This renders the pages people reach from the sidebar and reads the DOM the way
a browser builds it, so it fails on any page whose markup takes the footer out
of its place.
"""

from __future__ import annotations

import uuid
from html.parser import HTMLParser

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

ROLES = ("enterprise_architect", "platform_admin", "procurement", "portfolio_manager", "cto")
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}


class _Nesting(HTMLParser):
    """Builds the open-element stack as a browser does for stray end tags:
    an end tag closes the nearest open element of that name (and anything
    left open inside it); one with no open match is ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[int, str]] = []
        self.count = 0
        self.main_id = None
        self.footer_grandparent = None
        self.footers = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.count += 1
        if tag == "main" and attrs.get("id") == "main-content":
            self.main_id = self.count
        if tag == "footer" and attrs.get("data-testid") == "app-footer":
            self.footer_grandparent = self.stack[-2][0] if len(self.stack) > 1 else None
            self.footers += 1
        if tag not in VOID:
            self.stack.append((self.count, tag))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][1] == tag:
                del self.stack[index:]
                return


def _user(db_session, org_id, role):
    from app.models.user import Role, User

    if Role.query.filter_by(name="Administrator").first() is None:
        Role.insert_roles()
    user = User(
        email=f"footer-{role}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Footer",
        last_name="Reader",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
        is_platform_admin=(role == "platform_admin"),
        # Genuine platform authority is the flag AND the Administrator role;
        # the persona string alone no longer stands in for it.
        role=Role.query.filter_by(name="Administrator").first() if role == "platform_admin" else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _endpoints(role):
    from app.utils.role_access import SIDEBAR_ZONES

    seen = []
    for zone in SIDEBAR_ZONES.get(role, []):
        for link in zone["links"]:
            if link["endpoint"] not in seen:
                seen.append(link["endpoint"])
    return seen


def test_footer_ends_the_page_content_on_every_sidebar_page(
    app, client, db_session, make_org, login_as
):
    from flask import url_for

    # One organisation per person: a new organisation's plan admits three.
    users = {role: _user(db_session, make_org(f"footer-{i}").id, role) for i, role in enumerate(ROLES)}
    db_session.commit()

    checked, misplaced = 0, []
    done = set()
    # Screens the sidebar reaches under another name or with a query string.
    extra = [
        ("platform_admin", "/ai-chat/"),
        ("platform_admin", "/dashboard/overview"),
        ("platform_admin", "/applications/"),
        ("platform_admin", "/arb/"),
        ("platform_admin", "/procurement/compliance"),
        ("platform_admin", "/dashboard/applications/vendors"),
        ("platform_admin", "/enterprise/requirements-backlog"),
        ("platform_admin", "/capability-map/"),
    ]
    for role, url in extra:
        login_as(client, users[role])
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
        parser = _Nesting()
        parser.feed(response.get_data(as_text=True))
        checked += 1
        if not (parser.footers == 1 and parser.footer_grandparent == parser.main_id):
            misplaced.append(url)
    for role, user in users.items():
        for endpoint in _endpoints(role):
            if endpoint in done:
                continue
            with app.test_request_context():
                try:
                    url = url_for(endpoint)
                except Exception:  # needs arguments, or not registered
                    continue
            done.add(endpoint)
            login_as(client, user)
            response = client.get(url)
            if response.status_code != 200 or b"app-footer" not in response.data:
                continue
            parser = _Nesting()
            parser.feed(response.get_data(as_text=True))
            checked += 1
            if not (
                parser.footers == 1
                and parser.main_id is not None
                and parser.footer_grandparent == parser.main_id
            ):
                misplaced.append(f"{endpoint} ({url})")

    assert checked >= 20, f"only {checked} pages rendered the footer; the page list drifted"
    assert misplaced == [], "footer moved out of its place by unbalanced markup: %s" % misplaced


def test_footer_ends_the_page_content_on_pages_that_list_records(
    app, client, db_session, make_org, login_as
):
    """Pages with a populated list render branches an empty organisation never
    reaches; the solutions list's pager is one."""
    from app.models.solution_models import Solution

    org = make_org("footer-solutions")
    user = _user(db_session, org.id, "solution_architect")
    db_session.add(Solution(
        name=f"Footer check {uuid.uuid4().hex[:6]}", organization_id=org.id, created_by_id=user.id,
    ))
    db_session.commit()

    login_as(client, user)
    response = client.get("/solutions/?status=all")
    assert response.status_code == 200
    assert b"Footer check" in response.data, "the list did not render the seeded solution"
    parser = _Nesting()
    parser.feed(response.get_data(as_text=True))
    assert parser.footers == 1
    assert parser.footer_grandparent == parser.main_id, "solutions list moves the footer out of its place"
