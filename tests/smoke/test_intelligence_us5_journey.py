"""A person reaches the Worked-out connections screen, reads it, and follows it on: in a real browser.

This drives the screen exactly as a person does, against tenants that are each in a
different state of having their connections worked out:

    never      three connected things, derivation has never run
    zero       two things and one relationship: derivation ran and found nothing to add
    derived    three connected things, derivation ran and stored what it worked out
    stale      the same, after someone edited a relationship it was worked out from
    large      a model one element above the size the model check will spend time on

Every assertion is made on the rendered page. Where a test compares the page with the
answer the server gave, it asks the server itself with the same session, so the two
cannot drift. Accessible names are read from the accessibility tree the browser builds
(Playwright's role and name computation), never from the markup.

The screen asks two questions, one after the other: the figures, then the model check.
The six cards are drawn from the first answer alone; the row beside them shows a loading
treatment until the second answers.

The pinned response-time series is one worker process's record and starts empty, so the
state "not enough measurements yet" is the normal one. The shared test server runs more
than one worker process where the platform allows (each with its own record), so the test
that fills the series past the floor runs against a server of its own with exactly one
process.
"""

import importlib
import json
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, _serve_application
from .test_accessibility_audit import RESULT_KINDS, TAGS
from .test_chief_architect_workbench_journey import _screenshot as _shell_screenshot

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/intelligence/worked-out-connections"
LABEL = "Worked-out connections"
MODEL_HEALTH_PATH = "/genome/model-health/"
MIN_TARGET = 24

RECALCULATING = "Recalculating…"
FINISHED = "Recalculation finished."
BUSY = "A recalculation is already running. Try again shortly."
NOT_WORKED_OUT = "We haven't worked out the indirect connections for your model yet."
NOT_ENOUGH = "Not enough measurements yet"
NOTHING_WORKED_OUT = "Nothing worked out yet"
ERROR_LINE = "We could not answer that just now."
DRIFT_UNAVAILABLE = "We could not check your model just now."
SIZE_UNKNOWN = "Findings are not counted here for a model of this size."

FIGURES = [
    ("explicit", "Explicit facts"),
    ("derived", "Worked-out facts"),
    ("ratio", "Ratio"),
    ("stale", "Stale count"),
    ("workedOutAt", "Last worked out"),
    ("response", "Response time"),
]
# The four figures that read "Not recorded" for a tenant that has never worked its connections out.
COUNT_KEYS = ["explicit", "derived", "ratio", "stale"]

# The two questions the screen asks of the one endpoint, and the recalculation it posts.
FIGURES_URL = re.compile(r"/api/v1/intelligence/yield\?part=figures$")
MODEL_CHECK_URL = re.compile(r"/api/v1/intelligence/yield\?part=model-check$")
RECOMPUTE_URL = "**/api/v1/intelligence/derivation/recompute"


def too_large_sentence(count):
    size = "1 element" if count == 1 else "{:,} elements".format(count)
    return "Your model has %s, so its findings are not counted here." % size


# --- the tenants the journey reads ------------------------------------------

# Each tenant's users get a password made for it; the harness's own personas keep the harness's.
_TENANT_PASSWORDS = {}


def _make_tenant(shape, roles=("solution_architect", "enterprise_architect")):
    """A fresh organisation in the given state, with one signed-in user per role.

    A tenant is made per test: working the connections out changes the tenant it runs in.
    """
    from sqlalchemy import insert

    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.modules.intelligence.services.query_service import DRIFT_COUNT_MAX_ELEMENTS

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    password = uuid.uuid4().hex
    out = {"shape": shape, "emails": {}}
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Worked Out %s" % suffix, slug="worked-out-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        for role in roles:
            user = User(
                email="wc.%s.%s@example.com" % (role, suffix), first_name="Worked", last_name=role,
                organization_id=org.id, enterprise_role=role, confirmed=True,
            )
            user.role = Role.query.filter_by(name="Architect").one()
            user.password = password
            db.session.add(user)
            out["emails"][role] = user.email
            _TENANT_PASSWORDS[user.email] = password
        db.session.commit()

        def element(name):
            row = ArchiMateElement(
                name="%s %s" % (name, suffix), type="ApplicationComponent", layer="application",
                organization_id=org.id,
            )
            db.session.add(row)
            db.session.commit()
            return row

        def relate(kind, source, target):
            row = ArchiMateRelationship(
                type=kind, source_id=source.id, target_id=target.id, organization_id=org.id,
            )
            db.session.add(row)
            db.session.commit()
            return row

        first, second = element("Ledger"), element("Gateway")
        # Plain ids: working the connections out resets the session the rows came from.
        org_id, first_id, second_id = org.id, first.id, second.id
        out.update(org=org_id, first=first_id)
        if shape == "zero":
            relate("Serving", first, second)
        else:
            third = element("Portal")
            relate("Composition", first, second)
            relate("Serving", second, third)
        if shape == "large":
            # One element above the limit, in one statement.
            out["elements"] = DRIFT_COUNT_MAX_ELEMENTS + 1
            db.session.execute(
                insert(ArchiMateElement),
                [
                    {"name": "Filler %s %d" % (suffix, i), "type": "ApplicationComponent",
                     "layer": "application", "organization_id": org_id}
                    for i in range(out["elements"] - 3)
                ],
            )
            db.session.commit()
        if shape in ("zero", "derived", "stale"):
            from app.modules.intelligence.services.derivation_runner import DerivationRunner

            DerivationRunner().run_and_persist(org_id, trigger="on_demand")
        if shape == "stale":
            edited = ArchiMateRelationship.query.filter_by(source_id=first_id, target_id=second_id).one()
            edited.description = "Reviewed by the platform team"
            db.session.commit()
    return out


@pytest.fixture
def never(live_server):
    return _make_tenant("never")


@pytest.fixture
def zero(live_server):
    return _make_tenant("zero")


@pytest.fixture
def derived(live_server):
    return _make_tenant("derived")


@pytest.fixture
def stale(live_server):
    return _make_tenant("stale")


@pytest.fixture
def large(live_server):
    return _make_tenant("large")


# --- driving the page -------------------------------------------------------


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    password = _TENANT_PASSWORDS.get(email, PASSWORD)
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", password)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _ready(page, factory):
    """The page's component has started, so a click will do something."""
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _dismiss_first_run(page):
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass


def _open(page, base, email, path=PATH, check=True):
    """Sign in and open a page. On the screen, wait for the figures and, unless ``check`` is
    False, for the model check as well."""
    page.context.clear_cookies()
    _login(page, base, email)
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    if path == PATH:
        _ready(page, "workedOutConnections")
        _settled(page)
        if check:
            _drift_settled(page)


def _settled(page):
    """The page has asked its figures question and drawn its answer (or its error)."""
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"workedOutConnections()\"]');"
        " const d = el && el._x_dataStack && el._x_dataStack[0];"
        " return !!d && d.loading === false; }"
    )


def _drift_settled(page):
    """The model check has answered: a count, too large to count, or could not check."""
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"workedOutConnections()\"]');"
        " const d = el && el._x_dataStack && el._x_dataStack[0];"
        " return !!d && !!d.drift && d.drift.state !== 'loading'; }"
    )


def _what_the_page_holds(page):
    """The component's own state, for a failure message: what it believes the answer said."""
    return page.evaluate(
        """() => {
            const el = document.querySelector('[x-data="workedOutConnections()"]');
            const d = el && el._x_dataStack && el._x_dataStack[0];
            if (!d) return null;
            const button = document.querySelector('[data-recompute-button]');
            return {
                state: d.state, showRecompute: d.showRecompute, recomputing: d.recomputing,
                recomputeFailed: d.recomputeFailed, statusLine: d.statusLine, staleLine: d.staleLine,
                loaded: d.loaded, failed: d.failed,
                buttonDisplay: button ? getComputedStyle(button).display : null,
            };
        }"""
    )


def _the_action_is_gone(page, base, after):
    """The page no longer offers "Work them out now". This waits for the page to settle rather than
    reading it at one instant, and fails if the action never goes. When it fails, the message says
    what the page holds and what the server says the state is, so a failure on a slower machine
    explains itself."""
    action = page.get_by_role("button", name="Work them out now")
    try:
        expect(action).to_have_count(0, timeout=15000)
    except AssertionError as err:
        raise AssertionError(
            "the action is still offered after %s. The page holds %r; the server says the state is %r.\n%s"
            % (after, _what_the_page_holds(page), _yield_api(page, base, "figures")["state"], err)
        ) from err


def _yield_api(page, base, part=None):
    url = base + "/api/v1/intelligence/yield" + ("?part=" + part if part else "")
    response = page.context.request.get(url)
    assert response.status == 200, response.text()
    return response.json()["data"]


def _model_check_api(page, base):
    return _yield_api(page, base, "model-check")


def _answer(data, status=200):
    """A route handler that answers the request with a payload (or, with a status other than
    200, with a failure)."""
    if status == 200:
        body = json.dumps({"success": True, "data": data})
    else:
        body = json.dumps({"success": False, "error": {"code": "ERR", "message": "down"}})
    return lambda route, request: route.fulfill(status=status, content_type="application/json", body=body)


def _shot(target, name, full_content=False, **options):
    """A screenshot for the evidence folder, when there is one."""
    folder = os.environ.get("T005_EVIDENCE_DIR") or os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        name = "t005-us5-" + name
        if full_content:
            # Keep the actual viewport composition before the shell-aware helper grows it.
            target.screenshot(path=os.path.join(folder, name + "-viewport.png"), full_page=False, **options)
            _shell_screenshot(target, name + "-expanded-content.png", directory=folder)
        else:
            target.screenshot(path=os.path.join(folder, name + ".png"), **options)


def _figure_row(page):
    return page.locator("[data-testid=figure-row]")


def _value(page, key):
    return page.locator("[data-testid=value-%s]" % key)


def _names(page):
    """The accessible name of every figure's value, in reading order, as the browser's
    accessibility tree gives it: {key: name}."""
    snapshot = _figure_row(page).aria_snapshot()
    names = re.findall(r'''- '?img "([^"]*)"''', snapshot)
    assert len(names) == len(FIGURES), snapshot
    return {key: names[i] for i, (key, _title) in enumerate(FIGURES)}


def _main_text_without_detail(page):
    return page.evaluate(
        """() => {
            const main = document.querySelector('main').cloneNode(true);
            main.querySelectorAll('[data-full-detail-region], script, style, template')
                .forEach((el) => el.remove());
            return main.textContent.replace(/\\s+/g, ' ').trim();
        }"""
    )


