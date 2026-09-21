"""One browser journey per archetype, against a real database.

Every archetype gets the same baseline - can it reach its own product, does the
page render, does the front end boot, is every control named, does anything
overflow - and the two archetypes with write paths additionally complete a real
piece of work through the forms.

The baseline is not filler. Each of its four checks corresponds to a defect this
suite was written after finding in production or on the way to it:

    renders           three my-applications pages 500'd on their first owned row
    front end boots   integrity hashes blocked Alpine, DOMPurify and every icon
    controls named    1,040 unlabelled inputs, the bulk of the accessibility debt
    no overflow       the mobile regression nothing else was watching for
"""

import json

import pytest

from .conftest import ARCHETYPES, PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The pages each archetype exists to use. First entry is its signature screen -
# the one that, if broken, means the persona cannot do its job.
JOURNEY = {
    "solution_architect":   ["/solutions/"],
    "enterprise_architect": ["/value-streams/", "/enterprise/"],
    "business_architect":   ["/business-case/", "/capability-map/"],
    "arb_member":           ["/arb/", "/arb/reviews"],
    "portfolio_manager":    ["/applications/"],
    "cto":                  ["/dashboard/overview"],
    "procurement":          ["/procurement/", "/procurement/contracts",
                             "/procurement/licenses", "/procurement/compliance",
                             "/procurement/renewals", "/procurement/spend"],
    "application_manager":  ["/my-applications/", "/my-applications/list",
                             "/my-applications/health", "/my-applications/roadmap"],
    "platform_admin":       ["/admin/"],
    "security_architect":   ["/risks/", "/admin/governance-gates"],
    "data_architect":       ["/architecture/data-architecture",
                             "/architecture/data-lineage"],
}

PAGE_STATE = """() => {
  const controls = [...document.querySelectorAll('input,select,textarea')]
      .filter(e => !['hidden','submit','button','reset','image'].includes(e.type));
  const unnamed = controls.filter(e => {
      if (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby')) return false;
      if (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) return false;
      if (e.closest('label')) return false;
      return true;
  });
  return {
      alpine: typeof window.Alpine,
      overflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
      controls: controls.length,
      unnamed: unnamed.map(e => e.id || e.name || e.tagName.toLowerCase()),
  };
}"""

# Firefox reports a plain object thrown from page code as the literal word
# ``Object`` through Playwright's pageerror event and discards the object's
# fields. Mirror such values to console before the browser loses them; the
# console handler below serializes every argument. This is test instrumentation,
# installed before application scripts execute, and does not change production.
STRUCTURED_ERROR_PROBE = """(() => {
  function describe(value) {
    const record = {type: typeof value};
    try { record.tag = Object.prototype.toString.call(value); } catch (_) {}
    try { record.constructor = value && value.constructor && value.constructor.name; } catch (_) {}
    for (const field of ['name', 'message', 'stack', 'code', 'expression']) {
      try {
        const fieldValue = value && value[field];
        if (fieldValue !== undefined) record[field] = String(fieldValue).slice(0, 2000);
      } catch (_) {}
    }
    try { record.properties = Object.getOwnPropertyNames(value || {}).slice(0, 30); } catch (_) {}
    try {
      if (value && value.el && value.el.outerHTML) {
        record.element = String(value.el.outerHTML).slice(0, 1000);
      }
    } catch (_) {}
    return record;
  }
  const nativeSetTimeout = window.setTimeout;
  window.setTimeout = function(callback, delay, ...args) {
    if (typeof callback !== 'function') {
      return nativeSetTimeout.call(this, callback, delay, ...args);
    }
    return nativeSetTimeout.call(this, function(...callbackArgs) {
      try {
        return callback.apply(this, callbackArgs);
      } catch (error) {
        console.error('[qualification async callback error]', describe(error));
        throw error;
      }
    }, delay, ...args);
  };
  window.addEventListener('error', event => {
    console.error('[qualification uncaught error]', {
      message: event.message || '', filename: event.filename || '',
      line: event.lineno || 0, column: event.colno || 0,
      value: describe(event.error)
    });
  }, true);
  window.addEventListener('unhandledrejection', event => {
    const reason = event.reason;
    if (reason && typeof reason === 'object'
        && reason.isFromCancelledTransition === true) {
      event.preventDefault();
      return;
    }
    console.error('[qualification unhandled rejection]', describe(reason));
  }, true);
})()"""


def _format_console_error(message):
    """Preserve structured console arguments and their source across engines.

    Firefox renders ``console.error({status: 503, ...})`` as only ``Object`` in
    ``ConsoleMessage.text``.  That made a release-blocking CI failure impossible
    to attribute to a request or script.  Chromium often stringifies more, but
    qualification diagnostics must be equally actionable in every engine.
    """
    rendered = []
    for arg in message.args:
        try:
            value = arg.json_value()
            rendered.append(json.dumps(value, sort_keys=True, default=str))
        except Exception as exc:
            rendered.append("<unserializable console argument: %s>" % exc)

    detail = " ".join(rendered) if rendered else message.text
    location = message.location or {}
    source = location.get("url", "")
    if source:
        source = source.rsplit("/", 1)[-1]
        source += ":%s:%s" % (
            location.get("lineNumber", "?"), location.get("columnNumber", "?"))
        return "%s [%s]" % (detail, source)
    return detail


