"""The Controls page counts the checks that really ran on a change the assistant made.

The one person in a workspace asks the assistant to move an application to production and also asks it a
question. The change is checked, reviewed and applied. They open Controls from the menu and see: one change
checked, its outcome (applied), the application's name, a link to the change's record that opens a page with
the checks on it, and a sentence that says changes are checked and answers in the chat are not checked by a
policy yet. A person in another workspace, who asked for nothing, opens Controls and sees none of it.

Before this change Controls said "No AI operations checked yet" however many changes had been checked, and
its figures were read from one in-memory list shared by every workspace.
"""
import re

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_change_reviewed_and_applied_journey import (  # noqa: F401 - shots is a fixture
    CHAT_REPLY,
    OTHERS,
    _ask,
    _one_person_workspace,
    _respond,
    _shot,
    _sign_in,
    shots,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

COVERED = "Changes the assistant makes are checked"
NOT_COVERED = "Answers in the chat are not checked by a policy yet."


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=CHAT_REPLY, respond=_respond)
    yield s
    s.close()


@pytest.fixture(scope="module")
def deepseek_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _open_controls(page, base):
    """Opens Controls the way a person does: from the menu."""
    page.goto(base + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.goto(base + "/policy-monitoring/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("policy-coverage")).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_a_change_the_assistant_made_is_counted_on_controls_and_another_workspace_sees_none_of_it(
    browser, deepseek_server, stub, shots, width, dark
):
    base = deepseek_server
    mine, other = _one_person_workspace(), _one_person_workspace()
    atlas_id, atlas_name = mine["atlas"]
    scheme = "dark" if dark else "light"

    def new_page():
        context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
        context.set_default_timeout(PAGE_TIMEOUT)
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        return context, context.new_page()

    context, page = new_page()
    try:
        _sign_in(page, base, mine["email"])
        # Before anything is asked, Controls says nothing has been checked, because nothing has.
        _open_controls(page, base)
        expect(page.get_by_test_id("policy-checks-empty")).to_contain_text("No changes have been checked")
        expect(page.get_by_test_id("policy-checked-value")).to_have_count(0)
        _shot(page, shots, "controls-nothing-checked-%s" % scheme)

        # One change goes through the assistant and the review loop.
        verdicts = _ask(page, base, stub, {
            "application_name": atlas_name, "new_status": "production", "rationale": "It is live for customers now.",
        }, "Move %s to production, it is live for customers now." % atlas_name)
        assert "Applied:" in verdicts.inner_text()

        # One question is answered in the same chat; it makes no change.
        stub.tool_script = []
        box = page.locator("#user-input")
        box.fill("How many applications do we have?")
        box.press("Enter")
        expect(page.locator("#messages-container")).to_contain_text(CHAT_REPLY, timeout=PAGE_TIMEOUT * 2)

        # Controls now counts the change, with its outcome, and says what is and is not checked.
        _open_controls(page, base)
        expect(page.get_by_test_id("policy-checked-value")).to_have_text("1")
        expect(page.get_by_test_id("policy-passed-value")).to_have_text("1")
        expect(page.get_by_test_id("policy-passed")).to_contain_text("No check found a problem with these changes")
        assert "reviewed by a second model" not in page.get_by_test_id("policy-passed").inner_text()
        expect(page.get_by_test_id("policy-failed-value")).to_have_text("0")
        expect(page.get_by_test_id("policy-applied-value")).to_have_text("1")
        assert page.get_by_text("No AI operations checked yet").count() == 0
        row = page.get_by_test_id("policy-check-row")
        expect(row).to_have_count(1)
        expect(row).to_contain_text(atlas_name)
        expect(row).to_contain_text("Applied")
        expect(row).to_contain_text("Passed")
        expect(page.get_by_test_id("policy-covered")).to_contain_text(COVERED)
        expect(page.get_by_test_id("policy-not-covered")).to_have_text(NOT_COVERED)
        _shot(page, shots, "controls-one-change-checked-%s" % scheme)

        # The change's link lands on its record, which has the checks on it.
        row.get_by_test_id("policy-check-link").click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied")
        assert atlas_name in page.get_by_test_id("review-record-checks").inner_text()
        _shot(page, shots, "controls-link-record-%s" % scheme)
    finally:
        context.close()

    # Another workspace's person asked for nothing: Controls shows no change and none of the first one's.
    context, page = new_page()
    try:
        _sign_in(page, base, other["email"])
        _open_controls(page, base)
        expect(page.get_by_test_id("policy-checks-empty")).to_contain_text("No changes have been checked")
        assert atlas_name not in page.locator("main").inner_text()
        _shot(page, shots, "controls-other-workspace-%s" % scheme)
        # The old read addresses hold nothing of the first workspace: they are gone, not answered.
        for address in ("status", "compliance", "violations", "alert"):
            response = page.goto(base + "/policy-monitoring/" + address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 404, address
            assert atlas_name not in page.content()
    finally:
        context.close()
