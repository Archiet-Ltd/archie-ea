"""The Gaps page gives the register's own unclosed count, and capability health reads one organisation.

Above 500 gaps the page lists only the first 500, so a count taken from the listed rows stops
agreeing with the change board, which counts the register. The page must show the register's count.
Capability health must show only the signed-in organisation's capabilities, never another's.
"""

from __future__ import annotations

import re
import uuid

GAPS = 503


def _user(db_session, org, label):
    from app.models.user import User

    user = User(email=f"{label}-{uuid.uuid4().hex[:6]}@example.test", first_name="G", last_name="H",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _sign_in(client, login_as, user_id):
    from app import db
    from app.models.user import User

    login_as(client, db.session.get(User, user_id))


def test_gaps_page_unclosed_figure_is_the_register_count_above_500_gaps(
        app, db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import count_gaps

    org = make_org("gap-big")
    other = make_org("gap-other")
    user = _user(db_session, org, "big")
    db_session.add_all([Gap(name=f"Big gap {i}", organization_id=org.id) for i in range(GAPS)])
    db_session.add_all([Gap(name=f"Other gap {i}", organization_id=other.id) for i in range(3)])
    db_session.commit()
    org_id, user_id = org.id, user.id
    db_session.expunge_all()

    register = count_gaps(org_id, has_work_package=False)
    assert register == GAPS

    _sign_in(client, login_as, user_id)
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    shown = re.search(r'data-testid="gap-unclosed-count">\s*(\d+)\s*<', html)

    assert shown, "the unclosed figure is missing from the Gaps page"
    assert int(shown.group(1)) == register


def test_gaps_page_and_board_agree_on_unplanned_with_a_resolved_gap(
        app, db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap
    from app.services.gap_register_service import count_gaps

    org = make_org("gap-unplanned")
    user = _user(db_session, org, "unplanned")
    db_session.add_all([
        Gap(name="Open gap, no work package", organization_id=org.id,
            resolution_status="identified"),
        Gap(name="Resolved gap, no work package", organization_id=org.id,
            resolution_status="resolved"),
    ])
    db_session.commit()
    org_id, user_id = org.id, user.id
    db_session.expunge_all()

    register = count_gaps(org_id, has_work_package=False, closed=False)
    assert register == 1

    _sign_in(client, login_as, user_id)

    # The Gaps page's unplanned figure counts open gaps only.
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)
    shown = re.search(r'data-testid="gap-unclosed-count">\s*(\d+)\s*<', html)
    assert shown, "the unclosed figure is missing from the Gaps page"
    assert int(shown.group(1)) == 1, shown.group(1)

    # The change board's "No work planned" figure counts open gaps only.
    board = client.get("/adm-kanban/gap-analysis").get_data(as_text=True)
    board_shown = re.search(r'data-testid="gap-unclosed">\s*(\d+)\s*<', board)
    assert board_shown, "the unclosed figure is missing from the change board"
    assert int(board_shown.group(1)) == 1, board_shown.group(1)


def test_gap_register_shows_risks_register_link(
        app, db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    org = make_org("gap-risks-link")
    user = _user(db_session, org, "risks-link")
    db_session.add(Gap(name="A gap", organization_id=org.id))
    db_session.commit()
    user_id = user.id
    db_session.expunge_all()

    _sign_in(client, login_as, user_id)
    html = client.get("/enterprise/implementation/gap-analysis").get_data(as_text=True)

    assert 'data-testid="gap-risks-register-link"' in html, "the Risks register link is missing"
    assert "These figures count gaps, not risks" in html, "the gaps-not-risks sentence is missing"

    # D-3: the sentence and link also show when a severity filter is active.
    html_critical = client.get("/enterprise/implementation/gap-analysis?severity=critical").get_data(as_text=True)
    assert 'data-testid="gap-risks-register-link"' in html_critical, "the Risks register link is missing with severity=critical"
    assert "These figures count gaps, not risks" in html_critical, "the gaps-not-risks sentence is missing with severity=critical"


def test_capability_health_lists_only_the_signed_in_organisations_capabilities(
        app, db_session, make_org, client, login_as):
    from app.models.business_capabilities import BusinessCapability

    org_a, org_b = make_org("ch-a"), make_org("ch-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    name_a, name_b = f"Alpha capability {uuid.uuid4().hex[:6]}", f"Bravo capability {uuid.uuid4().hex[:6]}"
    db_session.add_all([BusinessCapability(name=name_a, organization_id=org_a.id),
                        BusinessCapability(name=name_b, organization_id=org_b.id)])
    db_session.commit()
    ids = (user_a.id, user_b.id)
    db_session.expunge_all()

    _sign_in(client, login_as, ids[0])
    page_a = client.get("/enterprise/strategic/capability-health").get_data(as_text=True)
    _sign_in(client, login_as, ids[1])
    page_b = client.get("/enterprise/strategic/capability-health").get_data(as_text=True)

    assert name_a in page_a and name_b not in page_a
    assert name_b in page_b and name_a not in page_b
