def test_exchange_service_normalizes_relationship_aliases():
    from app.modules.architecture.services.archimate_exchange_service import (
        ArchiMateExchangeService,
    )

    service = ArchiMateExchangeService()

    assert service._normalize_relationship_type("Uses") == "Serving"
    assert service._normalize_relationship_type("Realises") == "Realization"
    assert service._normalize_relationship_type("RealisationRelationship") == "Realization"


def test_relationship_matrix_normalizes_to_pascal_case():
    from app.modules.architecture.services.archimate_relationship_matrix import _normalize_rel_type

    assert _normalize_rel_type("uses") == "Serving"
    assert _normalize_rel_type("servingrelationship") == "Serving"
    assert _normalize_rel_type("realises") == "Realization"
    assert _normalize_rel_type(None) == "Association"


def test_routes_normalize_relationship_aliases_to_canonical_lowercase():
    from app.modules.architecture.routes.archimate_routes import _normalize_rel_type

    assert _normalize_rel_type("Uses") == "serving"
    assert _normalize_rel_type("Realises") == "realization"
    assert _normalize_rel_type("RealisationRelationship") == "realization"


def test_viewpoint_service_normalizes_relationship_aliases_to_canonical_lowercase():
    from app.services.archimate_viewpoint_service import _normalize_rel_type

    assert _normalize_rel_type("Uses") == "serving"
    assert _normalize_rel_type("RealisationRelationship") == "realization"
    assert _normalize_rel_type(None) == "association"
