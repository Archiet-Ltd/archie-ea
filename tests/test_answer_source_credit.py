"""A cited answer credits each connection to where it really came from.

Whatever the question kind, a link a connector wrote is never credited to people, and a
link worked out from others says so. The data-flow answer is built on its own walk, so it
is tested through that walk as well as on bare rows.
"""

import json

import pytest

from app.modules.intelligence.services.answer_envelope import (
    KIND_DATA_FLOW,
    KIND_IMPACT,
    connection_sources,
    row_source,
)


def _row(kind="explicit", provenance=None, origins=None):
    relation = {"kind": kind, "provenance": provenance or {}}
    if origins:
        relation["origins"] = origins
    return {"relation": relation}


@pytest.mark.parametrize("question_kind", [KIND_IMPACT, KIND_DATA_FLOW])
@pytest.mark.parametrize(
    "row, expected",
    [
        (_row(provenance={"kind": "manual"}), "recorded by people"),
        (_row(), "recorded by people"),
        (_row(provenance={"kind": "connector"}), "read from a connected system"),
        (_row(provenance={"kind": "derived"}), "worked out from recorded links"),
        (_row(provenance={"kind": "import"}), "imported"),
        (_row(provenance={"kind": "sample"}), "sample data"),
        (_row(provenance={"kind": "manual"}, origins={"7": {"system": "github"}}), "read from GitHub"),
        (_row(kind="derived"), "worked out from recorded links"),
    ],
)
def test_row_source_follows_the_stamp_not_a_default(question_kind, row, expected):
    assert row_source(question_kind, row) == expected


def test_summary_counts_each_source():
    answer = {
        "question_kind": KIND_DATA_FLOW,
        "rows": [_row(provenance={"kind": "manual"}), _row(provenance={"kind": "connector"}),
                 _row(provenance={"kind": "connector"})],
    }
    result = connection_sources(answer)
    assert result["sentence"] == "2 read from a connected system, 1 recorded by people"
    assert result["all_people"] is False


def test_data_flow_walk_credits_a_connector_link_to_its_system(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("flow")
    other = make_org("flow-other")

    def element(owner, name, kind):
        row = ArchiMateElement(name=name, type=kind, layer="application", organization_id=owner.id)
        db_session.add(row)
        db_session.flush()
        return row

    data = element(org, "Customer records", "DataObject")
    writer = element(org, "Billing app", "ApplicationComponent")
    drawn = element(org, "Ledger app", "ApplicationComponent")
    foreign_data = element(other, "Foreign records", "DataObject")
    foreign_app = element(other, "Foreign app", "ApplicationComponent")
    db_session.add(ArchiMateRelationship(
        type="Access", source_id=writer.id, target_id=data.id, organization_id=org.id,
        access_mode="write", source_kind="connector", source_label="GitHub acme/shop",
        source_ref=json.dumps({"system": "github", "repository": "acme/shop", "commit": "abc1234", "file": "a.yml"})))
    db_session.add(ArchiMateRelationship(
        type="Access", source_id=drawn.id, target_id=data.id, organization_id=org.id,
        access_mode="write", source_kind="manual", source_label="Grace Hopper"))
    db_session.add(ArchiMateRelationship(
        type="Access", source_id=foreign_app.id, target_id=foreign_data.id, organization_id=other.id,
        access_mode="write", source_kind="connector", source_label="GitHub other/repo",
        source_ref=json.dumps({"system": "github", "repository": "other/repo", "commit": "ffff000", "file": "b.yml"})))
    db_session.flush()

    with tenant_ctx(org.id):
        result = IntelligenceQueryService.data_flow(data.id)
    answer = {"question_kind": KIND_DATA_FLOW, "rows": result["rows"]}
    sources = {row["element_id"]: row_source(KIND_DATA_FLOW, row) for row in result["rows"]}
    assert sources[writer.id] == "read from GitHub"
    assert sources[drawn.id] == "recorded by people"
    assert connection_sources(answer)["sentence"] == "1 read from GitHub, 1 recorded by people"
    assert "other/repo" not in json.dumps(result["rows"])
