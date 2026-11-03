"""Capability Health uses each word for one thing: "Critical" is not both an importance label and a score band."""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def test_critical_does_not_mean_two_things(db_session, make_org, client, login_as):
    from app.models.business_capability import BusinessCapability
    from tests.test_work_package_writer import _user

    org = make_org("wording")
    user = _user(db_session, org, "wording", role="enterprise_architect")
    db_session.add(BusinessCapability(name="Order handling", organization_id=org.id, strategic_importance="critical"))
    db_session.flush()
    login_as(client, user)
    html = client.get("/strategic/capability-health").get_data(as_text=True)
    html = re.sub(r"<(script|style|template).*?</\1>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", html)

    assert "Critical" not in text, "the word still names a score band or an importance label"
    assert "High-importance Capabilities" in text
    assert re.search(r">\s*At risk\s*<", html), "the distribution names its score band"
