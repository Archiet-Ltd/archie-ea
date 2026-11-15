"""A saved answer keeps the source each row was read from, even when the link carries only a label.

An enterprise architect asks the AI chat what breaks if the order hub fails. Two of the hub's links
were written by the GitHub connector with the label "GitHub" and no repository reference. The chat
answer names GitHub for them; the saved answer opened from it must say the same for the same rows and
must not read "Source not recorded". A second organisation holds the same shape of data.
"""

import re

import pytest
from playwright.sync_api import expect

from .test_cited_answer_sources_journey import _answer, _login, _seed, _shot, page  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _label_only(ours):
    """Take the repository reference off the organisation's connector-written links, leaving the label."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        for row in ArchiMateRelationship.query.filter_by(organization_id=ours["org_id"], source_kind="connector"):
            row.source_ref = None
            row.source_label = "GitHub"
        db.session.commit()


@pytest.fixture(scope="module")
def world(live_server):
    ours, other = _seed("Ours"), _seed("Other")
    _label_only(ours)
    _label_only(other)
    return {"ours": ours, "other": other}


def test_saved_answer_names_github_for_every_row_the_chat_names_it_for(page, live_server, world):  # noqa: F811
    ours, other = world["ours"], world["other"]
    card = _answer(page, live_server, ours)
    expect(card.locator("[data-source-summary]")).to_have_text(
        re.compile(r"^5 connections in your records: 3 recorded by people, 2 read from GitHub\.$"))
    card.get_by_test_id("answer-open").click()
    page.wait_for_url(re.compile(r"/intelligence/answers/"))

    main = page.locator("main")
    expect(page.locator("[data-answer-basis]")).to_contain_text("3 recorded by people, 2 read from GitHub")
    rows = page.locator("[data-answer-rows] tbody tr")
    read = [rows.nth(i).inner_text() for i in range(rows.count()) if "shipping" in rows.nth(i).inner_text().lower()]
    assert len(read) == 2, read
    for text in read:
        assert "Read from GitHub" in text, text
        assert "Source not recorded" not in text, text
    for i in range(rows.count()):
        text = rows.nth(i).inner_text()
        if "billing" in text.lower():
            assert "Entered by a person" in text and "GitHub" not in text, text
    assert "Source not recorded" not in main.inner_text()
    assert other["noun"].lower() not in main.inner_text().lower()
    _shot(page, "saved-answer-label-only")
