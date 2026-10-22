"""One definition of "owned": the dashboard's owner coverage and the Ask
question "applications with no owner" give the same answer (PR 713).

An application is owned when it has an accountable owner: a primary or business
ownership record, or a non-empty legacy application/business owner text. A
technical owner on its own does not make it owned.
"""
from __future__ import annotations

import json
import re

import pytest
from bs4 import BeautifulSoup

from tests.test_application_owner_one_store import _person
from tests.test_application_owner_writer import _make_app

pytestmark = pytest.mark.usefixtures("db_session")


def _four_applications(db_session, org):
    from app.services.element_ownership_service import set_element_owner

    primary = _person(db_session, org, "primaryowner")
    technical = _person(db_session, org, "technicalowner")
    with_record = _make_app(db_session, org, "WithPrimary")
    with_text = _make_app(db_session, org, "WithLegacyBusinessText", legacy_business_owner="Olivia Outsider")
    technical_only = _make_app(db_session, org, "TechnicalOnly")
    nothing = _make_app(db_session, org, "Nothing")
    set_element_owner(element_type="application", element_id=with_record.id, user_id=primary.id,
                      organization_id=org.id, ownership_type="primary")
    set_element_owner(element_type="application", element_id=technical_only.id, user_id=technical.id,
                      organization_id=org.id, ownership_type="technical")
    db_session.flush()
    return primary, with_record, with_text, technical_only, nothing


def _dashboard_owner_coverage(app, user, org):
    from flask import g
    from flask_login import login_user

    from app.modules.dashboard.routes.dashboard_views import _build_overview_context

    with app.test_request_context("/dashboard/overview"):
        login_user(user)
        g.current_org_id = org.id
        return _build_overview_context()["data_coverage"]


def _v2_dashboard_owner_coverage(app, user, org, monkeypatch):
    from flask import g
    from flask_login import login_user

    from app.modules.dashboard.v2.routes import dashboard_views as v2

    captured = {}

    def fake_render(template, **context):
        captured.update(context)
        return ""

    monkeypatch.setattr(v2, "render_template", fake_render)
    with app.test_request_context("/dashboard/overview"):
        login_user(user)
        g.current_org_id = org.id
        v2.overview()
    return captured["data_coverage"]


def _v1_coverage_debug(app, user, org):
    """The v1 coverage debug view. Only the v1 dashboard serves it (the v2 module,
    which is the one registered by default, has no such route), so it is called
    directly rather than over HTTP."""
    from flask import g
    from flask_login import login_user

    from app.modules.dashboard.routes.dashboard_views import api_coverage_debug

    with app.test_request_context("/dashboard/api/coverage-debug"):
        login_user(user)
        g.current_org_id = org.id
        response = api_coverage_debug.__wrapped__() if hasattr(api_coverage_debug, "__wrapped__") else api_coverage_debug()
        assert response.status_code == 200
        return json.loads(response.get_data(as_text=True))


def _every_surface(app, db_session, org, user, monkeypatch, client, login_as):
    """What every surface that counts owned applications says for *org*."""
    from flask import g
    from flask_login import login_user

    from app.modules.intelligence.services.query_catalogue import _applications_without_owner
    from app.services.gap_analysis_service import ArchitecturalGapAnalyzer

    answer = _applications_without_owner(org.id)
    unowned = {row["id"] for group in answer["groups"] for row in group["rows"]}

    login_as(client, user)
    response = client.get("/applications/")
    assert response.status_code == 200
    text = " ".join(BeautifulSoup(response.get_data(as_text=True), "html.parser").get_text().split())
    m = re.search(r"(\d+) of (\d+) applications in the portfolio have a named owner", text)
    assert m is not None, "portfolio banner not found"

    debug = _v1_coverage_debug(app, user, org)
    assert debug["ok"] is True

    with app.test_request_context("/"):
        login_user(user)
        g.current_org_id = org.id
        orphaned_ids = {o["application_id"] for o in ArchitecturalGapAnalyzer().analyze_portfolio_gaps()
                        .get("orphaned_applications", [])}

    return {
        "ask_unowned": unowned,
        "ask_total": answer["total"],
        "dashboard_percent": _dashboard_owner_coverage(app, user, org)["owner"],
        "dashboard_v2_percent": _v2_dashboard_owner_coverage(app, user, org, monkeypatch)["owner"],
        "banner": (int(m.group(1)), int(m.group(2))),
        "debug": (debug["owner"], debug["total"]),
        "gap_orphaned": orphaned_ids,
    }


