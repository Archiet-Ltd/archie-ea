"""Browser journey for /ai-chat.

The page had no browser coverage at all — no smoke journey, no authorisation row,
no entry in the a11y baseline — while carrying roughly 2,500 lines of inline
JavaScript. That is how a dead stop button, a composer handoff writing a key
nobody reads, and a sidebar tab that never hid its own panel all shipped
unnoticed: nothing ever loaded the page in a browser and looked.

This file is the instrument the client rebuild is verified against, so its
assertions are deliberately about **transport and DOM**, never about what the
model says. The LLM provider is unconfigured in CI — `/ai-chat/models` returns an
empty list there — and a test that needs a working provider is a test that fails
for reasons unrelated to the code under change, which is how browser suites get
switched off.

Concretely, each assertion is a defect class the rebuild can reintroduce silently:

    page boots          2,500 lines move into modules; one dangling reference
                        and the whole front end stops at the first exception
    console clean       an Alpine expression error is a warning in the console
                        and nothing at all in an HTTP 200
    panels toggle       'history' was missing from switchSidebarTab's list, so
                        picking another tab left the Chats panel on screen
    composer sends      Enter was bound to nothing for a long time; the only way
                        to send was the button, which is also a keyboard trap
    request is issued   the point of the composer is that a request leaves the
                        browser — assert that, not the answer that comes back
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The four sidebar panels. Contractual ids: the rebuild moves the code that
# drives them out of the template, but these are what the markup promises.
TABS = ("context", "query", "alerts")

# Console noise that is not the page's doing. Kept deliberately short — every
# entry added here is a class of error this test stops seeing.
IGNORED_CONSOLE = (
    "favicon",
    # GET /ai-chat/api/health/llm answers 503 by design when no LLM provider is
    # configured (legacy_compat.py:169-179), which is the state of any CI box
    # without API keys. The browser logs every failed resource load as a console
    # error, so this is environmental, not a defect.
    #
    # It is not simply swallowed: test_the_degraded_provider_banner_is_shown
    # asserts the UI surfaces that 503 to the user, so the condition is proven
    # handled rather than hidden.
    "503 (service unavailable)",
)


def _login(page, base, email):
    """Sign in the way a user does. Mirrors test_accessibility_audit.py.

    The login form is Alpine-driven: @submit disables the button, so Playwright's
    actionability check blocks an ordinary click. Force-clicking the real control
    and then waiting for the URL to stop being the login page is robust to that
    and to the navigation race, while still exercising the button a user presses.
    """
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:                     # newer Playwright dropped the kwarg
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.fixture(scope="module")
def authed_context(browser, live_server, seeded):
    """One signed-in browser context for the whole module.

    /account/login is rate-limited to 10 requests per minute. The smoke suite as
    a whole already sits near that ceiling, so five more logins — one per test in
    this file — pushed it over and refused a later archetype's sign-in with
    "Rate limit exceeded". The archetype that lost varied with timing, which is
    what made it look like a race.

    Signing in once and issuing each test its own page from the same context
    keeps the cookie and costs one login instead of five.
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    login_page = ctx.new_page()
    _login(login_page, live_server, seeded["emails"]["enterprise_architect"])
    login_page.close()
    yield ctx
    ctx.close()


@pytest.fixture
def page(authed_context):
    """A page whose JavaScript errors are recorded from the first navigation.

    The listeners are attached before any goto, which is the whole point: an
    exception thrown while the inline scripts are still evaluating happens long
    before any assertion could run, and is invisible afterwards.

    Both channels are collected because they carry different failures.
    `console` gets explicit console.error calls and Alpine's expression errors;
    `pageerror` gets uncaught exceptions — the ReferenceError a half-extracted
    module produces — which Playwright does not deliver as a console event.
    """
    pg = authed_context.new_page()
    pg.console_errors = []
    pg.page_errors = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.page_errors.append(str(e)))
    yield pg
    pg.close()


# Errors whose stack names another document. Clearing the lists after login is
# not enough on its own: an Alpine x-intersect handler on /dashboard/overview
# (the login redirect target) starts a fetch that rejects *after* we have
# navigated away, so it lands in this page's bucket with the dashboard's stack
# still attached. Filtering on provenance rather than timing keeps the assertion
# about /ai-chat without blinding it to anything /ai-chat does.
#
# `layered diagram fetch failed` is a real defect — on /dashboard/overview, which
# this branch does not touch. Recorded in docs/known-issues, not silenced here.
FOREIGN_DOCUMENTS = ("/dashboard/overview",)


