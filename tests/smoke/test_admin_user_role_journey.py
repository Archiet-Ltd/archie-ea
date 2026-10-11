"""platform_admin: change a user's account type (Role/Permission vocabulary)
and confirm it persisted -- a real write journey, not just page reachability.

No prior smoke test drives /admin/user/<id>/change-account-type. This is a
consequential admin flow: it is the write path for the `Role`/`Permission`
vocabulary noted in memory as one of Archie's two/three parallel
authorization systems (separate from `enterprise_role`, which drives
sidebar/persona selection) -- getting this journey wrong silently changes
what a user can do platform-wide.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _account_type_text(page, live_server, target_user_id):
    """The role name the read-only user page reports right now.

    The change-account-type form's own dropdown does not pre-select the
    user's current role (it is built with no `obj=user`, so nothing carries
    a `selected` attribute and the browser just shows whichever option
    sorts first) -- so it cannot be trusted to say what the role was before
    this test touched it. The read-only info page has no such form-default
    problem: it prints `user.role.name` directly.
    """
    page.goto(live_server + '/admin/user/%s' % target_user_id, timeout=PAGE_TIMEOUT)
    dd = page.locator("dt", has_text="Account type").locator("xpath=following-sibling::dd[1]")
    expect(dd).to_be_visible(timeout=PAGE_TIMEOUT)
    return dd.inner_text().strip()


def _option_value_for_label(select, label):
    for option in select.locator('option').all():
        if option.inner_text().strip() == label:
            return option.get_attribute('value')
    raise AssertionError('no <option> in the account-type select is labelled %r' % label)


def _set_role(page, live_server, target_user_id, value):
    """Submit the change-account-type form with `value` selected, and wait
    for the flashed confirmation -- the one piece of this journey worth
    repeating for the restore at the end, so the restore is proven too
    rather than assumed to have taken effect."""
    page.goto(
        live_server + '/admin/user/%s/change-account-type' % target_user_id,
        timeout=PAGE_TIMEOUT,
    )
    select = page.locator('select[name="role"]')
    expect(select).to_be_visible(timeout=PAGE_TIMEOUT)
    label = select.locator('option[value="%s"]' % value).inner_text()
    select.select_option(value)
    page.get_by_role('button', name='Update role', exact=True).click()
    expect(page.get_by_text('successfully changed to', exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
    return label


def test_platform_admin_changes_user_account_type_and_it_persists(browser, live_server, seeded):
    page = browser.new_page()
    target_user_id = seeded['ids']['app_manager_user']
    try:
        _login(page, live_server, seeded['emails']['platform_admin'])

        # The role this user actually holds walking in, read from the page
        # that has no form-default bug, so the restore below returns it to
        # its real starting state rather than to whatever the edit form
        # happened to show pre-selected.
        original_role_name = _account_type_text(page, live_server, target_user_id)

        response = page.goto(
            live_server + '/admin/user/%s/change-account-type' % target_user_id,
            timeout=PAGE_TIMEOUT,
        )
        assert response.status == 200
        assert page.title() != '403 — Forbidden'

        select = page.locator('select[name="role"]')
        expect(select).to_be_visible(timeout=PAGE_TIMEOUT)

        original_value = _option_value_for_label(select, original_role_name)

        # Pick whichever option isn't the role this user actually holds, so
        # the test proves a real change rather than re-submitting the
        # current value (or, thanks to the form-default bug above, silently
        # re-submitting a value that was never the current one).
        options = select.locator('option').all()
        option_values = [o.get_attribute('value') for o in options]
        target_value = next(v for v in option_values if v and v != original_value)

        try:
            target_label = _set_role(page, live_server, target_user_id, target_value)

            # ---- Persistence: reload and re-check via the read-only user info page ----
            page.goto(
                live_server + '/admin/user/%s' % target_user_id,
                timeout=PAGE_TIMEOUT,
            )
            expect(page.get_by_text(target_label, exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
        finally:
            # `app_manager_user` is the same application_manager the shared
            # seeded fixture hands to every other smoke test in this session
            # under the "application_manager" archetype, with its Architect
            # role assumed by the tests that read /applications/<id>/edit,
            # which requires exactly that role. Leaving the account type
            # changed here would 403 every one of those tests for the rest
            # of the run for a reason that has nothing to do with what they
            # check -- restore it, proving the restore took effect the same
            # way the change itself was proven.
            _set_role(page, live_server, target_user_id, original_value)
            assert _account_type_text(page, live_server, target_user_id) == original_role_name
    finally:
        page.close()
