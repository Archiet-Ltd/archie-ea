"""The rationalization screen never reports more applications scored than it has applications.

Goal of the viewer: read "Scored: N of M" and trust it. A score kept on a decommissioned record is not one of
the M applications the page counts, so it must not be one of the N.
"""

from __future__ import annotations

import re
import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _clear_auth_caches():
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _sign_in(client, db_session, org):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(
        email=f"scored-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Scored",
        last_name="Viewer",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    db_session.commit()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid


def _app(db_session, org, name, lifecycle=None):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id, lifecycle_status=lifecycle
    )
    db_session.add(row)
    db_session.flush()
    return row


def _score(db_session, org, application, disposition="invest"):
    from app.models.application_rationalization import ApplicationRationalizationScore

    db_session.add(
        ApplicationRationalizationScore(
            application_component_id=application.id,
            organization_id=org.id,
            disposition_action=disposition,
            overall_health_score=60,
            rationalization_action="invest",
        )
    )
    db_session.flush()


def test_scored_never_exceeds_the_applications_counted(client, db_session, make_org):
    org = make_org("scored-a")
    other = make_org("scored-b")
    live = _app(db_session, org, "Live")
    retired = _app(db_session, org, "Retired", "5. Decommissioned")
    _score(db_session, org, live)
    _score(db_session, org, retired, "insufficient_evidence")
    # A second organisation with its own scored applications must not move this one's figure.
    for _ in range(3):
        _score(db_session, other, _app(db_session, other, "Other"))
    db_session.commit()
    _sign_in(client, db_session, org)

    _clear_auth_caches()
    response = client.get("/applications/rationalization")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    figure = re.search(r'data-testid="stat-scored".*?(\d+)\s+of\s+(\d+)', body, re.S)
    assert figure, "the Scored figure is on the page"
    scored, total = int(figure.group(1)), int(figure.group(2))
    assert (scored, total) == (1, 1)


def test_the_summary_figures_behind_the_screen_agree_with_its_scored_count(client, db_session, make_org):
    org = make_org("summary-a")
    other = make_org("summary-b")
    live = _app(db_session, org, "Live")
    retired = _app(db_session, org, "Retired", "5. Decommissioned")
    _score(db_session, org, live)
    _score(db_session, org, retired)
    _score(db_session, other, _app(db_session, other, "Other"))
    db_session.commit()
    _sign_in(client, db_session, org)

    _clear_auth_caches()
    response = client.get("/applications/rationalization/api/executive-summary")
    assert response.status_code == 200
    data = response.get_json()
    assert data["total_scored"] == 1
    assert sum(data["disposition_distribution"].values()) == 1
    assert sum(data["score_buckets"].values()) == 1


def test_the_workbench_the_scored_card_opens_lists_the_same_applications(client, db_session, make_org):
    org = make_org("bench-a")
    other = make_org("bench-b")
    live = _app(db_session, org, "Live")
    retired = _app(db_session, org, "Retired", "5. Decommissioned")
    _score(db_session, org, live)
    _score(db_session, org, retired)
    _score(db_session, other, _app(db_session, other, "Other", "5. Decommissioned"))
    db_session.commit()
    _sign_in(client, db_session, org)

    _clear_auth_caches()
    card = re.search(r'data-testid="stat-scored".*?(\d+)\s+of\s+(\d+)', client.get("/applications/rationalization").get_data(as_text=True), re.S)
    link = re.search(r'href="([^"]+)"[^>]*data-testid="stat-scored"|data-testid="stat-scored"[^>]*href="([^"]+)"',
                     client.get("/applications/rationalization").get_data(as_text=True))
    assert link, "the Scored card links somewhere"
    href = link.group(1) or link.group(2)
    assert client.get(href).status_code == 200

    _clear_auth_caches()
    data = client.get("/applications/rationalization/api/portfolio-workbench").get_json()
    assert data["total"] == int(card.group(1)) == 1
    assert [r["app_id"] for r in data["results"]] == [live.id]
    assert sum(data["facets"]["dispositions"].values()) == 1
    assert not any("decommissioned" in key.lower() for key in data["facets"]["lifecycle_statuses"])