def _detail_text(page):
    return page.locator("[data-full-detail-region]").text_content()


def _status_text(page):
    return page.locator("[data-testid=derivation-status-line]").inner_text().strip()


def _drift_row(page):
    return page.get_by_test_id("drift-row")


def _drift_state(page):
    return _drift_row(page).get_attribute("data-state")


def _sidebar_ask(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    page.get_by_test_id("sidebar").get_by_role("link", name="Ask a question").click()
    page.wait_for_url(re.compile(r"/intelligence/ask$"))
    _ready(page, "askSurface")


def _through_the_header_action(page):
    page.get_by_role("link", name=LABEL, exact=True).click()
    page.wait_for_url(re.compile(r"/intelligence/worked-out-connections$"))
    _ready(page, "workedOutConnections")
    _settled(page)
    _drift_settled(page)
    _figure_row(page).wait_for(state="visible")


def _drift_total(org_id):
    from app import create_app
    from app.modules.genome.services.drift_detector import detect_model_drift

    with create_app("testing").app_context():
        return detect_model_drift(org_id)["summary"]["total"]


def _plural(n, one, many):
    return one if n == 1 else many


# --- the journey with a pointer ---------------------------------------------


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect"])
def test_a_person_reaches_the_screen_from_ask_reads_it_and_follows_the_drift_link(
    persona, page, live_server, seeded, never
):
    """From the sidebar to the existing drift page, as two personas.

    Open Ask from the sidebar, click the Worked-out connections header action, read the
    title, the breadcrumb and the figure row, see every absent value read as a reason and
    not a zero, expand Full detail, follow the drift link and land on the page that owns
    those findings.
    """
    _login(page, live_server, never["emails"][persona])
    _sidebar_ask(page, live_server)
    _through_the_header_action(page)

    assert page.url.endswith(PATH)
    assert page.locator("h1").inner_text().strip() == LABEL
    crumbs = page.locator('nav[aria-label="Breadcrumb"] li')
    assert [c.inner_text().strip() for c in crumbs.all()] == ["Home", LABEL]
    assert page.locator('nav[aria-label="Breadcrumb"] li[aria-current="page"]').inner_text().strip() == LABEL

    names = _names(page)
    for key in COUNT_KEYS:
        title = dict(FIGURES)[key]
        assert names[key] == "%s: Not recorded" % title
        text = _value(page, key).inner_text().strip()
        assert text == "Not recorded", (key, text)
        assert not re.search(r"\d", text)
    assert names["workedOutAt"] == "Last worked out: %s" % NOTHING_WORKED_OUT
    assert page.get_by_text(NOT_WORKED_OUT).is_visible()

    toggle = page.locator("[data-full-detail-toggle]")
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    page.locator("[data-full-detail-region]").wait_for(state="visible")
    assert page.locator("[data-detail-row]").count() == 4

    drift = page.get_by_role("link", name="See drift findings and fixes")
    assert drift.get_attribute("href") == MODEL_HEALTH_PATH
    drift.click()
    page.wait_for_url(re.compile(r"/genome/model-health/?$"))
    assert page.locator("h1").inner_text().strip() == "Model Health"


def test_twin_map_reaches_the_same_screen_with_its_own_header_action(page, live_server, seeded, never):
    _login(page, live_server, never["emails"]["enterprise_architect"])
    page.goto(live_server + "/intelligence/twin-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "twinMapSurface")
    _through_the_header_action(page)
    assert page.url.endswith(PATH)
    assert page.locator("h1").inner_text().strip() == LABEL


@pytest.mark.parametrize("width,height", [(1440, 900), (1024, 900), (390, 844)])
def test_integrated_shell_visual_evidence(width, height, page, live_server, seeded, never, stale):
    """Collect viewport and expanded-content evidence for independent visual review."""
    original_viewport = page.viewport_size
    prefix = "shell-%dx%d-" % (width, height)
    try:
        page.set_viewport_size({"width": width, "height": height})
        _login(page, live_server, never["emails"]["enterprise_architect"])
        for path, factory, title, name in (
            ("/intelligence/ask", "askSurface", "Ask", "ask-idle"),
            ("/intelligence/twin-map", "twinMapSurface", "Twin map", "twin-map-idle"),
        ):
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response is not None and response.status == 200
            assert page.url == live_server + path
            _dismiss_first_run(page)
            _ready(page, factory)
            # Entry context has no selected element and therefore no impact request to await.
            page.wait_for_function(
                "(f) => { const d = document.querySelector('[x-data=\"' + f + '()\"]')._x_dataStack[0];"
                " return d.state === 'idle' && d.busy === false; }",
                arg=factory,
            )
            expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
            expect(page.get_by_role("link", name=LABEL, exact=True)).to_be_visible()
            _shot(page, prefix + name, full_content=True, animations="disabled")

        # Reach the uncomputed state through the real Twin map header action.
        _through_the_header_action(page)
        assert _what_the_page_holds(page)["state"] == "not_computed"
        assert _drift_state(page) == "counted"
        expect(page.get_by_text(NOT_WORKED_OUT)).to_be_visible()
        expect(page.get_by_role("button", name="Work them out now")).to_be_visible()
        _shot(page, prefix + "worked-out-not-computed", full_content=True, animations="disabled")

        _open(page, live_server, stale["emails"]["enterprise_architect"])
        assert page.url == live_server + PATH
        assert _what_the_page_holds(page)["state"] == "stale"
        assert _drift_state(page) == "counted"
        expect(page.get_by_text(re.compile(r"^Last worked out .+ — may be out of date\.$"))).to_be_visible()
        expect(page.get_by_role("button", name="Work them out now")).to_be_visible()
        _shot(page, prefix + "worked-out-stale-action", full_content=True, animations="disabled")
    finally:
        page.set_viewport_size(original_viewport)
        page.goto("about:blank")


# --- the label ----------------------------------------------------------------


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect"])
def test_the_rendered_title_and_breadcrumb_leaf_read_exactly_the_label(persona, page, live_server, seeded, never):
    """Both, in the rendered document: the page's one h1, the breadcrumb's current leaf
    and the tab title, and no other heading or link carries the label."""
    _open(page, live_server, never["emails"][persona])
    assert [h.inner_text().strip() for h in page.locator("h1").all()] == [LABEL]
    leaf = page.locator('nav[aria-label="Breadcrumb"] li[aria-current="page"]')
    assert leaf.count() == 1 and leaf.inner_text().strip() == LABEL
    assert page.title().strip() == LABEL
    assert page.get_by_test_id("sidebar").get_by_text(LABEL).count() == 0


# --- not worked out yet ---------------------------------------------------------


def test_never_worked_out_is_a_null_payload_four_cards_that_say_not_recorded_and_one_that_says_nothing_yet(
    page, live_server, seeded, never
):
    """The payload and the page say the same thing: nothing has been worked out, so no
    count is a number. In the accessibility tree each value is named for what it is; the
    four counts read "Not recorded" (the badge's words are on the card) and the last time
    reads "Nothing worked out yet"; no card renders a zero, a blank or an unnamed value."""
    _open(page, live_server, never["emails"]["solution_architect"])
    data = _yield_api(page, live_server)
    assert data["state"] == "not_computed"
    assert "derivation_not_computed" in data["reasons"]
    assert all(data[k] is None for k in ("explicit_count", "derived_count", "ratio", "stale_count", "computed_at"))

    names = _names(page)
    for key in COUNT_KEYS:
        assert names[key] == "%s: Not recorded" % dict(FIGURES)[key]
        card = _value(page, key)
        assert card.get_by_text("Not recorded", exact=True).is_visible()
        assert card.inner_text().strip() not in ("", "0")
    assert names["workedOutAt"] == "Last worked out: %s" % NOTHING_WORKED_OUT
    assert _value(page, "workedOutAt").inner_text().strip() == NOTHING_WORKED_OUT
    assert page.locator("[data-testid=last-recalculation]").get_by_text("Not recorded", exact=True).is_visible()
    # Every card has a name at all: none is a bare number.
    for key, _title in FIGURES:
        assert page.locator("[data-testid=value-%s]" % key).get_attribute("aria-label")


def test_the_not_computed_message_and_button_sit_in_a_polite_status_region(page, live_server, seeded, never):
    _open(page, live_server, never["emails"]["solution_architect"])
    region = page.get_by_test_id("derivation-status")
    assert region.get_attribute("role") == "status"
    assert region.get_attribute("aria-live") == "polite"
    assert region.get_by_text(NOT_WORKED_OUT).is_visible()
    button = region.get_by_role("button", name="Work them out now")
    assert button.is_visible()
    assert page.evaluate("(el) => !!el.closest('[role=status][aria-live=polite]')", button.element_handle())


def test_a_measured_zero_reads_as_a_zero_and_not_as_not_recorded(page, live_server, seeded, zero):
    """Derivation ran and had nothing to add: the same cards carry numbers, their names
    carry the zero, and nothing on the page says the connections have not been worked out.
    The run stored nothing, so no time was stamped on anything: the last-worked-out card
    says so, and the time the run finished stands in for nothing."""
    _open(page, live_server, zero["emails"]["solution_architect"])
    data = _yield_api(page, live_server)
    assert data["state"] == "current"
    assert (data["explicit_count"], data["derived_count"], data["stale_count"]) == (1, 0, 0)
    assert data["ratio"] == 0
    assert data["computed_at"] is None and data["last_run_at"], "a run was recorded, and stored no time"

    names = _names(page)
    assert names["explicit"] == "Explicit facts: 1 relationship"
    assert names["derived"] == "Worked-out facts: 0 relationships"
    assert names["ratio"] == "Ratio: 0 worked out for every explicit one"
    assert names["stale"] == "Stale count: 0 relationships"
    assert names["workedOutAt"] == "Last worked out: %s" % NOTHING_WORKED_OUT, names["workedOutAt"]
    assert "0" in _value(page, "derived").inner_text()
    assert "Not recorded" not in _value(page, "derived").inner_text()
    assert page.get_by_text(NOT_WORKED_OUT).count() == 0
    assert page.get_by_role("button", name="Work them out now").count() == 0 or not page.get_by_role(
        "button", name="Work them out now"
    ).is_visible()

    # The last run's own time appears nowhere: not on the card, not in Full detail.
    page.locator("[data-full-detail-toggle]").click()
    page.locator("[data-full-detail-region]").wait_for(state="visible")
    rows = {
        r.locator("dt").inner_text().strip(): r.locator("dd").inner_text().strip()
        for r in page.locator("[data-detail-row]").all()
    }
    assert rows["Worked out at"] == NOTHING_WORKED_OUT, rows
    stamp = data["last_run_at"][:16]
    assert stamp not in page.locator("main").text_content()
    assert not re.search(r"\d{1,2} \w{3}\w* \d{4}, \d{2}:\d{2}", page.locator("main").inner_text())


def test_the_two_states_are_different_in_the_tree_not_only_on_screen(page, live_server, seeded, never, zero):
    _open(page, live_server, never["emails"]["solution_architect"])
    unmeasured = _names(page)
    _open(page, live_server, zero["emails"]["solution_architect"])
    measured = _names(page)
    for key in ("explicit", "derived", "stale"):
        assert unmeasured[key].endswith("Not recorded")
        assert not measured[key].endswith("Not recorded")
        assert unmeasured[key] != measured[key]


# --- a populated tenant: every figure has its title and unit in its name ---------


def test_every_figure_reads_its_title_its_value_and_its_unit_together(page, live_server, seeded, derived):
    _open(page, live_server, derived["emails"]["solution_architect"])
    data = _yield_api(page, live_server)
    assert data["state"] == "current" and data["derived_count"] >= 1

    names = _names(page)
    n, m = data["explicit_count"], data["derived_count"]
    assert names["explicit"] == "Explicit facts: %d %s" % (n, _plural(n, "relationship", "relationships"))
    assert names["derived"] == "Worked-out facts: %d %s" % (m, _plural(m, "relationship", "relationships"))
    ratio = ("%.2f" % data["ratio"]).rstrip("0").rstrip(".")
    assert names["ratio"] == "Ratio: %s worked out for every explicit one" % ratio
    assert names["stale"] == "Stale count: 0 relationships"
    assert re.fullmatch(r"Last worked out: \d{1,2} \w+\.? \d{4}, \d{2}:\d{2}", names["workedOutAt"]), names["workedOutAt"]
    assert re.fullmatch(
        r"Response time: (Not enough measurements yet, \d+ recent measurements? on this server"
        r"|Within [\d.]+ seconds? for 95 in 100 impact questions, from \d+ recent measurements? on this server"
        r"|Over [\d.]+ seconds? for more than 5 in 100 impact questions, from \d+ recent measurements? on this server)",
        names["response"],
    ), names["response"]
    for key, name in names.items():
        assert ":" in name and not re.fullmatch(r"[\d.,]+", name), (key, name)
    assert _value(page, "derived").inner_text().split()[0] == str(m)


def test_the_caption_and_the_value_are_announced_together(page, live_server, seeded, derived):
    """In reading order each figure is its caption immediately followed by its named value."""
    _open(page, live_server, derived["emails"]["enterprise_architect"])
    snapshot = _figure_row(page).aria_snapshot()
    order = re.findall(r'''- (?:figure "([^"]*)"|'img "([^"]*)"')''', snapshot)
    figures = [f for f, _ in order if f]
    values = [v for _, v in order if v]
    assert figures == [title for _key, title in FIGURES]
    assert len(values) == len(FIGURES)
    flat = [f or v for f, v in order]
    for title, value in zip(figures, values):
        at = flat.index(title)
        assert flat[at + 1] == value and value.startswith(title + ":"), (title, value)


def test_the_ratio_is_reported_and_never_targeted(page, live_server, seeded, derived):
    _open(page, live_server, derived["emails"]["solution_architect"])
    body = page.locator("main")
    assert body.get_by_text(
        "We don't set a target for this yet — this is the first real measurement, not a borrowed benchmark."
    ).is_visible()
    for selector in ("progress", "meter", "[role=progressbar]", "[aria-valuenow]", "[role=meter]"):
        assert body.locator(selector).count() == 0, selector
    text = _main_text_without_detail(page)
    for word in ("good", "bad", "healthy", "excellent", "poor", "on track", "behind", "goal", "achieved", "above target", "below target"):
        assert not re.search(r"\b%s\b" % re.escape(word), text, re.I), word
    data = _yield_api(page, live_server)
    assert not [k for k in data if re.search("target|goal|benchmark|verdict|score|grade", k)]


# --- the response time ------------------------------------------------------------


def test_below_the_floor_the_card_says_so_with_the_count_and_shows_no_number(page, live_server, seeded, never):
    _open(page, live_server, never["emails"]["solution_architect"])
    # The count is the one in the answer the page itself received: the shared server may answer
    # from more than one process, each with a record of its own, so another request could differ.
    with page.expect_response(FIGURES_URL) as received:
        page.reload(wait_until="domcontentloaded")
    data = received.value.json()["data"]
    _ready(page, "workedOutConnections")
    _settled(page)
    assert data["p95_latency_seconds"] is None and data["sample_count"] < 100
    assert "insufficient_samples_for_p95" in data["reasons"]

    count = data["sample_count"]
    card = _value(page, "response")
    assert card.get_by_text(NOT_ENOUGH, exact=True).is_visible()
    assert _names(page)["response"] == "Response time: %s, %d recent %s on this server" % (
        NOT_ENOUGH, count, _plural(count, "measurement", "measurements"))
    assert not re.search(r"\bseconds?\b", card.inner_text())


# The response time and the last-worked-out time, worded exactly, in every state the answer can put
# them in. The script builds each card from the answer; here it is handed answers directly, in the
# page as served, so what is read is the shipped script in a real browser.

BASE_ANSWER = {
    "state": "current",
    "explicit_count": 2, "derived_count": 1, "ratio": 0.5, "stale_count": 0,
    "computed_at": "2026-09-20T10:39:24.553980", "last_run_at": "2026-09-20T11:15:02.000000",
    "last_recompute_duration_ms": 62, "engine_version": ["1.0.0"],
    "p95_latency_seconds": None, "sample_count": 0,
    "p95": {
        "latency_seconds": None, "sample_count": 0, "scope": "process_estate_wide",
        "query": "cross_layer_impact", "depth": 4, "include_derived": True, "reason": None,
    },
    "reasons": ["drift_count_not_requested"],
    "drift_finding_count": None,
}
MEASURED_FROM = (
    "Impact questions traced 4 hops out, with worked-out connections included, "
    "as answered by this web server since it last started"
)


def _answer_with(**changes):
    """The base answer with some fields changed; a value of DROP removes the field."""
    data = json.loads(json.dumps(BASE_ANSWER))
    for key, value in changes.items():
        if value is DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


DROP = object()


def _p95(**changes):
    block = dict(BASE_ANSWER["p95"])
    block.update(changes)
    return block


def _models(page, answer):
    """What the shipped script makes of an answer: the cards, Full detail's rows, the out-of-date
    line and whether the action is offered."""
    return page.evaluate(
        """(answer) => {
            const component = window.workedOutConnections();
            component.apply(answer);
            return JSON.parse(JSON.stringify({
                cards: component.cards, details: component.details, staleLine: component.staleLine,
                statusLine: component.statusLine, showRecompute: component.showRecompute,
            }));
        }""",
        answer,
    )


RESPONSE_CASES = [
    # id, changes to the answer, the value read, the accessible name, Full detail's 95th percentile row
    ("measured 1", dict(p95_latency_seconds=1.0, sample_count=240, reasons=[]),
     "Within 1 second",
     "Response time: Within 1 second for 95 in 100 impact questions, from 240 recent measurements on this server",
     "1 second, the top of the time range within which 95 in 100 questions were answered"),
    ("measured 0.5", dict(p95_latency_seconds=0.5, sample_count=240, reasons=[]),
     "Within 0.5 seconds",
     "Response time: Within 0.5 seconds for 95 in 100 impact questions, from 240 recent measurements on this server",
     "0.5 seconds, the top of the time range within which 95 in 100 questions were answered"),
    ("measured 2", dict(p95_latency_seconds=2.0, sample_count=100, reasons=[]),
     "Within 2 seconds",
     "Response time: Within 2 seconds for 95 in 100 impact questions, from 100 recent measurements on this server",
     "2 seconds, the top of the time range within which 95 in 100 questions were answered"),
    ("measured 0.005", dict(p95_latency_seconds=0.005, sample_count=101, reasons=[]),
     "Within 0.005 seconds",
     "Response time: Within 0.005 seconds for 95 in 100 impact questions, from 101 recent measurements on this server",
     "0.005 seconds, the top of the time range within which 95 in 100 questions were answered"),
    ("measured from one measurement", dict(p95_latency_seconds=0.025, sample_count=1, reasons=[]),
     "Within 0.025 seconds",
     "Response time: Within 0.025 seconds for 95 in 100 impact questions, from 1 recent measurement on this server",
     "0.025 seconds, the top of the time range within which 95 in 100 questions were answered"),
    ("above the highest range",
     dict(sample_count=240, p95=_p95(p95_exceeds_seconds=5.0, reason="p95_above_highest_bucket"),
          reasons=["p95_above_highest_bucket"]),
     "Over 5 seconds",
     "Response time: Over 5 seconds for more than 5 in 100 impact questions, from 240 recent measurements on this server",
     "Over 5 seconds: fewer than 95 in 100 questions were answered within 5 seconds, the longest time range measured"),
    ("not enough, 37", dict(sample_count=37, reasons=["insufficient_samples_for_p95"]),
     NOT_ENOUGH,
     "Response time: Not enough measurements yet, 37 recent measurements on this server",
     NOT_ENOUGH),
    ("not enough, 1", dict(sample_count=1, reasons=["insufficient_samples_for_p95"]),
     NOT_ENOUGH,
     "Response time: Not enough measurements yet, 1 recent measurement on this server",
     NOT_ENOUGH),
    ("not enough, 0", dict(sample_count=0, reasons=["insufficient_samples_for_p95"]),
     NOT_ENOUGH,
     "Response time: Not enough measurements yet, 0 recent measurements on this server",
     NOT_ENOUGH),
    ("not enough is chosen by its reason, whatever the count", dict(sample_count=9000, reasons=["insufficient_samples_for_p95"]),
     NOT_ENOUGH,
     "Response time: Not enough measurements yet, 9000 recent measurements on this server",
     NOT_ENOUGH),
    # A null figure is not, by itself, "not enough measurements".
    ("a null figure with 9000 measurements and no reason", dict(sample_count=9000, reasons=[]),
     None, "Response time: Not recorded", None),
    ("a null figure with 100 measurements and some other reason", dict(sample_count=100, reasons=["no_recompute_duration_recorded"]),
     None, "Response time: Not recorded", None),
    ("above the range with no figure to say so", dict(sample_count=240, reasons=["p95_above_highest_bucket"]),
     None, "Response time: Not recorded", None),
    ("no sample count, even with the reason", dict(sample_count=DROP, reasons=["insufficient_samples_for_p95"]),
     None, "Response time: Not recorded", None),
    ("a figure but no sample count", dict(p95_latency_seconds=1.0, sample_count=DROP, reasons=[]),
     None, "Response time: Not recorded", None),
]


@pytest.mark.parametrize("case", RESPONSE_CASES, ids=[c[0] for c in RESPONSE_CASES])
def test_the_response_time_card_reads_exactly_as_worded_in_every_state(page, live_server, seeded, never, case):
    _, changes, value, name, detail = case
    _open(page, live_server, never["emails"]["solution_architect"])
    got = _models(page, _answer_with(**changes))
    card = got["cards"]["response"]
    assert card["name"] == name
    if value is None:
        assert card["absent"] is True and card["text"] == ""
    else:
        assert card["absent"] is False and card["text"] == value
    assert got["details"][2] == {"label": "Response time, 95th percentile", "value": detail}


def test_the_response_time_card_and_its_full_detail_render_as_worded(page, live_server, seeded, never):
    """The same states in the page itself: what is seen, what the accessibility tree announces, and
    the two Full detail rows. The old "exact" row is gone."""
    _open(page, live_server, never["emails"]["solution_architect"])
    seen = {}
    for label, changes, value, name, detail in (
        ("measured", dict(p95_latency_seconds=1.0, sample_count=240, reasons=[]),
         "Within 1 second", RESPONSE_CASES[0][3], RESPONSE_CASES[0][4]),
        ("above", RESPONSE_CASES[5][1], "Over 5 seconds", RESPONSE_CASES[5][3], RESPONSE_CASES[5][4]),
        ("not-enough", RESPONSE_CASES[6][1], NOT_ENOUGH, RESPONSE_CASES[6][3], RESPONSE_CASES[6][4]),
        ("absent", RESPONSE_CASES[10][1], None, RESPONSE_CASES[10][3], RESPONSE_CASES[10][4]),
    ):
        page.unroute(FIGURES_URL)
        page.route(FIGURES_URL, _answer(_answer_with(**changes)))
        page.reload(wait_until="domcontentloaded")
        _ready(page, "workedOutConnections")
        _settled(page)
        assert _names(page)["response"] == name, label
        card = _value(page, "response")
        if value is None:
            assert card.inner_text().strip() == "Not recorded", label
        else:
            assert card.inner_text().split("\n")[0].strip() == value, label
        page.locator("[data-full-detail-toggle]").click()
        page.locator("[data-full-detail-region]").wait_for(state="visible")
        rows = {
            r.locator("dt").inner_text().strip(): r.locator("dd").inner_text().strip()
            for r in page.locator("[data-detail-row]").all()
        }
        assert "Response time, exact" not in rows, rows
        assert rows["Response time, 95th percentile"] == (detail or "Not recorded"), (label, rows)
        assert rows["Response time, measured from"] == MEASURED_FROM, (label, rows)
        seen[label] = rows
        _shot(_figure_row(page), "card-response-time-" + label)
    assert len(seen) == 4


def test_the_card_wording_is_recorded_for_every_state(page, live_server, seeded, never):
    """Every state of the response-time and last-worked-out cards, as the shipped script words it: what is
    read on the card, what the accessibility tree announces, and the rows in Full detail. Written to the
    evidence folder when there is one."""
    _open(page, live_server, never["emails"]["solution_architect"])
    table = []
    for label, changes, value, name, detail in RESPONSE_CASES:
        got = _models(page, _answer_with(**changes))
        card = got["cards"]["response"]
        assert card["name"] == name
        table.append({
            "card": "Response time", "state": label,
            "read_on_the_card": ("Not recorded" if card["absent"] else card["text"] + " / " + card["unit"]),
            "announced_as": card["name"],
            "full_detail_95th_percentile": got["details"][2]["value"] or "Not recorded",
            "full_detail_measured_from": got["details"][3]["value"] or "Not recorded",
        })
    for label, changes in (
        ("a time was stored", {}),
        ("nothing was stored (a run that found nothing, or none yet)", dict(computed_at=None)),
        ("no time in the answer", dict(computed_at=DROP)),
        ("a time that cannot be read", dict(computed_at="not a time")),
    ):
        got = _models(page, _answer_with(**changes))
        card = got["cards"]["workedOutAt"]
        table.append({
            "card": "Last worked out", "state": label,
            "read_on_the_card": "Not recorded" if card["absent"] else card["text"],
            "announced_as": card["name"],
            "full_detail_worked_out_at": got["details"][1]["value"] or "Not recorded",
        })
    for label, changes in (
        ("out of date, with a time", dict(state="stale", stale_count=1)),
        ("out of date, no time", dict(state="stale", stale_count=1, computed_at=None)),
    ):
        got = _models(page, _answer_with(**changes))
        table.append({"card": "Out-of-date line", "state": label, "read_on_the_page": got["staleLine"],
                      "the_action_is_offered": got["showRecompute"]})
    folder = os.environ.get("T005_EVIDENCE_DIR")
    print("[wording] " + json.dumps(table, sort_keys=True))
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "card-wording.json"), "w", encoding="utf-8") as fh:
            json.dump(table, fh, indent=2, ensure_ascii=False)
    assert len(table) == len(RESPONSE_CASES) + 6
    assert all(row["announced_as"].startswith(row["card"] + ": ") for row in table if "announced_as" in row)


