"""The application spreadsheet import keeps the vendor and hosting columns it is given.

The page sends a column mapping with the file; the import honours it, reports a mapping
to a field that does not exist, and writes rows to the importing organisation only.
"""
from __future__ import annotations

import io
import json
import uuid

import pytest

PLAIN = (
    "Name,Vendor,Public Cloud Provider\n"
    "{a},GitHub,AWS\n"
    "{b},Atlassian,Azure\n"
)
CUSTOM = (
    "App Title,Supplier,Hosted On\n"
    "{a},GitHub,AWS\n"
    "{b},Atlassian,Azure\n"
)
MAPPING = {"App Title": "name", "Supplier": "vendor_name", "Hosted On": "cloud_provider"}


@pytest.fixture(autouse=True)
def _clean_cache():
    from app.modules.import_batch.services import import_orchestrator as mod

    mod._import_idempotency_cache.clear()
    yield
    mod._import_idempotency_cache.clear()


def _admin(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    user = User(
        email=f"csv-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Csv",
        last_name="Importer",
        organization_id=org.id,
        role=Role.query.filter_by(name="User").first(),
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin")
    user.grant_org_admin()
    db_session.flush()
    return user


def _names():
    token = uuid.uuid4().hex[:8]
    return f"Alpha {token}", f"Beta {token}"


def _post(client, login_as, user, text, mapping=None, mode="skip"):
    login_as(client, user)
    data = {"file": (io.BytesIO(text.encode()), "apps.csv"), "import_mode": mode}
    if mapping is not None:
        data["field_mappings"] = json.dumps(mapping)
    return client.post(
        "/applications/import", data=data, content_type="multipart/form-data",
        follow_redirects=False,
    )


def _apps(db_session, org, names):
    from app.models.application_layer import ApplicationComponent

    db_session.expire_all()
    rows = ApplicationComponent.query.filter(
        ApplicationComponent.organization_id == org.id,
        ApplicationComponent.name.in_(names),
    ).all()
    return {r.name: r for r in rows}


def _flashes(client):
    with client.session_transaction() as sess:
        return [(c, m) for c, m in sess.get("_flashes", [])]


def test_two_row_file_keeps_vendor_and_hosting(client, db_session, make_org, login_as):
    org = make_org("csva")
    user = _admin(db_session, org)
    a, b = _names()
    _post(client, login_as, user, PLAIN.format(a=a, b=b))
    apps = _apps(db_session, org, [a, b])
    assert {k: (v.vendor_name, v.cloud_provider) for k, v in apps.items()} == {
        a: ("GitHub", "AWS"),
        b: ("Atlassian", "Azure"),
    }


def test_custom_headers_with_field_mappings_give_the_same_result(
    client, db_session, make_org, login_as
):
    org = make_org("csvb")
    user = _admin(db_session, org)
    a, b = _names()
    _post(client, login_as, user, CUSTOM.format(a=a, b=b), mapping=MAPPING)
    apps = _apps(db_session, org, [a, b])
    assert {k: (v.vendor_name, v.cloud_provider) for k, v in apps.items()} == {
        a: ("GitHub", "AWS"),
        b: ("Atlassian", "Azure"),
    }


def test_unknown_mapping_target_is_reported(client, db_session, make_org, login_as):
    org = make_org("csvc")
    user = _admin(db_session, org)
    a, b = _names()
    bad = dict(MAPPING, Supplier="vendor_nmae")
    _post(client, login_as, user, CUSTOM.format(a=a, b=b), mapping=bad)
    errors = [m for c, m in _flashes(client) if c == "error"]
    assert any("vendor_nmae" in m for m in errors), errors
    assert _apps(db_session, org, [a, b]) == {}


def test_mapping_for_a_column_the_file_lacks_is_reported(
    client, db_session, make_org, login_as
):
    org = make_org("csvd")
    user = _admin(db_session, org)
    a, b = _names()
    bad = dict(MAPPING, Missing="vendor_name")
    _post(client, login_as, user, CUSTOM.format(a=a, b=b), mapping=bad)
    errors = [m for c, m in _flashes(client) if c == "error"]
    assert any("Missing" in m for m in errors), errors
    assert _apps(db_session, org, [a, b]) == {}


def test_rows_land_in_the_importing_organisation_only(
    client, db_session, make_org, login_as
):
    from app.models.application_layer import ApplicationComponent

    org_a, org_b = make_org("csve"), make_org("csvf")
    user = _admin(db_session, org_a)
    a, b = _names()
    other = ApplicationComponent(
        name=a, organization_id=org_b.id, vendor_name="Other Vendor",
        cloud_provider="Other Cloud", deployment_status="planned",
    )
    db_session.add(other)
    db_session.flush()
    other_id = other.id

    _post(client, login_as, user, PLAIN.format(a=a, b=b))

    mine = _apps(db_session, org_a, [a, b])
    assert mine[a].vendor_name == "GitHub" and mine[a].cloud_provider == "AWS"
    assert mine[b].vendor_name == "Atlassian"
    from sqlalchemy import text

    theirs = db_session.execute(
        text(
            "SELECT id, vendor_name, cloud_provider FROM application_components "
            "WHERE organization_id = :o AND name IN (:a, :b)"
        ),
        {"o": org_b.id, "a": a, "b": b},
    ).all()
    assert [tuple(r) for r in theirs] == [(other_id, "Other Vendor", "Other Cloud")]


@pytest.mark.parametrize("target", ["notes", "application_code", "total_cost_of_ownership", "id", "organization_id"])
def test_mapping_to_a_field_the_import_does_not_read_is_reported(
    client, db_session, make_org, login_as, target
):
    org = make_org("csvg")
    user = _admin(db_session, org)
    a, b = _names()
    bad = dict(MAPPING, Supplier=target)
    _post(client, login_as, user, CUSTOM.format(a=a, b=b), mapping=bad)
    errors = [m for c, m in _flashes(client) if c == "error"]
    assert any(target in m for m in errors), errors
    assert _apps(db_session, org, [a, b]) == {}


def test_a_mapped_column_does_not_also_feed_another_field(
    client, db_session, make_org, login_as
):
    org = make_org("csvh")
    user = _admin(db_session, org)
    a, b = _names()
    text = "Name,Vendor,Public Cloud Provider\n{a},GitHub,AWS\n{b},Atlassian,Azure\n".format(a=a, b=b)
    mapping = {"Name": "name", "Vendor": "description", "Public Cloud Provider": "cloud_provider"}
    _post(client, login_as, user, text, mapping=mapping)
    apps = _apps(db_session, org, [a, b])
    assert {k: (v.description, v.vendor_name, v.cloud_provider) for k, v in apps.items()} == {
        a: ("GitHub", None, "AWS"),
        b: ("Atlassian", None, "Azure"),
    }


def _existing(db_session, org, name):
    from app.models.application_layer import ApplicationComponent

    app = ApplicationComponent(
        name=name, organization_id=org.id, vendor_name="Old Vendor",
        cloud_provider="Old Cloud", deployment_status="planned",
    )
    db_session.add(app)
    db_session.flush()
    return app


def test_update_mode_blank_cells_keep_stored_vendor_and_hosting(
    client, db_session, make_org, login_as
):
    org = make_org("csvi")
    user = _admin(db_session, org)
    a, _ = _names()
    _existing(db_session, org, a)
    _post(client, login_as, user, f"Name,Vendor,Public Cloud Provider\n{a},,\n", mode="update")
    row = _apps(db_session, org, [a])[a]
    assert (row.vendor_name, row.cloud_provider) == ("Old Vendor", "Old Cloud")


def test_update_mode_values_replace_stored_vendor_and_hosting(
    client, db_session, make_org, login_as
):
    org = make_org("csvj")
    user = _admin(db_session, org)
    a, _ = _names()
    _existing(db_session, org, a)
    _post(client, login_as, user, f"Name,Vendor,Public Cloud Provider\n{a},GitHub,AWS\n", mode="update")
    row = _apps(db_session, org, [a])[a]
    assert (row.vendor_name, row.cloud_provider) == ("GitHub", "AWS")
