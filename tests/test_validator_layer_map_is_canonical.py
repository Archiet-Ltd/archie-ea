"""Validator layer map uses the canonical LAYER_TYPES from archimate_viewpoint_service.

Asserts that _is_valid_type_for_layer returns the correct values when using
the single canonical map (no second map in the validator).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _svc():
    from app.modules.architecture.services.archimate_validation_service import (
        ArchiMateValidationService,
    )
    return ArchiMateValidationService()


def test_validator_uses_canonical_map():
    """_is_valid_type_for_layer returns correct results per the canonical map."""

    svc = _svc()

    # Product is in the canonical business layer
    assert svc._is_valid_type_for_layer("Product", "business") is True

    # Metric is in the canonical motivation layer
    assert svc._is_valid_type_for_layer("Metric", "motivation") is True

    # Equipment is in the canonical technology layer
    assert svc._is_valid_type_for_layer("Equipment", "technology") is True

    # Equipment passed as 'physical' is treated as technology per the canonical map
    assert svc._is_valid_type_for_layer("Equipment", "physical") is True

    # WorkPackage is valid in the 'implementation & migration' layer
    assert svc._is_valid_type_for_layer("WorkPackage", "implementation & migration") is True

    # WorkPackage with mixed-case layer name still works
    assert svc._is_valid_type_for_layer(
        "WorkPackage", "Implementation & Migration"
    ) is True

    # BusinessActor in technology is not valid
    assert svc._is_valid_type_for_layer("BusinessActor", "technology") is False


def test_validator_returns_false_for_invalid_pairs():
    """Genuine mismatches still return False."""

    svc = _svc()

    # BusinessActor in technology should be False
    assert svc._is_valid_type_for_layer("BusinessActor", "technology") is False

    # A random invalid pairing
    assert svc._is_valid_type_for_layer("MadeUpType", "business") is False