"""The result footer's confidence rule (high, medium, low) and its isolation between organisations."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from app import db
from app.services import result_footer_service as svc

NOW = datetime(2026, 11, 2, 12, 0)


def _fact(**kw):
    base = dict(label="A fact", href="/x", source_kind="connector", source_label="GitHub",
                as_of=NOW - timedelta(days=10))
    base.update(kw)
    return svc.fact(**base)


def test_high_when_every_input_has_a_recorded_origin_and_is_recent():
    footer = svc.build_footer([_fact(), _fact(label="Another", source_kind="import", source_label="model.xml")], NOW)
    assert footer["confidence"] == "high"
    assert footer["sources"] == ["Synced by a connector from GitHub", "Imported from model.xml"]
    assert footer["newest_input"] == NOW - timedelta(days=10)
    assert footer["computed_at"] == NOW


def test_medium_when_an_input_is_old_undated_or_entered_by_hand():
    old = svc.build_footer([_fact(as_of=NOW - timedelta(days=91))], NOW)
    undated = svc.build_footer([_fact(as_of=None)], NOW)
    by_hand = svc.build_footer([_fact(source_kind="manual", source_label=None, who="Ada Lovelace")], NOW)
    assert [f["confidence"] for f in (old, undated, by_hand)] == ["medium"] * 3
    assert "older than 90 days" in old["confidence_reason"]
    assert "Entered by the team, Ada Lovelace" in by_hand["sources"]
    assert svc.build_footer([_fact(as_of=NOW - timedelta(days=90))], NOW)["confidence"] == "high"


def test_low_names_the_missing_input():
    footer = svc.build_footer([_fact(), svc.fact("Linked elements: none recorded", missing=True)], NOW)
    assert footer["confidence"] == "low"
    assert footer["missing"] == ["Linked elements: none recorded"]
    assert "Linked elements: none recorded" in footer["confidence_reason"]
    nothing = svc.build_footer([], NOW)
    assert nothing["confidence"] == "low" and "no input facts" in nothing["confidence_reason"]


def test_another_organisations_element_never_reaches_a_footer(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement

    org_a, org_b = make_org("footer-a"), make_org("footer-b")
    mine = ArchiMateElement(name="Mine", type="ApplicationComponent", layer="application", organization_id=org_a.id)
    theirs = ArchiMateElement(name="Theirs", type="ApplicationComponent", layer="application",
                              organization_id=org_b.id, source_kind="connector", source_label="Secret connector")
    db_session.add_all([mine, theirs])
    db_session.flush()

    known = svc.element_facts(org_a.id, [mine.id, theirs.id])
    assert set(known) == {mine.id}

    risk = SimpleNamespace(likelihood=2, impact=2, owner=None, updated_at=NOW, created_at=NOW)
    touches = [{"name": "Mine", "kind": "element", "id": mine.id, "element_id": mine.id},
               {"name": "Theirs", "kind": "element", "id": theirs.id, "element_id": theirs.id}]
    footer = svc.risk_footer(risk, touches, None, known, None, lambda t: "/e/%d" % t["element_id"], NOW)
    assert footer["confidence"] == "low"
    assert footer["missing"] == [svc.NOT_IN_MODEL]
    assert "Theirs" not in repr(footer) and "Secret connector" not in repr(footer)
    assert all("Secret connector" not in s for s in footer["sources"])
    assert svc.element_facts(org_b.id, [mine.id]) == {}


def test_a_shared_reference_element_is_named_and_dated_like_any_other(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement

    org = make_org("footer-shared")
    shared = ArchiMateElement(name="Shared reference", type="ApplicationComponent", layer="application",
                              organization_id=org.id, source_kind="import", source_label="reference.xml",
                              source_recorded_at=NOW - timedelta(days=5))
    db_session.add(shared)
    db_session.flush()
    # The row is written by an organisation, and the column is NOT NULL today. A shared reference element has
    # no organisation, so relax the constraint inside this test's own transaction (it rolls back with the
    # session) to hold such a row.
    db_session.execute(db.text("ALTER TABLE archimate_elements ALTER COLUMN organization_id DROP NOT NULL"))
    db_session.execute(db.text("UPDATE archimate_elements SET organization_id = NULL WHERE id = :id"),
                       {"id": shared.id})
    db_session.expire_all()

    known = svc.element_facts(org.id, [shared.id])
    assert known[shared.id]["name"] == "Shared reference"
    risk = SimpleNamespace(likelihood=2, impact=2, owner="Ada Lovelace", updated_at=NOW, created_at=NOW)
    touches = [{"name": "Shared reference", "kind": "element", "id": shared.id, "element_id": shared.id}]
    footer = svc.risk_footer(risk, touches, None, known, None, lambda t: "/e/%d" % t["element_id"], NOW)
    assert footer["missing"] == []
    assert [e["label"] for e in footer["evidence"]][1:] == ["Shared reference"]
    assert footer["newest_input"] == NOW
    assert "no longer recorded" not in repr(footer)


def test_source_pairs_each_origin_with_the_fact_it_belongs_to():
    footer = svc.build_footer([
        _fact(label="Recorded score", subject="Score", source_kind="manual", source_label=None, who="Ada Lovelace"),
        _fact(label="Spoke", subject="Element", source_kind="unknown", source_label=None),
        _fact(label="Hub", subject="Element", source_kind="unknown", source_label=None),
    ], NOW)
    assert footer["sources"] == ["Score: entered by the team, Ada Lovelace", "Element: no recorded origin"]


def test_medium_reason_names_which_input_is_the_weak_one():
    footer = svc.build_footer([
        _fact(label="Recorded score", subject="Score", source_kind="manual", source_label=None),
        _fact(label="Spoke", subject="Spoke", source_kind="sample", source_label=None),
        _fact(label="Hub", subject="Hub", as_of=NOW - timedelta(days=200)),
        _fact(label="Fine", subject="Fine"),
    ], NOW)
    reason = footer["confidence_reason"]
    assert footer["confidence"] == "medium"
    assert "Score was entered by hand" in reason
    assert "Spoke is sample data" in reason
    assert "Hub is older than 90 days" in reason
    assert "Fine" not in reason


# --- the builders behind each page's footer ---------------------------------------------------------------


def _kinds(footer):
    return footer["sources"]


def test_origin_of_reads_the_state_a_record_carries():
    assert svc.origin_of(None) == {"source_kind": "manual", "source_label": None}
    assert svc.origin_of("") == {"source_kind": "manual", "source_label": None}
    assert svc.origin_of("Abacus") == {"source_kind": "connector", "source_label": "Abacus"}
    assert svc.origin_of("power_platform_coe") == {"source_kind": "connector", "source_label": "Power Platform CoE"}
    assert svc.origin_of("import_2026")["source_kind"] == "import"
    assert svc.origin_of("csv") == {"source_kind": "import", "source_label": "csv"}
    assert svc.origin_of("ai")["source_kind"] == "derived"
    assert svc.origin_of("anything else")["source_kind"] == "manual"
    assert svc.origin_of("abacus", sample=True) == {"source_kind": "sample", "source_label": None}


def test_application_footer_names_the_record_and_its_owner(app):
    row = SimpleNamespace(id=7, name="Ledger", data_source="abacus", updated_at=NOW, created_at=NOW)
    with app.test_request_context("/"):
        owned = svc.application_footer(row, ["Ada Lovelace"], NOW)
        bare = svc.application_footer(row, [], NOW)
    assert [e["label"] for e in owned["evidence"]] == ["Ledger", "Owner: Ada Lovelace"]
    assert owned["evidence"][0]["href"].endswith("/7") or "7" in owned["evidence"][0]["href"]
    assert "Record: synced by a connector from Abacus" in owned["sources"]
    assert owned["confidence"] == "medium"
    assert bare["confidence"] == "low" and bare["missing"] == ["Owner: none recorded"]


def test_application_rows_and_vendor_footers_keep_organisations_apart(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a, org_b = make_org("vf-a"), make_org("vf-b")
    shared = VendorOrganization(name="Okta %s" % org_a.slug, status="active", named_by_organization_id=org_a.id,
                                is_seed_data=True)
    foreign = VendorOrganization(name="Foreign %s" % org_b.slug, status="active", named_by_organization_id=org_b.id,
                                 is_seed_data=True)
    db_session.add_all([shared, foreign])
    db_session.flush()
    mine = ApplicationComponent(name="Mine app", organization_id=org_a.id, vendor_organization_id=shared.id)
    theirs = ApplicationComponent(name="Their app", organization_id=org_b.id, vendor_organization_id=foreign.id)
    db_session.add_all([mine, theirs])
    db_session.flush()

    assert set(svc.application_rows(org_a.id, [mine.id, theirs.id])) == {mine.id}
    assert svc.application_rows(None, [mine.id]) == {}

    rows = [{"id": shared.id, "name": shared.name, "systems": [{"id": mine.id, "name": "Mine app"}],
             "system_count": 1},
            {"id": foreign.id, "name": "Foreign vendor", "systems": [{"id": theirs.id, "name": "Their app"}],
             "system_count": 1}]
    footers = svc.vendor_footers(org_a.id, rows, NOW)
    # The vendor this organisation names is read from the vendor row (sample data); the other organisation's
    # vendor is not read at all, so it is not stated as sample data and its system is not named.
    assert "Vendor: sample data" in footers[shared.id]["sources"]
    assert footers[foreign.id]["newest_input"] is None
    assert footers[foreign.id]["sources"][0] == "Vendor: entered by the team"
    assert "Their app" not in repr(footers)
    assert [e["label"] for e in footers[shared.id]["evidence"]][1:] == ["Mine app"]


def test_capability_footers_read_only_this_organisations_capabilities(db_session, make_org):
    from app.models.unified_capability import UnifiedCapability

    org_a, org_b = make_org("cf-a"), make_org("cf-b")
    mine = UnifiedCapability(name="Mine cap", organization_id=org_a.id, level=1, strategic_importance="high")
    theirs = UnifiedCapability(name="Their cap", organization_id=org_b.id, level=1, strategic_importance="high")
    db_session.add_all([mine, theirs])
    db_session.flush()
    scores = [{"capability_id": c.id, "capability_name": c.name, "coverage_count": 0} for c in (mine, theirs)]
    out = svc.capability_footers(org_a.id, scores, NOW)
    assert out[theirs.id]["missing"] == [svc.NOT_IN_MODEL]
    assert "Their cap" not in repr(out[theirs.id])
    assert out[mine.id]["missing"] != [svc.NOT_IN_MODEL]
    assert svc.capability_footers(None, scores, NOW) == {}


def test_impact_footer_never_names_another_organisations_element(db_session, make_org, app):
    from app.models.archimate_core import ArchiMateElement

    org_a, org_b = make_org("if-a"), make_org("if-b")
    mine = ArchiMateElement(name="Mine hub", type="ApplicationComponent", layer="application", organization_id=org_a.id)
    theirs = ArchiMateElement(name="Their spoke", type="ApplicationComponent", layer="application",
                              organization_id=org_b.id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    analysis = {"element_id": mine.id, "direct_dependencies": [{"id": theirs.id}], "indirect_dependencies": [],
                "total_affected": 1, "estimated_financial_risk": None}
    with app.test_request_context("/"):
        footer = svc.impact_footer(org_a.id, analysis, NOW)
        outside = svc.impact_footer(org_b.id, analysis, NOW)
    assert footer["evidence"][0]["label"] == "Mine hub"
    assert "Their spoke" not in repr(footer)
    assert svc.NOT_IN_MODEL in footer["missing"]
    assert "Cost at risk: could not be worked out" in footer["missing"]
    # Asked by the other organisation, the changed element is the one it cannot see.
    assert "Mine hub" not in repr(outside)
    assert outside["confidence"] == "low"


def test_roadmap_summary_footer_counts_only_this_organisations_records(db_session, make_org, app):
    from app.models.implementation_migration import Gap
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a, org_b = make_org("rs-a"), make_org("rs-b")
    db_session.add_all([Gap(name="Mine gap", organization_id=org_a.id), Gap(name="Their gap 1", organization_id=org_b.id),
                        Gap(name="Their gap 2", organization_id=org_b.id),
                        UnifiedWorkPackage(name="Their package", organization_id=org_b.id)])
    db_session.flush()
    with app.test_request_context("/"):
        footer = svc.roadmap_summary_footer(org_a.id, 3, 1, NOW)
    labels = [e["label"] for e in footer["evidence"]]
    assert "Capabilities: 3" in labels and "Domains: 1" in labels
    assert "Gaps: 1" in labels
    assert "Work packages: none recorded" in labels
    assert "Work packages: none recorded" in footer["missing"]


def test_a_linked_gap_states_its_own_origin_and_date(app):
    stamped = NOW - timedelta(days=3)
    derived = SimpleNamespace(id=1, name="Derived gap", source_table="gaps_legacy", updated_at=stamped, created_at=stamped)
    by_hand = SimpleNamespace(id=2, name="Typed gap", source_table=None, updated_at=None, created_at=NOW - timedelta(days=9))
    bare = {"id": 3, "name": "Unstated gap"}
    older = NOW - timedelta(days=30)
    wp = SimpleNamespace(id=5, name="Rollout", source_type=None, updated_at=older, created_at=older,
                         progress_percentage=10, priority="high")
    with app.test_request_context("/"):
        footer = svc.work_package_detail_footer(wp, "Ada", [derived, by_hand, bare], None, NOW)
    assert footer["sources"][3:] == [
        "Derived gap: worked out by the product", "Typed gap: entered by the team", "Unstated gap: no recorded origin"]
    # The package is older than its gaps, so the newest input is the newest gap's own date; the unstated gap adds none.
    assert footer["newest_input"] == stamped


def test_gap_rows_are_this_organisations_only(db_session, make_org):
    from app.models.implementation_migration import Gap

    org_a, org_b = make_org("gr-a"), make_org("gr-b")
    mine, theirs = Gap(name="Mine", organization_id=org_a.id), Gap(name="Theirs", organization_id=org_b.id)
    db_session.add_all([mine, theirs])
    db_session.flush()
    assert set(svc.gap_rows(org_a.id, [mine.id, theirs.id])) == {mine.id}
    assert svc.gap_rows(None, [mine.id]) == {}
def test_a_label_that_only_repeats_the_origin_is_not_printed_twice():
    footer = svc.build_footer([
        _fact(label="Calibration Ledger", subject="Element", source_kind="manual",
              source_label="Entered by the team", as_of=NOW - timedelta(days=3)),
    ], NOW)
    assert footer["sources"] == ["Element: entered by the team"]
    assert "(Entered by the team)" not in " ".join(footer["sources"])


def test_a_row_that_prints_the_work_package_name_is_not_footed_with_it_again():
    wp = SimpleNamespace(id=7, name="Appoint billing owner", source_type=None, updated_at=NOW, created_at=NOW,
                         progress_percentage=40, priority="high")
    alone = svc.work_package_footer(wp, NOW)
    beside_its_name = svc.work_package_footer(wp, NOW, name_shown=True)
    assert "Appoint billing owner" in [e["label"] for e in alone["evidence"]]
    labels = [e["label"] for e in beside_its_name["evidence"]]
    assert "Appoint billing owner" not in labels
    assert labels == ["Work package record", "Progress: 40%"]
    assert all(not e["href"] for e in beside_its_name["evidence"])
    assert beside_its_name["confidence"] == alone["confidence"]


def test_a_vendor_page_footer_names_the_vendor_record_without_linking_to_itself(app):
    vendor = SimpleNamespace(id=9, name="Salesforce", is_seed_data=False, updated_at=NOW, created_at=NOW)
    contract = SimpleNamespace(contract_name="CRM licence", updated_at=NOW, created_at=NOW)
    with app.test_request_context("/"):
        footer = svc.vendor_detail_footer(1, vendor, [], [contract], NOW)
    labels = [e["label"] for e in footer["evidence"]]
    assert labels == ["Vendor record", "CRM licence"]
    assert all(not e["href"] for e in footer["evidence"])
    assert footer["sources"][0].startswith("Vendor: entered by the team")
    assert footer["missing"] == []


def test_a_work_package_page_footer_repeats_neither_its_name_nor_the_gaps_listed_above_it(app):
    stamped = NOW - timedelta(days=3)
    gap = SimpleNamespace(id=1, name="Billing has no owner", source_table=None, updated_at=stamped, created_at=stamped)
    wp = SimpleNamespace(id=5, name="Appoint billing owner", source_type=None, updated_at=stamped, created_at=stamped,
                         progress_percentage=10, priority="high")
    capability = SimpleNamespace(name="Billing", updated_at=stamped, created_at=stamped)
    with app.test_request_context("/"):
        footer = svc.work_package_detail_footer(wp, "Ada", [gap], capability, NOW, records_listed=True)
    labels = [e["label"] for e in footer["evidence"]]
    assert labels == ["Work package record", "Progress: 10%", "Owner: Ada", "1 gap", "Capability record"]
    assert all(not e["href"] for e in footer["evidence"])
    assert footer["sources"] and footer["newest_input"] == stamped


def test_a_vendor_page_footer_does_not_link_the_systems_listed_above_it(db_session, make_org, app):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("vf-a")
    one = ApplicationComponent(name="Sales Cloud", organization_id=org.id)
    two = ApplicationComponent(name="Service Cloud", organization_id=org.id)
    db_session.add_all([one, two])
    db_session.flush()
    vendor = SimpleNamespace(id=9, name="Salesforce", is_seed_data=False, updated_at=NOW, created_at=NOW)
    with app.test_request_context("/"):
        footer = svc.vendor_detail_footer(org.id, vendor, [one, two], [], NOW, systems_listed=True)
    labels = [e["label"] for e in footer["evidence"]]
    assert labels == ["Vendor record", "Sales Cloud", "Service Cloud"]
    assert all(not e["href"] for e in footer["evidence"])
    with app.test_request_context("/"):
        linked = svc.vendor_detail_footer(org.id, vendor, [one, two], [], NOW)
    assert all(e["href"] for e in linked["evidence"][1:])


def test_the_plateau_card_footer_names_the_record_not_the_plateau():
    plateau = SimpleNamespace(name="Target state", updated_at=NOW, created_at=NOW, target_date=NOW)
    footer = svc.plateau_footer(plateau, NOW)
    assert [e["label"] for e in footer["evidence"]][0] == "Plateau record"
