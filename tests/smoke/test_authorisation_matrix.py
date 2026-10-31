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
    # The contract, licence and spend pages follow the one cost-visibility rule:
    # whoever may see cost figures (cto, portfolio_manager, platform_admin) may
    # read them, besides the procurement roles (contracts) and the spend roles
    # (licences, spend: procurement, portfolio_manager, finance). So the CTO is
    # expected to reach all three. Creating and editing stay with procurement.
    "/procurement/contracts":  {"procurement", "portfolio_manager", "cto"},
    "/procurement/licenses":   {"procurement", "portfolio_manager", "finance", "cto"},
    "/procurement/spend":      {"procurement", "portfolio_manager", "finance", "cto"},
    "/procurement/compliance": {"procurement", "portfolio_manager"},
    # R1-B34 (TB-0135): viewing the register is @login_required only (every
    # archetype can see a formula's active version); activating a new one
    # is gated to portfolio_manager by @requires_role in
    # app/modules/formula_register/routes.py. This row is the view page.
    "/admin/formula-register/": set(ARCHETYPES),
    # application_mgmt.compliance_frameworks_dashboard is @login_required only
    # (RegulatoryFramework/ComplianceControl, a different store from the
    # procurement compliance page above) -- every persona can reach it.
    "/dashboard/compliance":   set(ARCHETYPES),
    # risk.risk_register and risk.risk_page (/risks/<id>, 404 for another
    # organisation's risk, pinned in tests/test_risk_ranking.py) are @login_required only.
    "/risks/":                 set(ARCHETYPES),
    # The Business Model Canvas list and each canvas (and its Value Proposition
    # Canvas) are @login_required only: every archetype reads them, and a canvas
    # of another organisation answers 404 (tests/test_business_model_canvas_model.py).
    # The writes carry require_roles("admin", "architect", "business_architect").
    "/business-model/":        set(ARCHETYPES),
    # My Applications is gated by the permission matrix (read on application),
    # which every archetype holds; the detail page keeps its own ownership check.
    "/my-applications/":       set(ARCHETYPES),
    "/my-applications/list":   set(ARCHETYPES),
    "/my-applications/health": set(ARCHETYPES),
    "/applications/ownership-coverage": {"cto", "portfolio_manager"},
    # The duplicate lookup in the Add Application dialog carries the same
    # @require_roles("admin", "architect") as the create route it precedes.
    # Every archetype holds the Architect role (platform_admin the
    # Administrator role), so every archetype reaches it; the read-only
    # Viewer role, which is refused, is pinned in tests/test_application_registration_check.py.
    "/applications/api/registration-check": set(ARCHETYPES),
    "/ai-chat":                set(ARCHETYPES),
    # Twin map: the page carries @login_required and no role gate, so every
    # archetype is expected to reach it (it redirects to the Composer). Stating
    # that in a row is what makes a role gate added later show up here as a row
    # change, and what keeps it in test_no_archetype_reaches_another_personas_
    # section_unauthenticated below. Ask is no longer a page: its address
    # redirects into /ai-chat, which has its own row above, and that redirect is
    # checked in tests/test_ai_chat_estate_answer.py.
    "/intelligence/twin-map":  set(ARCHETYPES),
    # R1-B30 (TB-0106): the Answers page (ask a set or risk question, list the
    # organisation's stored answers). @login_required and no role gate, like Ask
    # and Twin map, so every archetype reaches it; each stored answer is fenced
    # to the organisation that asked it (an address from another organisation
    # is a 404, pinned in the module tests and the journey).
    "/intelligence/answers":   set(ARCHETYPES),
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
    # R1-B56: Agent Registry (owner/charter/delegated-limits per agent) is
    # gated to platform_admin via @requires_role / _guard in
    # agent_registry_routes.py -- registering and activating an agent is
    # not a persona's job.
    "/admin/agent-registry/":  set(),
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
    "/data-governance/no-steward": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    "/data-governance/retention-breaches": {
        "solution_architect", "enterprise_architect", "business_architect",
        "security_architect", "data_architect",
    },
    # CTO scorecard (supported estate, open exceptions, end-of-support
    # exposure): gated on the "governance" section via can_access_section --
    # the same section-based predicate shape as the Interface Register rows
    # above. governance is granted, per ROLE_SECTION_ACCESS, to
    # solution_architect, enterprise_architect, business_architect,
    # arb_member, portfolio_manager, cto, security_architect, data_architect,
    # compliance and risk; procurement, application_manager, finance,
    # operations and non_technical_owner do not carry it and must be denied.
    # The escalate POST route carries the identical _guard() and is covered
    # by the same boundary.
    "/architecture/cto-scorecard/": {
        "solution_architect", "enterprise_architect", "business_architect",
        "arb_member", "portfolio_manager", "cto", "security_architect",
        "data_architect", "compliance", "risk",
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
    # Gap and work package pickers (link a gap to a work package): both carry
    # @login_required and no role gate, are fenced to the caller's organisation
    # by the query, and every persona may search; writing the link is the
    # role-gated POST pinned in GAP_WORK_PACKAGE_LINK_POST_POLICY below.
    "/capability-map/api/roadmap/gaps/search":          set(ARCHETYPES),
    "/capability-map/api/roadmap/work-packages/search": set(ARCHETYPES),
}
for _allowed in POLICY.values():
    _allowed.add("platform_admin")