def _js_errors(page):
    """Every JavaScript error this page produced, filtered for noise and origin."""
    return [
        err for err in (page.console_errors + page.page_errors)
        if not any(ignored in err.lower() for ignored in IGNORED_CONSOLE)
        and not any(doc in err for doc in FOREIGN_DOCUMENTS)
    ]


def _open_chat(page, live_server, seeded):
    """Land on a settled /ai-chat. The context is already signed in."""
    # Errors carried over from any earlier navigation in this context belong to
    # that page, not this one. The listeners are attached at page creation and
    # never reset themselves, so scope the assertion here.
    page.console_errors.clear()
    page.page_errors.clear()
    response = page.goto(live_server + "/ai-chat", wait_until="domcontentloaded",
                         timeout=PAGE_TIMEOUT)
    status = response.status if response else 0
    assert status < 400, "/ai-chat -> HTTP %d\n%s" % (status, live_server.tail())
    # The inline scripts wire everything on DOMContentLoaded and then fetch the
    # model list and the context panel; give those a moment to fail if they mean to.
    page.wait_for_timeout(1500)
    try:
        # The first-run overlay covers the viewport and would make every
        # subsequent interaction fail for reasons unrelated to this page.
        page.eval_on_selector_all(
            "[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass
    return response


def _visible_panels(page):
    return [t for t in TABS if page.locator("#panel-%s" % t).is_visible()]


def test_ai_chat_renders_and_the_front_end_boots(page, live_server, seeded):
    """The page loads, Alpine is alive, and the composer is on screen.

    `window.Alpine` being an object is the cheapest proof the front end got
    further than its first script tag — an integrity-blocked or exception-halted
    bundle leaves it undefined while the server still happily returns 200.
    """
    _open_chat(page, live_server, seeded)

    assert page.evaluate("() => typeof window.Alpine") == "object", \
        "the front end did not boot — window.Alpine is not an object"
    assert page.locator("#chat-form").count() == 1, "the composer form is not in the page"
    assert page.locator("#user-input").is_visible(), "the composer textarea is not visible"
    assert page.locator("#messages-container").count() == 1, "there is no transcript container"

    errors = _js_errors(page)
    assert not errors, (
        "%d JavaScript error(s) on /ai-chat:\n  - %s\n\n"
        "The page renders regardless, which is why this went unnoticed: an "
        "exception during boot stops every listener registered after it, and "
        "the server still returns 200."
        % (len(errors), "\n  - ".join(errors[:8])))


def test_persona_storage_denial_keeps_chat_initialized(page, live_server, seeded):
    """Storage is optional: an enterprise-architect chat still boots without it.

    Only the persona preference key is denied so this journey isolates chat's
    storage contract from unrelated shell preferences. Both browser methods
    throw the same SecurityError private browsing produces.
    """
    page.add_init_script("""
        (() => {
            const originalGetItem = Storage.prototype.getItem;
            const originalSetItem = Storage.prototype.setItem;
            const denied = key => String(key).startsWith('archie_chat_persona_v2');
            Storage.prototype.getItem = function(key) {
                if (denied(key)) throw new DOMException('Storage denied', 'SecurityError');
                return originalGetItem.call(this, key);
            };
            Storage.prototype.setItem = function(key, value) {
                if (denied(key)) throw new DOMException('Storage denied', 'SecurityError');
                return originalSetItem.call(this, key, value);
            };
        })()
    """)

    _open_chat(page, live_server, seeded)
    page.get_by_role("button", name="Chat options").click()

    persona = page.locator("#persona-selector")
    assert persona.input_value() == "enterprise_architect", (
        "a denied local preference must fall back to the signed-in role default"
    )
    assert page.locator("#domain-selector").input_value() == "architecture", (
        "the role default must still apply its configured domain in memory"
    )

    persona.select_option("data_architect")
    assert persona.input_value() == "data_architect", (
        "a denied preference write must not undo the persona chosen for this visit"
    )
    assert page.locator("#domain-selector").input_value() == "data_architecture", (
        "a manual persona switch must still update its configured domain in memory"
    )
    assert not _js_errors(page), _js_errors(page)


def test_persona_switch_system_message_is_not_clipped(page, live_server, seeded):
    """The "Persona switched to: ..." notice must land fully inside the
    scrollable transcript, not straddle its bottom edge.

    Found 11 Sep 2026 during a visual-design pass on production: switching
    persona appends a system-message pill to #messages-container and calls
    scrollToBottom(), but scrollToBottom() ran *before* lucide.createIcons()
    swapped the pill's <i data-lucide> placeholder for a real inline <svg> --
    which grows the pill's height enough that the scroll position captured a
    moment earlier falls short of the true bottom. The result: the pill's
    lower ~60% renders clipped by the container's own overflow, with only a
    thin, barely-readable sliver of its top edge visible above the always-
    below the composer. Fixed in render.js's scrollToBottom() by
    re-applying the scroll on the next animation frame as well.
    """
    _open_chat(page, live_server, seeded)

    page.get_by_role("button", name="Chat options").click()
    page.locator("#persona-selector").select_option("data_architect")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)  # scrollToBottom's rAF follow-up

    pill = page.locator("#messages-container div", has_text="Persona switched to:").last
    assert pill.count() > 0, "no persona-switch system message was appended"

    box = pill.bounding_box()
    container_box = page.locator("#messages-container").bounding_box()
    assert box is not None and container_box is not None
    container_bottom = container_box["y"] + container_box["height"]
    assert box["y"] + box["height"] <= container_bottom + 1, (
        "the persona-switch message extends %.1fpx past the scrollable "
        "container's bottom edge (message bottom=%.1f, container bottom=%.1f) "
        "-- it renders visually clipped instead of fully scrolled into view"
        % (box["y"] + box["height"] - container_bottom, box["y"] + box["height"], container_bottom)
    )
    assert not _js_errors(page), _js_errors(page)


