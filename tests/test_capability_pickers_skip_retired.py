"""No capability picker offers a capability retired into another.

A database that held duplicates before the provenance index existed keeps the
retired copies as rows. Each picker must read through the one accessor,
UnifiedCapability.active(), so a person is never offered the retired copy.
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    from app.models.user import Role, User

    user = User(
        email=f"picker-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Picker",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.password = "correct-horse-battery"
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user.role = role
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def seeded(db_session, make_org):
    """Two organisations; the first holds a kept capability and a retired copy."""
    from app.models.unified_capability import UnifiedCapability, ValueStream

    tag = uuid.uuid4().hex[:8]
    org, other = make_org("pickers"), make_org("pickers-other")
    kept = UnifiedCapability(
        name=f"Kept {tag}", level=1, organization_id=org.id, code=f"K{tag}"
    )
    db_session.add(kept)
    db_session.flush()
    retired = UnifiedCapability(
        name=f"Retired {tag}", level=1, organization_id=org.id, code=f"R{tag}",
        retired_into_id=kept.id,
    )
    foreign = UnifiedCapability(
        name=f"Foreign {tag}", level=1, organization_id=other.id, code=f"F{tag}"
    )
    stream = ValueStream(name=f"Stream {tag}", code=f"V{tag}", organization_id=org.id)
    db_session.add_all([retired, foreign, stream])
    db_session.flush()
    return {
        "org": org, "user": _user(db_session, org), "tag": tag,
        "stream": stream, "kept": kept, "retired": retired,
    }


def _names(payload):
    if isinstance(payload, dict):
        for key in ("capabilities", "data", "items"):
            if key in payload:
                return _names(payload[key])
        inner = payload.get("data")
        return _names(inner) if inner is not None else []
    return [row["name"] for row in payload]


def _offered(client, login_as, seeded, url):
    login_as(client, seeded["user"])
    resp = client.get(url)
    assert resp.status_code == 200, (url, resp.status_code, resp.data[:300])
    names = _names(resp.get_json())
    tag = seeded["tag"]
    return [n for n in names if tag in n]


def _assert_only_kept(offered, seeded):
    assert f"Kept {seeded['tag']}" in offered, offered
    assert f"Retired {seeded['tag']}" not in offered, offered
    assert f"Foreign {seeded['tag']}" not in offered, offered


def test_active_accessor_leaves_out_retired(seeded):
    from app.models.unified_capability import UnifiedCapability

    ids = {c.id for c in UnifiedCapability.active().all()}
    assert seeded["kept"].id in ids
    assert seeded["retired"].id not in ids


def test_api_v1_capabilities_leaves_out_retired(client, login_as, seeded):
    _assert_only_kept(
        _offered(client, login_as, seeded, "/api/v1/capabilities/?per_page=100&search=" + seeded["tag"]),
        seeded,
    )


def test_arb_review_form_leaves_out_retired(client, login_as, seeded):
    _assert_only_kept(_offered(client, login_as, seeded, "/arb/api/form-data"), seeded)


def test_value_stream_row_picker_leaves_out_retired(client, login_as, seeded):
    url = f"/value-streams/{seeded['stream'].id}/api/unmapped-capabilities?q={seeded['tag']}"
    _assert_only_kept(_offered(client, login_as, seeded, url), seeded)


def test_capability_map_summary_leaves_out_retired(client, login_as, seeded):
    _assert_only_kept(
        _offered(client, login_as, seeded, "/capability-map/api/capabilities"), seeded
    )


def test_vendor_analysis_dropdown_leaves_out_retired(client, login_as, seeded):
    _assert_only_kept(_offered(client, login_as, seeded, "/dashboard/api/capabilities"), seeded)