ACCOUNT_POST_POLICY = {
    "/account/switch-organization": set(ARCHETYPES),
}

# Initiative goal links: add is @login_required only, like the sibling
# portfolio write routes -- deliberately open to every signed-in persona; the
# boundary that matters is the organisation, enforced in the service and
# covered by tests/test_initiative_goal_links.py. An empty form is posted so
# the route answers (redirect) before it looks anything up. The remove route
# carries the same decorator; it needs a real link to answer 302, so its
# boundary is pinned in the isolation tests instead.
GOAL_LINK_POST_POLICY = {
    "/portfolio/initiatives/0/goals": set(ARCHETYPES),
}

# Linking a gap to a work package (and removing the link) is role-gated by
# require_roles on the route: a persona holding the Architect role (every seeded
# archetype) or Administrator (platform_admin) reaches the handler, which answers
# 404 for the id 0 used here; a read-only Viewer is answered 403 before the
# handler runs (pinned with a real Viewer account in tests/test_gap_work_package_link.py).
GAP_WORK_PACKAGE_LINK_POST_POLICY = {
    "/capability-map/api/roadmap/gaps/0/link-work-package/0":   set(ARCHETYPES),
    "/capability-map/api/roadmap/gaps/0/unlink-work-package/0": set(ARCHETYPES),
}

for _allowed in GAP_WORK_PACKAGE_LINK_POST_POLICY.values():
    _allowed.add("platform_admin")

OVERSIGHT_POST_POLICY = {
    "/ai-chat/oversight/pause":  set(),
    "/ai-chat/oversight/resume": set(),
}

for _allowed in OVERSIGHT_POST_POLICY.values():
    _allowed.add("platform_admin")

# AI Systems Register routes: release (admin_required); assess, classify,
# grounding-source propose/accept/refuse and the owner write routes
# (permission_required(GENERAL)). These are tested with a real element id
# (and, for the grounding accept/refuse routes, a real approval id) seeded in
# the fixture below.
AI_SYSTEMS_RELEASE_PERMITTED = {"platform_admin"}
AI_SYSTEMS_ASSESS_PERMITTED = {
    "solution_architect", "enterprise_architect", "business_architect",
    "arb_member", "portfolio_manager", "cto", "procurement",
    "application_manager", "security_architect", "data_architect",
    "finance", "compliance", "risk", "operations", "non_technical_owner",
    "platform_admin",
}
AI_SYSTEMS_CLASSIFY_PERMITTED = AI_SYSTEMS_ASSESS_PERMITTED
AI_SYSTEMS_OWNER_WRITE_PERMITTED = AI_SYSTEMS_ASSESS_PERMITTED
# The three grounding-source routes share the same permission_required(GENERAL)
# gate as the classification routes: any archetype with general permission can
# propose, accept or refuse a grounding source.
AI_SYSTEMS_GROUNDING_SOURCE_PERMITTED = AI_SYSTEMS_ASSESS_PERMITTED

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

