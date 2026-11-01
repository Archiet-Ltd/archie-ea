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
    # A kept child and a retired copy of it under the kept parent, so a picker
    # that lists the children of a chosen capability can be checked too.
    child = UnifiedCapability(
        name=f"Kept child {tag}", level=2, organization_id=org.id, code=f"C{tag}",
        parent_capability_id=kept.id,
    )
    db_session.add(child)
    db_session.flush()
    retired_child = UnifiedCapability(
        name=f"Retired child {tag}", level=2, organization_id=org.id, code=f"X{tag}",
        parent_capability_id=kept.id, retired_into_id=child.id,
    )
    # Typed pairs for the pickers that list one kind of capability.
    typed = []
    for kind in ("APPLICATION", "MANUFACTURING"):
        keep = UnifiedCapability(
            name=f"Kept {kind.lower()} {tag}", level=1, organization_id=org.id,
            code=f"K{kind[:1]}{tag}", specialization_type=kind,
        )
        db_session.add(keep)
        db_session.flush()
        typed.append(keep)
        db_session.add(UnifiedCapability(
            name=f"Retired {kind.lower()} {tag}", level=1, organization_id=org.id,
            code=f"R{kind[:1]}{tag}", specialization_type=kind, retired_into_id=keep.id,
        ))
    stream = ValueStream(name=f"Stream {tag}", code=f"V{tag}", organization_id=org.id)
    db_session.add_all([retired, retired_child, foreign, stream])
    db_session.flush()
    return {
        "org": org, "user": _user(db_session, org), "tag": tag,
        "stream": stream, "kept": kept, "retired": retired, "child": child, "foreign": foreign,
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


# ---------------------------------------------------------------------------
# Every other picker and list that feeds a selection.
# ---------------------------------------------------------------------------


def _text(client, login_as, seeded, url, method="get", **kwargs):
    login_as(client, seeded["user"])
    resp = getattr(client, method)(url, **kwargs)
    assert resp.status_code == 200, (url, resp.status_code, resp.data[:300])
    return resp.get_data(as_text=True)


def _only_kept_in(text, seeded, kept="Kept", retired="Retired"):
    tag = seeded["tag"]
    assert f"{kept} {tag}" in text, text[:500]
    assert f"{retired} {tag}" not in text, "retired capability offered"


@pytest.mark.parametrize(
    "url",
    [
        "/api/capabilities/grouped",
        "/api/capabilities",
        "/dashboard/api/unified-capabilities",
        "/dashboard/api/unified-capabilities?level=1",
        "/applications/dashboard/api/capabilities",
        "/capability-map/api/capabilities/tree",
        "/capability-map/api/capabilities/hierarchy",
        "/capability-map/simple",
        "/capability-map/hierarchy",
    ],
)
def test_picker_endpoint_leaves_out_retired(client, login_as, seeded, url):
    text = _text(client, login_as, seeded, url)
    _only_kept_in(text, seeded)


def test_related_capabilities_leaves_out_retired_child(client, login_as, seeded):
    text = _text(
        client, login_as, seeded, "/dashboard/api/related-capabilities",
        method="post",
        json={"capability_ids": [seeded["kept"].id], "target_level": 2},
    )
    _only_kept_in(text, seeded, kept="Kept child", retired="Retired child")


def test_architecture_assistant_catalogue_leaves_out_retired(client, login_as, seeded):
    text = _text(client, login_as, seeded, "/api/architecture-assistant/capabilities?per_page=100&search=" + seeded["tag"])
    _only_kept_in(text, seeded)


def test_solution_design_picker_leaves_out_retired(client, login_as, seeded):
    text = _text(client, login_as, seeded, "/solutions/api/capabilities-by-type?type=BUSINESS&search=" + seeded["tag"])
    _only_kept_in(text, seeded)


def test_raci_matrix_columns_leave_out_retired(client, login_as, seeded):
    text = _text(client, login_as, seeded, "/organization/raci/api/data")
    _only_kept_in(text, seeded)


def test_vendor_portfolio_overview_leaves_out_retired_kinds(client, login_as, seeded):
    text = _text(client, login_as, seeded, "/api/vendors/analytics/overview")
    tag = seeded["tag"]
    assert f"Kept application {tag}" in text, text[:500]
    assert f"Retired application {tag}" not in text


def test_framework_listing_query_leaves_out_retired(db_session, seeded):
    """The capability framework listing joins a domain, so assert its rows directly."""
    from app.models.unified_capability import UnifiedCapability

    ids = {c.id for c in UnifiedCapability.active().all()}
    assert seeded["child"].id in ids and seeded["kept"].id in ids
    assert seeded["retired"].id not in ids


def test_record_page_still_opens_for_a_retired_capability(client, login_as, seeded):
    """A retired capability is not offered, but its record still answers."""
    login_as(client, seeded["user"])
    resp = client.get(f"/api/v1/capabilities/{seeded['retired'].id}")
    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    data = body.get("data", body)
    assert data["name"] == f"Retired {seeded['tag']}"


def test_api_v1_capability_record_returns_the_fields(client, login_as, seeded):
    login_as(client, seeded["user"])
    resp = client.get(f"/api/v1/capabilities/{seeded['kept'].id}")
    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    data = body.get("data", body)
    assert data["name"] == f"Kept {seeded['tag']}"
    assert data["level"] == 1
    assert "domain" in data and "status" in data


def test_api_v1_capability_record_of_another_organisation_is_not_found(
    client, login_as, seeded
):
    login_as(client, seeded["user"])
    resp = client.get(f"/api/v1/capabilities/{seeded['foreign'].id}")
    assert resp.status_code == 404
