"""T-L1-IMPORT-OPS: one ArchiMate model import screen, not two.

Two screens used to import the same OEF XML file: the sidebar-reached
``/architecture/import/oef`` (``architect_ui.import_oef``) and
``/solutions/import/archimate``. The first is the screen; it now carries the
preview and strategy choice the second had, over the one import engine
(``ArchiMateImportService``). The old URL redirects (302) to it.
"""
import os

import pytest

db_required = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set - page tests need PostgreSQL",
)


def test_import_page_route_is_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules() if "GET" in (r.methods or ())}
    assert "/solutions/import/archimate" in rules
    assert "/architecture/import/oef" in rules


def test_catalog_templates_compile(app):
    """Jinja syntax errors surface at render time; catch them at load time instead."""
    for name in (
        "architecture/elements.html",
        "archimate_crud/import_oef.html",
        "archimate_crud/dashboard.html",
    ):
        app.jinja_env.get_template(name)


def test_catalog_links_to_the_canonical_import_page(app):
    """The element catalog's header action and empty-state link both point
    at the canonical screen, and neither carries the retired URL."""
    source = app.jinja_env.loader.get_source(app.jinja_env, "architecture/elements.html")[0]
    assert source.count("url_for('architect_ui.import_oef')") >= 2
    assert "/solutions/import/archimate" not in source


def test_no_template_links_to_the_old_import_url():
    """Static grep across every shipped template: nothing links to the
    retired second import screen any more."""
    import pathlib

    templates_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
    offenders = []
    for path in templates_dir.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "/solutions/import/archimate" in text:
            offenders.append(str(path.relative_to(templates_dir)))
    assert not offenders, "templates still linking to the retired import screen: %r" % offenders


@db_required
def test_old_import_url_redirects_to_the_canonical_screen(app, client, db_session, login_as, make_org):
    from app.models import User

    org = make_org("oef-entry-redirect")
    user = User(email="oef-entry-redirect@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    resp = client.get("/solutions/import/archimate", follow_redirects=False)
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/architecture/import/oef")


@db_required
def test_old_import_url_preserves_query_string_on_redirect(app, client, db_session, login_as, make_org):
    from app.models import User

    org = make_org("oef-entry-redirect-qs")
    user = User(email="oef-entry-redirect-qs@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    resp = client.get("/solutions/import/archimate?ref=catalog", follow_redirects=False)
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/architecture/import/oef?ref=catalog")


@db_required
def test_canonical_import_page_renders_for_a_logged_in_user(app, client, db_session, login_as, make_org):
    from app.models import User

    # confirmed=True: an unconfirmed user is redirected to /account/unconfirmed by a
    # global before_request check, which would otherwise make this test measure email
    # confirmation instead of the route under test. is_active is flask_login.UserMixin's
    # read-only property (not a settable column) and is omitted.
    org = make_org("oef-entry")
    user = User(email="oef-entry@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"  # ``password`` is a write-only property (app/models/user.py); there is no set_password method
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    resp = client.get("/architecture/import/oef")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Import ArchiMate Model" in body
    assert "oef_file" in body


@db_required
def test_following_the_redirect_lands_on_a_real_200(app, client, db_session, login_as, make_org):
    org = make_org("oef-entry-follow")
    from app.models import User

    user = User(email="oef-entry-follow@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    resp = client.get("/solutions/import/archimate", follow_redirects=True)
    assert resp.status_code == 200
    assert resp.request.path == "/architecture/import/oef"


@db_required
def test_old_import_url_survives_a_non_utf8_query_string(app, client, db_session, login_as, make_org):
    from app.models import User

    org = make_org("oef-entry-redirect-bytes")
    user = User(email="oef-entry-redirect-bytes@example.com", organization_id=org.id, confirmed=True)
    user.password = "x"
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    # A Latin-1 "é" as a client sends it: percent-encoded, so the bytes are
    # not valid UTF-8 once unquoted. The redirect must carry it through
    # untouched rather than 500. (A raw unencoded 0xE9 byte never reaches
    # this route: Werkzeug's own request.args decode, read by middleware
    # first, rejects it.)
    resp = client.get("/solutions/import/archimate?ref=caf%E9", follow_redirects=False)
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/architecture/import/oef?ref=caf%E9")
    assert "/architecture/import/oef?ref=caf" in resp.headers["Location"]


def test_canonical_screen_offers_preview_and_every_strategy(app):
    source = app.jinja_env.loader.get_source(app.jinja_env, "archimate_crud/import_oef.html")[0]
    for needle in (
        "architect_ui.import_oef_preview",
        "'skip_duplicates'",
        "'update_existing'",
        "'create_all'",
        'data-testid="btn-preview-import"',
        'data-testid="relationship-import-result"',
    ):
        assert needle in source, needle
    for native in ("alert(", "confirm(", "prompt("):
        assert native not in source, native