MEASURED_FROM_CASES = [
    ("as answered", {}, MEASURED_FROM),
    ("one hop", dict(depth=1), MEASURED_FROM.replace("4 hops", "1 hop")),
    ("three hops", dict(depth=3), MEASURED_FROM.replace("4 hops", "3 hops")),
    ("without worked-out connections", dict(include_derived=False),
     "Impact questions traced 4 hops out, without worked-out connections, "
     "as answered by this web server since it last started"),
    ("an unknown query", dict(query="something_else"), None),
    ("a query named like an object property", dict(query="constructor"), None),
    ("no depth", dict(depth=None), None),
    ("a depth that is not a number", dict(depth="4"), None),
    ("a fractional depth", dict(depth=2.5), None),
    ("include_derived not a boolean", dict(include_derived="true"), None),
    ("no scope", dict(scope=None), None),
]


@pytest.mark.parametrize("case", MEASURED_FROM_CASES, ids=[c[0] for c in MEASURED_FROM_CASES])
def test_what_the_response_time_was_measured_from_is_worded_for_a_reader_or_not_recorded(
    page, live_server, seeded, never, case
):
    _, changes, expected = case
    _open(page, live_server, never["emails"]["solution_architect"])
    answer = _answer_with(p95_latency_seconds=1.0, sample_count=240, reasons=[])
    answer["p95"] = _p95(**changes)
    assert _models(page, answer)["details"][3] == {"label": "Response time, measured from", "value": expected}
    missing = _answer_with(p95_latency_seconds=1.0, sample_count=240, reasons=[], p95=DROP)
    assert _models(page, missing)["details"][3]["value"] is None


