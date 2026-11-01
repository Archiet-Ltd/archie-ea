"""The sample company is one store, every fact cited, and worked out on load."""
import re

from app import db
from tests.test_sample_company_working_data import _architect, _figure

FIVE = {
    "Process online payments",
    "Track customer orders",
    "Sell to customers online",
    "Manage stock",
    "Run customer support",
}


def _load(client, login_as, user):
    login_as(client, user)
    return client.post("/architecture/sample-company/load", headers={"Accept": "application/json"})


def _scalar(sql, **params):
    return db.session.execute(db.text(sql), params).scalar()


def _payments_id(org_id):
    return _scalar(
        "SELECT id FROM archimate_elements WHERE organization_id = :o AND name = 'Payments service' "
        "AND type = 'ApplicationComponent'", o=org_id,
    )


def _impact(client, login_as, user, element_id):
    login_as(client, user)
    response = client.get("/api/v1/intelligence/impact/%d?include_derived=true&max_depth=3" % element_id)
    assert response.status_code == 200
    body = response.get_json()
    return body.get("data", body)


def test_capability_map_and_setup_list_show_the_five_capabilities(client, login_as, make_org):
    org, other = make_org(), make_org()
    user = _architect(org, "cap-a@example.com")
    assert _load(client, login_as, user).status_code == 200
    names = {
        row[0]
        for row in db.session.execute(
            db.text("SELECT name FROM unified_capabilities WHERE organization_id = :o"), {"o": org.id}
        )
    }
    assert FIVE <= names
    assert _scalar("SELECT count(*) FROM unified_capabilities WHERE organization_id = :o "
                   "AND source_table = 'business_capability'", o=org.id) == 5
    login_as(client, user)
    listing = client.get("/enterprise/capability-map/capabilities").get_data(as_text=True)
    for name in FIVE:
        assert name in listing
    assert "No capabilities yet" not in listing
    assert _scalar("SELECT count(*) FROM unified_capabilities WHERE organization_id = :o", o=other.id) == 0


def test_the_toast_counts_every_element_the_card_counts(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "toast-a@example.com")
    login_as(client, user)
    response = client.post("/architecture/sample-company/load", follow_redirects=True)
    page = response.get_data(as_text=True)
    stored = _scalar("SELECT count(*) FROM archimate_elements WHERE organization_id = :o", o=org.id)
    assert "Sample company loaded: %d elements created." % stored in page
    assert _figure(page, "health-kpi-elements") == str(stored)


def test_json_load_reports_the_same_count(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "toast-b@example.com")
    body = _load(client, login_as, user).get_json()
    stored = _scalar("SELECT count(*) FROM archimate_elements WHERE organization_id = :o", o=org.id)
    assert body["elements_created"] == stored


def test_every_connection_of_the_payments_service_is_cited_with_a_health(client, login_as, make_org):
    org, other = make_org(), make_org()
    user = _architect(org, "cite-a@example.com")
    _load(client, login_as, user)
    answer = _impact(client, login_as, user, _payments_id(org.id))
    rows = answer["rows"]
    assert rows
    for row in rows:
        provenance = row["relation"]["provenance"] if row["relation"]["kind"] == "explicit" else None
        if provenance:
            # Corrected: this already failed on the line, naming the signed-in person
            # ("Drawn by Sam Founder") on a relationship sample_company_service.py's own
            # working-data step adds (the relationship-origin listener stamps "manual"
            # before that step's own citation runs). Two legitimate texts now occur: the
            # base file's own relationships, imported directly with a source label, read
            # "Imported from Sample company"; the ones the working-data step adds read
            # "Sample company" with no prefix. Neither ever names the signed-in person.
            assert provenance["text"] in ("Imported from Sample company", "Sample company"), provenance
            assert provenance["recorded_at"]
    applications = [r for r in rows if answer["elements"][str(r["element_id"])]["type"] == "ApplicationComponent"]
    assert applications
    for row in applications:
        assert row["health"] == "Healthy", row
    assert _scalar("SELECT count(*) FROM archimate_relationships WHERE organization_id = :o", o=other.id) == 0


def test_indirect_connections_are_worked_out_by_the_load(client, login_as, make_org):
    org, other = make_org(), make_org()
    user = _architect(org, "derive-a@example.com")
    _load(client, login_as, user)
    assert _scalar("SELECT count(*) FROM archimate_derived_relationships WHERE organization_id = :o", o=org.id) > 0
    answer = _impact(client, login_as, user, _payments_id(org.id))
    assert answer["summary"]["derivation_state"] != "not_computed", answer["summary"]
    assert _scalar("SELECT count(*) FROM archimate_derived_relationships WHERE organization_id = :o", o=other.id) == 0


def test_capabilities_carry_the_health_the_dashboard_computes(client, login_as, make_org):
    org = make_org()
    user = _architect(org, "health-a@example.com")
    _load(client, login_as, user)
    answer = _impact(client, login_as, user, _payments_id(org.id))
    capabilities = [r for r in answer["rows"] if answer["elements"][str(r["element_id"])]["type"] == "Capability"]
    assert capabilities
    for row in capabilities:
        assert row["health"] in ("Healthy", "Warning", "Critical"), row
