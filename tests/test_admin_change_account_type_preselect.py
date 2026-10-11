"""Regression tests: the change-account-type form must start on the user's
current role, not silently default to the first option in the drop-down.

Before this fix, GET /admin/user/<id>/change-account-type built its form with
no current value, so the rendered <select name="role"> fell back to WTForms'
default (the first role returned by the query). An admin who opened the page
and pressed Save without touching the drop-down would reassign the user to
whatever role sorts first -- a silent privilege change with no drop-down
interaction at all.
"""

import re
import uuid

import pytest

_OPTION_PATTERN = re.compile(
    r'<option\s+([^>]*?)>(?P<label>[^<]*)</option>', re.DOTALL
)
_VALUE_PATTERN = re.compile(r'value="(?P<value>\d+)"')


def _parse_role_options(select_html):
    """Return [(value, is_selected), ...] for every <option> in a rendered
    <select>, independent of attribute order (WTForms renders the selected
    option's "selected" attribute before "value", so a parser that assumes a
    fixed attribute order silently finds nothing selected)."""
    options = []
    for match in _OPTION_PATTERN.finditer(select_html):
        attrs = match.group(1)
        value_match = _VALUE_PATTERN.search(attrs)
        if not value_match:
            continue
        options.append((value_match.group("value"), "selected" in attrs))
    return options


def _extract_role_select(body):
    select_match = re.search(
        r'<select[^>]*name="role"[^>]*>(.*?)</select>', body, re.DOTALL
    )
    assert select_match, "role <select> not found in the rendered form"
    return select_match.group(1)


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Preselect {label} {suffix}", slug=f"preselect-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _get_role(db_session, name):
    from app.models.user import Role

    role = Role.query.filter_by(name=name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=name).first()
    return role


def _make_user(db_session, org, role, *, is_platform_admin=False, email=None):
    from app.models.user import User

    user = User(
        email=email or f"preselect-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        # Pass role=<Role instance>, not role_id=<int> -- User.__init__
        # overwrites an unresolved role_id with the default role before the
        # relationship resolves.
        role=role,
        is_platform_admin=is_platform_admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


class TestChangeAccountTypePreselect:
    """The role drop-down must open on the user's current role."""

    def test_form_preselects_the_users_current_role(self, app, db_session, login_as, client):
        org = _make_org(db_session, "sel")
        admin_role = _get_role(db_session, "Administrator")
        architect_role = _get_role(db_session, "Architect")
        admin = _make_user(db_session, org, admin_role, is_platform_admin=True)
        target = _make_user(db_session, org, architect_role)
        db_session.commit()

        with app.app_context():
            login_as(client, admin)
            resp = client.get(f"/admin/user/{target.id}/change-account-type")

        assert resp.status_code == 200
        select_html = _extract_role_select(resp.data.decode())
        options = _parse_role_options(select_html)

        selected_values = [value for value, is_selected in options if is_selected]
        assert selected_values == [str(architect_role.id)], (
            f"expected only role {architect_role.id} ({architect_role.name}) selected, "
            f"got {selected_values} (options: {options})"
        )

    def test_form_preselects_correctly_for_a_second_role_too(self, app, db_session, login_as, client):
        """Same assertion, different current role -- proves this reads the
        user's actual role rather than happening to match whatever sorts
        first."""
        org = _make_org(db_session, "sel2")
        admin_role = _get_role(db_session, "Administrator")
        user_role = _get_role(db_session, "User")
        admin = _make_user(db_session, org, admin_role, is_platform_admin=True)
        target = _make_user(db_session, org, user_role)
        db_session.commit()

        with app.app_context():
            login_as(client, admin)
            resp = client.get(f"/admin/user/{target.id}/change-account-type")

        assert resp.status_code == 200
        select_html = _extract_role_select(resp.data.decode())
        options = _parse_role_options(select_html)

        selected_values = [value for value, is_selected in options if is_selected]
        assert selected_values == [str(user_role.id)], (
            f"expected only role {user_role.id} ({user_role.name}) selected, "
            f"got {selected_values} (options: {options})"
        )

    def test_submitting_the_form_unchanged_does_not_change_the_role(
        self, app, db_session, login_as, client
    ):
        """Opening the form and pressing Save without touching the
        drop-down must be a no-op. This submits whatever value the GET
        response actually pre-selected -- exactly what a browser submits
        when a user never interacts with the <select> -- rather than
        assuming it matches the user's real role.

        The target's role is deliberately not the first role in the
        drop-down's query order, so a regression that falls back to "first
        option in the list" cannot pass this test by coincidence."""
        org = _make_org(db_session, "noop")
        admin_role = _get_role(db_session, "Administrator")
        user_role = _get_role(db_session, "User")
        admin = _make_user(db_session, org, admin_role, is_platform_admin=True)
        target = _make_user(db_session, org, user_role)
        db_session.commit()

        with app.app_context():
            login_as(client, admin)
            get_resp = client.get(f"/admin/user/{target.id}/change-account-type")
            assert get_resp.status_code == 200
            select_html = _extract_role_select(get_resp.data.decode())
            options = _parse_role_options(select_html)

            selected = [value for value, is_selected in options if is_selected]
            all_values = [value for value, _ in options]
            # A browser always submits exactly one value for a <select>: the
            # pre-selected option if the user never touches it, otherwise
            # falls back to the first <option> in source order (the native
            # behaviour when no option is marked selected). Mirror that here
            # instead of assuming any particular value, so this exercises
            # the real bug (wrong pre-selection) rather than a hand-picked
            # one.
            submitted_value = selected[0] if selected else all_values[0]

            post_resp = client.post(
                f"/admin/user/{target.id}/change-account-type",
                data={"role": submitted_value},
                follow_redirects=True,
            )
            assert post_resp.status_code == 200

            # Read the persisted role in the same app context as the
            # request: popping this context tears down the scoped session
            # and a query issued after that point does not observe this
            # test's yet-to-be-committed savepoint the same way.
            from app.models.user import User

            refreshed = User.query.get(target.id)
            assert refreshed.role_id == user_role.id, (
                "submitting the form without touching the drop-down changed "
                f"the role from {user_role.id} ({user_role.name}) to "
                f"{refreshed.role_id} ({refreshed.role.name if refreshed.role else None})"
            )
