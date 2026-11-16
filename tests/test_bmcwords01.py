"""Tests for the bmcwords01 seat: the two public canvas use-case pages tell the
true story (no sparkle, no AI drafting, no free-text boxes) and the canvas
view really shows the controls those pages name.

Covers acceptance criteria 1 and 2 of the bmcwords01 brief.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content" / "pages" / "function-per-segment"

# Phrases that must not appear on either page: the canvas has no sparkle
# button, no AI-drafted suggestions, no free-text "one idea per line" boxes,
# and nothing saves on click-out. Entries are model records added through a
# dialog.
FORBIDDEN = ["sparkle", "draft", "ai-drafted", "one idea per line", "click out"]

PAGE_SLUGS = [
    "uc-s1-02-canvas-on-one-page",
    "uc-s1-06-single-point-of-failure",
]


def _page_source(slug: str) -> str:
    return (CONTENT_ROOT / f"{slug}.md").read_text(encoding="utf-8")


def test_both_pages_contain_no_forbidden_claims():
    """AC1: neither page names a control the canvas does not render."""
    for slug in PAGE_SLUGS:
        source = _page_source(slug).lower()
        for phrase in FORBIDDEN:
            assert phrase not in source, (
                f"{slug}.md contains banned phrase '{phrase}'"
            )


# ── AC2: the canvas view really shows the controls uc-s1-02 names ──────────

def _canvas(db_session, org):
    from app.models.business_model import BusinessModelCanvas

    canvas = BusinessModelCanvas(name="Test canvas", organization_id=org.id)
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _user(db_session, org):
    from app.models.user import Role, User

    user = User(
        email=f"bmc-{__import__('uuid').uuid4().hex[:8]}@example.com",
        first_name="Canvas", last_name="Tester",
        organization_id=org.id, confirmed=True, enterprise_role="business_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Architect").first() or Role(name="Architect")
    db_session.add(role)
    db_session.flush()
    user.role = role
    db_session.commit()
    return user


def test_canvas_view_shows_every_control_uc_s1_02_names(
    db_session, make_org, client, login_as, tenant_ctx,
):
    """AC2: /business-model/<id> renders the controls the uc-s1-02 page names
    by label: an "Add to ..." button on each box, an "(i)" About popover, and
    Edit and Remove on each entry."""
    org = make_org("bmc-controls")
    canvas = _canvas(db_session, org)
    user = _user(db_session, org)
    with tenant_ctx(org.id):
        from app.modules.business_model_canvas import model_service as ms
        ms.add_entry(canvas, "customer_segments", name="Grocery chains")
        db_session.commit()
    login_as(client, user)
    page = client.get(f"/business-model/{canvas.id}").get_data(as_text=True)
    assert page is not None and len(page) > 0

    # Each box has an "Add to ..." button (aria-label "Add to <box title>").
    # Cost Structure is readonly (costs come from application records), so it
    # has no add button; every other box does.
    for box_title in (
        "Key Partners", "Key Activities", "Key Resources", "Value Propositions",
        "Customer Relationships", "Channels", "Customer Segments",
        "Revenue Streams",
    ):
        assert f'aria-label="Add to {box_title}"' in page, (
            f"/business-model/{canvas.id}: missing 'Add to {box_title}' button"
        )

    # Each box has an "(i)" About popover (aria-label "About <box title>").
    for box_title in (
        "Key Partners", "Key Activities", "Key Resources", "Value Propositions",
        "Customer Relationships", "Channels", "Customer Segments",
        "Cost Structure", "Revenue Streams",
    ):
        assert f'aria-label="About {box_title}"' in page, (
            f"/business-model/{canvas.id}: missing 'About {box_title}' popover"
        )

    # Each entry carries Edit and Remove.
    assert "Edit this entry" in page, (
        f"/business-model/{canvas.id}: missing 'Edit this entry' control"
    )
    assert "Remove from this canvas" in page, (
        f"/business-model/{canvas.id}: missing 'Remove from this canvas' control"
    )
