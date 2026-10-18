"""Launch gate 9: the public Known issues page, its sitemap entry and the legal links.

The site content carries no support email address on purpose (the contact page
uses an enquiry form), so the footer contact-address checks are not part of this
file; the Known issues page points people at the contact page instead.
"""

from __future__ import annotations

import re

BANNED = (".py", "app/", "docs/", "VERIFIED", "TBC", "ASSUMPTION")


def test_known_issues_page_is_public_and_plain(app):
    with app.test_client() as client:
        rv = client.get("/known-issues")
        assert rv.status_code == 200
        html = rv.data.decode()
        main = html.split("</h1>", 1)[-1].split("<footer", 1)[0]
        items = re.findall(r"<li\b", main)
        assert 1 <= len(items) <= 8
        text = re.sub(r"<[^>]+>", " ", main)
        for word in BANNED:
            assert word not in text, word


def test_known_issues_page_in_sitemap(app):
    with app.test_client() as client:
        assert "/known-issues" in client.get("/sitemap.xml").data.decode()


def test_known_issues_linked_from_contact_page(app):
    with app.test_client() as client:
        assert 'href="/known-issues"' in client.get("/contact").data.decode()


def test_terms_and_privacy_still_resolve(app):
    with app.test_client() as client:
        for path in ("/terms", "/privacy"):
            assert client.get(path).status_code == 200


def _use_case_url():
    from app.services.public_pages import load_all_pages

    return next(pg.url for pg in load_all_pages() if pg.url.startswith("/use-cases/"))


def test_public_footers_link_contact_and_known_issues(app):
    with app.test_client() as client:
        for path in ("/", _use_case_url()):
            rv = client.get(path)
            assert rv.status_code == 200, path
            html = rv.data.decode()
            for href in ("/contact", "/known-issues"):
                assert f'href="{href}"' in html, (path, href)
        for href in ("/contact", "/known-issues"):
            assert client.get(href).status_code == 200


def test_signed_in_screen_has_help_and_contact(app, client, db_session, make_org, login_as):
    from tests.test_footer_placement import _user

    user = _user(db_session, make_org("gate9-help").id, "enterprise_architect")
    db_session.commit()
    login_as(client, user)
    rv = client.get("/dashboard/overview")
    assert rv.status_code == 200
    html = rv.data.decode()
    assert 'href="/known-issues"' in html
    assert 'href="/contact"' in html