def test_the_last_worked_out_card_says_when_the_stored_connections_were_worked_out_and_nothing_else(
    page, live_server, seeded, never
):
    _open(page, live_server, never["emails"]["solution_architect"])
    time_name = re.compile(r"Last worked out: \d{1,2} \w+\.? \d{4}, \d{2}:\d{2}")

    measured = _models(page, _answer_with())
    assert time_name.fullmatch(measured["cards"]["workedOutAt"]["name"])
    assert measured["details"][1] == {"label": "Worked out at", "value": "2026-09-20T10:39:24.553980Z"}
    assert measured["staleLine"] is None and measured["showRecompute"] is False

    # No time was stored: nothing has been worked out, whatever the last run's own time says.
    for state in ("not_computed", "current"):
        nothing = _models(page, _answer_with(state=state, computed_at=None))
        assert nothing["cards"]["workedOutAt"]["name"] == "Last worked out: %s" % NOTHING_WORKED_OUT
        assert nothing["cards"]["workedOutAt"]["text"] == NOTHING_WORKED_OUT
        assert nothing["details"][1] == {"label": "Worked out at", "value": NOTHING_WORKED_OUT}

    # The answer does not carry the time at all, or carries one that cannot be read.
    for changes in (dict(computed_at=DROP), dict(computed_at="not a time"), dict(computed_at="")):
        absent = _models(page, _answer_with(**changes))
        assert absent["cards"]["workedOutAt"]["absent"] is True
        assert absent["cards"]["workedOutAt"]["name"] == "Last worked out: Not recorded"
        assert absent["details"][1]["value"] is None

    out_of_date = _models(page, _answer_with(state="stale", stale_count=1))
    assert re.fullmatch(r"Last worked out .+ — may be out of date\.", out_of_date["staleLine"])
    assert out_of_date["showRecompute"] is True
    no_time = _models(page, _answer_with(state="stale", stale_count=1, computed_at=None))
    assert no_time["staleLine"] == "May be out of date."

    not_worked_out = _models(page, _answer_with(state="not_computed", computed_at=None))
    assert not_worked_out["statusLine"] == NOT_WORKED_OUT and not_worked_out["showRecompute"] is True


def test_the_last_worked_out_card_renders_as_worded(page, live_server, seeded, never, zero):
    _open(page, live_server, zero["emails"]["solution_architect"])
    _shot(_figure_row(page), "card-last-worked-out-nothing-yet")
    assert _names(page)["workedOutAt"] == "Last worked out: %s" % NOTHING_WORKED_OUT
    for label, changes, expected in (
        ("measured", {}, re.compile(r"Last worked out: \d{1,2} \w+\.? \d{4}, \d{2}:\d{2}")),
        ("absent", dict(computed_at=DROP), re.compile(r"Last worked out: Not recorded")),
    ):
        page.unroute(FIGURES_URL)
        page.route(FIGURES_URL, _answer(_answer_with(**changes)))
        page.reload(wait_until="domcontentloaded")
        _ready(page, "workedOutConnections")
        _settled(page)
        assert expected.fullmatch(_names(page)["workedOutAt"]), label
        _shot(_figure_row(page), "card-last-worked-out-" + label)


# --- staleness ---------------------------------------------------------------------


def test_a_stale_tenant_says_when_it_was_worked_out_and_that_it_may_be_out_of_date(page, live_server, seeded, stale):
    _open(page, live_server, stale["emails"]["solution_architect"])
    data = _yield_api(page, live_server)
    assert data["state"] == "stale" and data["stale_count"] >= 1

    line = page.get_by_text(re.compile(r"^Last worked out .+ — may be out of date\.$"))
    assert line.is_visible()
    clock = page.get_by_role("img", name="Last worked out", exact=True)
    assert clock.count() == 1 and clock.is_visible()
    n = data["stale_count"]
    assert _names(page)["stale"] == "Stale count: %d %s" % (n, _plural(n, "relationship", "relationships"))
    # A cue that is not colour alone: the words and the clock both carry it.
    assert page.locator("[aria-label='Last worked out'] [data-lucide]").count() == 1