# Impact API (GET /api/v1/intelligence/impact/<id>): carries only
# @login_required — no enterprise-role gate — so every archetype is expected
# to reach it.  Pagination parameters (cursor, page_size) and response fields
# (total, next_cursor, health) are on the same route.  Tested separately
# below because the path includes a dynamic element id.
IMPACT_API_PERMITTED = set(ARCHETYPES) | {"platform_admin"}


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
def seeded_risk(seeded):
    """A real Risk in the seeded org, for the risk page's <id> path."""
    from app import create_app, db
    from app.models.risk import Risk

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        title = "Auth-matrix probe risk %s" % org_id
        risk = Risk.query.filter_by(title=title, organization_id=org_id).first()
        if risk is None:
            risk = Risk(title=title, likelihood=3, impact=3, organization_id=org_id)
            db.session.add(risk)
            db.session.commit()
        return risk.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_risk_page_authorisation(archetype, page, live_server, seeded, seeded_risk):
    """GET /risks/<id> carries @login_required and no role gate, like the risk
    register it is opened from; the risk is fenced to its organisation by the lookup."""
    _login(page, live_server, seeded["emails"][archetype])
    path = "/risks/%d" % seeded_risk
    actual = _observe(page, live_server, path)
    assert actual == ALLOWED, "%s could not reach %s: expected ALLOWED" % (archetype, path)


def test_risk_page_rejects_anonymous(page, live_server, seeded_risk):
    path = "/risks/%d" % seeded_risk
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


@pytest.mark.parametrize("path,allowed", GOAL_LINK_POST_POLICY.items())
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_initiative_goal_link_post_authorisation(
    archetype, path, allowed, page, live_server, seeded
):
    """The goal-link add route is login_required-open to every persona."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.post(
        live_server + path, form={"csrf_token": csrf}, max_redirects=0
    )
    expected = ALLOWED if archetype in allowed else DENIED
    actual = ALLOWED if response.status < 400 else DENIED
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, path, expected, actual)
    )


@pytest.mark.parametrize("path,allowed", GAP_WORK_PACKAGE_LINK_POST_POLICY.items())
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_gap_work_package_link_post_authorisation(
    archetype, path, allowed, page, live_server, seeded
):
    """The link routes are role-gated: a role that may write reaches the handler
    (404 for a record that does not exist), anyone else is refused before it."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.post(
        live_server + path, form={"csrf_token": csrf}, max_redirects=0
    )
    expected = ALLOWED if archetype in allowed else DENIED
    actual = DENIED if response.status in (401, 403) else ALLOWED
    assert actual == expected, (
        "%s reached %s: expected %s, got %s" % (archetype, path, expected, response.status)
    )
    if actual == ALLOWED:
        assert response.status == 404, (archetype, path, response.status)


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


# Role-gated ARB writes. Each is probed with an id that does not exist, except
# the four decisions, which use a real submitted review (a missing id answers
# 404 to everyone before the role is asked): a role the route admits reaches
# the handler, anyone else is refused before it.
ARB_APPROVER_ARCHETYPES = {"enterprise_architect", "arb_member", "cto", "platform_admin"}
ARB_WRITE_POLICY = {
    ("POST", "/arb/sessions/999999999/complete"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/sessions/999999999/cancel"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/sessions/999999999/delete"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/reviews/999999999/assign"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/reviews/999999999/delete"): ARB_APPROVER_ARCHETYPES,
    ("PUT", "/arb/api/exceptions/999999999/approve"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/api/arb/{review}/review"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/api/arb/{review}/approve"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/api/arb/{review}/reject"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/api/arb/{review}/request-changes"): ARB_APPROVER_ARCHETYPES,
    ("POST", "/arb/initialize_standards"): {"platform_admin"},
}


@pytest.fixture(scope="module")
def seeded_arb_review(seeded):
    """A submitted review of the seeded organisation whose submitter is a
    persona outside the approver set."""
    from app import create_app, db
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        number = "REV-MATRIX-%s" % org_id
        item = ARBReviewItem.query.filter_by(review_number=number).first()
        if item is None:
            submitter = User.query.filter_by(
                email=seeded["emails"]["solution_architect"]
            ).one()
            item = ARBReviewItem(
                review_number=number,
                title="Auth-matrix probe review",
                review_type="architecture",
                status="submitted",
                submitter_id=submitter.id,
                organization_id=org_id,
            )
            db.session.add(item)
            db.session.commit()
        return item.id


