"""Every result page in this product carries an Evidence / Source / Confidence /
Date footer (``components/provenance.html``'s ``result_footer`` macro). Before
this fix, ``/genome/roadmap/`` carried none, and ``/capability-roadmap``'s gap
tile stated a contradiction a reader could not resolve: "Capability Gaps
Detected: 0 signals ... not yet recorded" sitting right beside the "Recorded
gaps" tile showing 10 -- 0 and 10 both describing "gaps" with no label telling
the reader they are different counts.

The footer on both pages must be driven by this organisation's real recorded
plateaus, work packages and gaps -- never placeholder text.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    import uuid

    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"rdm-footer-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(), last_name="Test", organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect", role_id=role.id if role else None)
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org, *, plateaus=2, work_packages=3, gaps=10):
    from app.models.implementation_migration import Plateau
    from app.services import gap_register_service, work_package_service

    for i in range(plateaus):
        db_session.add(Plateau(organization_id=org.id, name=f"Plateau {org.id}-{i}", sequence_order=i))
    for i in range(work_packages):
        work_package_service.create_work_package(organization_id=org.id, name=f"Package {org.id}-{i}")
    for i in range(gaps):
        gap_register_service.create_gap(organization_id=org.id, name=f"Gap {org.id}-{i}", gap_type="coverage")
    db_session.flush()


def _footer_section(html, testid):
    """The markup from the named ``data-testid`` marker onward, or '' if absent.

    Good enough to assert presence/absence of nested testids and text within it; not a real
    HTML-boundary parse."""
    start = html.find('data-testid="%s"' % testid)
    if start == -1:
        return ""
    return html[start:start + 4000]


def test_capability_roadmap_footer_has_all_four_terms_and_gap_wording_is_not_contradictory(
    db_session, make_org, client, login_as
):
    org = make_org("evfoot-cap")
    user = _user(db_session, org, "cap")
    _seed(db_session, org, plateaus=2, work_packages=3, gaps=10)

    login_as(client, user)
    html = client.get("/capability-roadmap").get_data(as_text=True)

    footer = _footer_section(html, "roadmap-summary-footer")
    assert footer, "no evidence footer rendered on /capability-roadmap"
    for testid in ("result-footer-evidence", "result-footer-source", "result-footer-confidence", "result-footer-date"):
        assert 'data-testid="%s"' % testid in footer, "%s missing from /capability-roadmap footer" % testid

    # The footer states the real recorded figures, not placeholder text.
    assert "Plateaus: 2" in footer
    assert "Work packages: 3" in footer
    assert "Gaps: 10" in footer

    # The "Recorded gaps" tile (10) and the gap-signals line must not read as
    # one contradicting the other: the old wording put "0 signals ... not yet
    # recorded" directly beside the 10-gap tile with nothing distinguishing
    # the two counts as different concepts.
    signals_section = _footer_section(html, "rdm-gap-signals")
    assert "Capability Gaps Detected: " not in signals_section
    assert "not recorded as gaps" in signals_section or "separate" in signals_section.lower()


def test_genome_roadmap_has_evidence_footer_driven_by_real_data(db_session, make_org, client, login_as):
    org = make_org("evfoot-genome")
    user = _user(db_session, org, "genome")
    _seed(db_session, org, plateaus=2, work_packages=3, gaps=10)

    login_as(client, user)
    html = client.get("/genome/roadmap/").get_data(as_text=True)

    footer = _footer_section(html, "roadmap-summary-footer")
    assert footer, "no evidence footer rendered on /genome/roadmap/"
    for testid in ("result-footer-evidence", "result-footer-source", "result-footer-confidence", "result-footer-date"):
        assert 'data-testid="%s"' % testid in footer, "%s missing from /genome/roadmap/ footer" % testid

    assert "Plateaus: 2" in footer
    assert "Work packages: 3" in footer
    assert "Gaps: 10" in footer
