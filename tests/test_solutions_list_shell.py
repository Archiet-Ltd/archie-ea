"""Solutions list on the screen system (shell-overhaul Wave 2, Task 4).

Product design review pinned three defects on `/solutions/`:

- three competing creation CTAs — "New Programme", "New from Template", and
  "New Solution" — with the visually-primary one ("New Programme") not
  matching the page's actual job (browsing/resuming solutions, not
  programmes).
- "New Solution" navigated to `architecture_journey.index`, whose own <h1>
  reads "Architecture Journey" — the button's label lied about where it was
  taking the user.
- the Programme / Solution / Initiative relationship was never explained
  anywhere on the page.

This file pins the rebuild's structural contract: exactly one primary CTA,
its label matching the truth of its destination page's <h1>, and the triage
stat cards (Needs Setup / In Design / Needs Attention / Ready for Review)
still present and still working as filters.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, make_org, label):
    from app.models.user import User

    org = make_org(f"solutions-list-shell-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"solutions-list-shell-{label}-{suffix}@example.com",
        first_name="Solutions",
        last_name="Shell",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    return user.id, org


def _get_list_html(app, db_session, make_org, label="get"):
    user_id, _ = _make_user(db_session, make_org, label)
    client = app.test_client()
    _login(client, user_id)
    resp = client.get("/solutions/")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return client, resp.get_data(as_text=True)


def _get_list_html_with_solution(app, db_session, make_org, label):
    """Seed one Solution so the `{% if solutions %}` branch (triage cards +
    table) renders instead of the empty state."""
    from app.models.solution_models import Solution

    user_id, org = _make_user(db_session, make_org, label)
    solution = Solution(
        name=f"Shell Test Solution {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        created_by_id=user_id,
        status="planned",
        governance_status="draft",
    )
    db_session.add(solution)
    db_session.flush()
    db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    resp = client.get("/solutions/")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return client, resp.get_data(as_text=True)


def _page_actions_slice(html):
    start = html.index('data-testid="page-actions"')
    tag_start = html.rindex("<", 0, start)
    # The actions container is a small, flat div (three buttons, no nested
    # divs) — its own close tag is the first "</div>" after the open tag.
    end = html.index("</div>", start)
    return html[tag_start:end]


def test_exactly_one_primary_cta_in_page_actions(app, db_session, make_org):
    """Three competing creation CTAs was the defect — exactly one bg-primary
    action inside the page-actions container now."""
    _, html = _get_list_html(app, db_session, make_org, "primary")
    actions_slice = _page_actions_slice(html)
    assert actions_slice.count("bg-primary text-primary-foreground") == 1, (
        "expected exactly one primary (bg-primary) action in the page "
        "actions row"
    )


def test_primary_cta_label_matches_destination_h1(app, db_session, make_org):
    """The primary CTA's label must not lie about what it does.

    Two kinds of primary action, two rules:
    - a link (navigates to another page): its label must match the
      destination page's own <h1> — fetch the href and compare.
    - a button that performs an action directly (nothing to navigate to,
      so no destination <h1> to check): its label must name the action it
      performs, and the button must actually be wired to that action, not
      just claim to be.
    """
    client, html = _get_list_html(app, db_session, make_org, "label")
    actions_slice = _page_actions_slice(html)

    link_match = re.search(
        r'<a[^>]*bg-primary text-primary-foreground[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        actions_slice,
        re.DOTALL,
    )
    if not link_match:
        # href may precede the class attribute — try the other order too.
        link_match = re.search(
            r'<a[^>]*href="([^"]+)"[^>]*bg-primary text-primary-foreground[^>]*>(.*?)</a>',
            actions_slice,
            re.DOTALL,
        )

    if link_match:
        href, inner = link_match.group(1), link_match.group(2)
        label_text = re.sub(r"<[^>]+>", " ", inner)
        label_text = re.sub(r"\s+", " ", label_text).strip()

        dest_resp = client.get(href)
        assert dest_resp.status_code == 200, dest_resp.get_data(as_text=True)[:2000]
        dest_html = dest_resp.get_data(as_text=True)
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", dest_html, re.DOTALL)
        assert h1_match, "destination page has no <h1>"
        h1_text = re.sub(r"<[^>]+>", " ", h1_match.group(1))
        h1_text = re.sub(r"\s+", " ", h1_text).strip()

        assert h1_text.lower() in label_text.lower(), (
            f"primary CTA label {label_text!r} does not match destination "
            f"<h1> {h1_text!r}"
        )
        return

    # Not a link — must be a button that performs an action directly.
    button_match = re.search(
        r'<button[^>]*bg-primary text-primary-foreground[^>]*>(.*?)</button>',
        actions_slice,
        re.DOTALL,
    )
    assert button_match, (
        "primary action in page-actions is neither a link with an href nor "
        f"a button: {actions_slice}"
    )
    tag, inner = button_match.group(0), button_match.group(1)
    label_text = re.sub(r"<[^>]+>", " ", inner)
    label_text = re.sub(r"\s+", " ", label_text).strip()

    assert "new solution" in label_text.lower(), (
        f"primary CTA button label {label_text!r} does not name the action "
        "it performs"
    )
    assert "startSolution" in tag, (
        f"primary CTA button is labelled {label_text!r} but is not wired "
        f"to the action it names: {tag!r}"
    )


def test_triage_stat_cards_present(app, db_session, make_org):
    """The triage cards (Needs Setup / In Design / Needs Attention / Ready
    for Review) are a genuine strength of this page — they must survive the
    rebuild, still filtering via ?status=<key>."""
    _, html = _get_list_html_with_solution(app, db_session, make_org, "triage")
    for label in (
        "Needs Setup",
        "In Design",
        "Needs Attention",
        "Ready for Review",
    ):
        assert label in html
    assert "?status=needs_setup" in html
    assert "?status=in_design" in html
    assert "?status=needs_attention" in html
    assert "?status=ready_for_review" in html


def test_relationship_sentence_present(app, db_session, make_org):
    """The Programme / Solution / Initiative relationship must be explained
    on the page — verified against the actual models, not invented. Scoped
    to a dedicated element (not just "Programme"/"Architecture Journey"
    appearing anywhere on the page — both words already show up incidentally
    in the sidebar nav and the template-picker modal copy)."""
    _, html = _get_list_html(app, db_session, make_org, "relationship")
    assert 'data-testid="programme-solution-relationship"' in html
    start = html.index('data-testid="programme-solution-relationship"')
    end = html.index("</p>", start)
    sentence = html[start:end]
    assert "Programme" in sentence
    assert "solution" in sentence.lower()
