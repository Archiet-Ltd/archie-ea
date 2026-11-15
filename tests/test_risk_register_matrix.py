"""The risk register shows its risks by probability and impact beside the ranked list.

A person reading the register to see where the exposure sits needs the matrix on the same page as the ranked
list, counting the same risks, and not the other organisation's.
"""
import re
import uuid

import pytest

from tests.test_risk_ranking import _world

pytestmark = pytest.mark.usefixtures("db_session")


def _cells(html):
    found = re.findall(r'aria-label="Probability (\d), impact (\d): (\d+) risks?"', html)
    return {(int(p), int(i)): int(n) for p, i, n in found}


def test_register_page_draws_the_matrix_from_this_organisations_risks(db_session, make_org, login_as, client):
    from app import db
    from app.models.user import User

    org, _other, _risks, _foreign = _world(db_session, make_org)
    user = User(email="m-%s@example.com" % uuid.uuid4().hex[:8], first_name="M", last_name="T",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db.session.add(user)
    db.session.flush()
    login_as(client, user)

    page = client.get("/risks/")
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    assert 'data-component="risk-matrix"' in html
    cells = _cells(html)
    assert len(cells) == 25
    # _world: Alpha (4, 5), Bravo and Zulu (2, 2), Narrow (5, 5), Bare (3, 3); the other organisation's (5, 5) is not here.
    assert {k: v for k, v in cells.items() if v} == {(4, 5): 1, (2, 2): 2, (5, 5): 1, (3, 3): 1}
    assert sum(cells.values()) == 5 and "Top risks (5)" in html
    assert html.index('id="ranked-risks"') < html.index('id="risk-matrix-card"')
