"""The board's line about pending reviews outside any review cycle opens exactly those reviews.

A review is pending when it is submitted, under review or pending information. The dashboard counts the ones
in no review cycle and links to the ledger filtered to them, over as many pages as there are.
"""
from __future__ import annotations

import re
import uuid

import pytest

from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")


def _reviews(db_session, org, noun, statuses):
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import User

    submitter = User(email=f"sub-{uuid.uuid4().hex[:8]}@example.com", first_name="Sub", last_name="Mitter",
                     organization_id=org.id, confirmed=True, enterprise_role="solution_architect")
    db_session.add(submitter)
    db_session.flush()
    for number, status in enumerate(statuses):
        db_session.add(ARBReviewItem(
            review_number="%s-%03d" % (noun[:6].upper(), number), title="%s %s %03d" % (noun, status, number),
            review_type="architecture_change", status=status, priority="medium", organization_id=org.id,
            submitter_id=submitter.id))
    db_session.flush()


def test_the_link_opens_every_pending_review_outside_a_cycle_over_all_its_pages(app, db_session, make_org):
    mine, theirs = make_org("arq-a"), make_org("arq-b")
    noun = "Queue" + uuid.uuid4().hex[:5]
    _reviews(db_session, mine, noun, ["submitted"] * 21 + ["approved", "rejected"])
    _reviews(db_session, theirs, "Other" + noun, ["submitted"] * 3)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()

    board = client.get("/arb/").get_data(as_text=True)
    link = re.search(r'data-testid="arb-outside-queue-link"', board) and re.search(
        r'href="([^"]+)"[^>]*data-testid="arb-outside-queue-link"', board)
    assert link, "the dashboard line has no link to the filtered ledger"
    assert "21 of the 21 pending reviews are not yet in a review cycle" in _answer(board)

    first = client.get(link.group(1).replace("&amp;", "&")).get_data(as_text=True)
    assert first.count("%s submitted" % noun) == 20
    assert "approved 0" not in first and "Other" + noun not in first
    nxt = re.search(r'href="([^"]+)"[^>]*>Next<', first)
    assert nxt and "cycle=none" in nxt.group(1), "the next page drops the filter"
    second = client.get(nxt.group(1).replace("&amp;", "&")).get_data(as_text=True)
    assert second.count("%s submitted" % noun) == 1
    assert "%s approved" % noun not in second and "%s rejected" % noun not in second


def test_the_unfiltered_ledger_still_lists_every_review(app, db_session, make_org):
    mine = make_org("arq-c")
    noun = "All" + uuid.uuid4().hex[:5]
    _reviews(db_session, mine, noun, ["submitted", "approved"])
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    html = client.get("/arb/reviews").get_data(as_text=True)
    assert "%s submitted" % noun in html and "%s approved" % noun in html
    assert "arb-outside-queue-filter" not in html


def test_choosing_a_status_or_type_keeps_the_outside_a_cycle_filter(app, db_session, make_org):
    mine, theirs = make_org("arq-k"), make_org("arq-l")
    noun = "Keep" + uuid.uuid4().hex[:5]
    _reviews(db_session, mine, noun, ["submitted"] * 2 + ["under_review", "approved"])
    _reviews(db_session, theirs, "Other" + noun, ["submitted"])
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()

    page = client.get("/arb/reviews?cycle=none").get_data(as_text=True)
    form = re.search(r'<form method="GET" action="/arb/reviews".*?</form>', page, re.S).group(0)
    assert re.search(r'<input type="hidden" name="cycle" value="none">', form), form
    # what the browser submits when a status is chosen: every field of the form
    chosen = client.get("/arb/reviews?cycle=none&status=submitted&review_type=all").get_data(as_text=True)
    assert chosen.count("%s submitted" % noun) == 2 and "%s under_review" % noun not in chosen
    assert "Showing the pending reviews that are not yet in a review cycle." in _answer(chosen)
    assert "Other" + noun not in chosen
    # the unfiltered ledger has no such field
    plain = client.get("/arb/reviews").get_data(as_text=True)
    assert 'name="cycle"' not in plain
