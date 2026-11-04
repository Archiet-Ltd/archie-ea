"""No sidebar entry is offered to a person whose page refuses them.

For every role a person can hold, in one organisation, sign in, read the sidebar the real
Home page carries and open each link in it. A link that answers 403 was offered to someone
its route turns away: the registry's requirement and the route's rule disagree. The test
lists every mismatch at once, so one run shows the whole gap.
"""

from __future__ import annotations

import pytest

from app.models.user import VALID_ROLES
from tests.test_sidebar_render import _person, _sidebar


def _org(make_org, label):
    """One organisation on a plan that admits a person for every role."""
    from app.services.billing_plans import set_contract_plan

    org = make_org(label)
    set_contract_plan(org, "enterprise", None)
    return org


def _ordinary_person(app, db_session, make_org, role, label, org):
    """A signed-in colleague with the persona ``role`` and the ordinary "User" account role: no
    administrator, and not the "Architect" account role, which would pass every architect-only page."""
    from app.models.user import Role, User

    client, _ = _person(app, db_session, make_org, role, label, org=org)
    Role.insert_roles()
    user = User.query.filter(User.email.like(f"nav3-{label}-%")).one()
    user.role = Role.query.filter_by(name="User").one()
    db_session.flush()
    return client


def _refused(app, db_session, make_org, role, org):
    client = _ordinary_person(app, db_session, make_org, role, f"offer-{role}", org)
    _, _, links = _sidebar(client)
    targets = sorted({link["href"] for link in links
                      if link["href"] and link["href"].startswith("/") and not link["href"].startswith("/account/logout")})
    refused = {}
    for href in targets:
        # A page that answers for itself is judged where it stands; only a hand-off to another page is followed.
        response = client.get(href)
        status = client.get(href, follow_redirects=True).status_code if response.status_code in (301, 302, 303, 307, 308) else response.status_code
        if status == 403:
            refused[href] = status
    return refused


def test_no_role_is_offered_a_sidebar_link_its_page_refuses(app, db_session, make_org):
    org = _org(make_org, "offer")
    mismatches = {}
    for role in VALID_ROLES:
        refused = _refused(app, db_session, make_org, role, org)
        if refused:
            mismatches[role] = refused
    assert mismatches == {}, "links offered to a role but answered 403 for it: %s" % mismatches


def test_controls_is_offered_only_to_roles_its_page_opens_for(app, db_session, make_org):
    org = _org(make_org, "controls")
    offered = {}
    for role in VALID_ROLES:
        client = _ordinary_person(app, db_session, make_org, role, f"controls-{role}", org)
        _, _, links = _sidebar(client)
        offered[role] = any(link["href"] == "/policy-monitoring/" for link in links)
    for role, shown in offered.items():
        if not shown:
            continue
        client = _ordinary_person(app, db_session, make_org, role, f"controls-open-{role}", org)
        page = client.get("/policy-monitoring/")
        assert page.status_code == 200, f"{role} is offered Controls but it answers {page.status_code}"
    assert offered["application_manager"] is False
    assert offered["procurement"] is False


# ---------------------------------------------------------------------------------------------
# Every other surface that lists pages: the All modules directory and the sidebar search (which
# the command palette reads). Each offers a page by its name; none may offer one the page refuses.
# ---------------------------------------------------------------------------------------------

_REDIRECTS = (301, 302, 303, 307, 308)


def _status(client, href):
    response = client.get(href)
    if response.status_code in _REDIRECTS:
        return client.get(href, follow_redirects=True).status_code
    return response.status_code


def _directory_pages(client):
    """(label, href) of every page the All modules directory offers."""
    import re

    html = client.get("/modules/").get_data(as_text=True)
    pages = {}
    for href, text in re.findall(r'<a\b[^>]*?href="(/[^"#]*)"[^>]*>(.*?)</a>', html, flags=re.S):
        label = " ".join(re.sub(r"<[^>]+>", " ", text).split())
        if label and not href.startswith(("/account/logout", "/modules")):
            pages[href] = label
    return pages


def _searched_pages(client, names):
    """(label, url) of every page the sidebar search returns when asked for each name."""
    pages = {}
    for name in names:
        payload = client.get("/api/sidebar/search", query_string={"q": name}).get_json() or {}
        for row in payload.get("results", []):
            if row.get("type") == "module" and str(row.get("url", "")).startswith("/"):
                pages[row["url"]] = row.get("name", name)
    return pages


@pytest.mark.parametrize("role", sorted(VALID_ROLES))
def test_no_role_is_offered_a_page_by_the_directory_or_search_that_it_refuses(app, db_session, make_org, role):
    org = _org(make_org, f"surfaces-{role}")
    mismatches = {}
    for role in (role,):
        client = _ordinary_person(app, db_session, make_org, role, f"surf-{role}", org)
        _, _, links = _sidebar(client)
        sidebar_names = {" ".join(link["text"].split()) for link in links if link["text"].strip()}
        directory = _directory_pages(client)
        # Ask search for every page name the directory and sidebar know, and for the page the
        # reviewers found missed: Controls is reachable by its old names.
        names = set(directory.values()) | sidebar_names | {"policy monitoring", "controls", "policy"}
        searched = _searched_pages(client, sorted(names))
        statuses = {}
        for surface, pages in (("modules", directory), ("search", searched)):
            for href, label in sorted(pages.items()):
                if href not in statuses:
                    statuses[href] = _status(client, href)
                if statuses[href] == 403:
                    mismatches.setdefault(role, {}).setdefault(surface, []).append(f"{label} -> {href}")
    assert mismatches == {}, "pages offered to a role but answered 403 for it: %s" % mismatches


def test_controls_stays_on_the_directory_and_in_search_for_roles_its_page_opens_for(app, db_session, make_org):
    org = _org(make_org, "controls-surfaces")
    for role in ("enterprise_architect", "solution_architect", "platform_admin"):
        client = _ordinary_person(app, db_session, make_org, role, f"ctl-surf-{role}", org)
        if client.get("/policy-monitoring/").status_code != 200:
            continue  # this ordinary account is not admitted; the matching test above judges it
        assert "/policy-monitoring/" in _directory_pages(client), role
        assert "/policy-monitoring/" in _searched_pages(client, ["policy monitoring"]), role
