"""The selector a recording uses to hide the signed-in person's address matches the sidebar markup.

A change to the sidebar footer that stops the selector matching would put a real address into a published
recording without anything failing; this holds the two together.
"""
import uuid

from bs4 import BeautifulSoup

from scripts.capture.module_clips import HIDE


def _address_selector():
    found = [s for s in HIDE if "@" in s]
    assert len(found) == 1, HIDE
    return found[0]


def _hidden_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return [el.get_text(strip=True) for el in soup.select(_address_selector())]


def test_the_selector_hides_exactly_the_signed_in_address_in_the_sidebar(app, db_session, make_org, tenant_ctx, login_as, client):
    from app import db
    from app.models.user import User

    org = make_org("recording")
    other = make_org("recording-other")
    email = "walker.%s@example.com" % uuid.uuid4().hex[:8]
    other_email = "bystander.%s@example.com" % uuid.uuid4().hex[:8]
    with tenant_ctx(org.id):
        user = User(email=email, first_name="Walker", last_name="Reed", organization_id=org.id, confirmed=True)
        db.session.add(user)
        db.session.flush()
    with tenant_ctx(other.id):
        db.session.add(User(email=other_email, first_name="Else", last_name="Where", organization_id=other.id, confirmed=True))
        db.session.flush()

    login_as(client, user)
    response = client.get("/dashboard/", follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert email in html
    assert _hidden_text(html) == [email]
    assert other_email not in html

    # the same page with the footer's address marked up differently is no longer hidden: the check notices
    changed = html.replace("<span class=\"truncate text-xs text-muted-foreground\" title=\"%s\">" % email,
                           "<span class=\"truncate text-xs text-muted-foreground\" data-address=\"%s\">" % email)
    assert changed != html
    assert _hidden_text(changed) == []
