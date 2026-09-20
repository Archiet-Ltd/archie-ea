"""Who can reach what: the access matrix, observed rather than assumed.

291 routes carry an explicit role gate. This drives every archetype against the
persona-exclusive sections and asserts the result matches an expectation derived
from the decorators themselves:

    requires_procurement        -> procurement, portfolio_manager  (+ platform_admin)
    requires_application_owner  -> application_manager             (+ platform_admin)
    admin_required              -> platform_admin

platform_admin passes every gate by design - requires_role() grants it
unconditionally - so it is expected to reach all of them.

Why observe instead of reading the decorators: a decorator only tells you what
was intended. It does not tell you whether the blueprint is registered, whether
another route shadows the path, or whether the gate runs before the handler
touches data. Earlier in this codebase a decorator scan reported 1,590 unguarded
mutating routes when the real number was 1, and separately reported an
unauthenticated /api/gdpr/delete that turned out never to be registered. Only
driving it settles either question.
"""

import pytest

from .conftest import ARCHETYPES, PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ALLOWED = "allowed"
DENIED = "denied"

# path -> the archetypes that should reach it, from the decorator definitions.
# platform_admin is added to every row because requires_role() grants it always.
#
# /ai-chat is the one deliberately open row. chat_views.index carries
# @login_required and no role gate, so every archetype is expected to reach it —
# stating that explicitly is what makes the row worth having. It pins both
# directions of a page that had no authorisation coverage at all: a role gate
# added later would lock eight personas out of the assistant and show up here,
# and test_no_archetype_reaches_another_personas_section_unauthenticated now also
# covers it, which matters because the chat sees the whole portfolio.
POLICY = {
    "/procurement/contracts":  {"procurement", "portfolio_manager"},
    "/procurement/licenses":   {"procurement", "portfolio_manager"},
    "/procurement/compliance": {"procurement", "portfolio_manager"},
    "/my-applications/":       {"application_manager"},
    "/my-applications/list":   {"application_manager"},
    "/my-applications/health": {"application_manager"},
    "/ai-chat":                set(ARCHETYPES),
    # Ask and Twin map: both pages carry @login_required and no role gate, so
    # every archetype is expected to reach them. Stating that in two rows is
    # what makes a role gate added later show up here as a row change, and what
    # keeps them in test_no_archetype_reaches_another_personas_section_
    # unauthenticated below. The data they show is fenced per tenant by the
    # impact endpoint they read, not by the page.
    "/intelligence/ask":       set(ARCHETYPES),
    "/intelligence/twin-map":  set(ARCHETYPES),
    # ArchiMate OEF import (dogfood-import-fixes, Task 01): the route carries
    # only @login_required -- no role gate at all -- despite update_existing
    # being able to overwrite elements across the whole enterprise model, per
    # refuter M9. Recording it here pins the actual (wide-open) boundary so a
    # role gate added later shows up as a row change, and a further widening
    # (e.g. an unauthenticated route) would also be visible.
    "/solutions/import/archimate": set(ARCHETYPES),
    # Error telemetry (10 Sep 2026): cross-tenant by design -- an error is an
    # operational fact about the platform, not a per-org one -- so gated by
    # platform_admin_required rather than the ordinary admin_required.
    "/admin/errors":           set(),
    # Interface Register (SAP S/4HANA Interface Register, Task 02): gated by
    # can_access_section(current_user, "data_integration") -- the same
    # section-based predicate the sidebar uses, not a requires_role()-style
    # decorator (root CLAUDE.md F-01/F-11/F-04: a sidebar link must never
    # 403). The design call: POLICY is keyed by "which archetypes actually
    # reach this path", observed from the server, not by how the guard is
    # implemented -- so a section-based guard fits this shape unchanged; no
    # schema extension needed. data_integration is granted, per
    # ROLE_SECTION_ACCESS in app/utils/role_access.py, to solution_architect
    # and enterprise_architect (both given the sidebar link's SDD-scoped
    # personas), and additionally to business_architect, security_architect
    # and data_architect (who share the section for other data_integration
    # surfaces already live there, without a sidebar link of their own --
    # reachable-but-not-linked is intentional and consistent with the rest of
    # this section, not a leak). arb_member, portfolio_manager, cto,
    # procurement and application_manager do not have data_integration and
    # must be denied. GET / and GET /new take no path parameter, so both are
    # exercisable directly, unlike /new's initiative_id which only changes
    # whether the guarded response is a 200 render or a 302 redirect to the
    # picker -- both are ALLOWED, and the guard runs before either.
    "/interface-register/":    {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    "/interface-register/new": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    # Task 03 (D5): /comparison takes an optional initiative_id query param --
    # like /new, the data_integration guard runs before that param is even
    # read, so a static POLICY row observes the same boundary. Without
    # initiative_id it 302s to the picker; both are ALLOWED per the /new
    # precedent above.
    "/interface-register/comparison": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    # Task 04: /costing takes the same optional initiative_id query param and
    # runs the identical _guard() call before it is read -- same data_integration
    # boundary as /comparison and /new above, no initiative_id 302s to the
    # picker either way.
    "/interface-register/costing": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
}
for _allowed in POLICY.values():
    _allowed.add("platform_admin")

# The versioned Transformation Room collection is portfolio data.  These are
# the persisted enterprise roles admitted by TransformationProgrammeService;
# programme assignments grant narrower access once a programme exists.
TRANSFORMATION_API_PATH = "/api/v1/transformation-programmes"
TRANSFORMATION_API_PERMITTED = {
    "enterprise_architect",
    "business_architect",
    "arb_member",
    "portfolio_manager",
    "cto",
    "platform_admin",
}


def _login(page, base, email, _attempts=2):
    """Sign in, retrying once, and say which failure actually happened.

    This used to swallow the wait_for_url timeout with `except Exception: pass`
    and then assert on page.url, so a navigation that simply had not finished
    reported as "could not sign in as ...". That made one archetype fail
    intermittently in a full smoke run — and a DIFFERENT archetype each time,
    which is the signature of a timing race rather than a credentials or
    authorisation problem. In isolation the file passed 11/11 every time.

    The app has no login rate limiting or account lockout, so a slow response
    under the load of a full browser suite was the only remaining explanation.
    Retrying once absorbs that; distinguishing the two causes means the next
    person does not have to rediscover which one they are looking at.
    """
    for attempt in range(1, _attempts + 1):
        page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        try:
            page.click("#submit", force=True, no_wait_after=True)
        except TypeError:
            page.locator("#submit").dispatch_event("click")

        timed_out = False
        try:
            page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
        except Exception:
            timed_out = True

        if "/account/login" not in page.url:
            return
        # Still on the login page. An actual rejection renders a flash; a timing
        # failure does not. Only the latter is worth retrying.
        rejected = page.locator(".alert-danger, .flash-error, [role=alert]").count() > 0
        if rejected:
            raise AssertionError(
                f"sign-in REJECTED for {email} — the page rendered an error, so this is "
                f"a credentials or account-state problem, not a timing one"
            )
        if attempt == _attempts:
            raise AssertionError(
                f"sign-in for {email} never navigated away from /account/login after "
                f"{_attempts} attempts (wait_for_url timed out: {timed_out}). No error "
                f"was rendered, so the form was accepted but the response did not "
                f"arrive within {PAGE_TIMEOUT}ms."
            )


def _observe(page, base, path):
    """ALLOWED or DENIED, from what the server actually did."""
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status = response.status if response else 0
    # A redirect back to login means the session was lost, not that the gate
    # refused - that would be a false DENIED and would hide a real leak.
    assert "/account/login" not in page.url, "session lost while probing %s" % path
    return ALLOWED if status < 400 else DENIED


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture(scope="module")
def transformation_users(seeded):
    """Temporarily remove the fixture's cross-cutting Administrator role.

    The canonical smoke seed gives every persona the legacy Administrator
    primary role so unrelated older pages remain reachable.  Task 4 correctly
    recognises that persisted role as transformation authority, so those users
    cannot measure the enterprise-role matrix.  For these final API probes use
    the ordinary Architect primary role, then restore the shared seed exactly.
    """
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    emails = seeded["emails"]
    original_role_ids = {}
    with app.app_context():
        role = Role.query.filter_by(name="Architect").first()
        assert role is not None
        for email in emails.values():
            user = User.query.filter_by(email=email).one()
            original_role_ids[email] = user.role_id
            user.role = role
        db.session.commit()
    try:
        yield emails
    finally:
        with app.app_context():
            for email, role_id in original_role_ids.items():
                User.query.filter_by(email=email).one().role_id = role_id
            db.session.commit()


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_archetype_reaches_exactly_what_policy_permits(archetype, page, live_server, seeded):
    """One row of the matrix per archetype.

    Both directions matter. A DENIED that should be ALLOWED is a persona that
    cannot do its job - that is how Procurement and Application Manager shipped
    read-only. An ALLOWED that should be DENIED is a tenant or role boundary
    failure.
    """
    _login(page, live_server, seeded["emails"][archetype])

    wrong = []
    for path, permitted in sorted(POLICY.items()):
        expected = ALLOWED if archetype in permitted else DENIED
        actual = _observe(page, live_server, path)
        if actual != expected:
            wrong.append("%s: expected %s, got %s" % (path, expected, actual))

    assert not wrong, (
        "%s has the wrong access:\n  - %s\n\n"
        "An unexpected ALLOWED is a broken role boundary. An unexpected DENIED is "
        "a persona that cannot do its job." % (archetype, "\n  - ".join(wrong)))


def test_platform_admin_passes_every_gate(page, live_server, seeded):
    """requires_role() grants platform_admin unconditionally - hold it to that."""
    _login(page, live_server, seeded["emails"]["platform_admin"])
    denied = [p for p in sorted(POLICY) if _observe(page, live_server, p) == DENIED]
    assert not denied, (
        "platform_admin was refused %s. requires_role() is documented as always "
        "granting platform_admin; either the code or the documentation is wrong."
        % denied)


def test_no_archetype_reaches_another_personas_section_unauthenticated(page, live_server):
    """The gates must not be the only thing standing between anonymous and data."""
    leaked = []
    for path in sorted(POLICY):
        response = page.goto(live_server + path, wait_until="domcontentloaded",
                             timeout=PAGE_TIMEOUT)
        # Anonymous must be redirected to login or refused - never served.
        served = response and response.status < 400 and "/account/login" not in page.url
        if served:
            leaked.append(path)
    assert not leaked, "these are reachable without signing in at all: %s" % leaked


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_transformation_api_authorisation_matrix(
    archetype, page, live_server, transformation_users
):
    """The live versioned endpoint enforces its server-owned portfolio roles."""
    _login(page, live_server, transformation_users[archetype])
    expected = ALLOWED if archetype in TRANSFORMATION_API_PERMITTED else DENIED
    actual = _observe(page, live_server, TRANSFORMATION_API_PATH)
    assert actual == expected, (
        f"{archetype} reached {TRANSFORMATION_API_PATH}: expected {expected}, got {actual}"
    )


INTERFACE_REGISTER_PERMITTED = {
    "solution_architect", "enterprise_architect", "business_architect",
    "security_architect", "data_architect", "platform_admin",
}


@pytest.fixture(scope="module")
def seeded_interface_element(seeded):
    """A real ApplicationInterface element, for the edit route's <id> path --
    GET /interface-register/ and /new take no id, but edit does, so the
    static POLICY dict (keyed by literal path) cannot cover it."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        element = ArchiMateElement.query.filter_by(
            type="ApplicationInterface", organization_id=org_id,
        ).order_by(ArchiMateElement.id.desc()).first()
        if element is None:
            element = ArchiMateElement(
                name="Auth-matrix probe interface", type="ApplicationInterface",
                layer="Application", organization_id=org_id,
            )
            db.session.add(element)
            db.session.commit()
        return element.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_interface_register_edit_route_authorisation(
    archetype, page, live_server, seeded, seeded_interface_element
):
    """GET /interface-register/<id>/edit -- the guard runs before the id is
    even resolved, so this observes the same data_integration boundary as
    the static POLICY rows above, for the one route that needs a real id."""
    _login(page, live_server, seeded["emails"][archetype])
    expected = ALLOWED if archetype in INTERFACE_REGISTER_PERMITTED else DENIED
    path = "/interface-register/%d/edit" % seeded_interface_element
    actual = _observe(page, live_server, path)
    assert actual == expected, (
        "%s reached %s: expected %s, got %s -- data_integration section "
        "access should match the static index/new rows" % (archetype, path, expected, actual)
    )


@pytest.fixture(scope="module")
def seeded_interface_initiative(seeded):
    """A real TechnologyRoadmapInitiative wired to an ArchitectureModel in the
    seeded org, for the comparison POST routes (D5) -- provision_comparison
    and raise_gap both need a real initiative_id to render past the guard
    into the CSRF-bearing forms, unlike the static POLICY rows above."""
    from app import create_app, db
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        arch = ArchitectureModel.query.filter_by(
            name="Auth-matrix probe architecture", organization_id=org_id,
        ).first()
        if arch is None:
            arch = ArchitectureModel(
                name="Auth-matrix probe architecture", organization_id=org_id,
            )
            db.session.add(arch)
            db.session.flush()
        initiative = TechnologyRoadmapInitiative.query.filter_by(
            name="Auth-matrix probe initiative", architecture_id=arch.id,
        ).first()
        if initiative is None:
            initiative = TechnologyRoadmapInitiative(
                name="Auth-matrix probe initiative",
                fiscal_year_start=2026,
                fiscal_year_end=2027,
                architecture_id=arch.id,
            )
            db.session.add(initiative)
        db.session.commit()
        return initiative.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_interface_register_provision_comparison_authorisation(
    archetype, page, live_server, seeded, seeded_interface_initiative
):
    """POST /interface-register/comparison/provision -- same data_integration
    boundary as the GET rows, observed on the write path: an allowed
    archetype must not be refused by the guard (a CSRF-related 400 or a
    service-level redirect are both fine -- only a 403 from _guard means
    DENIED), and a denied archetype must get exactly the 403 the guard
    renders."""
    _login(page, live_server, seeded["emails"][archetype])
    comparison_path = "/interface-register/comparison?initiative_id=%d" % seeded_interface_initiative
    expected = ALLOWED if archetype in INTERFACE_REGISTER_PERMITTED else DENIED
    actual = _observe(page, live_server, comparison_path)
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, comparison_path, expected, actual)
    )
    if expected == DENIED:
        # The guard 403s the GET itself, before any form exists to submit --
        # nothing further to check on the write path for a denied archetype.
        return
    # This fixture (and the initiative_id it seeds) is module-scoped, so the
    # first allowed archetype in this parametrized run provisions the real
    # pair -- every archetype after it sees the comparison page WITHOUT the
    # "set up" form (already provisioned), by the same idempotent design
    # proven in tests/test_interface_plateau_pair.py. Only submit the form
    # when it is actually present; either way, the GET above already proved
    # the guard did not refuse this archetype.
    provision_button = page.locator('[data-testid="provision-comparison"]')
    if provision_button.count() == 0:
        return
    csrf = page.locator('input[name="csrf_token"]').first.input_value()
    response = page.request.post(
        live_server + "/interface-register/comparison/provision",
        form={"initiative_id": str(seeded_interface_initiative), "csrf_token": csrf},
        max_redirects=0,
    )
    assert response.status != 403, (
        "%s was refused provision_comparison by the data_integration guard "
        "despite being permitted by the GET rows" % archetype
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_interface_register_raise_gap_authorisation(
    archetype, page, live_server, seeded, seeded_interface_initiative, seeded_interface_element
):
    """POST /interface-register/<id>/gaps -- same data_integration boundary,
    observed on the raise-gap write path. A denied archetype must get exactly
    the 403 the guard renders; an allowed archetype must not be refused by
    the guard even if the underlying service rejects the request for a
    reason unrelated to authorisation (no plateau pair provisioned yet in
    this fixture's initiative -- that is a 400, not a 403, and this test only
    asserts the boundary, not the gap-raising business rule already covered
    in tests/test_interface_register_service.py)."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf_token = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.post(
        live_server + "/interface-register/%d/gaps" % seeded_interface_element,
        form={
            "initiative_id": str(seeded_interface_initiative),
            "gap_type": "new_interface",
            "csrf_token": csrf_token,
        },
        max_redirects=0,
    )
    expected_denied = archetype not in INTERFACE_REGISTER_PERMITTED
    if expected_denied:
        assert response.status == 403, (
            "%s reached raise_gap: expected 403, got %s" % (archetype, response.status)
        )
    else:
        assert response.status != 403, (
            "%s was refused raise_gap by the data_integration guard despite "
            "being permitted by the GET rows" % archetype
        )


