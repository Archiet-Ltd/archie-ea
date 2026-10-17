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
                "health_scorecard": "4 recorded risks"}.get(key)
    if expected:
        assert expected in footer_a

    login_as(client, user_b)
    html_b = client.get(path).get_data(as_text=True)
    footer_b = html_b.split('data-four-properties="%s"' % key)[1].split("</section>")[0]
    assert "3 recorded gaps" not in footer_b and "4 recorded risks" not in footer_b
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
