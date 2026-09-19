"""Applications list on the screen system (shell-overhaul Wave 2, Task 1).

Product design review + 1024px screenshot evidence pinned two structural
defects on `/applications/`:

- the six-button action row (Import/Export/Consolidation/Design Solution/
  Vendor Match/Add Application) overflows and clips ("Vendor" cut off) — no
  wrap, no hierarchy among the six.
- the "AM" column shows unlabeled colored dots with no explanation of what
  they mean.

This file pins the rebuild's structural contract. It does not touch
filtering/pagination/bulk-action behaviour — those are covered elsewhere.
"""

from __future__ import annotations

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

    org = make_org(f"apps-list-shell-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"apps-list-shell-{label}-{suffix}@example.com",
        first_name="Apps",
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


def _get_list_html(app, db_session, make_org):
    user, _ = _make_user(db_session, make_org, "get")
    client = app.test_client()
    _login(client, user)
    resp = client.get("/applications/")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return resp.get_data(as_text=True)


def _get_list_html_with_app(app, db_session, make_org, label):
    """Seed one application so the desktop table (and its AM column) render
    instead of the empty state — the table markup only exists in the
    `{% if applications %}` branch."""
    from app.models.application_portfolio import ApplicationComponent

    user_id, org = _make_user(db_session, make_org, label)
    app_obj = ApplicationComponent(
        name=f"Shell Test App {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add(app_obj)
    db_session.flush()
    db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    resp = client.get("/applications/")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    return resp.get_data(as_text=True), app_obj


def test_exactly_one_h1(app, db_session, make_org):
    html = _get_list_html(app, db_session, make_org)
    assert html.count("<h1") == 1


def test_page_actions_container_wraps(app, db_session, make_org):
    """The action row must carry a wrap-or-overflow class so six buttons
    never clip at 1024px again."""
    html = _get_list_html(app, db_session, make_org)
    assert 'data-testid="page-actions"' in html

    start = html.index('data-testid="page-actions"')
    tag_start = html.rindex("<", 0, start)
    tag_end = html.index(">", start)
    open_tag = html[tag_start:tag_end]

    assert "flex-wrap" in open_tag or "overflow-x-auto" in open_tag, (
        "the page-actions container needs flex-wrap or overflow-x-auto so "
        "it never clips at narrow widths again"
    )


def test_exactly_one_primary_action(app, db_session, make_org):
    """Six buttons with no hierarchy was a defect — exactly one primary
    (bg-primary) action inside the page-actions container; the rest are
    secondary outline buttons."""
    html = _get_list_html(app, db_session, make_org)
    start = html.index('data-testid="page-actions"')
    # Grab a generous slice — the actions container's own inner content —
    # rather than trying to balance nested divs.
    actions_slice = html[start : start + 6000]
    end_marker = actions_slice.find('data-testid="btn-add-application"')
    assert end_marker != -1

    assert actions_slice.count("bg-primary text-primary-foreground") == 1, (
        "expected exactly one primary (bg-primary) action in the page "
        "actions row"
    )


def test_table_wrapper_has_overflow_x_auto(app, db_session, make_org):
    html, _ = _get_list_html_with_app(app, db_session, make_org, "overflow")
    assert 'data-testid="applications-table"' in html
    table_idx = html.index('data-testid="applications-table"')
    preceding = html[max(0, table_idx - 2000) : table_idx]
    assert "overflow-x-auto" in preceding


def test_am_column_header_has_truthful_label(app, db_session, make_org):
    """The AM column encodes a real field (ApplicationComponent.archimate_element_id)
    — it must be labeled, not just a bare 'AM' header with unlabeled dots."""
    html, _ = _get_list_html_with_app(app, db_session, make_org, "am-header")
    header_idx = html.index(">AM<")
    th_start = html.rindex("<th", 0, header_idx)
    th_open_tag = html[th_start : html.index(">", th_start)]
    assert "title=" in th_open_tag or "aria-label=" in th_open_tag


def test_am_column_dots_have_aria_label(app, db_session, make_org):
    """Each dot in the AM column must carry an aria-label explaining its
    state (linked vs not linked to ArchiMate) — a colored dot with only a
    hover title is invisible to assistive tech and to anyone who doesn't
    hover."""
    html, app_obj = _get_list_html_with_app(app, db_session, make_org, "am-dot")

    assert app_obj.name in html
    row_idx = html.index(f'data-testid="app-row-{app_obj.id}"')
    row_end = html.index("</tr>", row_idx)
    row_html = html[row_idx:row_end]

    # Isolate the AM-column dot itself (a rounded-full span) rather than the
    # whole row — the row's own action menu also contains the word
    # "ArchiMate" and unrelated aria-labels, which would make this assertion
    # pass even with an unlabeled dot.
    dot_idx = row_html.index("rounded-full")
    dot_tag_start = row_html.rindex("<span", 0, dot_idx)
    dot_tag_end = row_html.index(">", dot_idx)
    dot_open_tag = row_html[dot_tag_start:dot_tag_end]

    assert "aria-label=" in dot_open_tag, (
        "AM column dot must carry an aria-label describing ArchiMate link "
        "state, not just a hover title"
    )
    assert "ArchiMate" in dot_open_tag


# ── ARCH-106: Data Quality banner must report real counts, not hardcoded 0 ──
#
# The banner used to read `<span>Owner: 0/{{ stats.total|dash }}</span>` and
# `<span>Vendor: 0/{{ stats.total|dash }}</span>` — the "0" numerators were
# literal template text, not computed from any data at all (fabricated-data
# violation). Against that old code this test's assertion is trivially false:
# two applications, one with an owner and one without, would still render
# "Owner: 0/2" because the "0" never varied with the data. The fix computes
# stats['owner_assigned'] / stats['vendor_assigned'] server-side in
# application_list() (app/modules/applications/routes/list_views.py) from
# real business_owner / vendor_name / vendor_product_id columns.


def test_data_quality_banner_reflects_real_owner_and_vendor_counts(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    user_id, org = _make_user(db_session, make_org, "dataquality")

    with_owner = ApplicationComponent(
        name=f"Owned App {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        business_owner="Jane Architect",
    )
    without_owner = ApplicationComponent(
        name=f"Unowned App {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db_session.add_all([with_owner, without_owner])
    db_session.flush()
    db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    resp = client.get("/applications/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The old hardcoded numerator: this string must never appear again.
    assert "Owner: 0/" not in html
    assert "Vendor: 0/" not in html

    # Exactly one of the two seeded applications has an owner; zero have a
    # vendor recorded. The banner must say so in real sentences.
    assert "1 of 2 applications in the portfolio have a named owner" in html
    assert "0 of 2 applications in the portfolio have a vendor recorded" in html


def test_data_quality_banner_owner_count_increases_with_data(app, db_session, make_org):
    """A second, independent check that the numerator actually tracks the
    data rather than being some other constant: three owned, one unowned."""
    from app.models.application_portfolio import ApplicationComponent

    user_id, org = _make_user(db_session, make_org, "dataquality2")

    apps = [
        ApplicationComponent(
            name=f"Owned {i} {uuid.uuid4().hex[:6]}",
            organization_id=org.id,
            business_owner=f"Owner {i}",
        )
        for i in range(3)
    ]
    apps.append(
        ApplicationComponent(
            name=f"Unowned {uuid.uuid4().hex[:6]}",
            organization_id=org.id,
        )
    )
    db_session.add_all(apps)
    db_session.flush()
    db_session.commit()

    client = app.test_client()
    _login(client, user_id)
    resp = client.get("/applications/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "3 of 4 applications in the portfolio have a named owner" in html
