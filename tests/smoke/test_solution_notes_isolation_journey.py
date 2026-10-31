"""A solution's notes belong to its organisation (T-sec02).

The person: an architect keeping a note log on their own solution. The goal: record a
meeting on the solution record's Notes card, find it again after a reload, and be sure
nobody in another organisation can read it or write onto the solution. A second
organisation's architect signs in too and tries the other organisation's solution, its
old notes address and its interface; they get the plain not-found page and nothing of
the note, while their own solution's Notes card works normally.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]


def _solution_for(org_id, author_id, name):
    """A solution the architect created, so the record page opens for them."""
    from app import create_app, db
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        solution = Solution(name=name, organization_id=org_id, created_by_id=author_id)
        db.session.add(solution)
        db.session.commit()
        sid = solution.id
        db.session.remove()
    return sid


def _stored(subject):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        row = db.session.execute(
            db.text("select organization_id, logged_by, solution_id from stakeholder_communications "
                    "where subject = :s"), {"s": subject}).one()
        db.session.remove()
    return row


def _context(browser, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    return ctx


def _shot(page, tag, name):
    root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "solution-notes")
    folder = os.path.abspath(os.path.join(root, tag))
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)


def _add_note(page, kind, subject, summary):
    """Record a note the way a person does: the Notes card's form on the solution record."""
    form = page.get_by_test_id("note-form")
    expect(form).to_be_visible()
    page.select_option("#note-kind", kind)
    page.fill("#note-subject", subject)
    page.fill("#note-summary", summary)
    page.get_by_test_id("note-add").click()
    expect(page.get_by_test_id("record-notes-list").get_by_text(subject)).to_be_visible()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_notes_stay_inside_their_organisation(browser, live_server, width, theme):
    tag = uuid.uuid4().hex[:6]
    a = create_fresh_org("architect")
    b = create_fresh_org("architect")
    sol_a = _solution_for(a["org_id"], a["user_ids"]["architect"], "Payments platform %s" % tag)
    sol_b = _solution_for(b["org_id"], b["user_ids"]["architect"], "Billing rewrite %s" % tag)
    note_a, note_b = "Kick-off with finance %s" % tag, "Vendor call %s" % tag

    ctx_a = _context(browser, width, theme)
    page_a = ctx_a.new_page()
    ctx_b = _context(browser, width, theme)
    page_b = ctx_b.new_page()
    try:
        # The owner opens the old notes address, lands on the record's Notes card, records a
        # meeting through the form and finds it again after a reload.
        sign_in(page_a, live_server, a["emails"]["architect"])
        page_a.goto("%s/solutions/%d/communications" % (live_server, sol_a), wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT)
        assert page_a.url.endswith("/solutions/%d#record-notes" % sol_a), page_a.url
        _add_note(page_a, "meeting", note_a, "Finance agreed the ledger cut-over date.")
        page_a.reload(wait_until="domcontentloaded")
        expect(page_a.get_by_test_id("record-notes-list").get_by_text(note_a)).to_be_visible()
        _shot(page_a, "%d-%s" % (width, theme), "01-owner-log")
        org_id, logged_by, solution_id = _stored(note_a)
        assert org_id == a["org_id"] and logged_by == a["user_ids"]["architect"] and solution_id == sol_a

        # The other organisation cannot read the record, its old notes address or the
        # interface, nor write onto the solution.
        sign_in(page_b, live_server, b["emails"]["architect"])
        for path in ("/solutions/%d" % sol_a, "/solutions/%d/communications" % sol_a):
            response = page_b.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 404, (path, response.status)
            assert note_a not in page_b.content()
        _shot(page_b, "%d-%s" % (width, theme), "02-other-org-refused")
        status, body = api(page_b, "GET", "/api/communications?solution_id=%d" % sol_a)
        assert status == 404 and note_a not in str(body)
        status, _ = api(page_b, "POST", "/solutions/%d/notes" % sol_a,
                        {"kind": "email", "subject": "planted %s" % tag, "summary": ""})
        assert status == 404
        status, _ = api(page_b, "POST", "/api/communications",
                        {"solution_id": sol_a, "comm_type": "email", "subject": "planted %s" % tag})
        assert status == 404

        # Their own solution's Notes card works, and holds only their own note.
        page_b.goto("%s/solutions/%d" % (live_server, sol_b), wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT)
        _add_note(page_b, "email", note_b, "The vendor confirmed the renewal terms.")
        assert note_a not in page_b.content()

        # And the owner's record never shows the other organisation's note or the planted one.
        page_a.reload(wait_until="domcontentloaded")
        expect(page_a.get_by_test_id("record-notes-list").get_by_text(note_a)).to_be_visible()
        assert note_b not in page_a.content() and "planted %s" % tag not in page_a.content()
    finally:
        ctx_a.close()
        ctx_b.close()