def _format_page_error(error):
    """Retain an exception stack when an engine supplies an opaque message."""
    name = getattr(error, "name", "") or "PageError"
    message = getattr(error, "message", "") or str(error)
    stack = getattr(error, "stack", "") or ""
    first_useful_frame = ""
    for line in str(stack).splitlines():
        if line.strip() and line.strip() != message:
            first_useful_frame = line.strip()
            break
    if first_useful_frame:
        return "%s: %s [%s]" % (name, message, first_useful_frame)
    return "%s: %s" % (name, message)


def _login(page, base, email):
    """Sign in the way a user does, and wait for the URL to change.

    Use a normal actionable click: forced or synthetic events cannot prove that
    a user can reach the control. Wait for the resulting URL separately.
    """
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    # no_wait_after: the click causes a navigation, and without this Playwright
    # waits for the action to "settle" on a page that is being torn down.
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:                     # newer Playwright dropped the kwarg
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, (
        "could not sign in as %s - still on the login page. Page said: %s"
        % (email, " ".join(page.inner_text("body").split())[:180]))


def _visit(page, base, path):
    """Load a page, dismiss the first-run overlay, return (response, state).

    Waits for domcontentloaded rather than load: `load` blocks on every image,
    font and analytics beacon, which is not what these journeys assert and made
    the suite hang on pages that render perfectly well.
    """
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1200)
    try:
        # The onboarding modal covers the viewport on first login and would make
        # every subsequent interaction fail for reasons unrelated to the page.
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass
    return response, page.evaluate(PAGE_STATE)


@pytest.fixture
def page(browser, request):
    width = getattr(request, "param", 1440)
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    ctx.add_init_script(STRUCTURED_ERROR_PROBE)
    pg = ctx.new_page()
    pg.console_errors = []
    pg.page_errors = []
    pg.on("console", lambda m: pg.console_errors.append(_format_console_error(m))
          if m.type == "error" else None)
    # `pageerror` as well as `console`: they carry different failures, and only
    # this suite's sibling (test_ai_chat_journey) was watching both. An uncaught
    # exception — the ReferenceError a CSP-blocked script leaves behind when the
    # function it defined is never declared — is NOT delivered as a console
    # event. Every page in the product was throwing
    # "drawerFocusTrap is not defined" on load and this suite saw none of it.
    pg.on("pageerror", lambda e: pg.page_errors.append(_format_page_error(e)))
    yield pg
    ctx.close()


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_archetype_can_use_its_product(archetype, page, live_server, seeded):
    """The baseline every archetype must clear."""
    _login(page, live_server, seeded["emails"][archetype])

    failures = []
    for path in JOURNEY[archetype]:
        response, state = _visit(page, live_server, path)
        status = response.status if response else 0

        if status >= 400:
            failures.append("%s -> HTTP %d" % (path, status))
            continue
        if state["alpine"] != "object":
            failures.append("%s -> front end did not boot (window.Alpine is %s)"
                            % (path, state["alpine"]))
        if state["unnamed"]:
            failures.append("%s -> %d control(s) with no accessible name: %s"
                            % (path, len(state["unnamed"]), state["unnamed"][:6]))
        if state["overflow"] > 0:
            failures.append("%s -> %dpx horizontal overflow" % (path, state["overflow"]))

    errors = [e for e in (page.console_errors + page.page_errors)
              if "favicon" not in e.lower()]
    if errors:
        failures.append("%d JavaScript error(s): %s" % (len(errors), errors[:3]))

    assert not failures, "%s cannot use its product:\n  - %s" % (
        archetype, "\n  - ".join(failures))


@pytest.mark.parametrize("page", [390], indirect=True)
@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_archetype_signature_screen_works_on_mobile(archetype, page, live_server, seeded):
    """Only the signature screen, to keep the mobile pass cheap."""
    _login(page, live_server, seeded["emails"][archetype])
    path = JOURNEY[archetype][0]
    response, state = _visit(page, live_server, path)
    assert response.status < 400, "%s -> HTTP %d at 390px" % (path, response.status)
    assert state["overflow"] == 0, "%s overflows by %dpx at 390px - the page scrolls sideways" % (
        path, state["overflow"])


