"""The not-recorded gap signals are one figure: the ADM board, the gap analysis and the capability roadmap agree."""
from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _architect(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="sig.%s@example.com" % uuid.uuid4().hex[:10], first_name="S", last_name="G",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        role=Role.query.filter_by(name="User").first(),
    )
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    return user


def _capabilities(db_session, org, names):
    from app.models.business_capabilities import BusinessCapability

    for name in names:
        db_session.add(BusinessCapability(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id))
    db_session.flush()


def test_the_board_and_the_roadmap_show_the_same_signal_figure(db_session, make_org, client, login_as):
    from app.models.application_portfolio import ApplicationComponent

    mine, theirs = make_org("signals-mine"), make_org("signals-theirs")
    user = _architect(db_session, mine)
    _capabilities(db_session, mine, ["Billing", "Onboarding", "Reporting"])
    _capabilities(db_session, theirs, ["Their one"] )
    # Orphaned applications and single points of failure are portfolio findings, not capability signals.
    for n in range(4):
        db_session.add(ApplicationComponent(
            name="Orphan %d %s" % (n, uuid.uuid4().hex[:6]), organization_id=mine.id, lifecycle_status="operational"))
    db_session.flush()

    login_as(client, user)
    roadmap = client.get("/capability-map/api/roadmap/gaps").get_json()
    board = client.get("/adm-kanban/gap-analysis").get_data(as_text=True)
    figure = int(re.search(r'data-testid="gap-signals">\s*(\d+)', board).group(1))

    assert roadmap["signal_count"] == len(roadmap["gaps"]) >= 3
    assert figure == roadmap["signal_count"]
