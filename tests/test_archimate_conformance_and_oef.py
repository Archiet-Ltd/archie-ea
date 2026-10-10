"""
ArchiMate 3.2 conformance validation + Open Exchange Format export.

Two defects are pinned here.

1. Metamodel validation contradicted itself. Two validators shipped side by side:
   a 527-pair matrix in app/config/archimate_relationship_matrix.py, and a
   hand-written table inside ArchiMateMetamodelValidator. The hand-written one
   scored 1/5 on textbook-valid patterns from the specification — it rejected
   BusinessActor assigned to BusinessRole, the single most common relationship in
   ArchiMate, while accepting BusinessActor composing a Goal, which the spec
   forbids. Which one a user hit depended on the code path. The validator now
   delegates to the matrix.

2. The OEF export was not a model, it was a data dump. It emitted only <name>,
   <elements> and <relationships> — no <views>, so every saved diagram layout was
   lost; no <properties>, so all custom metadata was lost; no <organizations>, so
   an importing tool dropped everything into one flat folder. Its own docstring
   claimed it exported "elements, relationships, views, and properties", and the
   word "views" appeared exactly once in the file: in that sentence.

The element-order assertions matter as much as the content ones: the Open Exchange
schema fixes the child sequence, and a file with the right data in the wrong order
is rejected by a validating importer.
"""

import json
import uuid

import pytest

NS = "{http://www.opengroup.org/xsd/archimate/3.0/}"

# Tests below that need the app (the OEF export fixture, and the empty-model
# export test) use the shared session-scoped ``app`` fixture from
# tests/conftest.py rather than defining their own — a hand-rolled copy used
# to live here, and the OEF fixture also used to insert its own Organization
# row (falling back to "seed one if the table is empty") through a plain
# request context with no cleanup, which was the leak this file used to leave
# behind on every run against an empty database.


# ---------------------------------------------------------------- conformance


class TestRelationshipConformance:
    """The validator must agree with the ArchiMate 3.2 permitted-relationship matrix."""

    # (source, target, relationship, permitted-by-spec)
    SPEC_CASES = [
        ("BusinessActor", "BusinessRole", "Assignment", True),
        ("BusinessRole", "BusinessProcess", "Assignment", True),
        ("Node", "Artifact", "Assignment", True),
        ("TechnologyProcess", "Artifact", "Access", True),
        ("ApplicationComponent", "ApplicationService", "Realization", True),
        ("ApplicationComponent", "BusinessProcess", "Serving", True),
        ("Goal", "Requirement", "Influence", True),
        ("BusinessActor", "Goal", "Composition", False),
        ("DataObject", "Goal", "Triggering", False),
        ("Artifact", "BusinessActor", "Assignment", False),
    ]

    @staticmethod
    def _validate(pair):
        from app.modules.architecture.services.archimate_metamodel_validator import (
            ArchiMateMetamodelValidator,
        )

        source, target, rel, _ = pair
        return ArchiMateMetamodelValidator().validate_model(
            [{"name": "S", "type": source}, {"name": "T", "type": target}],
            [{"source": "S", "target": "T", "type": rel}],
        )

    @pytest.mark.parametrize("case", SPEC_CASES, ids=lambda c: f"{c[0]}-{c[2]}-{c[1]}")
    def test_matches_the_specification(self, case):
        source, target, rel, permitted = case
        result = self._validate(case)
        accepted = not result["errors"]
        assert accepted == permitted, (
            f"{source} --{rel}--> {target}: validator says "
            f"{'valid' if accepted else 'invalid'}, ArchiMate 3.2 says "
            f"{'valid' if permitted else 'invalid'}. {result['errors'][:1]}"
        )

    def test_agrees_with_the_matrix_on_every_case(self):
        """The validator and the matrix must never disagree — that was the bug."""
        from app.config.archimate_relationship_matrix import is_valid_relationship

        for case in self.SPEC_CASES:
            source, target, rel, _ = case
            assert (not self._validate(case)["errors"]) == is_valid_relationship(
                source, target, rel
            ), f"validator and matrix disagree on {source} --{rel}--> {target}"

    def test_rejection_names_what_would_be_permitted(self):
        """An error that only says 'invalid' cannot be acted on."""
        result = self._validate(("BusinessActor", "Goal", "Composition", False))
        assert result["errors"]
        message = result["errors"][0]
        assert "BusinessActor" in message and "Goal" in message
        assert "permitted" in message.lower()

    def test_types_absent_from_the_matrix_warn_rather_than_reject(self):
        """Absence of a rule is not evidence of a violation.

        Treating "no entry" as "forbidden" would trade the old false positives
        for new ones. This originally covered Junction, Grouping and Location,
        which the matrix did not know. It knows all three now (see
        tests/test_archimate_matrix_completeness.py), so the behaviour is
        exercised with a type that is genuinely unknown - which is what the rule
        was always about.
        """
        from app.modules.architecture.services.archimate_metamodel_validator import (
            ArchiMateMetamodelValidator,
        )

        result = ArchiMateMetamodelValidator().validate_model(
            [{"name": "X", "type": "SomeFutureConcept"},
             {"name": "P", "type": "BusinessProcess"}],
            [{"source": "X", "target": "P", "type": "Triggering"}],
        )
        assert not result["errors"], "an unknown type must not be rejected outright"
        assert any("not in the ArchiMate relationship matrix" in w for w in result["warnings"])

    def test_the_three_other_concepts_are_validated_not_waved_through(self):
        """Grouping, Location and Junction are known now, so they get checked.

        Being unknown meant every relationship touching them was waved through
        with a warning. A Grouping aggregating its members is the most ordinary
        construct in ArchiMate and deserves a real answer.
        """
        from app.modules.architecture.services.archimate_metamodel_validator import (
            ArchiMateMetamodelValidator,
        )

        for concept in ("Junction", "Grouping", "Location"):
            result = ArchiMateMetamodelValidator().validate_model(
                [{"name": "X", "type": concept}, {"name": "P", "type": "BusinessProcess"}],
                [{"source": "X", "target": "P", "type": "Triggering"}],
            )
            assert not result["errors"], f"{concept} must not be rejected"
            assert not any("not in the ArchiMate relationship matrix" in w
                           for w in result["warnings"]), (
                f"{concept} is still being reported as unknown to the matrix")

    def test_unknown_relationship_type_warns(self):
        from app.modules.architecture.services.archimate_metamodel_validator import (
            ArchiMateMetamodelValidator,
        )

        result = ArchiMateMetamodelValidator().validate_model(
            [{"name": "A", "type": "BusinessActor"}, {"name": "B", "type": "BusinessRole"}],
            [{"source": "A", "target": "B", "type": "Telepathy"}],
        )
        assert not result["errors"]
        assert any("Unknown relationship type" in w for w in result["warnings"])

    def test_hand_rolled_table_is_gone(self):
        """Guard against the second source of truth being reintroduced."""
        from app.modules.architecture.services import archimate_metamodel_validator as mod

        assert not hasattr(mod.ArchiMateMetamodelValidator, "RELATIONSHIP_RULES")