@pytest.mark.parametrize("method,path", list(ARB_WRITE_POLICY))
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_arb_write_routes_authorisation(
    archetype, method, path, page, live_server, seeded, seeded_arb_review
):
    """Complete, cancel, delete, assign, exception approval, the four decisions and
    standards setup are refused to every persona outside the approver set."""
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.fetch(
        live_server + path.format(review=seeded_arb_review),
        method=method,
        headers={"X-CSRFToken": csrf, "Content-Type": "application/json"},
        data="{}",
        max_redirects=0,
    )
    expected = ALLOWED if archetype in ARB_WRITE_POLICY[(method, path)] else DENIED
    actual = DENIED if response.status in (401, 403) else ALLOWED
    assert actual == expected, (
        "%s %s %s: expected %s, got %s" % (archetype, method, path, expected, response.status)
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
    assert archetype in IMPACT_API_PERMITTED, (
        f"{archetype} not in IMPACT_API_PERMITTED set"
    )
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


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_intelligence_answers_list_route_authorisation(archetype, page, live_server, seeded):
    """R1-B30 (TB-0106): GET /api/v1/intelligence/answers (the organisation's
    stored answers) carries only ``@login_required`` -- no enterprise-role
    gate -- so every one of the eleven canonical archetypes is expected to
    reach it once authenticated. Asking (POST) shares the gate and the
    per-organisation fence; an address is opened through the unguessable
    32-character token on ``/answers/<address>``, which is 404 for any other
    organisation. Anonymous is covered separately below.
    """
    _login(page, live_server, seeded["emails"][archetype])
    path = "/api/v1/intelligence/answers"
    actual = _observe(page, live_server, path)
    assert actual == ALLOWED, (
        f"{archetype} could not reach {path}: expected ALLOWED (login_required only)"
    )


def test_intelligence_answers_routes_reject_anonymous_browser_session(page, live_server):
    for path in ("/api/v1/intelligence/answers", "/api/v1/intelligence/answers/" + "a" * 32):
        response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response is not None
        assert response.status == 401, path


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_intelligence_answers_ask_and_open_authorisation(archetype, page, live_server, seeded):
    """R1-B30: POST /api/v1/intelligence/answers (ask) and GET
    /api/v1/intelligence/answers/<address> (open) carry only ``@login_required``,
    so no archetype is refused by a role gate. An empty question is the 400
    that says so (a refused caller would get 401/403 before the body is read);
    an address nobody was issued is the 404 an address of another organisation
    also answers with."""
    _login(page, live_server, seeded["emails"][archetype])
    csrf_token = _csrf_token(page)
    asked = page.request.post(
        live_server + "/api/v1/intelligence/answers",
        data={},
        headers={"X-CSRFToken": csrf_token},
        max_redirects=0,
    )
    assert asked.status == 400, (
        "%s asking an answer: expected 400 (reached, empty question), got %s" % (archetype, asked.status)
    )
    opened = page.request.get(
        live_server + "/api/v1/intelligence/answers/" + "a" * 32, max_redirects=0
    )
    assert opened.status == 404, (
        "%s opening an unissued address: expected 404, got %s" % (archetype, opened.status)
    )


def test_intelligence_answers_ask_rejects_anonymous_browser_session(page, live_server):
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    token = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    response = page.request.post(
        live_server + "/api/v1/intelligence/answers",
        data={"question": "set_impact"},
        headers={"X-CSRFToken": token},
        max_redirects=0,
    )
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


# Chat workflow routes: write-gated (Permission.GENERAL) and, for the step routes,
# scoped to the caller's own workspace. Every seeded archetype holds the Architect
# role, so each reaches the handler: the start routes answer 400 for the empty body
# used here, the step routes 404 for a workspace id that does not exist. A Viewer
# is answered 403 before either. The cross-user case with real workspaces is pinned
# in tests/test_chat_workflow_route_gate.py.
CHAT_WORKFLOW_POST_EXPECTED = {
    "/ai-chat/workflow/greenfield/start": 400,
    "/ai-chat/workflow/brownfield/start": 400,
    "/ai-chat/workflow/greenfield/step/BRIEF/confirm": 404,
    "/ai-chat/workflow/greenfield/step/BRIEF/skip": 404,
    "/ai-chat/workflow/brownfield/step/CONTEXT_LOAD/confirm": 404,
    "/ai-chat/workflow/brownfield/step/CONTEXT_LOAD/skip": 404,
}


def _post_chat_workflow(page, live_server, path):
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    csrf_token = page.locator('meta[name="csrf-token"]').get_attribute("content") or ""
    return page.request.post(
        live_server + path,
        data={"workspace_id": 2147483000} if "/step/" in path else {},
        headers={"X-CSRFToken": csrf_token},
        max_redirects=0,
    )


@pytest.mark.parametrize("path,expected_status", CHAT_WORKFLOW_POST_EXPECTED.items())
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_chat_workflow_post_route_authorisation(
    archetype, path, expected_status, page, live_server, seeded
):
    _login(page, live_server, seeded["emails"][archetype])
    response = _post_chat_workflow(page, live_server, path)
    assert response.status == expected_status, (
        "%s posting %s: expected %s (gate passed), got %s"
        % (archetype, path, expected_status, response.status)
    )


@pytest.mark.parametrize("path", CHAT_WORKFLOW_POST_EXPECTED)
def test_chat_workflow_post_routes_refuse_a_read_only_account(
    path, page, live_server, technology_links_viewer
):
    _login(page, live_server, technology_links_viewer)
    response = _post_chat_workflow(page, live_server, path)
    assert response.status == 403, "a Viewer reached %s: %s" % (path, response.status)


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


# --- AI Systems Register authorisation matrix ---------------------------------


@pytest.fixture(scope="module")
def seeded_ai_system_element(seeded):
    """A real AI-system ArchiMateElement in the seeded org, for the release
    and assess POST routes that need a real element id."""
    from app import create_app, db
    from app.modules.enterprise_genome.services.ai_systems_seed import (
        register_ai_system,
    )

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        el = register_ai_system(
            db.session, org_id,
            name="Auth-matrix probe AI system",
            provider="anthropic",
            model_id="claude-opus-5",
        )
        db.session.commit()
        return el.id


@pytest.fixture
def seeded_grounding_approval(seeded, seeded_ai_system_element):
    """A pending grounding-source approval in the seeded org, so the
    grounding-source accept and refuse POST routes have a real approval id in
    the URL. Function-scoped: every probe gets its own pending approval, so a
    permitted archetype's accept never leaves a decided approval for the next
    probe to hit."""
    from app import create_app, db
    from app.models.models import ArchiMateElement
    from app.modules.enterprise_genome.services.ai_system_grounding import (
        propose_grounding_source,
    )

    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        element = db.session.get(ArchiMateElement, seeded_ai_system_element)
        data_entity = ArchiMateElement(
            name="Auth-matrix probe data entity",
            type="DataObject",
            layer="application",
            organization_id=org_id,
            custom_properties={},
        )
        db.session.add(data_entity)
        db.session.flush()
        approval = propose_grounding_source(
            element=element,
            data_entity_id=data_entity.id,  # a real data entity of this org
            organization_id=org_id,
            proposed_by_user_id=None,
        )
        db.session.commit()
        return approval.id


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_release_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element
):
    """POST /genome/ai-systems/<id>/release is admin_required: only
    platform_admin can reach it."""
    _login(page, live_server, seeded["emails"][archetype])
    csrf_token = _csrf_token(page)
    response = page.request.post(
        live_server + "/genome/ai-systems/%d/release" % seeded_ai_system_element,
        form={"csrf_token": csrf_token},
        max_redirects=0,
    )
    expected_denied = archetype not in AI_SYSTEMS_RELEASE_PERMITTED
    if expected_denied:
        assert response.status == 403, (
            "%s reached release: expected 403, got %s" % (archetype, response.status)
        )
    else:
        assert response.status != 403, (
            "%s was refused release by the admin_required gate despite "
            "being permitted" % archetype
        )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_assess_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element
):
    """POST /genome/ai-systems/<id>/assess is permission_required(GENERAL):
    every archetype with GENERAL permission can reach it; a Viewer (permissions=0)
    cannot."""
    _login(page, live_server, seeded["emails"][archetype])
    csrf_token = _csrf_token(page)
    response = page.request.post(
        live_server + "/genome/ai-systems/%d/assess" % seeded_ai_system_element,
        form={"csrf_token": csrf_token},
        max_redirects=0,
    )
    expected_denied = archetype not in AI_SYSTEMS_ASSESS_PERMITTED
    if expected_denied:
        assert response.status == 403, (
            "%s reached assess: expected 403, got %s" % (archetype, response.status)
        )
    else:
        # A permitted archetype may get a 302 (redirect) or a 400/409 (service
        # refusal like no accepted class) — anything except 403 is fine.
        assert response.status != 403, (
            "%s was refused assess by the permission_required gate despite "
            "being permitted" % archetype
        )


