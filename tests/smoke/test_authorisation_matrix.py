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
    # R1-B36 (TB-0146): finance added via requires_procurement_or_finance --
    # licences/spend are a finance persona's own numbers. Deliberately NOT
    # extended to contracts/renewals/compliance below, which stay
    # requires_procurement-only.
    "/procurement/licenses":   {"procurement", "portfolio_manager", "finance"},
    "/procurement/spend":      {"procurement", "portfolio_manager", "finance"},
    "/procurement/compliance": {"procurement", "portfolio_manager"},
    # application_mgmt.compliance_frameworks_dashboard is @login_required only
    # (RegulatoryFramework/ComplianceControl, a different store from the
    # procurement compliance page above) -- every persona can reach it.
    "/dashboard/compliance":   set(ARCHETYPES),
    # risk.risk_register is @login_required only.
    "/risks/":                 set(ARCHETYPES),
    "/my-applications/":       {"application_manager"},
    "/my-applications/list":   {"application_manager"},
    "/my-applications/health": {"application_manager"},
    "/applications/ownership-coverage": {"cto", "portfolio_manager"},
    "/ai-chat":                set(ARCHETYPES),
    # Ask and Twin map: both pages carry @login_required and no role gate, so
    # every archetype is expected to reach them. Stating that in two rows is
    # what makes a role gate added later show up here as a row change, and what
    # keeps them in test_no_archetype_reaches_another_personas_section_
    # unauthenticated below. The data they show is fenced per tenant by the
    # impact endpoint they read, not by the page.
    "/intelligence/ask":       set(ARCHETYPES),
    "/intelligence/twin-map":  set(ARCHETYPES),
# Model Health / Drift: carries @login_required and no role gate, so every
    # archetype is expected to reach it.
    "/genome/model-health/":   set(ARCHETYPES),
    # Traceability check and element properties: @login_required and no role
    # gate on the page, so every archetype reads them; the answer is fenced
    # per tenant by the service behind each page. Saving a property definition
    # is role-gated on its POST route and pinned in
    # tests/test_metamodel_properties.py.
    "/intelligence/traceability": set(ARCHETYPES),
    "/metamodel/properties":   set(ARCHETYPES),
    # ArchiMate OEF import (dogfood-import-fixes, Task 01; retired as its own
    # screen by T-L1-IMPORT-OPS): this URL now redirects to the canonical
    # import screen at /architecture/import/oef, which carries the same
    # @login_required-only boundary -- no role gate at all. Recording it
    # here pins the actual (wide-open) boundary so a role gate added later
    # shows up as a row change, and a further widening (e.g. an
    # unauthenticated route) would also be visible.
    "/solutions/import/archimate": set(ARCHETYPES),
    # Error telemetry (10 Sep 2026): cross-tenant by design -- an error is an
    # operational fact about the platform, not a per-org one -- so gated by
    # platform_admin_required rather than the ordinary admin_required.
    "/admin/errors":           set(),
    # Team page (invitations by e-mail): gated to the organisation's own
    # administrators (an org_admin OrgRole row) or a platform admin. No seeded
    # archetype except platform_admin holds org_admin, so every other persona
    # is refused -- inviting people into an organisation is not a persona's
    # job, it is its administrator's.
    "/admin/team":             set(),
    # Agent oversight: pause/resume all agent writes, view refused-call log,
    # and check classification status — all gated by org_admin, which no
    # seeded archetype except platform_admin holds.
    "/ai-chat/oversight/state":          set(),
    "/ai-chat/oversight/refused-calls":  set(),
    "/ai-chat/oversight/classification/status": set(),
    # Tool catalogue export: gated by security_architect or platform_admin.
    "/ai-chat/oversight/catalogue/export": {"security_architect"},
    # Billing: plan, checkout, limits, invoices. Gated by admin_required, which
    # no ordinary persona role carries; only the administrator can buy, change
    # or cancel the organisation's plan.
    "/admin/billing/":         set(),
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
    # Data governance (system of record, undeclared copies, master data
    # domains, standards check): gated by the same data_integration section
    # predicate as the Interface Register above, so the same five personas reach
    # it and arb_member, portfolio_manager, cto, procurement and
    # application_manager are refused.
    "/data-governance/entities": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    "/data-governance/undeclared-copies": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    "/data-governance/domains": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    "/data-governance/models": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    # The organisation's audit trail (query, export, verify). Gated by
    # governance_gate_reader_required: administrators, plus security
    # architects as readers. Every other persona is denied.
    "/admin/audit-log":        {"security_architect"},
# Data-subject requests and the personal-data trace: the Data Protection
    # Officer's work, carried by the security architect persona (it owns the
    # compliance section). Every other persona is denied.
    "/compliance/data-subject-requests": {"security_architect"},
    "/compliance/personal-data-trace":   {"security_architect"},
    # Service status: current platform health, incident history and a
    # subscribe action. @login_required and no role gate -- every signed-in
    # persona reaches it from the sidebar footer. The state it shows is
    # platform-wide; the only thing a user changes is their own subscription.
    "/status":                 set(ARCHETYPES),
    # Gap register: @login_required and no role gate on the
    # implementation_planning blueprint, so every signed-in persona reaches
    # it; the gaps it shows are fenced per organisation by Gap's TenantMixin.
    "/implementation/gaps":    set(ARCHETYPES),
}
for _allowed in POLICY.values():
    _allowed.add("platform_admin")

