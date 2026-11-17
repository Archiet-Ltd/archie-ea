"""A team sees which requirements no test proves, and which ones last failed.

    enterprise architect: a requirement with no test is listed as unverified; they link a test
    to it and record a passing result, so it leaves the list; they record a failure and it
    returns under "last failed". The requirement's own page shows the test, its result, the
    commit and the date. Another organisation's requirements and tests never show.
"""

import os
import pathlib
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization
    from app.modules.architecture.services import requirement_verification_service as svc

    app = create_app("testing")
    noun = "Ver %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        other = Organization(name="Other %s" % noun, slug="other-%s" % uuid.uuid4().hex[:8])
        db.session.add(other)
        db.session.flush()
        other_id = other.id
        db.session.commit()

        def element(owner, name, kind):
            row = ArchiMateElement(name=name, type=kind, organization_id=owner,
                                   layer="motivation" if kind == "Requirement" else "technology")
            db.session.add(row)
            db.session.commit()
            return row

        mine = element(org_id, "%s Orders are totalled to the cent" % noun, "Requirement")
        theirs = element(other_id, "%s Other organisation requirement" % noun, "Requirement")
        theirs_id = theirs.id
        svc.link_test(theirs_id, other_id, new_test_name="%s Other organisation test" % noun)
        theirs_failing = element(other_id, "%s Other organisation failing" % noun, "Requirement")
        test = svc.link_test(theirs_failing.id, other_id, new_test_name="%s Other failing test" % noun)
        svc.record_result(test.id, other_id, "failed", commit="0ther00")
        return {"org": org_id, "other": other_id, "noun": noun, "req": mine.id, "req_name": mine.name}


def _cleanup(seed):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        for owner in (seed["org"], seed["other"]):
            names = ArchiMateElement.query.filter(ArchiMateElement.organization_id == owner,
                                                  ArchiMateElement.name.like("%s %%" % seed["noun"]))
            ids = [row.id for row in names]
            if not ids:
                continue
            try:
                ArchiMateRelationship.query.filter(
                    ArchiMateRelationship.organization_id == owner,
                    db.or_(ArchiMateRelationship.source_id.in_(ids), ArchiMateRelationship.target_id.in_(ids)),
                ).delete(synchronize_session=False)
                ArchiMateElement.query.filter(ArchiMateElement.organization_id == owner,
                                              ArchiMateElement.id.in_(ids)).delete(synchronize_session=False)
                db.session.commit()
            except Exception:
                db.session.rollback()
        try:
            Organization.query.filter_by(id=seed["other"]).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()


@pytest.fixture(scope="module")
def world(seeded, live_server):
    seed = _seed(seeded["ids"]["org"])
    yield seed
    _cleanup(seed)


