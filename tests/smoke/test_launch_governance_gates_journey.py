"""An organisation's administrator defines a governance gate and it holds.

On /admin/governance-gates the administrator of a brand-new organisation adds
a gate through the "Add Gate" dialog, edits its threshold through the row's
Edit control, reloads, and sees both the gate and the edited threshold. A
second brand-new organisation's administrator sees none of it, cannot read or
change it through the page's interface, and can define a gate with the same
name for their own organisation.

The last step is the one the product used to fail: gate names were unique
across the whole platform, so once one organisation had configured a gate
every other organisation got "Failed to create gate" for the same name -
which the dialog then reported as a network error. On an existing database
run ``flask --app manage reconcile-schema`` once (it is part of the
schema deploy) before this journey.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

GATES_PATH = "/admin/governance-gates"


def _make_administrator(user_id):
    """Give the organisation's own administrator the Administrator role.

    Configuring gates needs the ADMINISTER permission; like the organisation
    itself, the person's role is set up directly rather than through a screen.
    """
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.user import Role, User

        user = db.session.get(User, user_id)
        user.role = Role.query.filter_by(name="Administrator").one()
        db.session.commit()
        db.session.remove()


def _admin_org():
    org = create_fresh_org("enterprise_architect", org_admin=True)
    _make_administrator(org["user_ids"]["enterprise_architect"])
    return org


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _open_gates(page, base):
    page.goto(base + GATES_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _create_gate(page, name, description, completeness):
    page.get_by_role("button", name="Add Gate", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#gg-gate-name").fill(name)
    dialog.locator("#gg-description").fill(description)
    dialog.locator("#gg-min-completeness").fill(str(completeness))
    with page.expect_response(
        lambda r: r.url.endswith("/admin/api/governance-gates") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as created:
        dialog.get_by_role("button", name="Create Gate").click()
    return created.value, dialog


def _gate_row(page, name):
    return page.locator("tbody tr").filter(has_text=name)


def test_admin_defines_edits_and_keeps_a_gate_to_their_organisation(browser, live_server):
    first = _admin_org()
    second = _admin_org()
    gate_name = "arb_readiness_%s" % first["suffix"]

    context, page = _new_page(browser)
    sign_in(page, live_server, first["emails"]["enterprise_architect"])
    _open_gates(page, live_server)
    expect(page.get_by_text("No configured overrides. System defaults are active.")).to_be_visible(
        timeout=PAGE_TIMEOUT)

    response, _dialog = _create_gate(page, gate_name, "Board readiness for launch", 70)
    assert response.status == 201, "creating the gate answered %s" % response.status
    row = _gate_row(page, gate_name)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("70%")
    expect(row).to_contain_text("Board readiness for launch")

    # Edit through the row's own Edit control.
    row.get_by_role("button", name="Edit " + gate_name).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.locator("#gg-gate-name")).to_have_value(gate_name)
    dialog.locator("#gg-min-completeness").fill("85")
    with page.expect_response(
        lambda r: "/admin/api/governance-gates/" in r.url and r.request.method == "PUT",
        timeout=PAGE_TIMEOUT,
    ) as updated:
        dialog.get_by_role("button", name="Save Changes").click()
    assert updated.value.status == 200, "editing the gate answered %s" % updated.value.status
    expect(page.get_by_text("Gate updated.")).to_be_visible(timeout=PAGE_TIMEOUT)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    row = _gate_row(page, gate_name)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("85%")
    expect(row).to_contain_text("Enabled")
    gate_id = next(g["id"] for g in api(page, "GET", "/admin/api/governance-gates")[1]["gates"]
                   if g["gate_name"] == gate_name)
    context.close()

    # A second organisation sees nothing of it and cannot touch it.
    context, page = _new_page(browser)
    sign_in(page, live_server, second["emails"]["enterprise_architect"])
    _open_gates(page, live_server)
    expect(page.get_by_text("No configured overrides. System defaults are active.")).to_be_visible(
        timeout=PAGE_TIMEOUT)
    expect(_gate_row(page, gate_name)).to_have_count(0)
    status, body = api(page, "GET", "/admin/api/governance-gates")
    assert status == 200 and all(g["gate_name"] != gate_name for g in body["gates"])
    status, _ = api(page, "PUT", "/admin/api/governance-gates/%d" % gate_id, {"min_completeness": 1})
    assert status == 404, "another organisation's gate answered %s to an edit" % status

    # ...and defines its own gate under the same name.
    response, dialog = _create_gate(page, gate_name, "Our own readiness gate", 60)
    assert response.status == 201, (
        "a second organisation could not define a gate named like the first one's (HTTP %s): %s"
        % (response.status, dialog.locator("p.text-destructive-emphasis").all_inner_texts()))
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    row = _gate_row(page, gate_name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text("60%")
    expect(row).to_contain_text("Our own readiness gate")

    # A duplicate inside one organisation is still refused, with the reason shown.
    response, dialog = _create_gate(page, gate_name, "Duplicate", 50)
    assert response.status == 409
    expect(dialog.get_by_text("Gate '%s' already exists" % gate_name)).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()

    # The first organisation's gate is untouched by all of this.
    context, page = _new_page(browser)
    sign_in(page, live_server, first["emails"]["enterprise_architect"])
    _open_gates(page, live_server)
    row = _gate_row(page, gate_name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text("85%")
    expect(row).to_contain_text("Board readiness for launch")
    context.close()
