"""Real Platform modal/core in three engines, with a nested app-shell opener.

Only HTML fixture and asset transport are local test boundaries. No app, login,
database, network API, native confirmation, or production data is involved.
"""

from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://modal-focus.test"
HTML = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/css/tailwind-output.css">
<script src="/static/vendor/purify.min.js"></script>
<script src="/static/js/bundles/core-admin.js"></script>
<script src="/static/js/ui/modal.js"></script></head><body>
<div id="shell"><main><input id="prior" aria-label="Previous field">
<button id="trigger" type="button"><span id="trigger-icon" aria-hidden="true">×</span>Delete Entity</button>
<button id="parent-trigger" type="button">Open parent</button>
<button id="unrelated" type="button">Unrelated action</button>
<button id="async-trigger" type="button">Open asynchronously</button>
<button id="observed-trigger" type="button">Open observed</button></main></div>
<div id="observed" role="dialog" aria-label="Observed" hidden>
<button id="replace-observed" type="button">Replace observed</button></div>
<script>
window.focusAttempts = [];
const originalFocus = HTMLElement.prototype.focus;
HTMLElement.prototype.focus = function(...args) {
  window.focusAttempts.push({id:this.id, inert:!!this.closest('[inert]'), connected:this.isConnected});
  return originalFocus.apply(this,args);
};
document.getElementById('trigger').addEventListener('click', function() {
  window.activeAtOpen = document.activeElement.id;
  Platform.modal.confirm('Delete this fixture?', {returnFocus:this}).then(result => {window.result = result;});
});
document.getElementById('parent-trigger').addEventListener('click', function() {
  Platform.modal.create({id:'parent',title:'Parent',content:'<p>Parent dialog</p>',buttons:[
    {label:'Open child',handler:function(){Platform.modal.confirm('Child confirmation', {
      returnFocus:document.querySelector('#parent [data-modal-btn="0"]')
    });},closeOnClick:false},
    {label:'Close parent',resolve:false}
  ]});
  Platform.modal.open('parent', undefined, {returnFocus:this});
});
document.getElementById('async-trigger').addEventListener('click', function() {
  Promise.resolve().then(function() {
    document.getElementById('prior').focus();
    Platform.modal.confirm('Asynchronously opened');
  });
});
document.getElementById('observed-trigger').addEventListener('click', function() {
  document.getElementById('observed').hidden = false;
});
document.getElementById('replace-observed').addEventListener('click', function() {
  document.getElementById('observed').hidden = true;
  Platform.modal.confirm('Replacement confirmation');
});
</script></body></html>"""


@pytest.fixture(scope="module", params=["chromium", "firefox", "webkit"])
def modal_browser(request):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch()
        yield browser
        browser.close()


@pytest.fixture
def modal_page(modal_browser):
    page = modal_browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route(BASE + "/static/**", lambda route: route.fulfill(
        path=str(ROOT / "app" / route.request.url[len(BASE) + 1:])))
    page.route(BASE + "/", lambda route: route.fulfill(content_type="text/html", body=HTML))
    page.goto(BASE + "/", wait_until="domcontentloaded")
    # modal.js starts its visibility observer in a timer set 100 ms after
    # DOMContentLoaded. A timer registered now is due later than that one, and
    # timers run in due order, so when this resolves the observer is running on
    # any engine and at any load, without guessing a sleep.
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 100))")
    yield page
    page.close()
    assert errors == []


def _activate(page, trigger, method):
    # Establish a different previous focus to expose pointer engines that don't
    # focus buttons automatically. Keyboard activation starts at the real button.
    page.get_by_role("textbox", name="Previous field").click()
    if method == "keyboard":
        page.keyboard.press("Tab")
        expect(trigger).to_be_focused()
        page.keyboard.press("Enter")
    else:
        trigger.locator("#trigger-icon").click()


@pytest.mark.parametrize("method", ["pointer", "keyboard"])
@pytest.mark.parametrize("dismissal", ["cancel", "escape"])
def test_dynamic_confirm_restores_invoking_button_after_dismissal(modal_page, method, dismissal):
    page = modal_page
    trigger = page.get_by_role("button", name="Delete Entity", exact=True)
    _activate(page, trigger, method)
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog).to_be_visible()
    expect(dialog.locator(":focus")).to_have_count(1)
    expect(page.locator("#shell")).to_have_attribute("inert", "")
    if dismissal == "cancel":
        dialog.get_by_role("button", name="Cancel", exact=True).click()
    else:
        page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    assert page.locator("#shell").get_attribute("inert") is None
    expect(trigger, str(page.evaluate("({activeAtOpen, focusAttempts})"))).to_be_focused()
    assert not [attempt for attempt in page.evaluate("window.focusAttempts") if attempt["inert"]]


def test_child_close_restores_parent_opener_but_keeps_background_inert(modal_page):
    page = modal_page
    trigger = page.get_by_role("button", name="Open parent", exact=True)
    trigger.click()
    parent = page.get_by_role("dialog", name="Parent", exact=True)
    expect(parent.locator(":focus")).to_have_count(1)
    child_trigger = parent.get_by_role("button", name="Open child", exact=True)
    child_trigger.click()
    child = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(child.locator(":focus")).to_have_count(1)
    expect(parent).to_have_attribute("inert", "")
    child.get_by_role("button", name="Cancel", exact=True).click()
    expect(child).to_be_hidden()
    assert parent.get_attribute("inert") is None
    expect(child_trigger).to_be_focused()
    expect(page.locator("#shell")).to_have_attribute("inert", "")
    page.keyboard.press("Tab")
    expect(parent.locator(":focus")).to_have_count(1)
    parent.get_by_role("button", name="Close parent", exact=True).click()
    expect(trigger).to_be_focused()
    assert page.locator("#shell").get_attribute("inert") is None


def test_removed_trigger_does_not_keep_background_inert_or_focus_detached_node(modal_page):
    page = modal_page
    page.get_by_role("button", name="Delete Entity", exact=True).click()
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    page.locator("#trigger").evaluate("element => element.remove()")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dialog).to_be_hidden()
    assert page.locator("#shell").get_attribute("inert") is None
    assert not [attempt for attempt in page.evaluate("window.focusAttempts") if not attempt["connected"]]
    # Existing contract has no named replacement when the opener was removed;
    # require an interactive page, not an invented fallback destination.
    page.get_by_role("textbox", name="Previous field").click()
    expect(page.get_by_role("textbox", name="Previous field")).to_be_focused()


def test_explicit_return_focus_overrides_incidental_active_element(modal_page):
    page = modal_page
    page.get_by_role("textbox", name="Previous field").click()
    page.evaluate("() => { Platform.modal.confirm('Explicit opener', {returnFocus: document.getElementById('trigger')}); }")
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#trigger")).to_be_focused()


def test_explicit_invoker_wins_when_pointer_preserves_previous_input_focus(modal_page):
    page = modal_page
    # A legitimate pointer handler may preserve input focus during mousedown.
    # Use native pointer/click dispatch, not a synthetic activation or a focus
    # assertion workaround, to make that engine-dependent state reproducible.
    page.locator("#trigger").evaluate(
        "element => element.addEventListener('mousedown', event => event.preventDefault())")
    page.get_by_role("textbox", name="Previous field").click()
    page.locator("#trigger-icon").click()
    assert page.evaluate("window.activeAtOpen") == "prior"
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#trigger")).to_be_focused()


def test_unrelated_click_does_not_override_later_programmatic_focus_origin(modal_page):
    page = modal_page
    page.get_by_role("button", name="Unrelated action", exact=True).click()
    page.locator("#prior").focus()
    page.evaluate("() => { Platform.modal.confirm('Programmatically opened'); }")
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#prior")).to_be_focused()


def test_microtask_open_uses_current_focus_not_earlier_click(modal_page):
    page = modal_page
    page.get_by_role("button", name="Open asynchronously", exact=True).click()
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#prior")).to_be_focused()


def test_microtask_open_without_focus_change_does_not_reuse_click_origin(modal_page):
    page = modal_page
    page.locator("#unrelated").evaluate("""element => {
        element.addEventListener('mousedown', event => event.preventDefault());
        element.addEventListener('click', () => Promise.resolve().then(() => {
            Platform.modal.confirm('Deferred without moving focus');
        }));
    }""")
    page.get_by_role("textbox", name="Previous field").click()
    page.get_by_role("button", name="Unrelated action", exact=True).click()
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#prior")).to_be_focused()


def test_observed_close_cannot_uninert_background_or_steal_new_modal_focus(modal_page):
    page = modal_page
    # The fallback observer is already running: the fixture waited for it.
    page.get_by_role("button", name="Open observed", exact=True).click()
    expect(page.locator("#observed :focus")).to_have_count(1)
    page.get_by_role("button", name="Replace observed", exact=True).click()
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    assert dialog.get_attribute("inert") is None
    expect(page.locator("#shell")).to_have_attribute("inert", "")
    page.keyboard.press("Tab")
    expect(dialog.locator(":focus")).to_have_count(1)


def test_closing_underlying_parent_keeps_top_modal_focus_and_isolation(modal_page):
    page = modal_page
    page.get_by_role("button", name="Open parent", exact=True).click()
    parent = page.get_by_role("dialog", name="Parent", exact=True)
    expect(parent.locator(":focus")).to_have_count(1)
    parent.get_by_role("button", name="Open child", exact=True).click()
    child = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(child.locator(":focus")).to_have_count(1)
    page.evaluate("Platform.modal.close('parent')")
    expect(child.locator(":focus")).to_have_count(1)
    assert child.get_attribute("inert") is None
    expect(page.locator("#shell")).to_have_attribute("inert", "")
    child.get_by_role("button", name="Cancel", exact=True).click()
    assert page.locator("#shell").get_attribute("inert") is None


@pytest.mark.parametrize("mode", ["declarative_open", "declarative_confirm", "confirm_submit", "confirm_submit_override"])
@pytest.mark.parametrize("method", ["pointer", "keyboard"])
def test_owned_handlers_return_focus_to_actual_invoker(modal_page, mode, method):
    page = modal_page
    page.evaluate("""mode => {
      const main = document.querySelector('main');
      const form = document.createElement('form');
      form.action = '/must-not-submit';
      form.method = 'post';
      form.innerHTML = '<button id="owned-trigger"><span id="owned-icon">Owned action</span></button>';
      main.appendChild(form);
      const button = form.querySelector('button');
      button.addEventListener('mousedown', event => event.preventDefault());
      if (mode === 'declarative_open') {
        button.type = 'button';
        button.dataset.modalOpen = 'owned';
        Platform.modal.create({id:'owned',title:'Confirm',buttons:[{label:'Cancel',resolve:false}]});
      } else if (mode === 'declarative_confirm') {
        form.dataset.confirm = 'Confirm fixture action?';
      } else {
        form.addEventListener('submit', event => Platform.modal.confirmSubmit(event, 'Confirm fixture action?',
          mode === 'confirm_submit_override' ? {returnFocus:document.getElementById('prior')} : undefined));
      }
    }""", mode)
    submissions = []
    page.on("request", lambda request: submissions.append(request.url) if request.method == "POST" else None)
    page.get_by_role("textbox", name="Previous field").click()
    if method == "keyboard":
        for _ in range(6):
            page.keyboard.press("Tab")
        expect(page.locator("#owned-trigger")).to_be_focused()
        page.keyboard.press("Enter")
    else:
        page.locator("#owned-icon").click()
    dialog = page.get_by_role("dialog", name="Confirm", exact=True)
    expect(dialog.locator(":focus")).to_have_count(1)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#prior" if mode == "confirm_submit_override" else "#owned-trigger")).to_be_focused()
    assert submissions == []