def _expect_gate(response, archetype, permitted, route):
    if archetype not in permitted:
        assert response.status == 403, (
            "%s reached %s: expected 403, got %s" % (archetype, route, response.status)
        )
    else:
        # A permitted archetype may get a redirect, a 4xx validation refusal or
        # a success - anything except the permission gate's 403.
        assert response.status != 403, (
            "%s was refused %s by the permission gate despite being permitted"
            % (archetype, route)
        )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_classify_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element
):
    """POST /genome/ai-systems/<id>/classify is permission_required(GENERAL):
    a Viewer cannot propose a classification."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.post(
        live_server + "/genome/ai-systems/%d/classify" % seeded_ai_system_element,
        form={"csrf_token": _csrf_token(page), "risk_class": "minimal"},
        max_redirects=0,
    )
    _expect_gate(response, archetype, AI_SYSTEMS_CLASSIFY_PERMITTED, "classify")


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_grounding_source_propose_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element
):
    """POST /genome/ai-systems/<id>/grounding-source/propose is
    permission_required(GENERAL), the same gate as the classification routes."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.post(
        live_server + "/genome/ai-systems/%d/grounding-source/propose"
        % seeded_ai_system_element,
        form={"csrf_token": _csrf_token(page), "data_entity_id": "1"},
        max_redirects=0,
    )
    _expect_gate(
        response, archetype, AI_SYSTEMS_GROUNDING_SOURCE_PERMITTED,
        "grounding source propose",
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_grounding_source_accept_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element,
    seeded_grounding_approval,
):
    """POST /genome/ai-systems/<id>/grounding-source/<approval_id>/accept is
    permission_required(GENERAL), the same gate as the classification routes."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.post(
        live_server + "/genome/ai-systems/%d/grounding-source/%d/accept"
        % (seeded_ai_system_element, seeded_grounding_approval),
        form={"csrf_token": _csrf_token(page)},
        max_redirects=0,
    )
    _expect_gate(
        response, archetype, AI_SYSTEMS_GROUNDING_SOURCE_PERMITTED,
        "grounding source accept",
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_grounding_source_refuse_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element,
    seeded_grounding_approval,
):
    """POST /genome/ai-systems/<id>/grounding-source/<approval_id>/refuse is
    permission_required(GENERAL), the same gate as the classification routes."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.post(
        live_server + "/genome/ai-systems/%d/grounding-source/%d/refuse"
        % (seeded_ai_system_element, seeded_grounding_approval),
        form={"csrf_token": _csrf_token(page)},
        max_redirects=0,
    )
    _expect_gate(
        response, archetype, AI_SYSTEMS_GROUNDING_SOURCE_PERMITTED,
        "grounding source refuse",
    )


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_owner_set_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element
):
    """POST /genome/api/ai-systems/<id>/owners is permission_required(GENERAL)."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.post(
        live_server + "/genome/api/ai-systems/%d/owners" % seeded_ai_system_element,
        data={},
        headers={"X-CSRFToken": _csrf_token(page), "Content-Type": "application/json"},
        max_redirects=0,
    )
    _expect_gate(response, archetype, AI_SYSTEMS_OWNER_WRITE_PERMITTED, "owner set")


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_system_owner_delete_route_authorisation(
    archetype, page, live_server, seeded, seeded_ai_system_element
):
    """DELETE /genome/api/ai-systems/<id>/owners/<owner_id> is
    permission_required(GENERAL)."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.delete(
        live_server + "/genome/api/ai-systems/%d/owners/0" % seeded_ai_system_element,
        headers={"X-CSRFToken": _csrf_token(page)},
        max_redirects=0,
    )
    _expect_gate(response, archetype, AI_SYSTEMS_OWNER_WRITE_PERMITTED, "owner delete")