# ---------------------------------------------------------------- OEF export


@pytest.fixture
def exported_model(db_session, make_org):
    """Build a small model with properties and a laid-out diagram, then export it.

    Function-scoped (was module-scoped): every row this creates now lives
    inside ``db_session``'s per-test savepoint, which is always rolled back,
    so nothing needs manual deletion. The ``db_session.commit()`` below is not
    a real commit — ``join_transaction_mode="create_savepoint"``
    (tests/conftest.py) turns it into a SAVEPOINT release inside that outer,
    rolled-back transaction. ``service.export_to_xml`` reads through the same
    session, so the export sees those rows regardless.
    """
    import xml.etree.ElementTree as ET

    from flask import g

    from app.models import ArchitectureModel
    from app.models.archimate_core import (
        SavedDiagram,
        SavedDiagramElement,
        SavedDiagramRelationship,
    )
    from app.models.models import ArchiMateElement, ArchiMateRelationship
    from app.modules.architecture.services import archimate_xml_export_service as service

    suffix = uuid.uuid4().hex[:8]

    org = make_org("oef")
    g.current_org_id = org.id

    model = ArchitectureModel(name=f"OEF Test Model {suffix}")
    db_session.add(model)
    db_session.flush()

    component = ArchiMateElement(
        name="Order Management",
        type="application_component",
        architecture_id=model.id,
        description="Core ordering application",
        # `properties` is a Text column holding JSON, not a JSON column.
        properties=json.dumps({"Owner": "Alice", "Criticality": "High"}),
    )
    record = ArchiMateElement(
        name="Customer Record", type="business_object", architecture_id=model.id
    )
    db_session.add_all([component, record])
    db_session.flush()

    rel = ArchiMateRelationship(
        type="access",
        architecture_id=model.id,
        source_id=component.id,
        target_id=record.id,
    )
    db_session.add(rel)
    db_session.flush()

    diagram = SavedDiagram(name=f"Layout {suffix}")
    db_session.add(diagram)
    db_session.flush()
    db_session.add_all(
        [
            SavedDiagramElement(
                diagram_id=diagram.id,
                element_id=component.id,
                position_x=10,
                position_y=20,
                width=200,
                height=80,
            ),
            SavedDiagramElement(
                diagram_id=diagram.id, element_id=record.id, position_x=300, position_y=20
            ),
            SavedDiagramRelationship(diagram_id=diagram.id, relationship_id=rel.id),
        ]
    )
    db_session.commit()

    xml = service.export_to_xml(model.id)

    return ET.fromstring(xml), xml


