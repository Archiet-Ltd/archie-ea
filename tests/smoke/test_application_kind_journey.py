"""Journey, in a browser: the Applications list and every count show applications only.

An architect's organisation holds "Alerts gateway and consumers" (composed of "Alerts Gateway" and
"Alerts Consumers"), "Billing", and a code repository "billing-repo" recorded as not software. The Applications
list and the Overview's Applications table show exactly the two applications, the Overview counts 2 and the
owner coverage counts out of 2; the page of the first lists its two parts; the architect changes "Billing"
to not software on its page and the list and the counts drop to 1. A viewer reads the kind and the reason
and has no control. The other organisation's records are never touched.
"""
import os
import re
import tempfile
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from . import fresh_org
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"
SHOTS = Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="application-kind-"))


def sign_in(page, base, email):
    """Sign in from a signed-out browser and wait only until the login page is left."""
    page.context.clear_cookies()
    fresh_org.sign_in(page, base, email)


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / (name + ".png")), full_page=True)


def seed_kind_org(with_viewer=False):
    """An organisation with two applications, three parts-or-repositories and, optionally, a viewer."""
    from app import create_app, db
    from app.models import ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role, User

    info = create_fresh_org(ARCHITECT, extra_roles=("portfolio_manager",))
    app = create_app("testing")
    with app.app_context():
        org_id = info["org_id"]

        def add(name, **fields):
            row = ApplicationComponent(name=name, organization_id=org_id, lifecycle_status="operational", **fields)
            db.session.add(row)
            db.session.commit()
            return row

        whole = add("Alerts gateway and consumers", legacy_application_owner="Dana Reed")
        gateway = add("Alerts Gateway", legacy_application_owner="Dana Reed")
        consumers = add("Alerts Consumers", legacy_application_owner="Dana Reed")
        billing = add("Billing")
        repo = add("billing-repo", repository_type="GitHub", application_kind="not_software")
        for part in (gateway, consumers):
            db.session.add(ArchiMateRelationship(
                type="composition", source_id=whole.archimate_element_id, target_id=part.archimate_element_id,
                organization_id=org_id))
        db.session.commit()
        info["ids"] = {r.name: r.id for r in (whole, gateway, consumers, billing, repo)}
        if with_viewer:
            viewer = User(email="viewer.%s@example.com" % uuid.uuid4().hex[:8], first_name="Vera", last_name="Viewer",
                          organization_id=org_id, confirmed=True, enterprise_role="non_technical_owner")
            viewer.role = Role.query.filter_by(name="Viewer").one()
            viewer.password = PASSWORD
            db.session.add(viewer)
            db.session.commit()
            info["viewer_email"] = viewer.email
        db.session.remove()
    return info


def _listed_names(page, live_server):
    """The names the Applications list shows, read from its rows."""
    page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    labels = page.locator("[data-testid^='btn-actions-']").evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
    names = [label[len("Actions for "):] for label in labels if label and label.startswith("Actions for ")]
    return sorted(set(names))


def _overview(page, live_server):
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("applications-row")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    names = sorted(row.locator("a").first.inner_text().strip() for row in rows.all())
    count = int(re.search(r"\d+", page.get_by_test_id("welcome-applications").inner_text()).group())
    return names, count


def _coverage_total(page, live_server):
    page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page.locator("main").inner_text()


def test_the_list_and_every_count_show_applications_and_a_change_of_kind_moves_them(page, live_server):
    org = seed_kind_org()
    ids = org["ids"]
    page.set_viewport_size({"width": 1440, "height": 900})
    # owner coverage is a portfolio manager's page: read it before and after the architect's change
    sign_in(page, live_server, org["emails"]["portfolio_manager"])
    coverage = _coverage_total(page, live_server)
    assert "Total applications\n\n2" in coverage and "With named owner\n\n1" in coverage, coverage
    sign_in(page, live_server, org["emails"][ARCHITECT])

    both = ["Alerts gateway and consumers", "Billing"]
    assert _listed_names(page, live_server) == both
    names, count = _overview(page, live_server)
    assert names == both, names
    assert count == 2
    _shot(page, "1440-light-overview")

    # the first application lists its two parts; each part's page says it is part of it
    page.goto("%s/applications/%d" % (live_server, ids["Alerts gateway and consumers"]), wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    parts = page.get_by_test_id("application-parts")
    expect(parts).to_contain_text("Alerts Gateway")
    expect(parts).to_contain_text("Alerts Consumers")
    expect(page.get_by_test_id("application-kind-label")).to_have_text("Application")
    parts.get_by_role("link", name="Alerts Gateway").click()
    expect(page.get_by_test_id("application-kind-label")).to_have_text("Part of an application", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("application-kind-reason")).to_contain_text("Part of Alerts gateway and consumers")

    # the architect says Billing is not software
    page.goto("%s/applications/%d" % (live_server, ids["Billing"]), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("#application-kind-select").select_option("not_software")
    page.get_by_test_id("application-kind-form").get_by_role("button", name="Save").click()
    expect(page.get_by_test_id("application-kind-label")).to_have_text("Not software", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("application-kind-reason")).to_contain_text("Set by Launch")
    page.reload()
    expect(page.get_by_test_id("application-kind-label")).to_have_text("Not software")
    _shot(page, "1440-light-billing-not-software")

    assert _listed_names(page, live_server) == ["Alerts gateway and consumers"]
    names, count = _overview(page, live_server)
    assert names == ["Alerts gateway and consumers"] and count == 1, (names, count)

    sign_in(page, live_server, org["emails"]["portfolio_manager"])
    coverage = _coverage_total(page, live_server)
    assert "Total applications\n\n1" in coverage and "With named owner\n\n1" in coverage, coverage
    assert "Leaves out 4 that are parts of an application or not software" in coverage, coverage

    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.classList.add('dark')")
    _shot(page, "390-dark-overview")


def test_a_viewer_reads_the_kind_and_the_reason_but_cannot_change_it(page, live_server):
    org = seed_kind_org(with_viewer=True)
    billing = org["ids"]["Billing"]
    sign_in(page, live_server, org["viewer_email"])
    page.goto("%s/applications/%d" % (live_server, billing), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("application-kind-label")).to_have_text("Application")
    expect(page.get_by_test_id("application-kind-reason")).to_contain_text("Not part of another application")
    expect(page.get_by_test_id("application-kind-form")).to_have_count(0)
    _shot(page, "1440-light-viewer")
    answer = page.evaluate(
        """async (url) => {
            const token = document.querySelector('meta[name="csrf-token"]').content;
            const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'application_kind=not_software'});
            return [r.status, await r.text()];
        }""",
        "/applications/%d/kind" % billing,
    )
    assert answer[0] == 403
    assert "does not have write access to Applications" in answer[1]