@pytest.fixture(scope="module")
def seeded_interface_gap(seeded, seeded_interface_initiative, seeded_interface_element):
    """A real Gap wired to the seeded initiative/element, created directly
    through the ORM -- so the attach-work-package authorisation test below
    does not depend on raise_gap's plateau-pair precondition (covered
    separately) to reach the route it is actually probing.

    Deliberately left at its default gap_kind (GAP_KIND_CAPABILITY_SHORTFALL)
    rather than GAP_KIND_PLATEAU_TRANSITION: the latter's before_insert
    listener (validate_gap_kind, Task 03) requires both
    originating_plateau_id and target_plateau_id, which is a business rule
    already covered in tests/test_interface_register_service.py and
    tests/test_interface_gap_capability_gap_isolation.py -- this fixture only
    needs A gap to exist so the route under test can be reached."""
    from app import create_app, db
    from app.models.implementation_migration import Gap

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        gap = Gap.query.filter_by(
            name="Auth-matrix probe gap", organization_id=org_id,
        ).first()
        if gap is None:
            gap = Gap(
                name="Auth-matrix probe gap",
                gap_type="new_interface",
                archimate_element_id=seeded_interface_element,
                organization_id=org_id,
            )
            db.session.add(gap)
            db.session.commit()
        return gap.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_interface_register_attach_work_package_authorisation(
    archetype, page, live_server, seeded, seeded_interface_initiative, seeded_interface_gap
):
    """POST /interface-register/gaps/<id>/work-packages -- same
    data_integration boundary, observed on the costed-work-package write
    path (US-6 AC6)."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf_token = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.post(
        live_server + "/interface-register/gaps/%d/work-packages" % seeded_interface_gap,
        form={
            "initiative_id": str(seeded_interface_initiative),
            "name": "Auth-matrix probe work package",
            "estimated_cost": "1000",
            "estimated_effort_hours": "10",
            "csrf_token": csrf_token,
        },
        max_redirects=0,
    )
    expected_denied = archetype not in INTERFACE_REGISTER_PERMITTED
    if expected_denied:
        assert response.status == 403, (
            "%s reached attach_work_package: expected 403, got %s" % (archetype, response.status)
        )
    else:
        assert response.status != 403, (
            "%s was refused attach_work_package by the data_integration guard "
            "despite being permitted by the GET rows" % archetype
        )


@pytest.fixture(scope="module")
def other_org_interface_initiative(seeded):
    """SDD §8.2 negative case: a TechnologyRoadmapInitiative rooted at an
    ArchitectureModel belonging to a DIFFERENT organisation than `seeded`'s.
    resolve_initiative() must 404 this for a solution_architect signed in as
    the seeded org -- TechnologyRoadmapInitiative itself carries no
    organization_id, so the entire isolation argument is the join through
    ArchitectureModel (which IS TenantMixin) -- an unauthenticated-looking
    200 here would mean that join is not actually doing the filtering."""
    from app import create_app, db
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        other_org = Organization.query.filter_by(slug="auth-matrix-other-org").first()
        if other_org is None:
            other_org = Organization(name="Auth-matrix other org", slug="auth-matrix-other-org")
            db.session.add(other_org)
            db.session.flush()
        arch = ArchitectureModel.query.filter_by(
            name="Auth-matrix other-org architecture", organization_id=other_org.id,
        ).first()
        if arch is None:
            arch = ArchitectureModel(
                name="Auth-matrix other-org architecture", organization_id=other_org.id,
            )
            db.session.add(arch)
            db.session.flush()
        initiative = TechnologyRoadmapInitiative.query.filter_by(
            name="Auth-matrix other-org initiative", architecture_id=arch.id,
        ).first()
        if initiative is None:
            initiative = TechnologyRoadmapInitiative(
                name="Auth-matrix other-org initiative",
                fiscal_year_start=2026,
                fiscal_year_end=2027,
                architecture_id=arch.id,
            )
            db.session.add(initiative)
        db.session.commit()
        return initiative.id


@pytest.fixture(scope="module")
def null_architecture_interface_initiative(seeded):
    """SDD §8.2's second negative case: an initiative with architecture_id IS
    NULL. resolve_initiative() must treat this identically to "not found" --
    a visible-but-unlinked initiative would let a picker offer a register that
    can never resolve its tenant, and worse, would make the guard's isolation
    argument silently optional rather than universal."""
    from app import create_app, db
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    app = create_app("testing")
    with app.app_context():
        initiative = TechnologyRoadmapInitiative.query.filter_by(
            name="Auth-matrix null-architecture initiative", architecture_id=None,
        ).first()
        if initiative is None:
            initiative = TechnologyRoadmapInitiative(
                name="Auth-matrix null-architecture initiative",
                fiscal_year_start=2026,
                fiscal_year_end=2027,
                architecture_id=None,
            )
            db.session.add(initiative)
            db.session.commit()
        return initiative.id


def test_interface_register_other_org_initiative_is_404_not_visible(
    page, live_server, seeded, other_org_interface_initiative
):
    """An initiative belonging to another organisation must 404 through the
    register, never render -- a data_integration-permitted archetype (here
    solution_architect) is used deliberately, so this observes the tenant
    boundary in isolation from the section guard already covered above."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    path = "/interface-register/?initiative_id=%d" % other_org_interface_initiative
    response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404, (
        "an initiative belonging to another org resolved to %s, not 404 -- "
        "resolve_initiative's tenant join may not be filtering" % response.status
    )


