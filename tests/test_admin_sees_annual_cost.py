"""An organisation Administrator reads annual cost whatever persona they were given; another
organisation's Administrator does not read it here, and a plain member with a narrow persona
still does not."""
import uuid

import pytest

from app.decorators.requires_role import may_see_spend_pages
from app.utils.role_access import can_see_costs

pytestmark = pytest.mark.usefixtures("db_session")


def _member(db_session, org, persona, admin=False):
    from app.models.user import User

    user = User(email=f"m-{uuid.uuid4().hex[:8]}@example.com", first_name="A", last_name="B",
                organization_id=org.id, enterprise_role=persona, confirmed=True)
    user.password = "Test-password-1!"
    db_session.add(user)
    db_session.flush()
    if admin:
        user.grant_org_admin()
        db_session.flush()
    return user


def test_administrator_with_an_architect_persona_reads_cost(db_session, make_org, tenant_ctx):
    mine, theirs = make_org(), make_org()
    admin = _member(db_session, mine, "enterprise_architect", admin=True)
    member = _member(db_session, mine, "enterprise_architect")
    with tenant_ctx(mine.id):
        assert can_see_costs(admin) is True
        assert may_see_spend_pages(admin) is True
        assert can_see_costs(member) is False
    # Administering one organisation grants nothing while another is the active one.
    with tenant_ctx(theirs.id):
        assert can_see_costs(admin) is False