def test_both_screens_report_two_owned_and_two_unowned(app, db_session, make_org, monkeypatch, client, login_as):
    org = make_org("owned-one-def")
    user, with_record, with_text, technical_only, nothing = _four_applications(db_session, org)

    seen = _every_surface(app, db_session, org, user, monkeypatch, client, login_as)

    # 2 of 4 applications owned: the Ask question, both dashboards (50%), the
    # My Applications portfolio banner, the coverage debug endpoint and gap
    # analysis (the two unowned have no capabilities or processes) all agree.
    assert seen["ask_unowned"] == {technical_only.id, nothing.id}
    assert seen["ask_total"] == 2
    assert seen["dashboard_percent"] == 50
    assert seen["dashboard_v2_percent"] == 50
    assert seen["banner"] == (2, 4)
    assert seen["debug"] == (2, 4)
    assert seen["gap_orphaned"] == {technical_only.id, nothing.id}


def test_an_owner_record_naming_a_user_of_another_organisation_is_unowned_everywhere(
        app, db_session, make_org, monkeypatch, client, login_as):
    """The record is in the application's organisation, but the user it names is not."""
    from app.models.application_owner import ApplicationOwner

    org = make_org("owned-one-def-here")
    elsewhere = make_org("owned-one-def-elsewhere")
    user, with_record, with_text, technical_only, nothing = _four_applications(db_session, org)
    stranger = _person(db_session, elsewhere, "strangerowner")
    foreign_user = _make_app(db_session, org, "OwnedByAStranger")
    db_session.add(ApplicationOwner(
        application_id=foreign_user.id, user_id=stranger.id, organization_id=org.id,
        ownership_type="primary",
    ))
    db_session.flush()

    seen = _every_surface(app, db_session, org, user, monkeypatch, client, login_as)

    # 2 of 5 owned. The stranger's record counts on no surface.
    assert seen["ask_unowned"] == {technical_only.id, nothing.id, foreign_user.id}
    assert seen["ask_total"] == 3
    assert seen["dashboard_percent"] == 40
    assert seen["dashboard_v2_percent"] == 40
    assert seen["banner"] == (2, 5)
    assert seen["debug"] == (2, 5)
    assert seen["gap_orphaned"] == {technical_only.id, nothing.id, foreign_user.id}


def test_python_predicate_agrees_with_the_sql(app, db_session, make_org):
    from app.services.element_ownership_service import application_is_owned

    org = make_org("owned-one-def-py")
    _user, with_record, with_text, technical_only, nothing = _four_applications(db_session, org)
    assert [application_is_owned(a) for a in (with_record, with_text, technical_only, nothing)] == [
        True, True, False, False]


def test_python_predicate_refuses_a_user_of_another_organisation(app, db_session, make_org):
    from app.models.application_owner import ApplicationOwner
    from app.services.element_ownership_service import application_is_owned

    org = make_org("owned-one-def-py-here")
    elsewhere = make_org("owned-one-def-py-elsewhere")
    stranger = _person(db_session, elsewhere, "strangerpy")
    application = _make_app(db_session, org, "OwnedByAStrangerPy")
    db_session.add(ApplicationOwner(
        application_id=application.id, user_id=stranger.id, organization_id=org.id,
        ownership_type="primary",
    ))
    db_session.flush()
    assert application_is_owned(application) is False
