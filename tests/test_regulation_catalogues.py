"""DORA and NIS2 are offered at sign-up, load as catalogues of their requirements, and link to applications.

Two real organisations in every test: what one loads or links is never seen by the other.
"""

import uuid

import pytest

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.modules.account.services import onboarding_service as onboarding
from tests.test_signup_canvases_and_frameworks import _adopted, _choose, _owner, two  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")


def _controls(code):
    rows = db.session.execute(
        db.text(
            "SELECT c.control_code, c.title, c.category, c.official_reference FROM compliance_controls c "
            "JOIN regulatory_frameworks f ON f.id = c.framework_id WHERE f.code = :code ORDER BY c.id"
        ), {"code": code},
    ).all()
    return rows


def _application(org, name="Payments core"):
    app_row = ApplicationComponent(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id)
    db.session.add(app_row)
    db.session.flush()
    return app_row


def test_dora_and_nis2_are_offered_at_sign_up_under_the_audit_goal():
    keys = {key for key, _label, _note in onboarding.FRAMEWORKS}
    assert {"dora", "nis2"} <= keys
    assert onboarding.FRAMEWORK_CATALOGUES["dora"] == "DORA" and onboarding.FRAMEWORK_CATALOGUES["nis2"] == "NIS2"
    assert {"dora", "nis2"} <= set(onboarding.suggested_frameworks("startup", "audit"))


def test_choosing_dora_loads_the_five_pillars_with_their_article_references(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["dora"])
    rows = _controls("DORA")
    pillars = {}
    for code, title, category, reference in rows:
        pillars.setdefault(category, []).append(int(code.rsplit(".", 1)[1]))
        assert reference == "Regulation (EU) 2022/2554, Article %s" % code.rsplit(".", 1)[1]
    assert {p: (min(a), max(a)) for p, a in pillars.items()} == {
        "ICT Risk Management": (5, 16), "Incident Reporting": (17, 23), "Resilience Testing": (24, 27),
        "Third-Party Risk": (28, 44), "Information Sharing": (45, 45),
    }
    assert sorted(a for articles in pillars.values() for a in articles) == list(range(5, 46))
    assert _adopted(two["one"])["DORA"] == 41
    assert "register of information" in " ".join(title for _c, title, _g, _r in rows).lower()
    described = db.session.execute(db.text(
        "SELECT description FROM compliance_controls WHERE control_code IN ('DORA-Art.28', 'DORA-Art.30')")).scalars().all()
    assert len(described) == 2 and all("subcontracting" in text for text in described)


def test_choosing_nis2_loads_measures_a_to_j_and_article_23_reporting(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["nis2"])
    codes = [code for code, _t, _g, _r in _controls("NIS2")]
    for letter in "abcdefghij":
        assert "NIS2-Art.21(2)(%s)" % letter in codes
    assert any(code.startswith("NIS2-Art.23") for code in codes)
    assert _adopted(two["one"])["NIS2"] == len(codes) == 13


def test_the_second_organisation_never_sees_the_first_ones_loaded_catalogue(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["dora"])
    _choose(client, login_as, two["two_owner"], [], ["nis2"])
    assert set(_adopted(two["one"])) == {"DORA"} and set(_adopted(two["two"])) == {"NIS2"}
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    login_as(client, two["one_owner"])
    client.get("/dashboard/compliance")
    assert {r["framework"] for r in ApplicabilityService.requirements_to_link(two["one"].id)} == {"DORA"}
    login_as(client, two["two_owner"])
    client.get("/dashboard/compliance")
    assert {r["framework"] for r in ApplicabilityService.requirements_to_link(two["two"].id)} == {"NIS2"}


def test_a_requirement_linked_to_an_application_shows_on_its_page(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["dora"])
    app_row = _application(two["one"])
    login_as(client, two["one_owner"])
    page = client.get("/applications/%d" % app_row.id).get_data(as_text=True)
    assert 'data-testid="application-requirements-empty"' in page and "DORA-Art.28" in page
    assert 'data-testid="link-requirement-form"' in page and 'for="link-requirement"' in page and "Requirement to link" in page
    control_id = db.session.execute(db.text("SELECT id FROM compliance_controls WHERE control_code = 'DORA-Art.28'")).scalar()
    reply = client.post("/applications/%d/requirements" % app_row.id, data={"control_id": control_id})
    assert reply.status_code == 302
    page = client.get("/applications/%d" % app_row.id).get_data(as_text=True)
    assert 'data-testid="application-requirement"' in page
    assert "ICT third-party risk and register of information" in page
    card = page.split('data-testid="application-requirements"')[1].split('data-slot="card"')[0].lower()
    assert "compliant" not in card and "certified" not in card
    again = client.post("/applications/%d/requirements" % app_row.id, data={"control_id": control_id})
    assert again.status_code == 302
    assert client.get("/applications/%d" % app_row.id).get_data(as_text=True).count('data-testid="application-requirement"') == 1


def test_another_organisation_can_neither_link_to_nor_see_the_first_ones_application(client, login_as, two):
    _choose(client, login_as, two["one_owner"], [], ["dora"])
    _choose(client, login_as, two["two_owner"], [], ["nis2"])
    first_row = _application(two["one"])
    first_app = first_row.id
    own_app = _application(two["two"]).id
    control_id = db.session.execute(db.text("SELECT id FROM compliance_controls WHERE control_code = 'DORA-Art.28'")).scalar()
    login_as(client, two["one_owner"])
    client.post("/applications/%d/requirements" % first_app, data={"control_id": control_id})
    login_as(client, two["two_owner"])
    assert client.post("/applications/%d/requirements" % first_app, data={"control_id": control_id}).status_code == 404
    db.session.expunge(first_row)  # a session-cached row would be handed back without the tenant filter
    assert client.get("/applications/%d" % first_app).status_code == 404
    client.post("/applications/%d/requirements" % own_app, data={"control_id": control_id})
    page = client.get("/applications/%d" % own_app).get_data(as_text=True)
    assert 'data-testid="application-requirement"' not in page


def test_an_organisation_that_adopted_no_framework_is_told_so_with_a_next_step(client, login_as, two):
    app_row = _application(two["one"])
    login_as(client, two["one_owner"])
    page = client.get("/applications/%d" % app_row.id).get_data(as_text=True)
    assert 'data-testid="application-requirements-none-adopted"' in page and "/frameworks/" in page


def test_a_catalogue_that_grew_after_adoption_tops_up_the_adopted_rows(client, login_as, two):
    from app.models.application_compliance import ApplicationComplianceControl

    _choose(client, login_as, two["one_owner"], [], ["dora"])
    ApplicationComplianceControl.query.filter(
        ApplicationComplianceControl.organization_id == two["one"].id,
        ApplicationComplianceControl.control_id.in_(
            db.session.execute(db.text("SELECT id FROM compliance_controls WHERE control_code = 'DORA-Art.45'")).scalars().all()
        ),
    ).delete(synchronize_session=False)
    db.session.flush()
    onboarding.run_starter_data(two["one"].id)
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    assert any(r["code"] == "DORA-Art.45" for r in ApplicabilityService.requirements_to_link(two["one"].id))

