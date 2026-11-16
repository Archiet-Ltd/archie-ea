"""A gap row names the store its capability id comes from, so two capabilities never share a gap."""
from __future__ import annotations

import uuid

import pytest

from tests.test_capability_gap_signals_cross_org import _user

pytestmark = pytest.mark.usefixtures("db_session")


def _pair_with_same_number(db_session, org):
    """A business capability and a unified-only capability whose numeric ids are equal."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    tag = uuid.uuid4().hex[:6]
    for _ in range(50):
        only = UnifiedCapability(name=f"Framework only {tag}", level=1, organization_id=org.id, scope="tenant")
        db_session.add(only)
        db_session.flush()
        if db_session.get(BusinessCapability, only.id) is None:
            biz = BusinessCapability(id=only.id, name=f"Business cap {tag}", code=f"G-{tag}",
                                     organization_id=org.id, level=1)
            db_session.add(biz)
            db_session.flush()
            return biz, only
    pytest.skip("no free business capability number to match a unified id")


def _signals(client):
    return {g["name"]: g for g in client.get("/capability-map/api/roadmap/gaps").get_json()["gaps"]}


def _convert(client, row):
    return client.post("/capability-map/api/roadmap/gaps/convert", json={"gaps": [row]})


def test_same_number_capabilities_get_distinct_gap_ids(db_session, make_org, client, login_as):
    org = make_org("gapid-a")
    login_as(client, _user(db_session, org, "ga"))
    biz, only = _pair_with_same_number(db_session, org)
    assert biz.id == only.id

    rows = _signals(client)
    business_row, unified_row = rows[biz.name], rows[only.name]
    assert business_row["capability_type"] == "business"
    assert unified_row["capability_type"] != "business"
    assert business_row["id"] != unified_row["id"]
    assert (business_row["capability_type"], business_row["capability_id"]) != (
        unified_row["capability_type"], unified_row["capability_id"])


def test_converting_one_gap_leaves_the_other_capabilitys_gap_alone(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Gap

    org = make_org("gapid-b")
    login_as(client, _user(db_session, org, "gb"))
    biz, only = _pair_with_same_number(db_session, org)
    rows = _signals(client)

    def recorded(row):
        return Gap.query.filter_by(source_capability_type=row["capability_type"],
                                   source_capability_id=row["capability_id"]).all()

    assert _convert(client, rows[biz.name]).status_code == 200
    (business_gap,) = recorded(rows[biz.name])
    before = (business_gap.id, business_gap.name, business_gap.priority)

    changed = dict(rows[only.name], priority="critical")
    assert _convert(client, changed).status_code == 200
    (still_business,) = recorded(rows[biz.name])
    assert (still_business.id, still_business.name, still_business.priority) == before
    (unified_gap,) = recorded(rows[only.name])
    assert unified_gap.id != business_gap.id

    # The other way round: converting the business gap again leaves the unified one as it was.
    unified_before = (unified_gap.id, unified_gap.name, unified_gap.priority)
    assert _convert(client, dict(rows[biz.name], priority="low")).status_code == 200
    (unified_after,) = recorded(rows[only.name])
    assert (unified_after.id, unified_after.name, unified_after.priority) == unified_before
