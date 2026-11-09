"""The first questions the chat offers get cited answers that agree with the overview.

The person is the administrator of a new organisation that has just connected GitHub (the same
recorded organisation as the connect journey, served by the same fixture; GitHub is never called).
They open the chat, click each question it offers, and see each answer cite where it was read from,
never say nothing is connected for an application the overview counts relationships for, state
counts that agree with what is drawn, and open a citation in a new tab with the conversation kept.
A second organisation's chat offers and answers only its own.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat, sign_in
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_connect_github_to_cited_answer_journey import (  # noqa: F401
    SKIP_WELCOME,
    TOKEN,
    admin,
    github_server,
    world,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("FIRST_QUESTIONS_SHOTS")


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def test_1_the_organisation_connects_github_and_approves_what_was_read(admin, world):
    admin.goto(world["base"] + "/admin/connectors/github", wait_until="domcontentloaded")
    form = admin.get_by_test_id("github-connect-form")
    form.get_by_label("GitHub organisation").fill("acme")
    form.get_by_label("Read-only token").fill(TOKEN)
    form.get_by_role("button", name="Connect and read my repositories").click()
    expect(admin.locator("#github-progress-line")).to_have_text(
        re.compile(r"Read 16 repositories\."), timeout=PAGE_TIMEOUT
    )
    admin.get_by_role("link", name="Review what was found").click()
    admin.get_by_role("button", name="Approve all (23)").click()
    expect(admin.locator("#main-content").get_by_text("Added 23 items to your model.")).to_be_visible()


def test_2_the_overview_counts_the_relationships(admin, world):
    admin.goto(world["base"] + "/dashboard/", wait_until="domcontentloaded")
    card = admin.get_by_test_id("card-relationships")
    expect(card).to_be_visible()
    text = card.inner_text()
    top = re.search(r"(.+?) is the most connected, with (\d+) relationships", text, re.S)
    assert top, text
    world["overview_top"] = (top.group(1).strip().splitlines()[-1].strip(), int(top.group(2)))
    assert world["overview_top"][1] >= 1


def test_3_each_offered_question_is_answered_with_a_citation_and_agreeing_counts(admin, world):
    open_chat(admin, world["base"])
    offered = admin.get_by_test_id("first-question-suggestion")
    expect(offered).to_have_count(4, timeout=PAGE_TIMEOUT)
    questions = [offered.nth(i).get_attribute("data-question") for i in range(4)]
    assert all(q.startswith("What breaks if ") for q in questions), questions
    shoot(admin, "offered")
    for index, question in enumerate(questions):
        if index:
            admin.get_by_role("button", name="Start a new chat").click()
            expect(admin.get_by_test_id("first-question-suggestion")).to_have_count(4, timeout=PAGE_TIMEOUT)
        admin.get_by_test_id("first-question-suggestion").nth(index).click()
        answer = admin.get_by_test_id("estate-answer")
        expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
        text = answer.inner_text()
        assert "Nothing is recorded as connected to this yet" not in text, question
        rows = answer.locator("[data-answer-row]")
        assert rows.count() >= 1, "%s: no connection shown" % question
        sources = answer.locator("[data-citation]")
        assert sources.count() == rows.count(), question
        for i in range(sources.count()):
            assert sources.nth(i).inner_text().strip() not in ("", "Source not recorded"), question
        stated = int(re.search(r"(\d+) connections? in your records", text).group(1))
        assert stated == rows.count() or "of the first" in text, (question, stated, rows.count())
        shoot(admin, "answer-%d" % index)
        world.setdefault("answered", []).append(question)
    assert len(world["answered"]) == 4


def test_4_a_citation_opens_in_a_new_tab_and_the_chat_stays(admin, world):
    admin.get_by_role("button", name="Start a new chat").click()
    expect(admin.get_by_test_id("first-question-suggestion")).to_have_count(4, timeout=PAGE_TIMEOUT)
    admin.get_by_test_id("first-question-suggestion").first.click()
    answer = admin.get_by_test_id("estate-answer")
    expect(answer).to_have_count(1, timeout=PAGE_TIMEOUT)
    links = answer.locator("a[href^='http']")
    assert links.count() >= 1, "the answer cites no external source"
    for i in range(links.count()):
        link = links.nth(i)
        assert link.get_attribute("target") == "_blank", link.get_attribute("href")
        assert "noopener" in (link.get_attribute("rel") or ""), link.get_attribute("href")
    chat_url = admin.url
    with admin.context.expect_page() as opened:
        links.first.click()
    assert opened.value is not admin
    assert admin.url == chat_url
    expect(admin.get_by_test_id("estate-answer")).to_be_visible()
    opened.value.close()


def test_5_a_link_in_a_written_answer_leaves_the_conversation_in_place(admin, world):
    """A link a written answer carries to another site opens in a new tab, as a citation does."""
    open_chat(admin, world["base"])
    html = admin.evaluate(
        "() => ArchieChat.render.renderMarkdown('See [the file](https://github.com/acme/x/blob/main/a.yml) and [the model](/architecture/model).')"
    )
    holder = admin.evaluate_handle("(h) => { const d = document.createElement('div'); d.innerHTML = h; return d; }", html)
    external = holder.as_element().query_selector("a[href^='https://github.com']")
    internal = holder.as_element().query_selector("a[href^='/architecture']")
    assert external.get_attribute("target") == "_blank" and "noopener" in external.get_attribute("rel")
    assert internal.get_attribute("target") is None


def test_6_another_organisation_is_offered_and_answered_only_its_own(browser, world):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, world["base"], world["two"]["emails"]["solution_architect"])
        open_chat(page, world["base"])
        page.wait_for_timeout(3000)
        offered = page.get_by_test_id("first-question-suggestion")
        names = [offered.nth(i).get_attribute("data-question") for i in range(offered.count())]
        assert not any(n and "Payments Service" in n for n in names), names
        page.locator("#user-input").fill("What breaks if Payments Service fails?")
        page.locator("#user-input").press("Enter")
        page.wait_for_timeout(5000)
        assert "acme/" not in page.locator("#messages-container").inner_text()
    finally:
        context.close()