def test_the_out_of_date_state_offers_the_action_and_working_them_out_posts_once_and_clears_the_state(
    page, live_server, seeded, stale
):
    """The state that is about freshness offers the same action as the not-worked-out state, in the
    same polite region as the line that says the figures may be out of date. One press posts one
    request for the caller's own tenant; the answer that follows carries no stale rows, the line and
    the action go, and the region says it finished."""
    _open(page, live_server, stale["emails"]["solution_architect"])
    assert _yield_api(page, live_server)["state"] == "stale"
    region = page.get_by_test_id("derivation-status")
    assert region.get_attribute("role") == "status" and region.get_attribute("aria-live") == "polite"
    assert region.get_by_text(re.compile(r"^Last worked out .+ — may be out of date\.$")).is_visible()
    button = region.get_by_role("button", name="Work them out now")
    assert button.is_visible()
    _shot(page.locator("main"), "out-of-date-with-the-action")

    posts, requests = [], []
    page.on("request", lambda r: requests.append((r.method, r.url)) if "/api/v1/intelligence/" in r.url else None)

    def count_and_go(route, request):
        posts.append(request.post_data_json)
        route.continue_()

    page.route(RECOMPUTE_URL, count_and_go)
    button.click()
    for _ in range(200):
        if _status_text(page) == FINISHED:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == FINISHED
    page.wait_for_timeout(500)

    assert posts == [{"scope": "tenant"}], "one press, one request, for the caller's own tenant"
    assert _yield_api(page, live_server)["state"] == "current"
    assert page.get_by_text(re.compile(r"may be out of date")).count() == 0 or not page.get_by_text(
        re.compile(r"may be out of date")
    ).first.is_visible()
    assert page.get_by_role("img", name="Last worked out", exact=True).count() == 0
    _the_action_is_gone(page, live_server, "working the connections out")
    assert _names(page)["stale"] == "Stale count: 0 relationships"
    assert page.evaluate("document.activeElement && document.activeElement.getAttribute('data-testid')") == "derivation-status"
    figures = [(m, u) for m, u in requests if "/intelligence/yield" in u]
    assert figures == [("GET", live_server + "/api/v1/intelligence/yield?part=figures")]


# --- the recalculation ---------------------------------------------------------------


def test_working_the_connections_out_posts_the_tenant_scope_updates_the_region_and_refreshes_the_cards(
    page, live_server, seeded, never
):
    """Click the button: the POST carries {"scope": "tenant"}; while it is held the region
    reads Recalculating… and the cards are still on the page; released, the page asks its
    question again, the region updates and the cards carry numbers."""
    _open(page, live_server, never["emails"]["solution_architect"])
    posts, held = [], []

    def hold(route, request):
        posts.append(request.post_data_json)
        held.append(route)

    page.route(RECOMPUTE_URL, hold)
    page.get_by_role("button", name="Work them out now").click()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert posts == [{"scope": "tenant"}]
    assert _status_text(page) == RECALCULATING
    assert page.get_by_test_id("derivation-status").get_attribute("aria-live") == "polite"
    assert _figure_row(page).is_visible(), "the cards stay while it runs"
    assert _names(page)["explicit"] == "Explicit facts: Not recorded"
    button = page.get_by_role("button", name="Work them out now")
    assert button.get_attribute("aria-disabled") == "true"

    with page.expect_response(
        lambda r: r.request.method == "GET" and "/api/v1/intelligence/yield" in r.url
    ) as again:
        held[0].continue_()
    assert again.value.status == 200

    for _ in range(100):
        if _status_text(page) != RECALCULATING:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == FINISHED
    assert _names(page)["explicit"] == "Explicit facts: 2 relationships"
    _the_action_is_gone(page, live_server, "the recalculation finished")
    expect(page.get_by_text(NOT_WORKED_OUT)).to_be_hidden()
    assert page.evaluate("document.activeElement && document.activeElement.getAttribute('data-testid')") == "derivation-status"
    data = _yield_api(page, live_server)
    assert data["state"] == "current" and data["explicit_count"] == 2


def test_after_a_completed_recalculation_one_request_is_made_and_the_model_check_row_keeps_its_value(
    page, live_server, seeded, never
):
    """The recalculation writes worked-out connections and nothing the model check reads, so the
    page asks the figures question again and only that: exactly one request, of the figures shape,
    and the row keeps the value it already had."""
    _open(page, live_server, never["emails"]["solution_architect"])
    assert _drift_state(page) == "counted"
    before = page.get_by_test_id("drift-text").inner_text().strip()
    assert re.fullmatch(r"\d+ things? to look at in your model", before), before

    requests = []
    page.on("request", lambda r: requests.append((r.method, r.url)) if "/api/v1/intelligence/" in r.url else None)
    page.get_by_role("button", name="Work them out now").click()
    for _ in range(200):
        if _status_text(page) == FINISHED:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == FINISHED
    page.wait_for_timeout(1000)  # a second question, were one coming, has been sent by now

    base = live_server + "/api/v1/intelligence/"
    assert requests == [
        ("POST", base + "derivation/recompute"),
        ("GET", base + "yield?part=figures"),
    ], requests
    assert _drift_state(page) == "counted"
    assert page.get_by_test_id("drift-text").inner_text().strip() == before
    assert _drift_row(page).get_by_test_id("drift-loading").is_visible() is False


def test_a_recalculation_already_running_updates_the_same_region_and_invents_nothing(
    page, live_server, seeded, never
):
    _open(page, live_server, never["emails"]["solution_architect"])
    posts = []

    def busy(route, request):
        posts.append(request.post_data_json)
        route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"success": False, "error": {"code": "RECOMPUTE_LOCKED", "message": "locked"}}),
        )

    page.route(RECOMPUTE_URL, busy)
    page.get_by_role("button", name="Work them out now").click()
    for _ in range(100):
        if _status_text(page) == BUSY:
            break
        page.wait_for_timeout(50)
    assert posts == [{"scope": "tenant"}]
    assert _status_text(page) == BUSY
    assert page.get_by_test_id("derivation-status").get_attribute("role") == "status"
    assert _names(page)["explicit"] == "Explicit facts: Not recorded", "nothing is filled in"
    assert page.get_by_role("button", name="Work them out now").get_attribute("aria-disabled") == "false"
    assert page.get_by_text(FINISHED).count() == 0


def test_a_recalculation_that_fails_says_it_could_not_answer_and_shows_the_unavailable_badge(
    page, live_server, seeded, never
):
    _open(page, live_server, never["emails"]["solution_architect"])
    page.route(
        RECOMPUTE_URL,
        lambda route, request: route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"success": False, "error": {"code": "RECOMPUTE_FAILED", "message": "no"}}),
        ),
    )
    page.get_by_role("button", name="Work them out now").click()
    for _ in range(100):
        if _status_text(page) == ERROR_LINE:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == ERROR_LINE
    # The badge is drawn with the line; wait for it rather than sampling the page at one instant.
    page.get_by_test_id("derivation-status").get_by_text("Could not measure").wait_for(state="visible")
    assert _names(page)["derived"] == "Worked-out facts: Not recorded"


# --- the model check: a count and one link, and when the count cannot be had ------------------


def test_the_figures_are_readable_and_named_while_the_model_check_is_still_being_asked(
    page, live_server, seeded, derived
):
    """The second question is held. The six cards are already on the page, correctly named, and
    were drawn before the second question was sent; the row beside them shows its loading treatment
    and no number. Released, the row becomes the detector's own count."""
    events, held = [], []

    def hold(route, request):
        held.append(route)

    page.on("request", lambda r: events.append(("asked", r.url)) if "/api/v1/intelligence/yield" in r.url else None)
    page.on("requestfinished", lambda r: events.append(("answered", r.url)) if "/api/v1/intelligence/yield" in r.url else None)
    page.route(MODEL_CHECK_URL, hold)
    _open(page, live_server, derived["emails"]["solution_architect"], check=False)
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the model check was never asked"

    data = _yield_api(page, live_server)
    names = _names(page)
    n, m = data["explicit_count"], data["derived_count"]
    assert names["explicit"] == "Explicit facts: %d %s" % (n, _plural(n, "relationship", "relationships"))
    assert names["derived"] == "Worked-out facts: %d %s" % (m, _plural(m, "relationship", "relationships"))
    assert names["stale"] == "Stale count: 0 relationships"
    assert re.fullmatch(r"Last worked out: .+\d{4}, \d{2}:\d{2}", names["workedOutAt"])
    assert names["response"].startswith("Response time: ")
    assert _figure_row(page).is_visible()

    row = _drift_row(page)
    assert _drift_state(page) == "loading"
    assert row.get_by_test_id("drift-loading").is_visible()
    assert "Loading" in row.get_by_test_id("drift-loading").text_content()
    assert row.get_by_test_id("drift-text").is_visible() is False
    assert row.get_by_role("link", name="See drift findings and fixes").count() == 0
    assert not re.search(r"\d", row.inner_text()), "no number while it is being asked"
    _shot(page.locator("main"), "figures-with-the-model-check-loading")

    # The second question was sent after the first was answered, not alongside it.
    kinds = [(kind, "figures" if "part=figures" in url else "model-check") for kind, url in events]
    assert kinds.index(("answered", "figures")) < kinds.index(("asked", "model-check")), kinds

    held[0].continue_()
    _drift_settled(page)
    expected = _drift_total(derived["org"])
    assert _drift_state(page) == "counted"
    assert row.get_by_test_id("drift-text").inner_text().strip() == "%d %s to look at in your model" % (
        expected, _plural(expected, "thing", "things"))
    assert _names(page) == names, "the cards did not change when the row answered"


