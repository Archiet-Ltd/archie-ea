"""security_architect: open the service-status page from the sidebar footer,
see the current status and a seeded incident, subscribe, reload, and see the
subscription kept.

Drives the real controls: the footer link and the Subscribe button (a form
post). The incident is seeded through the model and removed afterwards, and
the subscription is put back as it was, so the journey can run again.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _with_app(fn):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        try:
            return fn(db)
        finally:
            db.session.remove()


def _seed_incident(title):
    def _do(db):
        from app.models.service_incident import ServiceIncident

        row = ServiceIncident(title=title, impact="degraded", summary="Answers take longer than usual.")
        db.session.add(row)
        db.session.commit()
        return row.id

    return _with_app(_do)


def _delete_incident(incident_id):
    def _do(db):
        from app.models.service_incident import ServiceIncident

        ServiceIncident.query.filter_by(id=incident_id).delete()
        db.session.commit()

    _with_app(_do)


def _set_subscription(email, value):
    def _do(db):
        from app.models.user import User
        from app.modules.monitoring.services.service_status import set_subscribed

        user = User.query.filter_by(email=email).one()
        set_subscribed(user.id, value)

    _with_app(_do)


def test_security_architect_subscribes_to_service_status(browser, live_server, seeded):
    email = seeded["emails"]["security_architect"]
    title = f"Slow answers {uuid.uuid4().hex[:6]}"
    incident_id = _seed_incident(title)
    _set_subscription(email, False)
    page = browser.new_page()
    try:
        _login(page, live_server, email)

        # Reached from the footer, not by typing the URL.
        page.get_by_test_id("footer-service-status").click()
        page.wait_for_url("**/status", timeout=PAGE_TIMEOUT)

        state = page.get_by_test_id("service-status-state")
        expect(state).to_have_attribute("data-state", "degraded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id(f"service-incident-{incident_id}")).to_contain_text(title)
        expect(page.get_by_test_id("service-status-open-incidents")).to_contain_text(title)

        page.get_by_test_id("service-status-subscribe").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_test_id("service-status-subscribed")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.reload()
        expect(page.get_by_test_id("service-status-subscribed")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("service-status-unsubscribe")).to_be_visible()
    finally:
        page.close()
        _delete_incident(incident_id)
        _set_subscription(email, False)