@pytest.fixture
def shots(tmp_path):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _shot(page, folder, name, testid):
    """The card a person is looking at, at the current width (the page scrolls inside its shell)."""
    page.get_by_test_id(testid).scroll_into_view_if_needed()
    page.get_by_test_id(testid).screenshot(
        path=str(pathlib.Path(folder) / ("%s-%d.png" % (name, page.viewport_size["width"]))))


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _coverage(page, base):
    page.goto(base + "/architecture/traceability", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page.locator('[data-testid="unverified-requirements"]'), page.locator('[data-testid="failed-requirements"]')


def test_a_requirement_moves_between_unverified_passing_and_last_failed(page, live_server, seeded, world, shots):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    name, noun = world["req_name"], world["noun"]
    test_name = "%s test_totals" % noun

    # Unverified: the requirement is listed; the other organisation's never is.
    unverified, failed = _coverage(page, live_server)
    expect(unverified).to_contain_text(name)
    expect(failed).not_to_contain_text(name)
    expect(page.locator("main")).not_to_contain_text("Other organisation")
    page.set_viewport_size({"width": 1440, "height": 900})
    _shot(page, shots, "coverage-unverified", "unverified-requirements")

    # Its page offers to link a test; the list's row opens it.
    unverified.get_by_role("link", name=name).click()
    page.wait_for_url(lambda url: "/traceability/%d" % world["req"] in url, timeout=PAGE_TIMEOUT)
    panel = page.locator('[data-testid="verified-by"]')
    expect(panel).to_contain_text("No test verifies this requirement")
    expect(panel).to_contain_text("Unverified")
    page.fill("#verify-new-test", test_name)
    page.get_by_test_id("link-test-submit").click()
    page.wait_for_url(lambda url: "/traceability/%d" % world["req"] in url, timeout=PAGE_TIMEOUT)
    row = page.locator('[data-testid="verifying-test"]').filter(has_text=test_name)
    expect(row).to_contain_text("Not run yet")

    # Record a passing result: the page shows test, result, commit and date.
    page.locator("#result-commit").fill("a1b2c3d")
    page.locator("#result-value").select_option("passed")
    page.get_by_test_id("record-result-submit").click()
    row = page.locator('[data-testid="verifying-test"]').filter(has_text=test_name)
    expect(row).to_contain_text("Passed")
    expect(row).to_contain_text("a1b2c3d")
    expect(row).to_contain_text("UTC")
    expect(panel).to_contain_text("Passing")
    page.set_viewport_size({"width": 1440, "height": 900})
    _shot(page, shots, "requirement-passing", "verified-by")

    # It has left the list, and is not under last failed either.
    unverified, failed = _coverage(page, live_server)
    expect(unverified).not_to_contain_text(name)
    expect(failed).not_to_contain_text(name)

    # Record a failure: it returns, under last failed, naming the failing test.
    page.goto("%s/architecture/traceability/%d" % (live_server, world["req"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("#result-commit").fill("f00dbab")
    page.locator("#result-value").select_option("failed")
    page.get_by_test_id("record-result-submit").click()
    row = page.locator('[data-testid="verifying-test"]').filter(has_text=test_name)
    expect(row).to_contain_text("Failed")
    expect(row).to_contain_text("f00dbab")
    expect(page.locator('[data-testid="verified-by"]')).to_contain_text("Last failed")

    unverified, failed = _coverage(page, live_server)
    expect(unverified).not_to_contain_text(name)
    expect(failed).to_contain_text(name)
    expect(failed).to_contain_text(test_name)
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.classList.add('dark')")
    _shot(page, shots, "coverage-last-failed-dark", "failed-requirements")

    # Both links land on a page with the thing clicked.
    failed.get_by_role("link", name=name).click()
    page.wait_for_url(lambda url: "/traceability/%d" % world["req"] in url, timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Verified by")).to_be_visible()
    page.evaluate("document.documentElement.classList.add('dark')")
    # On a phone the commit and time sit under the test's name, still readable.
    stacked = page.locator('[data-testid="verifying-test"]').filter(has_text=test_name)
    expect(stacked.locator("span.md\\:hidden")).to_contain_text("f00dbab")
    expect(stacked.locator("span.md\\:hidden")).to_be_visible()
    _shot(page, shots, "requirement-failed-dark", "verified-by")
    page.locator('[data-testid="verifying-test"]').filter(has_text=test_name).get_by_role("link", name=test_name).click()
    expect(page.locator("h1")).to_contain_text(test_name)

    # The other organisation's failing test is never listed for this one.
    _, failed = _coverage(page, live_server)
    expect(failed).not_to_contain_text("Other organisation")


@pytest.fixture
def empty_org_person(live_server):
    """An architect in an organisation that has recorded nothing yet."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name="Empty %s" % suffix, slug="empty-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        user = User(email="empty-%s@example.com" % suffix, first_name="Empty", last_name="Architect",
                    organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()
        role = Role.query.filter_by(name="Architect").first()
        if role is not None:
            user.role_id = role.id
        db.session.commit()
        ids = (org.id, user.id, user.email)
    yield {"email": ids[2]}
    with app.app_context():
        try:
            User.query.filter_by(id=ids[1]).delete(synchronize_session=False)
            Organization.query.filter_by(id=ids[0]).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()


def test_an_organisation_with_no_requirements_is_told_why_and_what_to_do(page, live_server, world, empty_org_person):
    _login(page, live_server, empty_org_person["email"])
    page.goto(live_server + "/architecture/traceability", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    note = page.get_by_test_id("verification-none")
    expect(note).to_contain_text("No requirement is recorded yet")
    expect(page.locator("main")).not_to_contain_text(world["noun"])
    page.get_by_role("link", name="Open the model composer").click()
    page.wait_for_url(lambda url: "composer" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("main, body").first).not_to_be_empty()


@pytest.fixture
def reader_person(live_server, world):
    """A member of the same organisation whose role does not let them change the model."""
    from app import create_app, db
    from app.core.auth.permissions import can_change_model
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        user = User(email="reader-%s@example.com" % suffix, first_name="Reader", last_name="Person",
                    organization_id=world["org"], confirmed=True, enterprise_role="procurement",
                    role=Role.query.filter_by(name="Viewer").one())
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        assert not can_change_model(user), "the reader must not be allowed to change the model"
        ids = (user.id, user.email)
    yield {"email": ids[1]}
    with app.app_context():
        try:
            User.query.filter_by(id=ids[0]).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()


def test_a_person_who_may_not_change_the_model_sees_the_tests_but_no_controls(page, live_server, world, reader_person):
    _login(page, live_server, reader_person["email"])
    page.goto("%s/architecture/traceability/%d" % (live_server, world["req"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator('[data-testid="verified-by"]')).to_be_visible()
    expect(page.get_by_test_id("link-test-form")).to_have_count(0)
    response = page.request.post("%s/architecture/requirements/%d/verified-by" % (live_server, world["req"]),
                                 form={"new_test_name": "Not allowed"})
    assert response.status == 403, response.status
    assert "Not allowed" not in page.content()
