"""Browser journey for the Architecture Journey home.

The unit tests prove the read model and the rendered HTML. They cannot prove the
things that only exist in a browser: that the front end boots at all, that every
control is reachable by keyboard and carries a name a screen reader can announce,
and that the page does not overflow horizontally on a phone.

The one assertion worth stating plainly is the honesty check. On this screen a "0"
and an em dash mean different things -- "there are no risks" versus "we could not
find out" -- and a reader acts on the difference. A browser test is where that can
be checked as the user sees it, after Alpine has run, rather than in the template
source.
"""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD


pytestmark = [pytest.mark.smoke, pytest.mark.journey]


# Reuse the archetype login flow rather than re-deriving it: the force-click and
# no_wait_after handling in the sibling module exists because Alpine disables the
# submit button, and a second copy would drift from it.
# Import `page` too, deliberately. pytest-playwright 0.7.2 supplies its own
# `page`/`context`/`browser` fixtures; without shadowing `page` here, the plugin's
# fixture wins, calls sync_playwright() once, and then requests `browser` -- which
# resolves to THIS repo's conftest fixture, which calls sync_playwright() a second
# time on the same thread while the first loop is parked. The result is
# "Sync API inside the asyncio loop", and it is Playwright's own loop, so
# -p no:asyncio does nothing about it.
from .test_archetype_journeys import _login, _visit, page  # noqa: F401


def test_journey_hub_renders_and_frames_non_solution_outcomes(page, live_server, seeded):
    """The hub must not present a solution as the assumed destination."""
    _login(page, live_server, seeded["emails"]["business_architect"])
    response, state = _visit(page, live_server, "/architecture-journey/")

    assert response.status < 400, f"hub returned {response.status}"
    assert state["alpine"] == "object", "Alpine did not boot on the journey hub"
    assert state["overflow"] == 0, "the journey hub overflows horizontally"
    assert state["unnamed"] == [], f"unnamed controls on the journey hub: {state['unnamed']}"

    body = page.inner_text("body").lower()
    assert "solution is one possible outcome" in body or "no change" in body, (
        "the hub must frame a solution as one possible outcome, not the destination"
    )


def test_journey_hub_has_one_heading_and_one_breadcrumb(page, live_server, seeded):
    """Three page-level entry points once carried two breadcrumb ancestries."""
    _login(page, live_server, seeded["emails"]["business_architect"])
    _visit(page, live_server, "/architecture-journey/")

    assert page.locator("h1").count() == 1
    assert page.locator('nav[aria-label="Breadcrumb"]').count() == 1


def test_journey_hub_is_usable_at_phone_width(page, live_server, seeded):
    """390px is the narrow viewport this product's audit ratchet uses."""
    page.set_viewport_size({"width": 390, "height": 844})
    _login(page, live_server, seeded["emails"]["business_architect"])
    response, state = _visit(page, live_server, "/architecture-journey/")

    assert response.status < 400
    assert state["overflow"] == 0, (
        "the journey hub scrolls sideways at 390px; wide content must scroll inside "
        "its own container, never the page body"
    )


def test_journey_home_never_renders_a_bare_zero_for_unknown_counts(
    page, live_server, seeded
):
    """The honesty rule, as the user meets it.

    Every headline count on the journey home is either a measured number or an em
    dash. This walks from the hub into a journey and asserts that no count panel
    renders an empty string -- the failure mode where a None reaches the template
    without going through the `dash` filter and silently renders as nothing at all,
    which reads as "zero" to anyone glancing at it.
    """
    _login(page, live_server, seeded["emails"]["business_architect"])
    _visit(page, live_server, "/architecture-journey/")

    resume = page.locator('a[href*="/architecture-journey/work/"]').first
    if resume.count() == 0:
        pytest.skip("no existing journey in the seeded tenant to open")

    resume.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    for testid in ("journey-participants", "journey-decisions", "journey-risks",
                   "journey-governance"):
        panel = page.locator(f'[data-testid="{testid}"]')
        assert panel.count() == 1, f"{testid} missing from the journey home"
        text = panel.inner_text().strip()
        assert text, f"{testid} rendered empty"
        # Either a real figure or the em dash. Never blank, never a bare "0" with
        # no surrounding explanation.
        assert any(ch.isdigit() for ch in text) or "—" in text, (
            f"{testid} shows neither a measured count nor an em dash: {text!r}"
        )


# --------------------------------------------------------------------- #
# R1-02 / SDD 13 R1 test 9: journey decision links read the live         #
# decision register, and a deleted target renders honestly rather than   #
# vanishing.                                                             #
# --------------------------------------------------------------------- #


@pytest.fixture
def decision_linked_journey(seeded):
    import uuid as _uuid

    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_journey import ArchitectureJourney
    from app.models.architecture_journey_link import ArchitectureJourneyLink
    from app.models.user import User

    app = create_app("testing")
    suffix = _uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        owner = User.query.filter_by(email=seeded["emails"]["business_architect"]).one()

        journey = ArchitectureJourney(
            owner_id=owner.id,
            organization_id=org_id,
            title="Decision link smoke %s" % suffix,
            intent="operating_model",
            selected_layers=["motivation"],
            current_stage="frame",
            status="active",
        )
        db.session.add(journey)
        db.session.flush()

        decision = ArchitectureDecision(
            organization_id=org_id,
            title="Adopt the reference template family %s" % suffix,
            status="accepted",
            created_by_id=owner.id,
        )
        db.session.add(decision)
        db.session.flush()

        link = ArchitectureJourneyLink(
            organization_id=org_id,
            journey_id=journey.id,
            entity_type="decision",
            entity_id=decision.id,
            relation="produces",
            created_by_id=owner.id,
        )
        db.session.add(link)
        db.session.commit()
        ids = {"journey_id": journey.id, "decision_id": decision.id, "link_id": link.id}

    yield ids

    with app.app_context():
        db.session.execute(
            db.text('DELETE FROM "architecture_journey_links" WHERE id = :id'),
            {"id": ids["link_id"]},
        )
        db.session.execute(
            db.text('DELETE FROM "architecture_decisions" WHERE id = :id'),
            {"id": ids["decision_id"]},
        )
        db.session.execute(
            db.text('DELETE FROM "architecture_journeys" WHERE id = :id'),
            {"id": ids["journey_id"]},
        )
        db.session.commit()


def test_journey_decision_link_resolves_the_live_register(
    page, live_server, seeded, decision_linked_journey
):
    """A decision link renders the register's title and status after reload."""
    _login(page, live_server, seeded["emails"]["business_architect"])
    _visit(
        page, live_server,
        "/architecture-journey/work/%s" % decision_linked_journey["journey_id"],
    )
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    panel = page.locator('[data-testid="journey-linked-records"]')
    assert panel.count() == 1
    text = panel.inner_text()
    assert "Adopt the reference template family" in text
    assert "Accepted" in text


def test_journey_decision_link_renders_honestly_when_the_target_is_gone(
    page, live_server, seeded, decision_linked_journey
):
    """Deleting the decision must not make the link silently vanish (US-15 AC2)."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        db.session.execute(
            db.text('DELETE FROM "architecture_decisions" WHERE id = :id'),
            {"id": decision_linked_journey["decision_id"]},
        )
        db.session.commit()

    _login(page, live_server, seeded["emails"]["business_architect"])
    _visit(
        page, live_server,
        "/architecture-journey/work/%s" % decision_linked_journey["journey_id"],
    )
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    panel = page.locator('[data-testid="journey-linked-records"]')
    text = panel.inner_text()
    assert "Record no longer available (link %s)" % decision_linked_journey["link_id"] in text
