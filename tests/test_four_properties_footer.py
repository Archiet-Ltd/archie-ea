"""The four properties under a list: the shared footer component and the five
screens that carry it."""

import uuid

import pytest
from flask import render_template_string

from tests.test_team_invite_acceptance import _make_org, _make_user

pytestmark = pytest.mark.usefixtures("db_session")

SCREENS = {
    "roadmap": "/capability-roadmap",
    "gaps": "/enterprise/implementation/gap-analysis",
    "risks": "/risks/",
    "capability_health": "/strategic/capability-health",
    "health_scorecard": "/dashboard/health",
}

FULL = {
    "source": "Recorded gaps in this organisation",
    "computed_at": "2026-10-17T12:00:00Z",
    "computed_label": "17 Oct 2026, 12:00 UTC",
    "confidence_basis": "drawn_facts_only",
    "confidence_percent": None,
    "trail_url": "/somewhere",
    "trail_label": "See the records",
}


def _render(app, props):
    with app.test_request_context("/"):
        return render_template_string(
            "{% from 'components/four_properties_footer.html' import four_properties_footer %}"
            "{{ four_properties_footer(props=props) }}",
            props=props,
        )


def test_component_shows_all_four_fields(app):
    html = _render(app, FULL)
    assert "Recorded gaps in this organisation" in html
    assert "17 Oct 2026, 12:00 UTC" in html
    assert "Counted from recorded data" in html
    assert 'href="/somewhere"' in html and "See the records" in html
    assert "—" not in html


@pytest.mark.parametrize("field,marker", [
    ("source", "source"), ("computed_at", "computed"),
    ("confidence_basis", "confidence"), ("trail_url", "trail"),
])
def test_component_missing_field_is_an_em_dash(app, field, marker):
    props = dict(FULL)
    props[field] = None
    html = _render(app, props)
    cell = html.split('data-fp="%s"' % marker)[1].split("</dd>")[0]
    assert "—" in cell


def test_component_shows_real_model_confidence(app):
    props = dict(FULL, confidence_basis="lowest_derived_row", confidence_percent=82)
    assert "82%" in _render(app, props)


def _seed(db_session, org, gaps, risks):
    from app.models.risk import Risk
    from app.services.gap_register_service import create_gap

    for i in range(gaps):
        create_gap(org.id, "Gap %s %s" % (i, uuid.uuid4().hex[:6]))
    for i in range(risks):
        db_session.add(Risk(organization_id=org.id, title="Risk %s" % i, likelihood=3, impact=3))
    db_session.flush()


@pytest.mark.parametrize("key,path", list(SCREENS.items()))
def test_screen_carries_footer_and_it_is_per_organisation(app, client, login_as, db_session, key, path):
    org_a, org_b = _make_org(db_session, "fp-a"), _make_org(db_session, "fp-b")
    user_a, user_b = _make_user(db_session, org_a, org_admin=True), _make_user(db_session, org_b, org_admin=True)
    _seed(db_session, org_a, gaps=3, risks=4)
    db_session.commit()

    login_as(client, user_a)
    page_a = client.get(path)
    assert page_a.status_code == 200, path
    html_a = page_a.get_data(as_text=True)
    assert 'data-four-properties="%s"' % key in html_a
    footer_a = html_a.split('data-four-properties="%s"' % key)[1].split("</section>")[0]
    assert "in this organisation" in footer_a
    assert "Counted from recorded data" in footer_a
    expected = {"roadmap": "3 recorded gaps", "gaps": "3 recorded gaps", "risks": "4 recorded risks",
                "health_scorecard": "4 open risks"}.get(key)
    if expected:
        assert expected in footer_a

    login_as(client, user_b)
    html_b = client.get(path).get_data(as_text=True)
    footer_b = html_b.split('data-four-properties="%s"' % key)[1].split("</section>")[0]
    assert "3 recorded gaps" not in footer_b and "4 recorded risks" not in footer_b and "4 open risks" not in footer_b
    assert org_a.name not in html_b