def test_deep_link_context_notice_is_not_pushed_below_the_greeting(page, live_server, seeded):
    """A deep-link arrival (?element_id=&context_type=&domain=) must not bury its own
    context notice hundreds of px below the fold behind the full suggestion-card grid.

    Companion to test_persona_switch_system_message_is_not_clipped above, and to the
    scrollTop assertion in tests/journeys/test_journey_ai_chat_opens_at_greeting.py::
    test_a_deep_link_context_notice_does_not_scroll_the_pane_past_the_greeting -- this
    is the real-server browser leg for the same fix. Two parts: appendSystemMessage's
    opts.noScroll keeps the pane at scrollTop 0, and app.js's _hideWelcomeSuggestions()
    collapses the ~1000px persona/domain suggestion grid (index.html's new
    #domain-welcome-suggestions wrapper) while leaving the greeting heading itself
    visible, so the notice lands inside the pane instead of below the collapsed grid.
    """
    page.console_errors.clear()
    page.page_errors.clear()
    response = page.goto(
        live_server + "/ai-chat?element_id=7&context_type=vendor&domain=vendor_intelligence",
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    status = response.status if response else 0
    assert status < 400, "/ai-chat (deep link) -> HTTP %d\n%s" % (status, live_server.tail())
    page.wait_for_timeout(1500)

    container = page.locator("#messages-container")
    assert container.evaluate("el => el.scrollTop") == 0, (
        "the pane scrolled past the greeting on a deep-link load"
    )
    suggestions = page.locator("#domain-welcome-suggestions")
    assert suggestions.count() == 0 or not suggestions.first.is_visible(), (
        "the persona/domain suggestion grid is still visible on a deep-link load"
    )

    notice = page.locator("#messages-container div", has_text="Vendor context loaded").last
    assert notice.count() > 0, "no deep-link context notice was appended"
    box = notice.bounding_box()
    container_box = container.bounding_box()
    assert box is not None and container_box is not None
    container_bottom = container_box["y"] + container_box["height"]
    assert box["y"] + box["height"] <= container_bottom + 1, (
        "the deep-link notice extends %.1fpx past the pane's bottom edge (notice "
        "bottom=%.1f, pane bottom=%.1f)"
        % (box["y"] + box["height"] - container_bottom, box["y"] + box["height"], container_bottom)
    )
    assert not _js_errors(page), _js_errors(page)


def test_approval_modal_distinguishes_loading_failure_stale_and_retry(page, live_server, seeded):
    """Reviewers never mistake a failed approval fetch for an empty queue."""
    state_timeout = 10_000
    page.add_init_script("""
        window.__approvalQueueMode = "pending";
        window.__approvalQueueReject = null;
        const nativeFetch = window.fetch.bind(window);
        window.fetch = function(input, init) {
            const url = typeof input === "string" ? input : input.url;
            if (!url.includes("/ai-chat/approvals/queue")) {
                return nativeFetch(input, init);
            }
            if (window.__approvalQueueMode === "pending") {
                return new Promise((resolve, reject) => {
                    window.__approvalQueueReject = reject;
                });
            }
            if (window.__approvalQueueMode === "failure") {
                return Promise.reject(new TypeError("approval queue offline"));
            }
            const approvals = window.__approvalQueueMode === "populated" ? [{
                id: 991,
                operation_type: "create",
                entity_type: "capability",
                summary: "Stale approval evidence",
                arguments: { name: "Stale capability" },
                created_at: "2026-08-21T12:00:00",
                expires_at: "2026-08-21T12:05:00",
                requester: { id: 99, display_name: "Queue Requester" },
            }] : [];
            return Promise.resolve(new Response(
                JSON.stringify({ success: true, approvals }),
                { status: 200, headers: { "Content-Type": "application/json" } }
            ));
        };
    """)

    _open_chat(page, live_server, seeded)
    # This journey exercises queue-state rendering, not the separately-covered
    # overflow-menu mechanics. Trigger the real modal opener directly so a
    # hidden menu's transition cannot consume the test's whole action timeout.
    page.evaluate("() => window.Platform.modal.open('ai-chat-approvals-modal')")
    page.locator("#ai-chat-approvals-modal").wait_for(
        state="visible", timeout=state_timeout
    )
    page.wait_for_function("() => typeof window.__approvalQueueReject === 'function'")

    unavailable = page.get_by_test_id("approval-queue-unavailable")
    empty = page.get_by_test_id("approval-queue-empty")
    assert page.evaluate("() => window.Alpine.store('approvals').loading") is True
    assert not unavailable.is_visible()
    assert not empty.is_visible()

    page.evaluate("() => window.__approvalQueueReject(new TypeError('approval queue offline'))")
    unavailable.wait_for(state="visible", timeout=state_timeout)
    assert "unavailable" in unavailable.inner_text().lower()
    assert not empty.is_visible(), "a failed first load must not look empty"

    page.evaluate("() => { window.__approvalQueueMode = 'populated'; }")
    page.get_by_role("button", name="Retry approval queue").click()
    page.wait_for_function(
        "() => window.Alpine.store('approvals').approvals.length === 1",
        timeout=state_timeout,
    )
    page.get_by_text("Stale approval evidence").wait_for(state="visible", timeout=state_timeout)
    assert not unavailable.is_visible()

    page.evaluate("() => { window.__approvalQueueMode = 'failure'; }")
    page.get_by_role("button", name="Retry approval queue").click()
    unavailable.wait_for(state="visible", timeout=state_timeout)
    assert "last known results" in unavailable.inner_text().lower()
    assert page.get_by_text("Stale approval evidence").is_visible(), (
        "a refresh failure must retain the last proven queue rows as explicitly stale"
    )

    page.evaluate("() => { window.__approvalQueueMode = 'empty'; }")
    page.get_by_role("button", name="Retry approval queue").click()
    empty.wait_for(state="visible", timeout=state_timeout)
    unavailable.wait_for(state="hidden", timeout=state_timeout)


def test_exactly_one_sidebar_panel_is_visible_at_a_time(page, live_server, seeded):
    """Each tab shows its own panel and hides the other three.

    Not a tautology: 'history' was absent from the list switchSidebarTab hides,
    so choosing Context or Query left the Chats panel stacked on top of it. The
    assertion is on the whole set, not on the chosen panel, because that is the
    half a per-tab check misses.
    """
    _open_chat(page, live_server, seeded)
    page.get_by_role("button", name="Chat options").click()
    page.wait_for_timeout(500)

    assert _visible_panels(page) == ["context"], (
        "the sidebar does not open on Context alone, it shows %s" % _visible_panels(page))

    for tab in TABS:
        page.click("#tab-%s" % tab)
        page.wait_for_timeout(300)
        visible = _visible_panels(page)
        assert visible == [tab], (
            "clicked #tab-%s and the visible panels were %s — a panel that does "
            "not hide overlays the one the user asked for" % (tab, visible))

    errors = _js_errors(page)
    assert not errors, "%d JavaScript error(s) while switching tabs:\n  - %s" % (
        len(errors), "\n  - ".join(errors[:8]))


def test_pressing_enter_sends_the_message_to_the_server(page, live_server, seeded):
    """Enter submits, the turn appears in the transcript, and a request leaves.

    Three separate things, all of which have been broken here at some point and
    none of which needs the model to answer:

      * Enter is bound. There were zero keydown listeners in this file for most
        of its life, so the composer could not be submitted from the keyboard at
        all — a WCAG failure as much as a usability one.
      * The user's own turn is rendered locally, before any response. That is
        DOM, not model output, so it holds with no provider configured.
      * A POST actually reaches /ai-chat/message or /ai-chat/message/stream.
        expect_request resolves when the request is issued, so this says nothing
        about whether an LLM was reachable — which is exactly what we want.
    """
    _open_chat(page, live_server, seeded)

    probe = "Smoke journey probe %s" % uuid.uuid4().hex[:8]
    page.fill("#user-input", probe)

    with page.expect_request(
        lambda r: "/ai-chat/message" in r.url and r.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as request_info:
        page.press("#user-input", "Enter")

    assert request_info.value, "no POST to /ai-chat/message* was issued"

    # The composer clears on send; leaving the text behind duplicates the turn
    # on the next Enter.
    assert page.input_value("#user-input") == "", "the composer did not clear on send"

    transcript = page.locator("#messages-container")
    transcript.locator(".message-content", has_text=probe).first.wait_for(
        state="visible", timeout=PAGE_TIMEOUT)

    errors = _js_errors(page)
    assert not errors, (
        "%d JavaScript error(s) while sending a message:\n  - %s\n\n"
        "A failing provider must render an error in the transcript, not throw."
        % (len(errors), "\n  - ".join(errors[:8])))


def test_the_degraded_provider_banner_is_shown(page, live_server, seeded):
    """A 503 from the health probe must reach the user, not just the console.

    IGNORED_CONSOLE tolerates that 503 because a CI box has no LLM keys. This
    test is the other half of that bargain: the condition is only ignorable
    because the UI is proven to surface it.
    """
    _open_chat(page, live_server, seeded)

    # The banner polls on init and every 60s; it is x-show'd off while healthy.
    page.wait_for_timeout(1500)
    banner = page.locator("[role=alert]", has_text="AI provider")
    assert banner.count() >= 1, (
        "No LLM provider is configured, /ai-chat/api/health/llm answered 503, and "
        "the user was told nothing. A silent degraded assistant is worse than an "
        "absent one: answers still appear, and nothing says they are unavailable."
    )


def test_opening_a_past_conversation_renders_its_messages(page, live_server, seeded):
    """Clicking a conversation in the rail must put its turns on screen.

    This is the regression test for a real defect. loadSession() guarded its
    render loop with `typeof appendMessage === 'function'` — true while the code
    lived in a classic <script> that hoisted the function onto window, always
    false once it moved into a module IIFE. The loop rendered nothing, inside a
    bare catch, so the pane went blank with no error.

    And blank was the better half: __threadId had already switched, so the next
    message continued a conversation the user could not see.

    Nothing caught it because no test called loadSession — the tab-switching test
    clicks the History tab, which only lists. The thread payload is stubbed so
    this asserts the client render path without needing a configured LLM.
    """
    _open_chat(page, live_server, seeded)

    marker_user = "probe-question-%s" % uuid.uuid4().hex[:8]
    marker_ai = "probe-answer-%s" % uuid.uuid4().hex[:8]
    page.route(
        "**/ai-chat/threads/stub-thread",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"messages": [{"role": "user", "content": "%s"},'
                 ' {"role": "assistant", "content": "%s"}]}' % (marker_user, marker_ai),
        ),
    )

    page.evaluate("() => window.loadSession('stub-thread')")
    page.wait_for_timeout(1200)

    body = page.locator("#messages-container").inner_text()
    assert marker_user in body, (
        "the user's turn from the stored conversation did not render — the "
        "transcript is blank while __threadId has already switched to it"
    )
    assert marker_ai in body, "the assistant's turn from the stored conversation did not render"
    assert not _js_errors(page), _js_errors(page)
