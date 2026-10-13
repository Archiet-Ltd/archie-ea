"""R1-05 / SDD 13 R1 test 6: an existing workstream with no ArchiMate element
shows "Not yet in the model"; clicking "Add to model" gives it one, and the
state survives a reload. Plus the authorisation row for the new write route
(security.md 10.1): allowed {enterprise_architect, cto}, every other
archetype -- platform_admin included -- refused. Deliberately NOT added to
POLICY in test_authorisation_matrix.py (it asserts platform_admin passes
every entry there).
"""

import uuid

import pytest

from .conftest import ARCHETYPES, PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

from .test_archetype_journeys import _login, _visit, page  # noqa: F401

ALLOWED_ROLES = {"enterprise_architect", "cto"}


@pytest.fixture
def bare_workstream(seeded):
    """A workstream created directly (as pre-R1-05 rows were): no element."""
    from app import create_app, db
    from app.models.strategic import StrategicInitiative
    from app.models.transformation_programme import ProgrammeWorkstream
    from app.models.user import User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org_id = seeded["ids"]["org"]
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        programme = StrategicInitiative(
            organization_id=org_id, name="Add-to-model smoke %s" % suffix,
            description="Fixture programme.", record_kind="transformation_programme",
            status="draft", owner_id=owner.id, revision=1,
        )
        db.session.add(programme)
        db.session.flush()
        workstream = ProgrammeWorkstream(
            organization_id=org_id, programme_id=programme.id, workstream_type="process",
            objective="Legacy workstream objective %s" % suffix, scope_expression={},
            lifecycle_stage="objective", lead_id=owner.id, revision=1,
        )
        db.session.add(workstream)
        db.session.commit()
        ids = {"programme_id": programme.id, "workstream_id": workstream.id}
    yield ids


def _url(ids):
    return "/solutions/programmes/%s/workstreams/%s/objective" % (
        ids["programme_id"], ids["workstream_id"])


def test_enterprise_architect_adds_an_existing_workstream_to_the_model(
    page, live_server, seeded, bare_workstream
):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _visit(page, live_server, _url(bare_workstream))

    assert page.locator('[data-testid="workstream-not-in-model"]').count() == 1
    with page.expect_response(
        lambda r: r.url.endswith("/archimate-element") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as posted:
        page.get_by_role("button", name="Add to model").click(force=True, no_wait_after=True)
    assert posted.value.status in (200, 303), posted.value.status
    # Fresh load of the page (the POST's 303 navigation may still be in
    # flight, which makes page.reload() abort) -- proves it persisted.
    _visit(page, live_server, _url(bare_workstream))

    assert page.locator('[data-testid="workstream-not-in-model"]').count() == 0
    assert "In the model" in page.locator('[data-testid="workstream-archimate-status"]').inner_text()


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_workstream_add_to_model_authorisation(archetype, page, live_server, seeded, bare_workstream):
    _login(page, live_server, seeded["emails"][archetype])
    page.goto(live_server + "/architecture-journey/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    token = page.evaluate("() => (document.querySelector('meta[name=csrf-token]') || {}).content || ''")
    response = page.request.post(
        live_server + _url(bare_workstream).replace("/objective", "/archimate-element"),
        headers={"X-CSRFToken": token},
        form={"csrf_token": token, "command_key": uuid.uuid4().hex},
        max_redirects=0,
    )
    if archetype in ALLOWED_ROLES:
        assert response.status in (200, 303), (archetype, response.status)
    else:
        assert response.status in (401, 403, 404), (archetype, response.status)
