"""No architecture page prints the word "None" where a value or an empty list belongs.

A rendered-page scan: each page under /architecture/ that an audit found showing
the literal word "None" is requested as a signed-in user of an organisation that
holds a sparse model (elements with no description, no owner, no relationships,
and swimlane columns that hold nothing), and every piece of text a person could
read is checked for the bare word. Two organisations are used: each page is
scanned for each, and one organisation's element names never appear on the
other's pages.
"""

from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser

import pytest

# The pages the audit listed, plus the element pages it named (detail, edit, new).
PAGES = [
    "/architecture/",
    "/architecture/dashboard",
    "/architecture/health",
    "/architecture/motivation",
    "/architecture/motivation/goals",
    "/architecture/motivation/drivers",
    "/architecture/motivation/principles",
    "/architecture/motivation/requirements",
    "/architecture/technology-lifecycle",
    "/architecture/technology/nodes",
    "/architecture/technology/devices",
    "/architecture/technology/system-software",
    "/architecture/technology/services",
    "/architecture/technology/interfaces",
    "/architecture/technology/networks",
    "/architecture/technology/artifacts",
    "/architecture/application/ApplicationComponent/new",
]

WORD = re.compile(r"(?<![\w'\"])None(?![\w'\"])")


class _VisibleText(HTMLParser):
    """Text nodes and the attributes a person reads (title, placeholder, aria-label,
    value of a text input), outside script, style and template-literal blocks."""

    READ_ATTRS = {"title", "placeholder", "aria-label", "alt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for name, value in attrs:
            if value and (name in self.READ_ATTRS or (name == "value" and tag in ("input", "textarea"))):
                if WORD.search(value):
                    self.found.append(f"<{tag} {name}={value!r}>")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and WORD.search(data):
            self.found.append(data.strip()[:120])


def _none_words(html: str) -> list[str]:
    parser = _VisibleText()
    parser.feed(html)
    return parser.found


def _user(db_session, org_id):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"none-scan-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Nora",
        last_name="Scan",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.flush()
    return user


def _sparse_model(db_session, org_id, prefix):
    """One element per layer the pages show, every optional field left empty."""
    from app.models import ArchiMateElement

    elements = {}
    for kind, layer in (("Goal", "motivation"), ("Node", "technology"),
                        ("ApplicationComponent", "application")):
        el = ArchiMateElement(name=f"{prefix} {kind}", type=kind, layer=layer, organization_id=org_id)
        db_session.add(el)
        db_session.flush()
        elements[kind] = el
    # The application pages read the application register, not the element table.
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(name=f"{prefix} Application", organization_id=org_id)
    db_session.add(component)
    db_session.flush()
    elements["component"] = component
    return elements


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("none-a")
    org_b = make_org("none-b")
    out = {
        "a": {"user": _user(db_session, org_a.id), "model": _sparse_model(db_session, org_a.id, "Aardvark")},
        "b": {"user": _user(db_session, org_b.id), "model": _sparse_model(db_session, org_b.id, "Bobolink")},
    }
    db_session.commit()
    # Forget the rows just written, so every page reads them back through the
    # organisation fence the way a real request does, not from this session's cache.
    ids = {k: {"user": v["user"].id, "model": {n: e.id for n, e in v["model"].items()}} for k, v in out.items()}
    db_session.expunge_all()
    return ids


def _pages_for(model):
    app_id = model["component"]
    return PAGES + [
        f"/architecture/application/ApplicationComponent/{app_id}",
        f"/architecture/application/ApplicationComponent/{app_id}/edit",
    ]


@pytest.mark.parametrize("which", ["a", "b"])
def test_no_architecture_page_prints_the_word_none(app, db_session, client, login_as, two_orgs, which):
    mine = two_orgs[which]
    other = two_orgs["b" if which == "a" else "a"]
    other_prefix = "Bobolink" if which == "a" else "Aardvark"
    from app.models.user import User

    login_as(client, db_session.get(User, mine["user"]))

    printed = {}
    served = 0
    for url in _pages_for(mine["model"]):
        resp = client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            continue
        served += 1
        html = resp.get_data(as_text=True)
        words = _none_words(html)
        if words:
            printed[url] = words
        assert other_prefix not in html, f"{url} shows another organisation's element"
    assert served >= 15, f"only {served} of the scanned pages rendered; the scan would prove nothing"
    assert not printed, f"pages printing the word None: {printed}"

    # Another organisation's application page never shows that application.
    other_app = other["model"]["component"]
    resp = client.get(f"/architecture/application/ApplicationComponent/{other_app}")
    assert other_prefix not in resp.get_data(as_text=True)


def test_the_scan_sees_a_printed_none():
    """The scanner itself: it catches the word as text and in a read attribute, and
    leaves words that merely contain it, and code, alone."""
    assert _none_words("<p class='italic'>None</p>") == ["None"]
    assert _none_words('<input type="text" value="None">')
    assert _none_words("<p>Nonetheless</p><script>var x = None;</script>") == []
    assert _none_words("<p x-text=\"el.description || 'None'\"></p>") == []