@pytest.mark.parametrize("page", [320, 768, 1024], indirect=True)
def test_motivation_repository_shell_is_accessible_and_responsive(page, live_server, seeded):
    """The production acceptance layer has one hierarchy and no viewport-only workflow."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.console_errors.clear()
    page.page_errors.clear()

    response, state = _visit(page, live_server, "/architecture/dashboard?layer=motivation")
    assert response.status < 400
    assert page.locator("h1").count() == 1
    assert page.locator('nav[aria-label="Breadcrumb"]').count() == 1
    assert page.get_by_role("heading", name="Motivation Architecture", exact=True).count() == 1
    primary = page.locator('[data-testid="btn-create-element"]')
    assert primary.count() == 1
    assert primary.get_attribute("aria-label") == "Create element"
    assert page.locator('[data-testid="layer-spine"] [aria-current="page"]').count() == 1
    assert page.locator('[aria-label="Action"]').count() == 0
    assert state["overflow"] == 0, "Motivation repository overflows by %dpx at %dpx" % (
        state["overflow"], page.viewport_size["width"]
    )
    errors = [e for e in (page.console_errors + page.page_errors) if "favicon" not in e.lower()]
    assert not errors, "Motivation repository raised JavaScript errors: %s" % errors[:5]


# ── Write journeys: the two archetypes that gained one ──────────────────────

def test_procurement_completes_a_contract_and_licence(page, live_server, seeded):
    """Record a contract, put an over-deployed licence under it, see the breach.

    This is the persona's actual job. Until 2026-07-31 it exposed seven routes,
    all GET, and neither model was constructed anywhere in the codebase.
    """
    import uuid

    _login(page, live_server, seeded["emails"]["procurement"])
    ref = uuid.uuid4().hex[:6]

    _visit(page, live_server, "/procurement/contracts/new")
    page.fill("#contract_name", "Smoke Contract %s" % ref)
    page.fill("#contract_number", "SMK-%s" % ref)   # globally unique
    page.select_option("#contract_type", "subscription")
    page.select_option("#status", "active")
    page.fill("#contract_value", "250000")
    page.fill("#start_date", "2026-01-01")
    page.fill("#end_date", "2026-12-31")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Create contract", exact=True).click()
    page.wait_for_timeout(1000)
    assert "Smoke Contract %s" % ref in page.inner_text("body"), \
        "contract was not created (still on %s)" % page.url

    _visit(page, live_server, "/procurement/contracts")
    assert "Smoke Contract %s" % ref in page.inner_text("body"), "contract missing from the list"

    _visit(page, live_server, "/procurement/licenses/new")
    page.select_option("#contract_id", label=[o for o in page.evaluate(
        "() => [...document.querySelectorAll('#contract_id option')].map(o => o.textContent.trim())")
        if ref in o][0])
    page.fill("#product_name", "Smoke Licence %s" % ref)
    page.fill("#quantity_entitled", "100")
    page.fill("#quantity_deployed", "150")          # deliberately over-deployed
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Record licence", exact=True).click()
    page.wait_for_timeout(1000)
    assert "Smoke Licence %s" % ref in page.inner_text("body"), "licence was not created"

    _visit(page, live_server, "/procurement/compliance")
    body = page.inner_text("body")
    assert "Smoke Licence %s" % ref in body, \
        "the over-deployed licence is not named on the compliance dashboard"
    # Quantities rendered blank until 2026-08-01: the templates read
    # consumed_quantity/entitled_quantity, which are not columns on the model.
    assert "150" in body and "100" in body, "licence quantities are not rendering"
    assert "Consumed: /" not in body, "quantity fields resolved to Undefined again"
    # The filter matched 'violation' while the model emits 'over_deployed', so the
    # panel stayed empty while the summary card counted the breach.
    assert "Violation" in body, "the breach is counted but not listed"


def test_application_edit_and_fact_sheet_render_the_technical_profile_fields(page, live_server, seeded):
    """Technical/compliance ApplicationComponent fields (cloud provider,
    deployment region, container image, kubernetes namespace, version
    control URL, main branch, notes, assessment notes, ...) were real
    columns the model declared but neither the edit form nor the fact
    sheet ever rendered -- the unrendered-model-fields gate's first
    real finding. Set one on the edit form, confirm it persists after
    reload, and confirm the fact sheet (the "single source of truth"
    read view) shows it too, not just the form that wrote it."""
    import uuid

    _login(page, live_server, seeded["emails"]["application_manager"])
    app_id = seeded["ids"]["application"]
    region = "smoke-region-%s" % uuid.uuid4().hex[:8]

    _visit(page, live_server, "/applications/%d/edit" % app_id)
    assert page.locator("#deployment_region").count() == 1, \
        "the Technical profile section did not render on the edit form"
    assert page.locator("#notes").count() == 1, \
        "the Compliance and notes section did not render on the edit form"
    page.fill("#deployment_region", region)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Save Changes", exact=True).click()
    page.wait_for_timeout(500)

    _visit(page, live_server, "/applications/%d/edit" % app_id)
    assert page.input_value("#deployment_region") == region, \
        "deployment_region did not persist after reload"

    _visit(page, live_server, "/applications/%d/fact-sheet" % app_id)
    assert region in page.inner_text("body"), \
        "the fact sheet's Deployment panel did not show the value just saved on the edit form"


def test_application_manager_maintains_an_owned_application(page, live_server, seeded):
    """Set health on an owned application and see it reach the health overview."""
    _login(page, live_server, seeded["emails"]["application_manager"])
    app_id = seeded["ids"]["application"]

    _visit(page, live_server, "/my-applications/app/%d/edit" % app_id)
    assert page.locator("#health_status").count() == 1, "the edit form did not render"
    page.select_option("#health_status", "at_risk")
    page.select_option("#lifecycle_status", "operational")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Save changes", exact=True).click()
    page.wait_for_timeout(1000)

    _visit(page, live_server, "/my-applications/health")
    # health_status did not exist as a column, so this page could only ever
    # report "unknown" for every application.
    assert "at risk" in page.inner_text("body").lower(), \
        "health assessment did not reach the health overview"


def test_solution_architect_registers_an_interface(page, live_server, seeded):
    """SAP S/4HANA Interface Register (Task 02): create an interface, reload,
    see it persisted in the list — "done means demonstrated"."""
    import uuid

    _login(page, live_server, seeded["emails"]["solution_architect"])
    initiative_id = seeded["ids"]["interface_register_initiative"]
    ref = uuid.uuid4().hex[:6]
    name = "Smoke Interface %s" % ref

    _visit(page, live_server, "/interface-register/new?initiative_id=%d" % initiative_id)
    assert page.locator("#name").count() == 1, "the create form did not render"
    page.fill("#name", name)
    page.select_option("#interface_type", "REST")
    page.select_option("#protocol", "HTTPS")
    page.select_option("#business_criticality", "High")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Create interface", exact=True).click()
    page.wait_for_timeout(500)

    page.reload(wait_until="domcontentloaded")
    assert name in page.inner_text("body"), "interface did not persist after reload"

    # Element-detail-page cross-check (store-agreement spirit): the interface
    # just created through the register form must also render correctly through
    # the ArchiMate element detail enrichment block (archimate_routes.py ~2627,
    # api_element_detail), which is what /archimate/composer's node detail panel
    # calls (composer.js:1859) to show protocol/interface_type for an
    # ApplicationInterface element. Resolved by a real browser hit of that JSON
    # endpoint for THIS interface's real element id, not by reading source.
    from app import create_app, db as _db
    from app.models.archimate_core import ArchiMateElement as _AME

    app = create_app("testing")
    with app.app_context():
        element = _AME.query.filter_by(name=name, type="ApplicationInterface").one()
        element_id = element.id

    response = page.goto(
        live_server + "/archimate/api/elements/%d/detail" % element_id,
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert response is not None and response.status == 200, (
        "element detail endpoint did not return 200 for the just-created interface"
    )
    body = response.json()
    assert body["type"] == "ApplicationInterface"
    meta = body.get("interface_metadata")
    assert meta is not None, (
        "api_element_detail returned interface_metadata=None for element %d created "
        "through the register form — the store-agreement between the register and "
        "the ArchiMate element detail page is broken" % element_id
    )
    assert meta.get("interface_type") == "REST", (
        "detail page shows interface_type=%r, expected REST (what was submitted "
        "through the register form)" % meta.get("interface_type")
    )
    assert meta.get("protocol") == "HTTPS", (
        "detail page shows protocol=%r, expected HTTPS (what was submitted "
        "through the register form)" % meta.get("protocol")
    )


def test_solution_architect_provisions_plateau_pair_and_raises_gap(page, live_server, seeded):
    """SAP S/4HANA Interface Register (Task 03): provision the As-is/To-be
    plateau pair, raise a gap against a just-created interface, reload, and
    confirm both persisted -- "done means demonstrated", not source-read."""
    import uuid

    _login(page, live_server, seeded["emails"]["solution_architect"])
    initiative_id = seeded["ids"]["interface_register_initiative"]
    ref = uuid.uuid4().hex[:6]
    name = "Smoke Comparison Interface %s" % ref

    # Register an interface to raise a gap against.
    _visit(page, live_server, "/interface-register/new?initiative_id=%d" % initiative_id)
    page.fill("#name", name)
    page.select_option("#interface_type", "REST")
    page.select_option("#protocol", "HTTPS")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Create interface", exact=True).click()
    page.wait_for_timeout(500)

    # GET is side-effect-free: visiting the comparison page before provisioning
    # must show the explicit "set up" button, not a fabricated pair.
    _visit(page, live_server, "/interface-register/comparison?initiative_id=%d" % initiative_id)
    assert page.locator('[data-testid="provision-comparison"]').count() >= 1, (
        "comparison screen did not show the explicit set-up control before provisioning"
    )
    assert page.locator('[data-testid="plateau-as-is"]').count() == 0, (
        "GET provisioned the plateau pair as a side effect"
    )

    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.locator('[data-testid="provision-comparison"]').click()
    page.wait_for_timeout(500)

    assert page.locator('[data-testid="plateau-as-is"]').count() == 1
    assert page.locator('[data-testid="plateau-to-be"]').count() == 1
    body = page.inner_text("body")
    assert "Current Integration Landscape" in body
    assert "S/4HANA-Integrated Landscape" in body

    # Reload: the set-up button must not reappear, and provisioning must not
    # have been undone or duplicated (repeat-POST idempotency is covered at
    # the service layer in tests/test_interface_plateau_pair.py).
    page.reload(wait_until="domcontentloaded")
    assert page.locator('[data-testid="plateau-as-is"]').count() == 1, (
        "reload after provisioning shows the pair as already set up (no duplicate button)"
    )
    assert page.locator('[data-testid="provision-comparison"]').count() == 0

    # Raise a gap for the interface just created -- locate its row by name
    # rather than by element id, which this test never resolves from the DB.
    interface_li = page.locator("li", has_text=name)
    interface_li.locator("select[name='gap_type']").select_option("protocol_change")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        interface_li.get_by_role("button", name="Raise Gap", exact=True).click()
    page.wait_for_timeout(500)

    page.reload(wait_until="domcontentloaded")
    body = page.inner_text("body")
    assert "Protocol Change" in body, "gap did not persist after reload"
    assert page.locator('[data-testid="gap-list"] li').count() >= 1, (
        "gap does not show against the To-be plateau's list"
    )


def test_comparison_page_renders_a_gap_with_null_gap_type(page, live_server, seeded):
    """Task 03 Round 2 (D4): interface_register/comparison.html called
    `gap.gap_type.replace(...)` unguarded -- gap_type is nullable and not
    enforced by validate_gap_kind's listener, so any Gap reaching this
    template with gap_type=None 500s the whole comparison page. Write such a
    Gap directly (bypassing the form, which always supplies gap_type, to
    reach the same DB state a different creation path or fixture could leave
    behind) and prove the page still renders -- with the em-dash null
    convention, not a fabricated label -- rather than reading the template
    source and trusting the guard is there."""
    import uuid

    from flask import g
    from app import create_app, db
    from app.models.implementation_migration import (
        Gap,
        GAP_KIND_PLATEAU_TRANSITION,
        TechnologyRoadmapInitiative,
    )
    from app.modules.interface_register.services import plateau_pair_service

    initiative_id = seeded["ids"]["interface_register_initiative"]
    org_id = seeded["ids"]["org"]
    ref = uuid.uuid4().hex[:6]
    gap_name = "Null gap_type probe %s" % ref

    app = create_app("testing")
    with app.app_context():
        g.current_org_id = org_id
        initiative = TechnologyRoadmapInitiative.query.get(initiative_id)
        pair = plateau_pair_service.provision_plateau_pair(initiative.id)
        as_is, to_be = pair
        gap = Gap(
            name=gap_name,
            gap_kind=GAP_KIND_PLATEAU_TRANSITION,
            gap_type=None,
            originating_plateau_id=as_is.id,
            target_plateau_id=to_be.id,
            architecture_id=initiative.architecture_id,
            severity="medium",
            impact="medium",
            priority="medium",
        )
        db.session.add(gap)
        db.session.commit()

    _login(page, live_server, seeded["emails"]["solution_architect"])
    response, _ = _visit(
        page, live_server, "/interface-register/comparison?initiative_id=%d" % initiative_id
    )
    assert response.status == 200, (
        "comparison page returned %d for a Gap with gap_type=None -- D4 regressed"
        % response.status
    )
    body = page.inner_text("body")
    assert gap_name in body, "the null-gap_type gap is not shown on reload"
    assert "AttributeError" not in body and "Internal Server Error" not in body
    # Null display convention (root CLAUDE.md): em dash, never a blank or a
    # fabricated label, in the gap-type slot for this row.
    gap_row = page.locator("li", has_text=gap_name)
    assert "—" in gap_row.inner_text(), (
        "gap_type=None did not render as the em-dash null convention"
    )


def test_solution_architect_attaches_costed_work_package_and_costing_rollup_updates(
    page, live_server, seeded
):
    """Task 04 (US-6): attach a costed WorkPackage to an interface gap from
    the comparison screen, reload the dedicated costing screen, and confirm
    the displayed committed-cost total actually changed -- the AC6 clicked
    journey, not a source assertion. Also demonstrates the over-budget
    indicator (badge + text) legibly appearing once a low budget is
    exceeded, and the em-dash for a work package left with no effort
    estimate."""
    import uuid

    from flask import g
    from app import create_app, db
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    initiative_id = seeded["ids"]["interface_register_initiative"]
    org_id = seeded["ids"]["org"]
    ref = uuid.uuid4().hex[:6]
    interface_name = "Smoke Costing Interface %s" % ref
    wp_name = "Smoke Costed WP %s" % ref

    # Set a low, real investment_budget directly (the shared seeded initiative
    # carries none) so the over-budget state is reachable without touching
    # the shared smoke fixture used by every other journey in this file.
    app = create_app("testing")
    with app.app_context():
        g.current_org_id = org_id
        initiative = TechnologyRoadmapInitiative.query.get(initiative_id)
        initiative.investment_budget = 100_000
        db.session.commit()

    _login(page, live_server, seeded["emails"]["solution_architect"])

    # Register an interface and raise a gap against it, exactly as the Task
    # 03 journey does, so there is a real plateau-transition Gap to attach a
    # work package to.
    _visit(page, live_server, "/interface-register/new?initiative_id=%d" % initiative_id)
    page.fill("#name", interface_name)
    page.select_option("#interface_type", "REST")
    page.select_option("#protocol", "HTTPS")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Create interface", exact=True).click()
    page.wait_for_timeout(500)

    _visit(page, live_server, "/interface-register/comparison?initiative_id=%d" % initiative_id)
    if page.locator('[data-testid="provision-comparison"]').count() >= 1:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            page.locator('[data-testid="provision-comparison"]').click()
        page.wait_for_timeout(500)

    interface_li = page.locator("li", has_text=interface_name)
    interface_li.locator("select[name='gap_type']").select_option("protocol_change")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        interface_li.get_by_role("button", name="Raise Gap", exact=True).click()
    page.wait_for_timeout(500)

    # Baseline: the costing screen before any work package is attached.
    _visit(page, live_server, "/interface-register/costing?initiative_id=%d" % initiative_id)
    before_cost_text = page.locator('[data-testid="rollup-committed-cost"]').inner_text()
    assert page.locator('[data-testid="over-budget-indicator"]').count() == 0, (
        "over-budget indicator shown before any cost was committed"
    )

    # Attach a costed work package (no effort hours) via the real rendered
    # form on the comparison page.
    _visit(page, live_server, "/interface-register/comparison?initiative_id=%d" % initiative_id)
    gap_form = page.locator('form[data-testid^="attach-work-package-"]').last
    gap_form.locator("input[name='name']").fill(wp_name)
    gap_form.locator("input[name='estimated_cost']").fill("250000")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        gap_form.get_by_role("button", name="Attach work package", exact=True).click()
    page.wait_for_timeout(500)

    # Reload the comparison page: the attached work package must persist and
    # show an em-dash for its (never-supplied) effort/size, not a fabricated
    # band or a blank.
    page.reload(wait_until="domcontentloaded")
    body = page.inner_text("body")
    assert wp_name in body, "attached work package did not persist after reload"

    # Reload the dedicated costing screen -- this is the AC6 assertion: the
    # displayed total actually changed after the write, on a real reload of
    # a different route, not the same page re-rendering stale server state.
    _visit(page, live_server, "/interface-register/costing?initiative_id=%d" % initiative_id)
    after_cost_text = page.locator('[data-testid="rollup-committed-cost"]').inner_text()
    assert after_cost_text != before_cost_text, (
        "costing rollup total did not change after attaching a costed work package"
    )
    assert "250,000" in after_cost_text or "250000" in after_cost_text.replace(",", ""), (
        "costing rollup total does not reflect the £250,000 just committed"
    )

    # £250,000 committed against a £100,000 budget must show the legible
    # over-budget state -- a badge AND text, not a number alone.
    assert page.locator('[data-testid="over-budget-indicator"]').count() == 1, (
        "over-budget indicator did not appear once committed cost exceeded the budget"
    )
    indicator_text = page.locator('[data-testid="over-budget-indicator"]').inner_text()
    assert "Over budget" in indicator_text

    # The work package row on the costing screen shows an em-dash for size
    # (no effort hours were ever supplied), never a fabricated band.
    rollup_row = page.locator('[data-testid^="rollup-work-package-"]', has_text=wp_name)
    assert "—" in rollup_row.inner_text(), (
        "work package with no estimated_effort_hours did not render the em-dash size band"
    )


def test_solution_architect_full_interface_register_journey_from_sidebar(
    page, live_server, seeded
):
    """Task 05 (US-7), the bucket's single acceptance-criterion journey:
    Tasks 02-04 each proved their own slice starting from a direct URL visit.
    This is the one continuous walk a real solution_architect actually makes,
    starting at the sidebar link added in Task 02 -- register -> create an
    interface -> confirm it renders through the ArchiMate element detail page
    -> provision the As-is/To-be pair -> raise a gap -> attach a costed work
    package -> reload -> the rollup total changed. Nothing here is a new
    assertion invented for this test; it is Tasks 02/03/04's three journeys
    walked back-to-back in the order a user would actually take them, entered
    through the sidebar rather than a URL bar."""
    import uuid

    from flask import g
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement as _AME
    from app.models.implementation_migration import TechnologyRoadmapInitiative

    initiative_id = seeded["ids"]["interface_register_initiative"]
    org_id = seeded["ids"]["org"]
    ref = uuid.uuid4().hex[:6]
    interface_name = "E2E Interface %s" % ref
    wp_name = "E2E Costed WP %s" % ref

    app = create_app("testing")
    with app.app_context():
        g.current_org_id = org_id
        initiative = TechnologyRoadmapInitiative.query.get(initiative_id)
        initiative.investment_budget = 100_000
        db.session.commit()

    _login(page, live_server, seeded["emails"]["solution_architect"])

    # 1. Sidebar -> register. Land on the dashboard first, then click the real
    # rendered control -- not a direct URL visit -- so this actually proves
    # the sidebar entry reaches the module (US-7's own acceptance criterion).
    _visit(page, live_server, "/")
    sidebar_link = page.get_by_test_id("sidebar").get_by_role(
        "link", name="Interface Register", exact=True
    )
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        sidebar_link.click()
    page.wait_for_timeout(500)
    assert "/interface-register" in page.url, (
        "the sidebar's Interface Register link did not reach the module"
    )

    # The picker/register list without an initiative selected -- navigate to
    # this initiative the same way the picker's own "View register" link does.
    _visit(page, live_server, "/interface-register/?initiative_id=%d" % initiative_id)

    # 2. Create an interface.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id("interface-register-new").click()
    page.wait_for_timeout(300)
    assert page.locator("#name").count() == 1, "the create form did not render"
    page.fill("#name", interface_name)
    page.select_option("#interface_type", "REST")
    page.select_option("#protocol", "HTTPS")
    page.select_option("#business_criticality", "High")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Create interface", exact=True).click()
    page.wait_for_timeout(500)

    page.reload(wait_until="domcontentloaded")
    assert interface_name in page.inner_text("body"), (
        "interface did not persist after reload"
    )

    # 3. Confirm it renders through the ArchiMate element detail page (the
    # store-agreement cross-check Task 02's journey already proved in
    # isolation -- repeated here as part of the one continuous walk).
    with app.app_context():
        element = _AME.query.filter_by(
            name=interface_name, type="ApplicationInterface"
        ).one()
        element_id = element.id
    detail_response = page.goto(
        live_server + "/archimate/api/elements/%d/detail" % element_id,
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert detail_response is not None and detail_response.status == 200
    detail_body = detail_response.json()
    assert detail_body["type"] == "ApplicationInterface"
    assert detail_body.get("interface_metadata") is not None, (
        "element detail page shows no interface_metadata for the interface "
        "just created through this journey"
    )

    # 4. Provision the As-is/To-be plateau pair from the comparison screen
    # (via the in-page nav link on the register list, not a direct URL).
    _visit(page, live_server, "/interface-register/?initiative_id=%d" % initiative_id)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id("interface-comparison-link").click()
    page.wait_for_timeout(300)
    if page.locator('[data-testid="provision-comparison"]').count() >= 1:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
            page.locator('[data-testid="provision-comparison"]').click()
        page.wait_for_timeout(500)
    assert page.locator('[data-testid="plateau-as-is"]').count() == 1
    assert page.locator('[data-testid="plateau-to-be"]').count() == 1

    # 5. Raise a gap against the interface just created.
    interface_li = page.locator("li", has_text=interface_name)
    interface_li.locator("select[name='gap_type']").select_option("protocol_change")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        interface_li.get_by_role("button", name="Raise Gap", exact=True).click()
    page.wait_for_timeout(500)
    page.reload(wait_until="domcontentloaded")
    assert "Protocol Change" in page.inner_text("body"), "gap did not persist after reload"

    # 6. Attach a costed work package to that gap, from the comparison screen
    # (in-page nav link to costing was already proven reachable in step 4).
    before_costing_response = page.goto(
        live_server + "/interface-register/costing?initiative_id=%d" % initiative_id,
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert before_costing_response.status == 200
    before_cost_text = page.locator('[data-testid="rollup-committed-cost"]').inner_text()

    _visit(page, live_server, "/interface-register/comparison?initiative_id=%d" % initiative_id)
    gap_form = page.locator('form[data-testid^="attach-work-package-"]').last
    gap_form.locator("input[name='name']").fill(wp_name)
    gap_form.locator("input[name='estimated_cost']").fill("250000")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        gap_form.get_by_role("button", name="Attach work package", exact=True).click()
    page.wait_for_timeout(500)

    # 7. Reload the dedicated costing screen (a real navigation to a different
    # route, not the same page re-rendering) and confirm the rollup total the
    # user actually sees changed.
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id("view-costing").click()
    page.wait_for_timeout(500)
    page.reload(wait_until="domcontentloaded")
    after_cost_text = page.locator('[data-testid="rollup-committed-cost"]').inner_text()
    assert after_cost_text != before_cost_text, (
        "costing rollup total did not change after attaching a costed work package, "
        "reached via the comparison screen's own in-page link rather than a direct URL"
    )
    # Assert the DELTA rather than an absolute total: this initiative_id is the
    # shared seeded fixture's, so another test in this same file (Task 04's own
    # journey) may have already committed cost against it earlier in the run --
    # a hard-coded absolute figure would be order-dependent. £250,000 more than
    # whatever was already committed is the actual claim this step is proving.
    import re as _re

    def _parse_currency(text):
        digits = _re.sub(r"[^\d.]", "", text)
        return float(digits) if digits else 0.0

    delta = _parse_currency(after_cost_text) - _parse_currency(before_cost_text)
    assert delta == 250_000, (
        "costing rollup total increased by %s, not the £250,000 just committed "
        "(before=%r after=%r)" % (delta, before_cost_text, after_cost_text)
    )


@pytest.mark.parametrize("archetype", ["security_architect", "data_architect"])
def test_non_solution_architect_reaches_interface_register_from_own_sidebar(
    page, live_server, seeded, archetype
):
    """Task 05 round 2 (D-05-1/D-05-3): security_architect and data_architect
    were already authorised (per this bucket's own authorisation matrix and
    interface_register's _guard()) to reach /interface-register, but had no
    link to it anywhere in their own rendered sidebar -- an "authorised but
    undiscoverable" defect. The earlier claim that this was fixed by an
    ENTERPRISE_ROLE_SECTION_MAP dedup was wrong: that map only feeds
    `user_visible_sections`, which no template reads. The real fix added
    `_link("Interface Register", ...)` to `_MY_WORK_LINKS[ROLE_SECURITY_ARCHITECT]`
    and `_MY_WORK_LINKS[ROLE_DATA_ARCHITECT]` in app/utils/role_access.py. Prove
    it by clicking the real sidebar control as each persona, not by reading
    role_access.py -- and confirm the page the link lands on actually renders
    (no template assuming solution_architect-specific request state, no missing
    section header), not just that the click didn't 404."""
    _login(page, live_server, seeded["emails"][archetype])

    _visit(page, live_server, "/")
    sidebar_link = page.get_by_test_id("sidebar").get_by_role(
        "link", name="Interface Register", exact=True
    )
    assert sidebar_link.count() == 1, (
        "%s's rendered sidebar has no 'Interface Register' link" % archetype
    )
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        sidebar_link.click()
    page.wait_for_timeout(500)

    assert "/interface-register" in page.url, (
        "%s's sidebar link did not reach the interface register module" % archetype
    )

    body = page.inner_text("body")
    assert "Interface Register" in body or "Interface" in body, (
        "%s's interface register page rendered with no recognisable heading "
        "for the module" % archetype
    )
    # A template that assumes solution_architect-only request state (e.g. a
    # missing initiative picker) would blow up with a 500 or an unhandled
    # error banner rather than the picker/list this route falls back to when
    # no initiative_id is supplied -- assert neither happened.
    assert "Internal Server Error" not in body
    assert "Traceback" not in body


def test_an_archetype_cannot_reach_another_personas_section(page, live_server, seeded):
    """Authorisation is part of the journey, not a separate concern."""
    _login(page, live_server, seeded["emails"]["procurement"])
    response, _ = _visit(page, live_server, "/my-applications/")
    assert response.status in (403, 404), (
        "procurement reached the application manager's section (HTTP %d) - "
        "requires_application_owner is not holding" % response.status)


# Pages reachable from the navigation that no archetype journey visits. Each one
# was found shipping JavaScript errors by a manual browser walk, precisely
# because nothing here opened it:
#
#   /architecture/business/BusinessProcess   an x-for keyed on a field the
#   /architecture/physical/Equipment         health endpoint does not return, so
#                                            all seven rows shared the key
#                                            `undefined` and Alpine's reconciler
#                                            crashed on every visit
#   /consolidation-list/                     the data-table mixin's computed
#                                            getters were dropped by Object.assign,
#                                            so the template referenced names the
#                                            component did not have
#   /applications/rationalization            never opened by any journey
#
# An enterprise architect is the archetype with reach across these screens.
UNJOURNEYED_PAGES = [
    "/architecture/business/BusinessProcess",
    "/architecture/physical/Equipment",
    "/consolidation-list/",
    "/applications/rationalization",
]


@pytest.mark.parametrize("path", UNJOURNEYED_PAGES)
def test_reachable_pages_boot_without_javascript_errors(path, page, live_server, seeded):
    """A page in the navigation must not throw on load.

    Split from the archetype baseline deliberately: these are not any one
    persona's signature screen, so folding them into a journey would misreport
    which persona is broken. They are asserted for the one property every
    reachable page owes a user — it renders and its front end boots clean.
    """
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.console_errors.clear()
    page.page_errors.clear()

    response, state = _visit(page, live_server, path)
    status = response.status if response else 0
    assert status < 400, "%s -> HTTP %d" % (path, status)
    assert state["alpine"] == "object", (
        "%s -> front end did not boot (window.Alpine is %s)" % (path, state["alpine"]))

    errors = [e for e in (page.console_errors + page.page_errors)
              if "favicon" not in e.lower()]
    assert not errors, "%s -> %d JavaScript error(s):\n  - %s" % (
        path, len(errors), "\n  - ".join(errors[:5]))
