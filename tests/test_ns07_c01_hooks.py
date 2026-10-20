"""Shared components converted to the canonical macros keep their ids, bindings and hooks.

Group C-01 of the tier 1 conformance burn-down: data table, admin layout (sidebar
opener, onboarding card, guided walkthrough), admin header (mark-all-read), card and
input macros. The markup was converted to the button macro and to canonical sizes;
every id, Alpine binding and click handler must still be on the rendered page.
"""

import pytest


@pytest.fixture
def _user(db_session, make_org):
    from app.models.user import User

    org = make_org("ns07-c01")
    user = User(email="ns07-c01-%s@example.com" % org.id, first_name="N", last_name="S", confirmed=True)
    user.organization_id = org.id
    user.enterprise_role = "enterprise_architect"
    db_session.add(user)
    db_session.commit()
    return user


def _render_base(app, user):
    from flask import render_template_string
    from flask_login import login_user

    with app.test_request_context("/"):
        login_user(user)
        return render_template_string(
            "{% extends 'layouts/admin_base.html' %}{% block content %}x{% endblock %}",
            nav_counts={},
        )


def _norm(html):
    return html.replace("&#39;", "'").replace("&#34;", '"').replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")


def test_admin_layout_keeps_hooks(app, _user):
    html = _norm(_render_base(app, _user))
    # sidebar opener
    assert 'aria-controls="admin-sidebar"' in html
    assert 'aria-label="Open sidebar"' in html
    assert "@click.stop=\"$store.sidebar.open = true\"" in html
    assert ":aria-expanded=\"$store.sidebar.open ? 'true' : 'false'\"" in html
    assert 'x-show="!isDesktop && !$store.sidebar.open"' in html
    assert "x-data=\"{ isDesktop: window.innerWidth >= 1024 }\"" in html
    assert "window.addEventListener('resize'" in html
    # backdrop
    assert '@click="$store.sidebar.open = false"' in html
    # guided walkthrough
    assert 'id="guided-mode-trigger"' in html
    assert 'x-show="steps.length > 0"' in html
    assert '@click="open = !open"' in html
    assert 'aria-label="Open guided walkthrough"' in html
    assert 'title="Guided walkthrough"' in html
    assert '@click="exit()"' in html
    assert '@click="next()"' in html
    assert "x-text=\"currentIndex === steps.length - 1 ? 'Done' : 'Next'\"" in html
    # header
    assert 'id="notif-mark-read-btn"' in html
    assert 'aria-label="Mark all notifications as read"' in html
    assert "Mark all read" in html


def test_onboarding_card_buttons_keep_hooks(app, db_session, make_org):
    from app.models.user import User

    org = make_org("ns07-c01-new")
    user = User(email="ns07-c01-new-%s@example.com" % org.id, first_name="N", last_name="S", confirmed=True)
    user.organization_id = org.id
    user.enterprise_role = None
    user.onboarding_completed_at = None
    db_session.add(user)
    db_session.commit()
    html = _norm(_render_base(app, user))
    assert "@click=\"dismissOnboarding()\"" in html
    assert "Skip for now" in html
    assert ":aria-label=\"primaryCTA[selectedRole]?.label || 'Go to Dashboard'\"" in html
    assert "dismissOnboarding(); window.location.href" in html


def test_data_table_keeps_hooks(app):
    from flask import render_template_string

    with app.test_request_context("/"):
        html = render_template_string(
            "{% from 'components/data_table.html' import data_table with context %}"
            "{{ data_table(id='t1', columns=[{'key':'name','label':'Name'}], rows_expr='window.rows',"
            " searchable=true, pagination=true, actions=true, on_view='openView(row)',"
            " on_edit='openEdit(row)', on_delete='removeRow(row)') }}"
        )
    html = _norm(html)
    assert 'id="t1"' in html
    assert '@click="nextPage()"' in html
    assert '@click="lastPage()"' in html
    assert '@click="firstPage()"' in html
    assert '@click="previousPage()"' in html
    assert ':disabled="currentPage >= totalPages"' in html
    assert 'aria-label="Go to next page"' in html
    assert 'aria-label="Go to last page"' in html
    assert '@click="removeRow(row); menuOpen = false"' in html
    assert '@click="openView(row); menuOpen = false"' in html
    assert '@click="openEdit(row); menuOpen = false"' in html
    assert 'x-show="menuOpen"' in html
    assert 'x-model.number="perPage"' in html
    assert 'x-model="search"' in html
    assert "Delete" in html


def test_card_and_input_macros_use_canonical_sizes(app):
    from flask import render_template_string

    with app.test_request_context("/"):
        html = render_template_string(
            "{% from 'components/card.html' import card %}{% from 'components/input.html' import input %}"
            "{% call card() %}x{% endcall %}{{ input(id='i1', name='n1') }}"
        )
    assert 'data-slot="card"' in html and "rounded-lg border" in html and "rounded-xl" not in html
    assert 'id="i1"' in html and 'name="n1"' in html and "h-9" in html and "h-10" not in html
