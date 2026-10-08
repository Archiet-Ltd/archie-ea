"""Org-admin toggle Playwright journey.

The platform admin toggles org-admin for a user through the organisation
detail page, and the promoted user can reach the team-management page.
This exercises the unified authority: the toggle writes both the User role
and the OrgRole row in one transaction, so every surface that answers "is
this user an org admin" sees the same answer.  On main the toggle only
writes the denormalised is_org_admin column, leaving OrgRole untouched, so
the team page (which reads OrgRole via rbac_service) returns 403.
"""
import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/logout", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    page.context.clear_cookies()
    page.goto(base + "/account/login", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url,
                          timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, f"could not sign in as {email}"


def test_org_admin_toggle_grants_and_revokes_team_access(
    page, live_server, seeded
):
    """Toggle org-admin through the browser and verify team access follows.

    Grant: the promoted user reaches /admin/team (200).
    Revoke: the demoted user is refused /admin/team (403).

    This fails on main because the toggle there writes only the
    is_org_admin column while the team page reads OrgRole — the two
    systems disagree and the promoted user gets a 403.
    """
    admin_email = seeded["emails"]["platform_admin"]
    target_email = seeded["emails"]["solution_architect"]
    org_id = seeded["ids"]["org"]

    # ── Grant org-admin ──────────────────────────────────────────────
    _login(page, live_server, admin_email)
    page.goto(
        live_server + f"/admin/organizations/{org_id}",
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    page.wait_for_selector("table", timeout=PAGE_TIMEOUT)

    # The data-confirm attribute on the form triggers a custom
    # Platform.modal.confirm dialog (ui/modal.js), not a native browser
    # dialog.  The handler sets form.dataset.confirmed = 'yes' and calls
    # requestSubmit() when the user confirms.  We do the same directly
    # so the toggle takes effect without depending on modal DOM details.
    #
    # Users are sorted by name; target the solution_architect row by email
    # so we toggle the right user.
    row = page.locator(f"tr:has-text('{target_email}')")
    row.locator("button:has-text('Make Admin')").click()
    page.evaluate(f"""() => {{
        const rows = document.querySelectorAll('tr');
        for (const row of rows) {{
            if (row.textContent.includes('{target_email}')) {{
                const form = row.querySelector('form');
                if (form) {{ form.dataset.confirmed = 'yes'; form.requestSubmit(); }}
                break;
            }}
        }}
    }}""")
    page.wait_for_load_state("networkidle")

    # ── Verify team access after grant ───────────────────────────────
    _login(page, live_server, target_email)
    resp = page.goto(
        live_server + "/admin/team",
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert resp.status == 200, (
        f"Expected 200 after org-admin grant, got {resp.status}"
    )

    # ── Revoke org-admin ─────────────────────────────────────────────
    _login(page, live_server, admin_email)
    page.goto(
        live_server + f"/admin/organizations/{org_id}",
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    page.wait_for_selector("table", timeout=PAGE_TIMEOUT)

    row = page.locator(f"tr:has-text('{target_email}')")
    row.locator("button:has-text('Revoke Admin')").click()
    page.evaluate(f"""() => {{
        const rows = document.querySelectorAll('tr');
        for (const row of rows) {{
            if (row.textContent.includes('{target_email}')) {{
                const form = row.querySelector('form');
                if (form) {{ form.dataset.confirmed = 'yes'; form.requestSubmit(); }}
                break;
            }}
        }}
    }}""")
    page.wait_for_load_state("networkidle")

    # ── Verify team access after revoke ──────────────────────────────
    _login(page, live_server, target_email)
    resp = page.goto(
        live_server + "/admin/team",
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    assert resp.status == 403, (
        f"Expected 403 after org-admin revoke, got {resp.status}"
    )