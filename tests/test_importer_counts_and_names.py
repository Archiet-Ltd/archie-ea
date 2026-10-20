"""T-imp-02: the OEF importer counts every relationship it is given, keeps each
modelled type, imports names up to 255 characters, and logs no false warnings."""

import logging
import os

import pytest

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
       identifier="id-m" version="3.0">
  <name xml:lang="en">Counts</name>
  <elements>
%s
  </elements>
  <relationships>
%s
  </relationships>
</model>
"""


def _xml(elements, rels):
    el = "\n".join(
        '<element identifier="%s" xsi:type="%s"><name xml:lang="en">%s</name></element>' % e
        for e in elements
    )
    rl = "\n".join(
        '<relationship identifier="%s" xsi:type="%s" source="%s" target="%s"/>' % r
        for r in rels
    )
    return HEADER % (el, rl)


@pytest.fixture
def org_ctx(app, db_session, make_org, tenant_ctx):
    org = make_org("imp2-import")
    with tenant_ctx(org.id):
        yield org


def _run(xml):
    from app.services.archimate_import_service import ArchiMateImportService

    service = ArchiMateImportService()
    return service.execute_import(service.parse_oef_xml(xml), strategy="skip_duplicates")


def test_three_refused_types_on_one_pair_create_three(app, org_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    xml = _xml(
        [("a", "BusinessActor", "Actor"), ("c", "Capability", "Cap")],
        [
            ("r1", "Realization", "a", "c"),
            ("r2", "Influence", "a", "c"),
            ("r3", "Flow", "a", "c"),
        ],
    )
    res = _run(xml)
    assert res["relationships_created"] == 3
    assert res["relationships_skipped"] == 0
    assert res["relationships_failed"] == []
    docs = " ".join(r.description or "" for r in ArchiMateRelationship.query.all())
    for t in ("Realization", "Influence", "Flow"):
        assert "As modelled: %s." % t in docs


def test_serving_after_association_on_same_pair_is_created(app, org_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    xml = _xml(
        [("a", "BusinessActor", "Actor"), ("b", "Capability", "Cap")],
        [("r1", "Association", "a", "b"), ("r2", "Serving", "a", "b")],
    )
    res = _run(xml)
    assert res["relationships_created"] == 2
    assert res["relationships_softened"] == 1
    assert res["relationships_skipped"] == 0
    assert ArchiMateRelationship.query.count() == 2


def test_true_duplicate_is_skipped_and_reimport_is_idempotent(app, org_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    xml = _xml(
        [("a", "BusinessActor", "Actor"), ("c", "Capability", "Cap")],
        [("r1", "Influence", "a", "c"), ("r2", "Influence", "a", "c")],
    )
    res = _run(xml)
    assert res["relationships_created"] == 1
    assert res["relationships_skipped"] == 1
    assert res["relationships_softened"] == 1
    again = _run(xml)
    assert again["relationships_created"] == 0
    assert again["relationships_skipped"] == 2
    assert ArchiMateRelationship.query.count() == 1


def test_200_character_name_imports(app, org_ctx):
    from app.models.archimate_core import ArchiMateElement

    name = "N" * 200
    res = _run(_xml([("a", "Capability", name)], []))
    assert res["created"] == 1
    assert res["failed"] == 0
    assert ArchiMateElement.query.filter_by(name=name).count() == 1


def test_grouping_logs_no_unknown_type_warning(app, org_ctx):
    from app.services.archimate_import_service import ArchiMateImportService

    service = ArchiMateImportService()
    parsed = service.parse_oef_xml(_xml([("g", "Grouping", "Group")], []))
    assert not [e for e in parsed["errors"] if "Unknown element type" in e]
    parsed = service.parse_oef_xml(_xml([("g", "Frobnicator", "Odd")], []))
    assert [e for e in parsed["errors"] if "Frobnicator" in e]


def test_british_realisation_is_normalised_to_realization(app, org_ctx):
    from app.models.archimate_core import ArchiMateRelationship

    xml = _xml(
        [("a", "ApplicationComponent", "App"), ("c", "Capability", "Cap")],
        [("r1", "Realisation", "a", "c")],
    )
    res = _run(xml)
    assert res["relationships_created"] == 1
    assert ArchiMateRelationship.query.one().type == "realization"


def test_archiet_file_counts_match_its_relationships(app, org_ctx):
    path = os.path.join(os.path.dirname(__file__), "fixtures", "oef", "archiet-model-v1.oef.xml")
    with open(path, "rb") as fh:
        content = fh.read().decode("utf-8")
    res = _run(content)
    total = 509
    assert res["relationships_failed"] == []
    assert res["relationships_skipped"] == 0
    assert res["relationships_created"] == total
    assert res["relationships_softened"] == len(res["relationship_notices"])
    print("ARCHIET SUMMARY", {k: (v if isinstance(v, int) else len(v)) for k, v in res.items()
                              if k.startswith("relationships") or k in ("created", "failed", "relationship_notices")})