# Approval inbox routes (the one approval queue). All four are login_required
# and answer for a reviewer with GENERAL permission, which every archetype
# holds; a read-only Viewer role is refused (covered against the services in
# tests/test_approval_impact_preview.py, since no archetype is read-only).
APPROVAL_INBOX_PERMITTED = set(ARCHETYPES)


@pytest.fixture
def seeded_impact_approval(seeded):
    """A pending change proposal in the seeded organisation, so the impact route
    has a real approval id in the URL. Function-scoped."""
    from app import create_app, db
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    app = create_app("testing")
    with app.app_context():
        approval = create_approval_record(
            organization_id=seeded["ids"]["org"],
            operation_type="create",
            entity_type="capability",
            summary="Auth-matrix probe proposal",
            operation_payload={"name": "Auth-matrix probe capability"},
            user_id=None,
        )
        db.session.commit()
        return approval.id


@pytest.mark.parametrize("path", [
    "/ai-chat/approvals/inbox",
    "/ai-chat/approvals/queue",
    "/ai-chat/approvals/decided",
])
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_approval_inbox_reads_authorisation(archetype, path, page, live_server, seeded):
    """The inbox page and its two JSON reads are reachable by every archetype
    that holds GENERAL permission (all of them) and by nobody else."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.get(live_server + path, max_redirects=0)
    if archetype in APPROVAL_INBOX_PERMITTED:
        assert response.status == 200, "%s was refused %s (%s)" % (archetype, path, response.status)
    else:
        assert response.status == 403, "%s reached %s (%s)" % (archetype, path, response.status)


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_approval_impact_route_authorisation(archetype, page, live_server, seeded, seeded_impact_approval):
    """GET /ai-chat/approvals/<id>/impact answers the same archetypes, with the
    impact state stated (a new capability is not in the model yet)."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.get(
        live_server + "/ai-chat/approvals/%d/impact" % seeded_impact_approval, max_redirects=0
    )
    if archetype in APPROVAL_INBOX_PERMITTED:
        assert response.status == 200, "%s was refused the impact route (%s)" % (archetype, response.status)
        assert response.json()["impact"]["state"] == "not_in_model"
    else:
        assert response.status == 403, "%s reached the impact route (%s)" % (archetype, response.status)


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_ai_chat_entity_matching_write_permission(archetype, page, live_server, seeded):
    """POST /ai-chat/entity-matching carries @login_required and no enterprise-
    role gate, but the chat writes it can reach (linking a capability to a
    solution) are refused to a user without write access. Every canonical
    archetype holds a write-capable role, so none is refused (403); the
    read-only Viewer refusal, with its audit row, is pinned in
    tests/test_ai_chat_write_gate.py."""
    _login(page, live_server, seeded["emails"][archetype])
    response = page.request.post(
        live_server + "/ai-chat/entity-matching",
        data={"document_text": "Attach the Customer Management capability to a solution."},
        headers={"X-CSRFToken": _csrf_token(page)},
        max_redirects=0,
    )
    assert response.status not in (401, 403), (
        "%s was refused on /ai-chat/entity-matching (got %s) despite holding write access"
        % (archetype, response.status)
    )


def test_ai_chat_entity_matching_rejects_anonymous(page, live_server):
    response = page.request.post(
        live_server + "/ai-chat/entity-matching",
        data={"document_text": "x"},
        max_redirects=0,
    )
    assert response.status in (302, 401)
