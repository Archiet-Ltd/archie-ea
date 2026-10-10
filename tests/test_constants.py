import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", ""),
        (" business ", "business"),
        ("Implementation & Migration", "implementation"),
        ("implementation migration", "implementation"),
        ("implementation and migration", "implementation"),
        ("implementation_migration", "implementation"),
        ("implementation-migration", "implementation"),
        ("implementationmigration", "implementation"),
        ("implementation/migration", "implementation"),
        ("implementation&migration", "implementation"),
        ("Physical", "physical"),
        ("UnknownLayer", "unknownlayer"),
    ],
)
def test_archimate_layer_normalize(raw, expected):
    from app.models.constants import ArchiMateLayer

    assert ArchiMateLayer.normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", ""),
        (" serving ", "serving"),
        ("ServingRelationship", "serving"),
        ("Serves", "serving"),
        ("Uses", "serving"),
        ("AccessRelationship", "access"),
        ("Accesses", "access"),
        ("InfluenceRelationship", "influence"),
        ("Influences", "influence"),
        ("TriggeringRelationship", "triggering"),
        ("Triggers", "triggering"),
        ("FlowRelationship", "flow"),
        ("Flows", "flow"),
        ("CompositionRelationship", "composition"),
        ("Composes", "composition"),
        ("AggregationRelationship", "aggregation"),
        ("Aggregates", "aggregation"),
        ("AssignmentRelationship", "assignment"),
        ("Assigns", "assignment"),
        ("RealizationRelationship", "realization"),
        ("RealisationRelationship", "realization"),
        ("Realization", "realization"),
        ("Realisation", "realization"),
        ("Realizes", "realization"),
        ("Realises", "realization"),
        ("SpecializationRelationship", "specialization"),
        ("SpecialisationRelationship", "specialization"),
        ("Specializes", "specialization"),
        ("Specialises", "specialization"),
        ("AssociationRelationship", "association"),
        ("Associates", "association"),
        ("Telepathy", "Telepathy"),
    ],
)
def test_archimate_relationship_type_normalize_lowercase(raw, expected):
    from app.models.constants import ArchiMateRelationshipType

    assert ArchiMateRelationshipType.normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", ""),
        ("servingrelationship", "Serving"),
        ("Uses", "Serving"),
        ("Accesses", "Access"),
        ("Realisation", "Realization"),
        ("Realises", "Realization"),
        ("Specialises", "Specialization"),
        ("Associates", "Association"),
        ("Telepathy", "Telepathy"),
    ],
)
def test_archimate_relationship_type_normalize_pascal_case(raw, expected):
    from app.models.constants import ArchiMateRelationshipType

    assert ArchiMateRelationshipType.normalize(raw, pascal_case=True) == expected