def test_the_drift_row_is_the_detectors_count_beside_one_link_and_nothing_else(page, live_server, seeded, derived):
    _open(page, live_server, derived["emails"]["solution_architect"])
    data = _model_check_api(page, live_server)
    expected = _drift_total(derived["org"])
    assert data["drift_finding_count"] == expected
    assert data["element_count"] == 3 and data["reasons"] == []
    assert _yield_api(page, live_server)["drift_finding_count"] is None, "the figures answer never carries it"

    row = page.get_by_test_id("drift-row")
    assert _drift_state(page) == "counted"
    assert row.get_by_text("%d %s to look at in your model" % (expected, _plural(expected, "thing", "things"))).is_visible()
    links = row.locator("a")
    assert links.count() == 1
    assert links.get_attribute("href") == MODEL_HEALTH_PATH
    assert links.inner_text().strip() == "See drift findings and fixes"
    # No list of findings, no severities, no fix control, no second table on this page.
    body = page.locator("main")
    for selector in ("table", "ul", "form", "select", "input"):
        assert body.locator(selector).count() == 0, selector
    # The only ordered list on the page is the breadcrumb trail.
    assert body.locator("ol").count() == body.locator('nav[aria-label="Breadcrumb"] ol').count() == 1
    assert not re.search(r"\b(severity|high|medium|low|fix it|apply)\b", _main_text_without_detail(page), re.I)
    assert page.locator("main a[href^='/genome/']").count() == 1
    _shot(page.locator("main"), "figures-with-the-model-check-counted")


@pytest.mark.parametrize("width", [1440, 390])
def test_a_model_above_the_size_limit_says_so_without_a_findings_action_or_number(
    page, live_server, seeded, large, width
):
    """A real model one element above the limit. The server does not run the detector; the row reads
    the sentence, offers no link, renders no zero, and the sentence is ordinary text in the
    accessibility tree (no badge, no status region)."""
    page.set_viewport_size({"width": width, "height": 900})
    _open(page, live_server, large["emails"]["solution_architect"])
    data = _model_check_api(page, live_server)
    assert data["drift_finding_count"] is None and data["reasons"] == ["model_too_large_for_drift_check"]
    assert data["element_count"] == large["elements"]

    sentence = too_large_sentence(large["elements"])
    row = _drift_row(page)
    assert _drift_state(page) == "too_large"
    assert row.get_by_test_id("drift-text").inner_text().strip() == sentence
    link = row.get_by_role("link", name="See drift findings and fixes")
    assert link.count() == 0
    assert row.locator("a, button, input, select, textarea, [tabindex]").count() == 0
    assert len(_names(page)) == 6
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    text = row.inner_text()
    assert not re.search(r"\bthings? to look at\b", text) and "Could not measure" not in text
    assert DRIFT_UNAVAILABLE not in text

    snapshot = row.aria_snapshot()
    assert "- paragraph: %s" % sentence in snapshot, snapshot
    assert "status" not in snapshot and "Could not measure" not in snapshot, snapshot
    assert "link" not in snapshot
    assert row.locator("[role=status]:visible").count() == 0
    capture = "figures-with-the-model-check-too-large-%dx900" % width
    _shot(page, capture, full_content=True)
    # The expanded capture restores the viewport. Scroll the real row in the
    # ordinary shell, then prove it fits before recording the natural viewport.
    row.scroll_into_view_if_needed()
    expect(row).to_be_in_viewport(ratio=1)
    assert page.viewport_size == {"width": width, "height": 900}
    assert row.evaluate("""el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 &&
            r.right <= window.innerWidth && r.bottom <= window.innerHeight &&
            el.scrollWidth <= el.clientWidth &&
            document.documentElement.scrollWidth <= window.innerWidth;
    }""")
    _shot(page, capture + "-result-viewport", full_page=False)


# Every answer the model check can give, drawn on the page, in exactly the words the row is specified to
# use. Only a real count is ever a number.
DRIFT_CASES = [
    # id, the answer, the row's state, the sentence read
    ("a count", dict(drift_finding_count=7, element_count=12, reasons=[]), "counted",
     "7 things to look at in your model"),
    ("a single thing", dict(drift_finding_count=1, element_count=12, reasons=[]), "counted",
     "1 thing to look at in your model"),
    ("a measured zero", dict(drift_finding_count=0, element_count=12, reasons=[]), "counted",
     "0 things to look at in your model"),
    ("too large, 3,000", dict(drift_finding_count=None, element_count=3000,
                              reasons=["model_too_large_for_drift_check"]), "too_large",
     "Your model has 3,000 elements, so its findings are not counted here."),
    ("too large, 12,000", dict(drift_finding_count=None, element_count=12000,
                               reasons=["model_too_large_for_drift_check"]), "too_large",
     "Your model has 12,000 elements, so its findings are not counted here."),
    ("too large, one element", dict(drift_finding_count=None, element_count=1,
                                    reasons=["model_too_large_for_drift_check"]), "too_large",
     "Your model has 1 element, so its findings are not counted here."),
    ("too large, size missing", dict(drift_finding_count=None, reasons=["model_too_large_for_drift_check"]),
     "too_large", SIZE_UNKNOWN),
    ("too large, size unreadable", dict(drift_finding_count=None, element_count="lots",
                                        reasons=["model_too_large_for_drift_check"]), "too_large", SIZE_UNKNOWN),
    ("could not check", dict(drift_finding_count=None, element_count=12, reasons=["source_unavailable"]),
     "unavailable", DRIFT_UNAVAILABLE),
    ("no count and no reason", dict(drift_finding_count=None, element_count=12, reasons=[]),
     "unavailable", DRIFT_UNAVAILABLE),
    ("a negative count", dict(drift_finding_count=-5, element_count=12, reasons=[]), "unavailable", DRIFT_UNAVAILABLE),
    ("a boolean count", dict(drift_finding_count=True, element_count=12, reasons=[]), "unavailable", DRIFT_UNAVAILABLE),
    ("a count that is text", dict(drift_finding_count="7", element_count=12, reasons=[]), "unavailable", DRIFT_UNAVAILABLE),
    ("a fractional count", dict(drift_finding_count=3.5, element_count=12, reasons=[]), "unavailable", DRIFT_UNAVAILABLE),
    ("no count at all", dict(element_count=12, reasons=[]), "unavailable", DRIFT_UNAVAILABLE),
    ("the figures' reason, not the check's", dict(drift_finding_count=None, reasons=["drift_count_not_requested"]),
     "unavailable", DRIFT_UNAVAILABLE),
]


def test_the_model_check_row_reads_exactly_as_worded_in_every_state(page, live_server, seeded, never):
    _open(page, live_server, never["emails"]["solution_architect"])
    for label, answer, state, sentence in DRIFT_CASES:
        page.unroute(MODEL_CHECK_URL)
        page.route(MODEL_CHECK_URL, _answer(dict(organization_id=1, **answer)))
        page.reload(wait_until="domcontentloaded")
        _ready(page, "workedOutConnections")
        _settled(page)
        _drift_settled(page)
        row = _drift_row(page)
        assert _drift_state(page) == state, label
        assert row.get_by_test_id("drift-loading").is_visible() is False, label
        link = row.get_by_role("link", name="See drift findings and fixes")
        if state == "unavailable":
            assert row.get_by_text(sentence).is_visible(), label
            assert row.get_by_text("Could not measure").is_visible(), label
            assert link.count() == 0, label
            assert not re.search(r"\bthings? to look at\b", row.inner_text()), label
        else:
            assert row.get_by_test_id("drift-text").inner_text().strip() == sentence, label
            if state == "counted":
                assert link.is_visible(), label
            else:
                assert link.count() == 0, label
                assert row.locator("a, button, [tabindex]").count() == 0, label
            assert row.get_by_text("Could not measure").count() == 0 or not row.get_by_text("Could not measure").is_visible()
        if label == "a measured zero":
            _shot(page.locator("main"), "figures-with-the-model-check-zero")
        if label == "too large, 3,000":
            _shot(page.locator("main"), "figures-with-the-model-check-too-large-3000")
        if label == "could not check":
            _shot(page.locator("main"), "figures-with-the-model-check-unavailable")


def test_a_model_check_that_fails_is_could_not_check_never_a_zero_and_never_asked_again(
    page, live_server, seeded, derived
):
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "part=model-check" in r.url else None)
    page.route(MODEL_CHECK_URL, _answer({}, status=500))
    _open(page, live_server, derived["emails"]["solution_architect"])
    row = _drift_row(page)
    assert _drift_state(page) == "unavailable"
    assert row.get_by_text(DRIFT_UNAVAILABLE).is_visible() and row.get_by_text("Could not measure").is_visible()
    assert row.locator("a").count() == 0 or not row.locator("a").first.is_visible()
    assert not re.search(r"\b0 things\b|things to look at", row.inner_text())
    # The figures did not wait for it and did not change.
    assert _names(page)["derived"].startswith("Worked-out facts: ")
    page.wait_for_timeout(500)
    assert len(asked) == 1, "asked once per page load"


def test_a_page_that_cannot_be_answered_says_so_shows_no_figures_and_asks_nothing_more(
    page, live_server, seeded, derived
):
    _open(page, live_server, derived["emails"]["solution_architect"])
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "part=model-check" in r.url else None)
    page.route(FIGURES_URL, _answer({}, status=500))
    page.reload(wait_until="domcontentloaded")
    _ready(page, "workedOutConnections")
    _settled(page)
    error = page.get_by_test_id("page-error")
    assert error.is_visible()
    assert error.get_by_text(ERROR_LINE).is_visible() and error.get_by_text("Could not measure").is_visible()
    assert not _figure_row(page).is_visible()
    assert page.get_by_test_id("drift-row").is_visible() is False
    page.wait_for_timeout(500)
    assert asked == [], "with no figures on the page there is no row to fill, so no second question"


# --- one disclosure, and the detail in it -------------------------------------------------


def test_technical_detail_is_inside_full_detail_and_nowhere_else(page, live_server, seeded, derived):
    _open(page, live_server, derived["emails"]["solution_architect"])
    data = _yield_api(page, live_server)

    outside = _main_text_without_detail(page)
    inside = _detail_text(page)
    assert page.locator("[data-full-detail-toggle]").count() == 1
    assert page.locator("[data-full-detail-region]").count() == 1
    for forbidden in ("details", "summary", "[role=tab]", "[role=tablist]", "[role=dialog]", "[aria-haspopup]"):
        assert page.locator("main " + forbidden).count() == 0, forbidden
    assert page.locator("main [aria-expanded]").count() == 1

    versions = ", ".join(data["engine_version"])
    assert versions and versions in inside and versions not in outside
    stamp = data["computed_at"]
    assert re.search(re.escape(stamp[:19]), inside), (stamp, inside)
    assert stamp[:19] not in outside
    for label in ("Engine version", "Worked out at", "Response time, 95th percentile", "Response time, measured from"):
        assert label in inside and label not in outside
    assert "Response time, exact" not in inside
    # The response time is worded for a reader even here: no series, label or query name anywhere.
    assert MEASURED_FROM in inside and MEASURED_FROM not in outside
    for internal in ("cross_layer_impact", "include_derived", "histogram", "bucket", "Prometheus", "worker", "process"):
        assert internal not in inside and internal not in outside, internal


