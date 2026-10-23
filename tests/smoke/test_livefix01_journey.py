"""Journey tests for the three defects found by the live-proof run on build b3219587.

Each test walks the page as the relevant persona in a fresh organisation,
asserting the outcome a person needs.  Each is red on main and green after
the fix.
"""

import json
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass


def _make_org_with_cto():
    """Create a fresh org with a CTO user and seed capabilities + mapping.

    Returns (org_id, email) so the test can sign in and navigate.
    """
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        Role.insert_roles()

        suffix = uuid.uuid4().hex[:8]
        org = Organization(
            name="Livefix Org %s" % suffix,
            slug="livefix-org-%s" % suffix,
        )
        db.session.add(org)
        db.session.flush()

        cto_role = Role.query.filter_by(name="CTO").first()
        user = User(
            email="cto-%s@example.com" % suffix,
            first_name="CTO",
            last_name="Livefix",
            organization_id=org.id,
            confirmed=True,
            enterprise_role="cto",
            role=cto_role,
        )
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()

        # Seed capabilities and a mapping so the full investment matrix renders.
        caps = []
        for i in range(3):
            cap = UnifiedCapability(
                name="Livefix Capability %d" % i,
                code="LIVEFIX-%d" % i,
                organization_id=org.id,
            )
            db.session.add(cap)
            db.session.flush()
            caps.append(cap)

        appc = ApplicationComponent(
            name="Livefix App",
            organization_id=org.id,
        )
        db.session.add(appc)
        db.session.flush()

        mapping = UnifiedApplicationCapabilityMapping(
            unified_capability_id=caps[0].id,
            application_component_id=appc.id,
        )
        db.session.add(mapping)
        db.session.commit()

        return org.id, user.email


def _make_fresh_org(role="enterprise_architect"):
    """Create a fresh org with a user of the given role.

    Returns (org_id, email).
    """
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        db.create_all()
        Role.insert_roles()

        suffix = uuid.uuid4().hex[:8]
        org = Organization(
            name="Livefix Empty %s" % suffix,
            slug="livefix-empty-%s" % suffix,
        )
        db.session.add(org)
        db.session.flush()

        role_obj = Role.query.filter_by(name="Enterprise Architect").first()
        user = User(
            email="ea-%s@example.com" % suffix,
            first_name="EA",
            last_name="Livefix",
            organization_id=org.id,
            confirmed=True,
            enterprise_role=role,
            role=role_obj,
        )
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()

        return org.id, user.email


class TestInvestmentPrioritiesNoConsoleError:
    """The investment priorities page loads with no script error."""

    def test_investment_priorities_no_console_error(self, browser, live_server):
        """A CTO in a workspace with capabilities and mappings sees the full
        investment matrix with no console error and no request to /api/client-error."""
        org_id, email = _make_org_with_cto()
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        console_errors = []
        client_error_requests = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("request", lambda r: client_error_requests.append(r.url) if "client-error" in r.url else None)

        _login(page, live_server, email)
        page.goto(live_server + "/architecture/investment-priorities", wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(6000)

        assert len(console_errors) == 0, (
            "Investment priorities page has console errors: %s" % json.dumps(console_errors)
        )
        assert len(client_error_requests) == 0, (
            "Investment priorities page posted to /api/client-error: %s" % client_error_requests
        )


class TestDecisionFormPlaceholder:
    """The decision form's consequences placeholder uses real line breaks."""

    def test_consequences_placeholder_has_no_backslash(self, browser, live_server):
        """The consequences textarea placeholder contains no backslash-n and
        renders as three visual lines."""
        org_id, email = _make_fresh_org("enterprise_architect")
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        _login(page, live_server, email)

        page.goto(live_server + "/architecture/decisions/new", wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(3000)

        ph = page.get_attribute("#consequences", "placeholder")
        assert "\\n" not in ph, (
            "Consequences placeholder contains literal backslash-n: %r" % ph
        )

        # Measure visual line count by rendering the placeholder text in a
        # hidden div with the same font and width.
        lines = page.evaluate("""() => {
            const el = document.getElementById('consequences');
            const div = document.createElement('div');
            div.style.cssText = 'position:absolute;visibility:hidden;white-space:pre-wrap;width:'
                + getComputedStyle(el).width + 'px;font:' + getComputedStyle(el).font;
            div.textContent = el.getAttribute('placeholder');
            document.body.appendChild(div);
            const h = div.getBoundingClientRect().height;
            document.body.removeChild(div);
            const lh = parseFloat(getComputedStyle(el).lineHeight) || 20;
            return Math.round(h / lh);
        }""")
        assert lines >= 3, (
            "Consequences placeholder renders as %d lines, expected at least 3" % lines
        )


class TestModelAsOfEmptyState:
    """The Model as of page shows the shared empty state with a link."""

    def test_model_as_of_shows_empty_state_with_link(self, browser, live_server):
        """In a workspace with no model, the Model as of page shows the
        empty-state text explaining why the page is empty and a link to add
        model elements."""
        org_id, email = _make_fresh_org("enterprise_architect")
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        _login(page, live_server, email)

        page.goto(live_server + "/intelligence/history/as-of", wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(4000)

        main = page.inner_text("main")
        assert "No model recorded for this date" in main, (
            "Empty-state title not found in main content"
        )
        assert "add or import" in main.lower(), (
            "Empty-state description not found in main content"
        )

        # The empty state must include a link that answers 200.
        link = page.locator('a[href*="composer"]').first
        assert link.count() > 0, (
            "No link to the composer found in the empty state"
        )
        href = link.get_attribute("href")
        # Navigate to the link to verify it lands on a page with content.
        page.goto(live_server + href, wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(3000)
        assert page.locator("main").inner_text().strip(), (
            "Empty-state link %s landed on an empty page" % href
        )