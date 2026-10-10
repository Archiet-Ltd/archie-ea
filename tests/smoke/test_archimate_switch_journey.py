"""R1-08 / SDD 13 R1 test 8: plain language by default, ArchiMate detail
behind a keyboard-operable switch whose state survives reload; no label
truncated to ambiguity at 1024px or the narrow layout.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

from .test_archetype_journeys import _login, _visit, page  # noqa: F401


@pytest.fixture
def typed_journey(seeded):
    from app import create_app, db
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.user import User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        journey = ArchitectureJourney(
            owner_id=owner.id, organization_id=org_id, title="Switch smoke %s" % suffix,
            intent="portfolio_change", selected_layers=["motivation"], programme_type="s4hana",
            journey_state={},
        )
        db.session.add(journey)
        db.session.commit()
        journey_id = journey.id
    yield {"journey_id": journey_id}


def _url(j):
    return "/architecture-journey/work/%s/programme-structure" % j["journey_id"]


def test_switch_is_off_by_default_and_shows_no_archimate_type_name(page, live_server, seeded, typed_journey):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, _url(typed_journey))
    switch = page.locator('[data-testid="archimate-switch"]')
    assert switch.get_attribute("aria-pressed") == "false"
    body = page.inner_text("body")
    assert "WorkPackage" not in body
    assert "Finance and controlling" in body  # plain workstream name


def test_switch_is_keyboard_operable_and_persists_after_reload(page, live_server, seeded, typed_journey):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, _url(typed_journey))
    switch = page.locator('[data-testid="archimate-switch"]')
    switch.focus()
    page.keyboard.press("Enter")
    assert switch.get_attribute("aria-pressed") == "true"
    body = page.inner_text("body")
    assert "process" in body.lower() or "technology" in body.lower()  # a raw workstream_type value now visible

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    switch = page.locator('[data-testid="archimate-switch"]')
    assert switch.get_attribute("aria-pressed") == "true", "state must survive reload (cookie + sessionStorage)"


def test_labels_are_not_truncated_to_ambiguity_at_narrow_and_1024(page, live_server, seeded, typed_journey):
    for width in (390, 1024):
        page.set_viewport_size({"width": width, "height": 900})
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        response, state = _visit(page, live_server, _url(typed_journey))
        assert response.status < 400
        assert state["overflow"] == 0, "the programme structure page scrolls sideways at %spx" % width