def test_full_detail_starts_collapsed_and_opens_and_closes_in_place(page, live_server, seeded, derived):
    _open(page, live_server, derived["emails"]["solution_architect"])
    toggle = page.locator("[data-full-detail-toggle]")
    region = page.locator("[data-full-detail-region]")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert not region.is_visible()
    controls = toggle.get_attribute("aria-controls")
    assert controls and page.locator("#%s" % controls).count() == 1
    before = page.url
    toggle.click()
    region.wait_for(state="visible")
    assert toggle.get_attribute("aria-expanded") == "true"
    toggle.click()
    region.wait_for(state="hidden")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.url == before


# --- what a person reads, in every state ---------------------------------------------------

BANNED = re.compile(r"\b(?:yield|health)\b", re.I)
# Status-label words, the same ones the page-copy test in the module's own tests guards.
STATUS_LABELS = re.compile(r"\b(?:VERIFIED|UNVERIFIED|TBC|TODO)\b|(?i:to be confirmed)")
SHAPES = re.compile(
    r"(\bapp/|\bdocs/|\bscripts/|\btests?/|\.py\b|\.html\b|\.js\b|\.json\b|\b[0-9a-f]{7,40}\b|"
    r"\barchie_[a-z_]+|\bintelligence_derivation_runs\b|\barchimate_derived_relationships\b)")


@pytest.mark.parametrize("shape", ["never", "zero", "derived", "stale", "large"])
def test_no_banned_word_status_label_or_path_is_read_in_any_state(shape, request, page, live_server, seeded):
    tenant = request.getfixturevalue(shape)
    _open(page, live_server, tenant["emails"]["solution_architect"])
    text = _main_text_without_detail(page)
    assert text
    assert BANNED.findall(text) == [], text
    assert STATUS_LABELS.findall(text) == [], text
    assert SHAPES.findall(text) == [], text
    # The accessible names are read too.
    names = " ".join(_names(page).values())
    assert BANNED.findall(names) == [] and STATUS_LABELS.findall(names) == []
    assert SHAPES.findall(names) == []


def test_no_banned_word_status_label_or_path_is_read_in_the_worded_states(page, live_server, seeded, never):
    """The wording table's states too: every card, every name and every Full detail row the script can
    write, for the answers above."""
    _open(page, live_server, never["emails"]["solution_architect"])
    texts = []
    for _label, changes, _value_read, _name, _detail in RESPONSE_CASES:
        got = _models(page, _answer_with(**changes))
        texts += [c["name"] for c in got["cards"].values()] + [c["text"] + " " + c["unit"] for c in got["cards"].values()]
        texts += [d["value"] or "" for d in got["details"][2:]] + [d["label"] for d in got["details"]]
    for _label, changes, _expected in MEASURED_FROM_CASES:
        answer = _answer_with(p95_latency_seconds=1.0, sample_count=240, reasons=[])
        answer["p95"] = _p95(**changes)
        texts += [d["value"] or "" for d in _models(page, answer)["details"][2:]]
    assert texts
    joined = " ".join(texts)
    assert BANNED.findall(joined) == [] and STATUS_LABELS.findall(joined) == [] and SHAPES.findall(joined) == [], joined


# --- the keyboard, from Ask to the drift page ------------------------------------------------

RECORD_POINTER = """
(() => {
  const note = (type) => {
    try {
      const seen = JSON.parse(sessionStorage.getItem('__pointer') || '[]');
      seen.push(type);
      sessionStorage.setItem('__pointer', JSON.stringify(seen));
    } catch (e) { /* recording only */ }
  };
  ['pointerdown', 'mousedown', 'mouseup', 'touchstart'].forEach((type) =>
    window.addEventListener(type, () => note(type), true));
})();
"""

DESCRIBE_FOCUS = """
() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getBoundingClientRect();
  const cs = getComputedStyle(a);
  const ring = (cs.boxShadow && cs.boxShadow !== 'none') ||
    (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0);
  const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
  const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
  const top = document.elementFromPoint(cx, cy);
  return {
    tag: a.tagName, id: a.id, testid: a.getAttribute('data-testid'),
    text: (a.innerText || a.value || '').trim().slice(0, 80),
    fullDetail: a.hasAttribute('data-full-detail-toggle'),
    visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none',
    focusVisible: a.matches(':focus-visible'),
    ring: !!ring,
    inViewport: r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth,
    obscured: !(top && (top === a || a.contains(top) || top.contains(a))),
  };
}
"""


def _focus(page):
    return page.evaluate(DESCRIBE_FOCUS)


def _assert_focus_is_visible(state, where):
    assert state is not None, "focus is on nothing at: %s" % where
    assert state["visible"], "focus landed on a hidden element at %s: %r" % (where, state)
    assert state["focusVisible"] and state["ring"], "no visible focus indicator at %s: %r" % (where, state)
    if "skip link" not in where:
        assert state["inViewport"], "focus is outside the window at %s: %r" % (where, state)
    assert not state["obscured"], "focus is covered by something else at %s: %r" % (where, state)


def _press(page, key, where):
    page.keyboard.press(key)
    if key in ("Tab", "Shift+Tab"):
        _assert_focus_is_visible(_focus(page), where)


def _tab_until(page, matches, where, limit=140):
    stops = []
    for _ in range(limit):
        _press(page, "Tab", where)
        state = _focus(page)
        stops.append(state["text"] or state["tag"])
        if matches(state):
            return state, stops
    raise AssertionError("never reached %s by Tab in %d presses; last stop: %r" % (where, limit, _focus(page)))


def test_the_whole_path_from_ask_to_the_drift_page_by_keyboard_alone(page, live_server, seeded, never):
    """Nothing here is a pointer event. From Ask, Tab to the Worked-out connections header
    action and press Enter; on the screen, Tab through it (the figures hold no control, so
    the first stops are the status region's button and the drift link), reach Full detail
    and open it with Enter, go back to the drift link and follow it. Focus is visible at
    every stop, never on a hidden element and never covered; a recorder in the page confirms
    no pointer event fired."""
    _login(page, live_server, never["emails"]["enterprise_architect"])
    page.add_init_script(RECORD_POINTER)
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")
    _dismiss_first_run(page)
    page.evaluate("sessionStorage.removeItem('__pointer')")

    _press(page, "Tab", "the skip link")
    assert _focus(page)["text"] == "Skip to main content"
    page.keyboard.press("Enter")
    _tab_until(page, lambda s: s["text"] == LABEL, "the Worked-out connections header action")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/intelligence/worked-out-connections$"))
    _ready(page, "workedOutConnections")
    _settled(page)
    _drift_settled(page)
    _figure_row(page).wait_for(state="visible")

    _press(page, "Tab", "the skip link on the new page")
    page.keyboard.press("Enter")
    stops = []
    button, seen = _tab_until(page, lambda s: s["text"] == "Work them out now", "the recalculation button")
    stops += seen
    drift_first, seen = _tab_until(page, lambda s: s["text"] == "See drift findings and fixes", "the drift link")
    stops += seen
    detail, seen = _tab_until(page, lambda s: s["fullDetail"], "Full detail")
    stops += seen
    toggle = page.locator("[data-full-detail-toggle]")
    assert toggle.get_attribute("aria-expanded") == "false"
    page.keyboard.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "true"
    page.locator("[data-full-detail-region]").wait_for(state="visible")
    _assert_focus_is_visible(_focus(page), "Full detail after opening it")
    page.keyboard.press("Space")
    assert toggle.get_attribute("aria-expanded") == "false"
    page.keyboard.press("Space")
    assert toggle.get_attribute("aria-expanded") == "true"

    _press(page, "Shift+Tab", "back to the drift link")
    assert _focus(page)["text"] == "See drift findings and fixes"
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/genome/model-health/?$"))
    assert page.locator("h1").inner_text().strip() == "Model Health"
    assert page.evaluate("JSON.parse(sessionStorage.getItem('__pointer') || '[]')") == [], (
        "a pointer event fired during a keyboard-only journey")
    print("[keyboard] stops on the screen: " + " > ".join(stops))


def test_the_recalculation_button_is_operated_from_the_keyboard_and_focus_is_kept(page, live_server, seeded, never):
    _open(page, live_server, never["emails"]["solution_architect"])
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    _tab_until(page, lambda s: s["text"] == "Work them out now", "the recalculation button")
    page.keyboard.press("Enter")
    for _ in range(200):
        if _status_text(page) == FINISHED:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == FINISHED
    _assert_focus_is_visible(_focus(page), "after the button went away")
    assert _focus(page)["testid"] == "derivation-status"


def test_the_action_in_the_out_of_date_state_is_operated_from_the_keyboard_and_focus_is_kept(
    page, live_server, seeded, stale
):
    _open(page, live_server, stale["emails"]["solution_architect"])
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    state, _ = _tab_until(page, lambda s: s["text"] == "Work them out now", "the recalculation button")
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "derivation/recompute" in r.url else None)
    page.keyboard.press("Enter")
    for _ in range(200):
        if _status_text(page) == FINISHED:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == FINISHED
    assert len(posts) == 1
    _assert_focus_is_visible(_focus(page), "after the button went away")
    assert _focus(page)["testid"] == "derivation-status"


# --- target size ---------------------------------------------------------------------------------


def _measure(locator):
    box = locator.bounding_box()
    assert box is not None, "control is not rendered"
    return {"width": round(box["width"], 1), "height": round(box["height"], 1), "x": box["x"], "y": box["y"]}


def _record(measurements):
    folder = os.environ.get("T005_EVIDENCE_DIR")
    print("[target-size] " + json.dumps(measurements, sort_keys=True))
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "target-size.json"), "w", encoding="utf-8") as fh:
            json.dump(measurements, fh, indent=2, sort_keys=True)