def test_interface_register_null_architecture_initiative_is_404_not_visible(
    page, live_server, seeded, null_architecture_interface_initiative
):
    """An initiative with architecture_id IS NULL must 404 through the
    register, never render -- it has no ArchitectureModel to root a tenant
    check on at all, so resolve_initiative rejects it outright rather than
    treating a NULL link as "visible to everyone"."""
    _login(page, live_server, seeded["emails"]["solution_architect"])
    path = "/interface-register/?initiative_id=%d" % null_architecture_interface_initiative
    response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404, (
        "an initiative with architecture_id IS NULL resolved to %s, not 404" % response.status
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_intelligence_impact_route_authorisation(
    archetype, page, live_server, seeded, seeded_interface_element
):
    """T-004 (API-1): GET /api/v1/intelligence/impact/<element_id> carries
    only ``@login_required`` -- no enterprise-role gate -- so every one of
    the eleven canonical archetypes is expected to reach it once
    authenticated, the same "deliberately open row" shape as /ai-chat above.
    Anonymous is covered separately below.
    """
    _login(page, live_server, seeded["emails"][archetype])
    path = "/api/v1/intelligence/impact/%d" % seeded_interface_element
    actual = _observe(page, live_server, path)
    assert actual == ALLOWED, (
        f"{archetype} could not reach {path}: expected ALLOWED (login_required only)"
    )


def test_intelligence_impact_route_rejects_anonymous_browser_session(page, live_server, seeded, seeded_interface_element):
    path = "/api/v1/intelligence/impact/%d" % seeded_interface_element
    response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None
    # Corrected against a live run: this is a JSON API route, so
    # login_required answers 401 directly rather than redirecting to the
    # login page (unlike an HTML page route). Tightened per the minor finding
    # to the precise-status pattern used a few lines below at :647-657 -- a
    # bare ">= 400" would also pass on an unrelated 500, which is not the
    # behaviour under test.
    assert response.status == 401


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_intelligence_yield_route_authorisation(archetype, page, live_server, seeded):
    """T-005 (API-5): GET /api/v1/intelligence/yield carries only
    ``@login_required`` -- no enterprise-role gate -- so every one of the
    eleven canonical archetypes is expected to reach it once authenticated,
    the same "deliberately open row" shape as the T-004 impact route above.
    Anonymous is covered separately below.
    """
    _login(page, live_server, seeded["emails"][archetype])
    path = "/api/v1/intelligence/yield"
    actual = _observe(page, live_server, path)
    assert actual == ALLOWED, (
        f"{archetype} could not reach {path}: expected ALLOWED (login_required only)"
    )


def test_intelligence_yield_route_rejects_anonymous_browser_session(page, live_server):
    path = "/api/v1/intelligence/yield"
    response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None
    assert response.status == 401


def test_transformation_api_rejects_anonymous_browser_session(page, live_server):
    response = page.goto(
        live_server + TRANSFORMATION_API_PATH,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    assert response is not None
    assert response.status == 401
    body = response.json()
    assert body["data"] is None
    assert body["errors"][0]["code"] == "not_authenticated"