ACCOUNT_POST_POLICY = {
    "/account/switch-organization": set(ARCHETYPES),
}

OVERSIGHT_POST_POLICY = {
    "/ai-chat/oversight/pause":  set(),
    "/ai-chat/oversight/resume": set(),
}

for _allowed in OVERSIGHT_POST_POLICY.values():
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


def _csrf_token(page):
    """Read the current page's CSRF token without forcing another navigation."""
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    assert token, "signed-in page did not expose a CSRF token"
    return token


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
        for archetype, email in emails.items():
            if archetype == "platform_admin":
                continue
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
def seeded_data_entity(seeded):
    """A real DataEntity in the seeded org, for the entity page's <id> path."""
    from app import create_app, db
    from app.models.process_data import DataDomain, DataEntity

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        entity = DataEntity.query.filter_by(organization_id=org_id).order_by(DataEntity.id.desc()).first()
        if entity is None:
            domain_name = "Auth-matrix probe domain %s" % org_id
            entity_name = "Auth-matrix probe entity %s" % org_id
            domain = DataDomain.query.filter_by(name=domain_name, organization_id=org_id).first()
            if domain is None:
                domain = DataDomain(name=domain_name, organization_id=org_id)
                db.session.add(domain)
                db.session.flush()
            entity = DataEntity.query.filter_by(name=entity_name, organization_id=org_id).first()
            if entity is None:
                entity = DataEntity(name=entity_name, domain_id=domain.id, organization_id=org_id)
                db.session.add(entity)
                db.session.commit()
            else:
                db.session.rollback()
        return entity.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_data_entity_page_authorisation(archetype, page, live_server, seeded, seeded_data_entity):
    """GET /architecture/data-entities/<id> (the entity and its CRUD matrix)
    carries @login_required and no role gate, like the entity catalog it is
    opened from, so every archetype in the entity's organisation reaches it.
    The data is fenced per tenant by the entity lookup, not by a role."""
    _login(page, live_server, seeded["emails"][archetype])
    path = "/architecture/data-entities/%d" % seeded_data_entity
    actual = _observe(page, live_server, path)
    assert actual == ALLOWED, "%s could not reach %s: expected ALLOWED" % (archetype, path)


def test_data_entity_page_rejects_anonymous(page, live_server, seeded_data_entity):
    path = "/architecture/data-entities/%d" % seeded_data_entity
    response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "/account/login" in page.url or (response is not None and response.status >= 400), (
        "%s was served without signing in" % path)


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
    csrf_token = _csrf_token(page)
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
    csrf_token = _csrf_token(page)
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


@pytest.mark.parametrize("path,allowed", ACCOUNT_POST_POLICY.items())
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_account_switch_organization_authorisation(
    archetype, path, allowed, page, live_server, seeded
):
    """The organisation switcher is available to any signed-in member."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf = page.locator('[data-testid="organization-memberships-card"] input[name="csrf_token"]').first.input_value()
    response = page.request.post(
        live_server + path,
        form={
            "organization_id": str(seeded["ids"]["org"]),
            "csrf_token": csrf,
        },
        max_redirects=0,
    )
    expected = ALLOWED if archetype in allowed else DENIED
    actual = ALLOWED if response.status < 400 else DENIED
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, path, expected, actual)
    )


@pytest.mark.parametrize("path,allowed", OVERSIGHT_POST_POLICY.items())
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_oversight_post_routes_authorisation(
    archetype, path, allowed, page, live_server, seeded
):
    """Oversight POST routes (pause, resume) are gated by org_admin, which
    no seeded archetype except platform_admin holds."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.post(
        live_server + path,
        data={"reason": "Auth-matrix probe", "csrf_token": csrf},
        max_redirects=0,
    )
    expected = ALLOWED if archetype in allowed else DENIED
    actual = ALLOWED if response.status < 400 else DENIED
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, path, expected, actual)
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


# The application technology panel (nodes and system software an application
# runs on). Reading the links carries @login_required only, so every archetype
# reaches it. Writing carries require_roles("admin", "architect"): every seeded
# archetype holds the Architect (or Administrator) role, so every one may write,
# and a read-only Viewer account in the same organisation is refused.
TECHNOLOGY_LINKS_PATH = "/architecture/api/applications/%d/technology-links"