def test_every_control_is_at_least_24_by_24_css_pixels_and_none_relies_on_spacing(page, live_server, seeded, never, stale):
    """Measured in the browser on the rendered controls, and recorded: the recalculation
    button (in both the states that offer it), the Full detail expander, the drift link and the
    header action on Ask and on Twin map. Each meets the size itself."""
    _login(page, live_server, never["emails"]["solution_architect"])
    measurements = {}
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")
    measurements["Header action (Ask)"] = _measure(page.get_by_role("link", name=LABEL, exact=True))
    page.goto(live_server + "/intelligence/twin-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "twinMapSurface")
    measurements["Header action (Twin map)"] = _measure(page.get_by_role("link", name=LABEL, exact=True))
    page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "workedOutConnections")
    _settled(page)
    _drift_settled(page)
    _figure_row(page).wait_for(state="visible")
    measurements["Work them out now"] = _measure(page.get_by_role("button", name="Work them out now"))
    measurements["Full detail"] = _measure(page.locator("[data-full-detail-toggle]"))
    measurements["See drift findings and fixes"] = _measure(page.get_by_role("link", name="See drift findings and fixes"))
    _open(page, live_server, stale["emails"]["solution_architect"])
    measurements["Work them out now (out of date)"] = _measure(page.get_by_role("button", name="Work them out now"))
    _record(measurements)

    small = {n: m for n, m in measurements.items() if m["width"] < MIN_TARGET or m["height"] < MIN_TARGET}
    assert {"Work them out now", "Full detail", "See drift findings and fixes"} <= set(measurements)
    assert not small, "controls under %dx%d CSS pixels: %r" % (MIN_TARGET, MIN_TARGET, small)


# --- the sidebar ------------------------------------------------------------------------------------


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect", "platform_admin", "cto"])
def test_the_screen_has_no_sidebar_link_for_any_persona(persona, page, live_server, seeded):
    _login(page, live_server, seeded["emails"][persona])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    sidebar = page.get_by_test_id("sidebar")
    assert sidebar.locator("a[href='%s']" % PATH).count() == 0
    assert sidebar.get_by_text(LABEL).count() == 0


# --- every state of the screen under the audit's rules ---------------------------------------------------


def _axe_the_page(label, page, records):
    """Run axe on the page as it is now, under the audit's own tag set, and keep what
    it found and whether the 2.2 target-size rule was evaluated at all."""
    axe = importlib.import_module("axe_playwright_python.sync_playwright").Axe()
    report = axe.run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    records[label] = {
        "violations": {
            v["id"]: [n.get("target") for n in v.get("nodes", [])] for v in data.get("violations", [])
        },
        "target_size": {
            kind: sum(len(r.get("nodes") or []) for r in data.get(kind, []) if r["id"] == "target-size")
            for kind in RESULT_KINDS
        },
        "target_size_evaluated": any(
            r["id"] == "target-size" for kind in RESULT_KINDS for r in data.get(kind, [])
        ),
        "axe_core": (data.get("testEngine") or {}).get("version"),
    }


def test_every_state_of_the_screen_has_no_violations_under_the_audits_rules(
    page, live_server, seeded, never, zero, derived, stale, large
):
    """The page in each state a person can meet it, audited with the same tag set as the
    plain-page audit (WCAG 2.2 AA, which adds target size): not worked out, recalculating,
    a recalculation already running, a run that found nothing, worked out, worked out with
    Full detail open, out of date (and its action, with the pointer on it), the model check
    still being asked, a model too large to count, the model check unavailable, the response
    time measured and above the range, and the page that could not be answered; and each control
    with the pointer resting on it. Zero violations in each, none recorded anywhere, and the
    target-size rule evaluated on every one."""
    records = {}

    _open(page, live_server, never["emails"]["solution_architect"])
    _axe_the_page("not worked out", page, records)
    page.locator("[data-full-detail-toggle]").click()
    page.locator("[data-full-detail-region]").wait_for(state="visible")
    _axe_the_page("not worked out, Full detail open", page, records)
    page.get_by_role("button", name="Work them out now").hover()
    _axe_the_page("the recalculation button hovered", page, records)

    held = []
    page.route(RECOMPUTE_URL, lambda route, request: held.append(route))
    page.get_by_role("button", name="Work them out now").click()
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert _status_text(page) == RECALCULATING
    _axe_the_page("recalculating", page, records)
    held[0].abort()
    page.unroute(RECOMPUTE_URL)
    page.route(
        RECOMPUTE_URL,
        lambda route, request: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"success": False, "error": {"code": "RECOMPUTE_LOCKED", "message": "locked"}}),
        ),
    )
    for _ in range(100):
        if _status_text(page) == ERROR_LINE:
            break
        page.wait_for_timeout(50)
    page.get_by_role("button", name="Work them out now").click()
    for _ in range(100):
        if _status_text(page) == BUSY:
            break
        page.wait_for_timeout(50)
    _axe_the_page("a recalculation is already running", page, records)

    _open(page, live_server, zero["emails"]["solution_architect"])
    _axe_the_page("a run that found nothing to add", page, records)

    _open(page, live_server, derived["emails"]["solution_architect"])
    _axe_the_page("worked out", page, records)
    page.locator("[data-full-detail-toggle]").click()
    page.locator("[data-full-detail-region]").wait_for(state="visible")
    _axe_the_page("worked out, Full detail open", page, records)
    page.get_by_role("link", name="See drift findings and fixes").hover()
    _axe_the_page("the drift link hovered", page, records)
    page.locator("[data-full-detail-toggle]").hover()
    _axe_the_page("Full detail hovered", page, records)

    for label, change in (
        ("the response time measured", dict(p95_latency_seconds=1.0, sample_count=240, reasons=[])),
        ("the response time above the range", RESPONSE_CASES[5][1]),
    ):
        page.unroute(FIGURES_URL)
        page.route(FIGURES_URL, _answer(_answer_with(**change)))
        page.reload(wait_until="domcontentloaded")
        _ready(page, "workedOutConnections")
        _settled(page)
        _drift_settled(page)
        _axe_the_page(label, page, records)
    page.unroute(FIGURES_URL)

    held_check = []
    page.route(MODEL_CHECK_URL, lambda route, request: held_check.append(route))
    page.reload(wait_until="domcontentloaded")
    _ready(page, "workedOutConnections")
    _settled(page)
    for _ in range(100):
        if held_check:
            break
        page.wait_for_timeout(50)
    assert _drift_state(page) == "loading"
    _axe_the_page("the model check is still being asked", page, records)
    held_check[0].abort()
    page.unroute(MODEL_CHECK_URL)

    page.route(
        MODEL_CHECK_URL,
        _answer(dict(organization_id=1, drift_finding_count=None, element_count=3000, reasons=["source_unavailable"])),
    )
    page.reload(wait_until="domcontentloaded")
    _ready(page, "workedOutConnections")
    _settled(page)
    _drift_settled(page)
    _axe_the_page("the model check unavailable", page, records)
    page.unroute(MODEL_CHECK_URL)
    page.route(FIGURES_URL, _answer({}, status=500))
    page.reload(wait_until="domcontentloaded")
    _ready(page, "workedOutConnections")
    _settled(page)
    _axe_the_page("the page could not be answered", page, records)
    page.unroute(FIGURES_URL)

    _open(page, live_server, large["emails"]["solution_architect"])
    _axe_the_page("the model is too large to count", page, records)

    _open(page, live_server, stale["emails"]["solution_architect"])
    _axe_the_page("out of date", page, records)
    page.get_by_role("button", name="Work them out now").hover()
    _axe_the_page("out of date, the action hovered", page, records)

    folder = os.environ.get("T005_EVIDENCE_DIR")
    print("[axe] " + json.dumps(records, sort_keys=True))
    if folder:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "axe-states.json"), "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, sort_keys=True)

    assert len(records) == 18
    for label, found in sorted(records.items()):
        assert found["violations"] == {}, "%s: %r" % (label, found["violations"])
        assert found["target_size_evaluated"], "the target-size rule did not run on %r" % label


# --- the response time above the floor, from the real record ------------------------------------------


@pytest.fixture(scope="module")
def one_process_server(request, ai_protocol_stub, app):
    """A second copy of the app, served by exactly ONE process.

    The response time is read from the metrics record of whichever web process answers, and the
    shared test server runs several (two workers where gunicorn is available), each with a record
    of its own; a request can reach a different process from the one that saw the questions asked.
    A server of one process makes "the process that answered" a single, known process, so a test
    that fills the record and then reads it back is exact rather than a matter of luck. It uses the
    same database, environment and start-up as the shared server, with one worker."""
    yield from _serve_application(request, ai_protocol_stub, app, workers=1)


def test_above_the_floor_the_card_reads_a_figure_with_the_count_it_came_from(page, one_process_server):
    """Ask the pinned question exactly 120 times (a whole-model, four-hop, worked-out impact query),
    then read the screen: the response time is a number in seconds, from 120 measurements, and its
    name says both.

    It runs on a server of one process, so the record the page reads is the record the questions
    were counted in: the count the answer reports must equal the number of questions asked, which
    fails loudly if more than one process is answering."""
    tenant = _make_tenant("derived")
    base = one_process_server
    _open(page, base, tenant["emails"]["solution_architect"])
    before = _yield_api(page, base)
    assert before["sample_count"] == 0 and "insufficient_samples_for_p95" in before["reasons"], before

    asked = 120
    url = "%s/api/v1/intelligence/impact/%s?include_derived=true&max_depth=4" % (base, tenant["first"])
    for _ in range(asked):
        assert page.context.request.get(url).status == 200
    data = _yield_api(page, base)
    assert data["sample_count"] == asked, (
        "%d questions were asked and the record holds %d: the server did not answer from one process"
        % (asked, data["sample_count"])
    )
    assert data["p95_latency_seconds"] is not None or "p95_above_highest_bucket" in data["reasons"]
    assert "insufficient_samples_for_p95" not in data["reasons"]

    page.reload(wait_until="domcontentloaded")
    _ready(page, "workedOutConnections")
    _settled(page)
    name = _names(page)["response"]
    assert re.fullmatch(
        r"Response time: (Within [\d.]+ seconds? for 95|Over [\d.]+ seconds? for more than 5) in 100 impact "
        r"questions, from \d+ recent measurements on this server",
        name,
    ), name
    assert NOT_ENOUGH not in name
    assert int(re.search(r"from (\d+) recent measurements", name).group(1)) == asked, name
    assert _yield_api(page, base)["sample_count"] == asked, "reading the screen asked no further question"
    page.locator("[data-full-detail-toggle]").click()
    page.locator("[data-full-detail-region]").wait_for(state="visible")
    rows = {
        r.locator("dt").inner_text().strip(): r.locator("dd").inner_text().strip()
        for r in page.locator("[data-detail-row]").all()
    }
    assert rows["Response time, measured from"] == MEASURED_FROM
    assert re.match(r"(\d[\d.]* seconds?, the top of the time range within which 95 in 100 questions were answered"
                    r"|Over 5 seconds: fewer than 95 in 100 questions were answered within 5 seconds, "
                    r"the longest time range measured)$", rows["Response time, 95th percentile"]), rows
    _shot(_figure_row(page), "card-response-time-measured-live")