class TestOpenExchangeExport:
    def test_child_order_matches_the_schema(self, exported_model):
        """The schema fixes the sequence; right data in the wrong order is rejected."""
        root, _ = exported_model
        assert [child.tag.replace(NS, "") for child in root] == [
            "name",
            "elements",
            "relationships",
            "organizations",
            "propertyDefinitions",
            "views",
        ]

    def test_elements_and_relationships_present(self, exported_model):
        root, _ = exported_model
        assert len(root.findall(f"{NS}elements/{NS}element")) == 2
        assert len(root.findall(f"{NS}relationships/{NS}relationship")) == 1

    def test_custom_properties_survive_the_export(self, exported_model):
        """Previously dropped entirely — this is most of the enterprise metadata."""
        root, _ = exported_model
        declared = {
            n.text
            for n in root.findall(f"{NS}propertyDefinitions/{NS}propertyDefinition/{NS}name")
        }
        assert {"Owner", "Criticality"} <= declared

        values = {v.text for v in root.iter(f"{NS}value")}
        assert {"Alice", "High"} <= values

        # Every reference must resolve to a declared definition.
        ids = {
            d.get("identifier")
            for d in root.findall(f"{NS}propertyDefinitions/{NS}propertyDefinition")
        }
        refs = {p.get("propertyDefinitionRef") for p in root.iter(f"{NS}property")}
        assert refs <= ids, f"dangling propertyDefinitionRef: {refs - ids}"

    def test_organizations_group_elements_by_layer(self, exported_model):
        """Without this an importing tool shows one flat folder."""
        root, _ = exported_model
        labels = [x.text for x in root.findall(f"{NS}organizations/{NS}item/{NS}label")]
        assert "Application" in labels and "Business" in labels

        element_ids = {e.get("identifier") for e in root.findall(f"{NS}elements/{NS}element")}
        refs = {
            i.get("identifierRef")
            for i in root.findall(f"{NS}organizations/{NS}item/{NS}item")
        }
        assert refs and refs <= element_ids

    def test_views_carry_diagram_geometry(self, exported_model):
        """The whole point of OEF over CSV: layout round-trips."""
        root, _ = exported_model
        nodes = root.findall(f"{NS}views/{NS}diagrams/{NS}view/{NS}node")
        assert len(nodes) == 2

        placed = {
            (n.get("x"), n.get("y"), n.get("w"), n.get("h")) for n in nodes
        }
        assert ("10", "20", "200", "80") in placed
        # Defaults fill in for the element saved without explicit size.
        assert ("300", "20", "180", "64") in placed

    def test_view_references_resolve(self, exported_model):
        """A dangling ref makes the file unopenable, which is worse than omitting views."""
        root, _ = exported_model
        element_ids = {e.get("identifier") for e in root.findall(f"{NS}elements/{NS}element")}
        rel_ids = {
            r.get("identifier") for r in root.findall(f"{NS}relationships/{NS}relationship")
        }
        nodes = root.findall(f"{NS}views/{NS}diagrams/{NS}view/{NS}node")
        node_ids = {n.get("identifier") for n in nodes}

        for node in nodes:
            assert node.get("elementRef") in element_ids

        connections = root.findall(f"{NS}views/{NS}diagrams/{NS}view/{NS}connection")
        assert connections, "the laid-out relationship should appear as a connection"
        for conn in connections:
            assert conn.get("relationshipRef") in rel_ids
            # source/target reference NODES, not elements.
            assert conn.get("source") in node_ids
            assert conn.get("target") in node_ids

    def test_declares_the_open_group_namespace(self, exported_model):
        _, xml = exported_model
        assert "http://www.opengroup.org/xsd/archimate/3.0/" in xml

    def test_export_of_an_empty_model_is_still_well_formed(self, app):
        """An empty model must produce a parseable file, not a crash or a stub."""
        import xml.etree.ElementTree as ET

        from app.modules.architecture.services import archimate_xml_export_service as service

        with app.test_request_context("/"):
            root = ET.fromstring(service.export_to_xml(-1))

        assert root.tag == f"{NS}model"
        assert root.find(f"{NS}name") is not None
        assert root.findall(f"{NS}elements/{NS}element") == []


class TestPropertyCoercion:
    """`properties` is a Text column holding JSON, so the shape varies in practice."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"a": "1"}, {"a": "1"}),
            ('{"a": "1"}', {"a": "1"}),
            ('{"n": 5, "b": true}', {"n": "5", "b": "True"}),
            ('{"keep": "x", "drop": {"nested": 1}}', {"keep": "x"}),
            ("not json at all", {}),
            ("[1,2,3]", {}),
            (None, {}),
            ("", {}),
        ],
    )
    def test_coercion(self, raw, expected):
        from app.modules.architecture.services.archimate_xml_export_service import (
            _coerce_properties,
        )

        assert _coerce_properties(raw) == expected