@pytest.fixture(scope="module")
def technology_links_viewer(seeded):
    """A read-only (Viewer role) account in the seeded organisation."""
    import uuid

    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        user = User(
            email="smoke.viewer.%s@example.com" % uuid.uuid4().hex[:8],
            first_name="Smoke", last_name="Viewer",
            organization_id=seeded["ids"]["org"], enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Viewer").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email


def _post_technology_link(page, live_server, application_id):
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf_token = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    # element_id 0 names no element: a permitted caller gets the 400 that says
    # so, a refused one gets the role gate's 403 before the body is read.
    return page.request.post(
        live_server + TECHNOLOGY_LINKS_PATH % application_id,
        data={"element_id": 0},
        headers={"X-CSRFToken": csrf_token},
        max_redirects=0,
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_application_technology_links_authorisation(archetype, page, live_server, seeded):
    _login(page, live_server, seeded["emails"][archetype])
    path = TECHNOLOGY_LINKS_PATH % seeded["ids"]["application"]
    assert _observe(page, live_server, path) == ALLOWED, "%s could not read %s" % (archetype, path)
    response = _post_technology_link(page, live_server, seeded["ids"]["application"])
    assert response.status == 400, (
        "%s writing a technology link: expected the 400 for an unknown element "
        "(the role gate passed), got %s" % (archetype, response.status)
    )


def test_application_technology_links_refuse_a_read_only_account(
    page, live_server, seeded, technology_links_viewer
):
    _login(page, live_server, technology_links_viewer)
    path = TECHNOLOGY_LINKS_PATH % seeded["ids"]["application"]
    assert _observe(page, live_server, path) == ALLOWED
    response = _post_technology_link(page, live_server, seeded["ids"]["application"])
    assert response.status == 403, "a Viewer wrote a technology link: %s" % response.status


OVERSIGHT_CLASSIFICATION_PERMITTED = {
    "platform_admin",
}


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_oversight_classification_check_route_authorisation(
    archetype, page, live_server, seeded
):
    """GET /ai-chat/oversight/classification/check/<tool_name> is gated by
    org_admin, which no seeded archetype except platform_admin holds."""
    _login(page, live_server, seeded["emails"][archetype])
    path = "/ai-chat/oversight/classification/check/create_solution"
    expected = ALLOWED if archetype in OVERSIGHT_CLASSIFICATION_PERMITTED else DENIED
    actual = _observe(page, live_server, path)
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, path, expected, actual)
    )


# Restore-before-an-import preview and confirm: enterprise_architect plus
# platform_admin (always granted by requires_role()). The guard is the same
# _restore_gate() for both routes; only the HTTP method differs at the same
# path template.
RESTORE_PERMITTED = {"enterprise_architect", "platform_admin"}


@pytest.fixture(scope="module")
def seeded_restore_point_log(seeded):
    """A real ImportSessionLog with snapshot_data in the seeded org,
    so the preview and restore endpoint tests have a real log_id."""
    import uuid
    from datetime import datetime

    from app import create_app, db
    from app.models.import_audit import ImportSessionLog

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        log = ImportSessionLog.query.filter_by(
            organization_id=org_id,
            import_source="oef_model_import",
        ).first()
        if log is None:
            log = ImportSessionLog(
                session_id=str(uuid.uuid4()),
                operation_type="import",
                user_id=seeded["ids"]["solution_architect_user"],
                organization_id=org_id,
                import_source="oef_model_import",
                status="completed",
                started_at=datetime.utcnow(),
                snapshot_data={
                    "version": 1,
                    "strategy": "skip_duplicates",
                    "model_name": "Auth-matrix probe model",
                    "model_id": None,
                    "created_element_ids": [],
                    "created_relationship_ids": [],
                    "updated_elements": {},
                    "domain_created": [],
                    "domain_linked": [],
                },
            )
            db.session.add(log)
            db.session.commit()
        return log.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_restore_preview_authorisation(
    archetype, page, live_server, seeded, seeded_restore_point_log
):
    """GET /architecture/import/oef/restore-points/<log_id> — the guard runs
    before the log is even resolved, so this observes the restore gate
    boundary for the route that needs a real log_id."""
    _login(page, live_server, seeded["emails"][archetype])
    expected = ALLOWED if archetype in RESTORE_PERMITTED else DENIED
    path = "/architecture/import/oef/restore-points/%d" % seeded_restore_point_log
    actual = _observe(page, live_server, path)
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, path, expected, actual)
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_restore_confirm_authorisation(
    archetype, page, live_server, seeded, seeded_restore_point_log
):
    """POST /architecture/import/oef/restore-points/<log_id>/restore — same
    restore gate boundary, observed on the write path. A denied archetype
    must get exactly the 403 the guard renders; an allowed archetype must
    not be refused by the guard even if the underlying restore fails."""
    _login(page, live_server, seeded["emails"][archetype])
    csrf_token = _csrf_token(page)
    response = page.request.post(
        live_server + "/architecture/import/oef/restore-points/%d/restore" % seeded_restore_point_log,
        form={"reapply": "[]", "csrf_token": csrf_token},
        max_redirects=0,
    )
    expected_denied = archetype not in RESTORE_PERMITTED
    if expected_denied:
        assert response.status == 403, (
            "%s reached restore confirm: expected 403, got %s" % (archetype, response.status)
        )
    else:
        assert response.status != 403, (
            "%s was refused restore confirm by the restore gate despite "
            "being permitted by the GET rows" % archetype
        )
