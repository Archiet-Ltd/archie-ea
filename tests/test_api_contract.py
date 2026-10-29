"""What every /api/ endpoint promises its callers, enforced across all 341 of them.

Contract tests usually pin one endpoint's response shape at a time. That does not
scale here - the front end calls dozens of endpoints, and pinning each one field
by field produces a suite that breaks on every harmless addition while missing
the failures that actually take the product down.

The failures that matter are cross-cutting, so the assertions are too:

  1. An /api/ path answers in JSON, always - including when it refuses. A front
     end that asks for JSON and receives an HTML error page dies at JSON.parse,
     and the user sees a generic script error instead of "you do not have
     access". The refusal was correct; the explanation was lost.

  2. An /api/ path does not answer an anonymous caller with 200. Anything that
     legitimately does is named in PUBLIC below, with the reason.

Both were found broken by driving every route rather than reading decorators.
Nine vendor endpoints returned HTTP 200 with all-null bodies to anonymous
callers, because @api.marshal_with sat ABOVE @login_required - decorators apply
bottom-up, so login_required produced its 401 and marshal_with then marshalled
that response object through the schema, yielding a 200 full of nulls. The view
body never ran and no data escaped, but the endpoints advertised success to
anyone who asked. Three more served HTML to JSON callers.

Reading the source would not have found either one. Both look correct.
"""

import pytest

pytestmark = pytest.mark.journey

# Endpoints that answer an anonymous caller on purpose. Every entry needs a
# reason, because "it was already like that" is how an unguarded endpoint gets
# grandfathered into a permanent exception.
PUBLIC = {
    "/api/auth/session": "the front end asks whether anyone is signed in; it "
                         "must work when nobody is, and returns "
                         "{authenticated: false, user: null}",
    "/api/v1/": "API index - lists endpoint names only, no data",
    "/api/v1/legacy-redirect": "deprecation notice pointing at /api/v1/",
    "/api/notifications": "returns an empty list to anonymous callers rather "
                          "than 401, so the header widget renders on public "
                          "pages. Carries no data when signed out.",
}


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("SECRET_KEY", "x" * 32)
    from app import create_app, db

    application = create_app("testing")
    with application.app_context():
        db.create_all()
    return application


@pytest.fixture(scope="module")
def api_get_routes(app):
    """Every /api/ GET route that needs no path parameters.

    Routes taking an id are excluded only because there is no id to supply, not
    because they are exempt - they inherit the same guards from the same
    decorators.
    """
    with app.app_context():
        routes = sorted(
            str(rule.rule)
            for rule in app.url_map.iter_rules()
            if str(rule.rule).startswith("/api/")
            and "GET" in rule.methods
            and not rule.arguments
        )
    assert len(routes) > 100, (
        "found only %d /api/ GET routes - blueprints register non-fatally, so a "
        "broken import silently shrinks this set and would make the whole file "
        "pass by testing almost nothing" % len(routes))
    return routes


@pytest.fixture(scope="module")
def anonymous_responses(app, api_get_routes):
    """Drive every route signed out, once, and reuse the results.

    Requests are issued with NO app context open. This is not incidental - see
    signed_in_client below for what happens otherwise.
    """
    client = app.test_client()
    results = {}
    for path in api_get_routes:
        response = client.get(path)
        results[path] = (
            response.status_code,
            (response.content_type or "").split(";")[0],
        )
    return results


@pytest.fixture
def signed_in_client(app):
    """A client authenticated as a platform_admin, plus its cleanup.

    The rows are created inside an app context that then EXITS, and the requests
    are made with no context open. That ordering matters and cost an hour to
    learn: a test client request reuses an already-pushed app context rather
    than pushing its own, and Flask-Login caches the resolved user on that
    context. Sign in after the context exists and the cached anonymous user
    wins - every request 401s while the session cookie is perfectly valid, and
    /api/auth/session reports authenticated: false. It reads exactly like a
    broken auth decorator.
    """
    import uuid

    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name="Contract %s" % marker, slug="contract-%s" % marker)
        db.session.add(org)
        db.session.commit()
        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            role = Role(name="Administrator")
            db.session.add(role)
            db.session.flush()
        user = User(
            email="contract-%s@example.com" % marker,
            first_name="Contract", last_name="Probe", confirmed=True,
            organization_id=org.id, enterprise_role="platform_admin",
        )
        user.role = role
        user.password = "contract-password-%s" % marker
        db.session.add(user)
        db.session.commit()
        user_id, org_id = user.id, org.id

    client = app.test_client()
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    yield client

    with app.app_context():
        from app.services.org_deletion_service import OrganizationDeletionService

        if db.session.get(Organization, org_id) is not None:
            OrganizationDeletionService.delete_organization(org_id)