def test_footer_renders_nothing_when_the_fields_cannot_be_worked_out(app, monkeypatch):
    from app.services import four_properties_service as svc

    def boom():
        raise RuntimeError("no data")

    monkeypatch.setitem(svc.SCREENS, "gaps", boom)
    with app.test_request_context("/"):
        assert svc.four_properties("gaps") is None
        html = render_template_string(
            "{% from 'components/four_properties_footer.html' import four_properties_footer %}"
            "{{ four_properties_footer('gaps') }}"
        )
    assert "data-four-properties" not in html


def _footer(html, key):
    return html.split('data-four-properties="%s"' % key)[1].split("</section>")[0]


def test_risks_footer_follows_to_the_risk_records_not_a_maturity_screen(app, client, login_as, db_session):
    org_a, org_b = _make_org(db_session, "fp-r-a"), _make_org(db_session, "fp-r-b")
    user_a, user_b = _make_user(db_session, org_a, org_admin=True), _make_user(db_session, org_b, org_admin=True)
    _seed(db_session, org_a, gaps=0, risks=2)
    db_session.commit()
    for user, count in ((user_a, "2 recorded risks"), (user_b, "no recorded risks")):
        login_as(client, user)
        footer = _footer(client.get("/risks/").get_data(as_text=True), "risks")
        assert count in footer
        assert "value-streams-at-risk" not in footer
        assert 'href="/risks/' in footer


def test_health_scorecard_footer_counts_open_risks_only(app, client, login_as, db_session):
    from app.models.risk import Risk, RiskStatus

    org_a, org_b = _make_org(db_session, "fp-o-a"), _make_org(db_session, "fp-o-b")
    user_a, user_b = _make_user(db_session, org_a, org_admin=True), _make_user(db_session, org_b, org_admin=True)
    _seed(db_session, org_a, gaps=0, risks=2)
    for status in (RiskStatus.CLOSED, RiskStatus.ACCEPTED, RiskStatus.MITIGATED):
        db_session.add(Risk(organization_id=org_a.id, title="Done %s" % status.value, likelihood=2, impact=2,
                            status=status))
    db_session.add(Risk(organization_id=org_b.id, title="Only other org", likelihood=2, impact=2,
                        status=RiskStatus.CLOSED))
    db_session.commit()
    login_as(client, user_a)
    footer = _footer(client.get("/dashboard/health").get_data(as_text=True), "health_scorecard")
    assert "2 open risks" in footer and "5 recorded" not in footer
    login_as(client, user_b)
    footer = _footer(client.get("/dashboard/health").get_data(as_text=True), "health_scorecard")
    assert "no open risks" in footer


def test_capability_health_footer_uses_the_dashboards_cached_result_and_time(app, client, login_as, db_session):
    import time

    from app.models.business_capabilities import BusinessCapability
    from app.modules.capabilities.services import capability_health_service as chs

    org_a, org_b = _make_org(db_session, "fp-c-a"), _make_org(db_session, "fp-c-b")
    user_a, user_b = _make_user(db_session, org_a, org_admin=True), _make_user(db_session, org_b, org_admin=True)
    for i in range(2):
        db_session.add(BusinessCapability(name="Cap %s" % i, organization_id=org_a.id))
    db_session.commit()
    chs._health_metrics_cache.clear()

    login_as(client, user_a)
    assert client.get("/strategic/capability-health").status_code == 200
    # A third capability appears after the dashboard worked its figures out,
    # and the cached result is 30 seconds old: the footer must say what the
    # dashboard shows, with the time the dashboard worked it out.
    db_session.add(BusinessCapability(name="Cap late", organization_id=org_a.id))
    db_session.commit()
    stamp = time.time() - 30
    _, result = chs._health_metrics_cache[org_a.id]
    chs._health_metrics_cache[org_a.id] = (stamp, result)
    footer = _footer(client.get("/strategic/capability-health").get_data(as_text=True), "capability_health")
    assert "2 recorded capabilities" in footer and "3 recorded" not in footer
    import datetime as dt
    label = dt.datetime.utcfromtimestamp(stamp).strftime("%d %b %Y, %H:%M UTC")
    assert label in footer

    login_as(client, user_b)
    footer_b = _footer(client.get("/strategic/capability-health").get_data(as_text=True), "capability_health")
    assert "no recorded capabilities" in footer_b
    chs._health_metrics_cache.clear()