def test_no_api_endpoint_serves_html(anonymous_responses):
    """HTML from an /api/ path is unparseable by the caller that asked for it."""
    html = [
        "%s -> HTTP %d %s" % (path, status, content_type)
        for path, (status, content_type) in sorted(anonymous_responses.items())
        if "json" not in content_type
    ]
    assert not html, (
        "%d /api/ endpoint(s) answered with something other than JSON:\n  %s\n\n"
        "The front end calls these with fetch() and parses the body. An HTML "
        "error page throws at JSON.parse, so a correct refusal surfaces to the "
        "user as a broken page."
        % (len(html), "\n  ".join(html)))


def test_no_api_endpoint_answers_anonymous_with_200(anonymous_responses):
    """A 200 to a signed-out caller is either a leak or a lie about one."""
    unexpected = [
        path for path, (status, _ct) in sorted(anonymous_responses.items())
        if status == 200 and path not in PUBLIC
    ]
    assert not unexpected, (
        "%d /api/ endpoint(s) returned 200 to an anonymous caller:\n  %s\n\n"
        "Either the endpoint is unguarded, or a decorator ordering bug is "
        "converting its 401 into a 200 - which is how nine vendor endpoints "
        "came to advertise success to anyone who asked. If one of these is "
        "deliberately public, add it to PUBLIC with the reason."
        % (len(unexpected), "\n  ".join(unexpected)))


def test_the_declared_public_endpoints_really_are_public(anonymous_responses):
    """An allowlist nobody prunes eventually excuses a real regression."""
    stale = [
        path for path in sorted(PUBLIC)
        if path in anonymous_responses and anonymous_responses[path][0] != 200
    ]
    assert not stale, (
        "%s in PUBLIC no longer returns 200 to anonymous callers. That is "
        "probably an improvement - remove the entry so the allowlist keeps "
        "meaning something." % stale)


def test_unknown_api_paths_404_as_json(app):
    """A typo'd URL must not hand the caller an HTML page either."""
    with app.app_context():
        client = app.test_client()
        response = client.get("/api/this-endpoint-does-not-exist")

    assert response.status_code == 404
    assert "json" in (response.content_type or ""), (
        "an unknown /api/ path returned %s. The front end cannot parse that, so "
        "a mistyped URL looks like a crash rather than a 404."
        % response.content_type)
    payload = response.get_json()
    assert payload.get("success") is False, (
        "404 body does not carry the success flag the rest of the API uses: %r"
        % payload)


def test_authenticated_callers_still_get_their_data(signed_in_client):
    """The guards must refuse anonymous callers without refusing everyone.

    Tightening auth is easy to overdo: the fix for nine endpoints returning 200
    to strangers must not turn into 401 for the people who are signed in. These
    are the endpoints whose decorators were reordered, so they are the ones most
    likely to have been broken by it.
    """
    checked = {}
    for path in ("/api/vendors/products/",
                 "/api/vendors/analytics/summary",
                 "/api/vendors/apqc/processes"):
        response = signed_in_client.get(path)
        checked[path] = (response.status_code,
                         (response.content_type or "").split(";")[0])

    refused = ["%s -> HTTP %d" % (p, s) for p, (s, _c) in sorted(checked.items())
               if s >= 400]
    assert not refused, (
        "a signed-in platform_admin was refused by %s.\n\n"
        "Moving @login_required above @api.marshal_with was meant to stop "
        "anonymous callers getting a 200, not to stop authenticated ones "
        "getting their data." % refused)
