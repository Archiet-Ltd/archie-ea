"""
-> app.modules.architecture.services.modeling_service

ArchiMate 3.2 Relationship Management Service

Provides comprehensive relationship validation and creation for all ArchiMate 3.2 relationship types.
"""

import logging
from typing import Dict, List, Optional, Tuple

from app import db
from app.models.models import ArchiMateElement, ArchiMateRelationship  # dead-code-ok (pre-existing)

logger = logging.getLogger(__name__)


class ArchiMateRelationshipService:
    """Service for managing ArchiMate 3.2 relationships with validation."""

    # ArchiMate 3.2 Complete Relationship Types
    RELATIONSHIP_TYPES = {
        "association": {
            "description": "Passive structural relationship",
            "allowed_directions": ["bidirectional"],
            "cross_layer": True,
        },
        "access": {
            "description": "Active structural relationship",
            "allowed_directions": ["unidirectional"],
            "cross_layer": True,
            "source_layers": ["business", "application", "technology"],
            "target_layers": ["application", "technology", "physical"],
        },
        "flow": {
            "description": "Active structural relationship",
            "allowed_directions": ["unidirectional", "bidirectional"],
            "cross_layer": True,
            "source_layers": ["business", "application", "technology"],
            "target_layers": ["business", "application", "technology"],
        },
        "triggering": {
            "description": "Active behavioral relationship",
            "allowed_directions": ["unidirectional"],
            "cross_layer": True,
            "behavioral_elements": True,
        },
        "specialization": {
            "description": "Passive dependency relationship",
            "allowed_directions": ["unidirectional"],
            "cross_layer": False,
            "same_layer_only": True,
        },
        "composition": {
            "description": "Aggregation relationship with strong ownership",
            "allowed_directions": ["unidirectional"],
            "cross_layer": False,
            "same_layer_only": True,
        },
        "aggregation": {
            "description": "Aggregation relationship without strong ownership",
            "allowed_directions": ["unidirectional"],
            "cross_layer": False,
            "same_layer_only": True,
        },
        "assignment": {
            "description": "Active structural relationship",
            "allowed_directions": ["unidirectional"],
            "cross_layer": True,
            "source_layers": ["business", "application"],
            "target_layers": ["business", "application", "technology"],
        },
        "realization": {
            "description": "Active dependency relationship",
            "allowed_directions": ["unidirectional"],
            "cross_layer": True,
        },
        "serving": {
            "description": "Active dependency relationship",
            "allowed_directions": ["unidirectional"],
            "cross_layer": True,
        },
        "influence": {
            "description": "Passive dependency relationship",
            "allowed_directions": ["bidirectional"],
            "cross_layer": True,
        },
    }

    # Relationship columns a caller may set through ``properties``.
    WRITABLE_PROPERTIES = frozenset(
        {"description", "access_mode", "flow_label", "custom_label", "created_by_id"}
    )

    @classmethod
    def validate_relationship(
        cls,
        source_element: ArchiMateElement,
        target_element: ArchiMateElement,
        relationship_type: str,
    ) -> Tuple[bool, str]:
        """
        Validate if a relationship between two elements is allowed according to ArchiMate 3.2 rules.

        Args:
            source_element: Source ArchiMate element
            target_element: Target ArchiMate element
            relationship_type: Type of relationship to create

        Returns:
            Tuple of (is_valid, error_message)
        """
        if relationship_type not in cls.RELATIONSHIP_TYPES:
            return False, f"Invalid relationship type: {relationship_type}"

        rules = cls.RELATIONSHIP_TYPES[relationship_type]

        # Check if elements exist
        if not source_element or not target_element:
            return False, "Source or target element not found"

        # Check for self-relationship (not allowed for most types)
        if source_element.id == target_element.id and relationship_type != "association":
            return False, "Self-relationships not allowed for this type"

        # Check layer constraints
        source_layer = (source_element.layer or "").lower()
        target_layer = (target_element.layer or "").lower()

        if rules.get("same_layer_only") and source_layer != target_layer:
            return False, f"{relationship_type} relationships must be within the same layer"

        if not rules.get("cross_layer", True) and source_layer != target_layer:
            return False, f"{relationship_type} relationships cannot cross layers"

        # Check specific layer constraints
        if "source_layers" in rules and source_layer not in rules["source_layers"]:
            return False, f"Source layer '{source_layer}' not allowed for {relationship_type}"

        if "target_layers" in rules and target_layer not in rules["target_layers"]:
            return False, f"Target layer '{target_layer}' not allowed for {relationship_type}"

        # Check for existing relationships
        existing = ArchiMateRelationship.query.filter_by(
            type=relationship_type, source_id=source_element.id, target_id=target_element.id
        ).first()

        if existing:
            return (
                False,
                f"Relationship of type {relationship_type} already exists between these elements",
            )

        return True, ""

    @classmethod
    def create_relationship(
        cls,
        source_element: ArchiMateElement,
        target_element: ArchiMateElement,
        relationship_type: str,
        architecture_id: int,
        properties: Optional[Dict] = None,
        organization_id: Optional[int] = None,
        rollback: bool = True,
    ) -> Optional[ArchiMateRelationship]:
        """
        Create a validated ArchiMate relationship.

        Args:
            source_element: Source ArchiMate element
            target_element: Target ArchiMate element
            relationship_type: Type of relationship
            architecture_id: Architecture model ID
            properties: Optional values for the relationship's own columns
                (see WRITABLE_PROPERTIES)
            organization_id: The owning organisation, for a caller with no
                request context (the tenant default only reads the request)
            rollback: roll the session's transaction back when the write fails
                (the default). A caller that holds a savepoint passes False and
                gets the exception, so only its savepoint is rolled back.

        Returns:
            Created ArchiMateRelationship or None if validation failed
        """
        # Validate the relationship
        is_valid, error_message = cls.validate_relationship(
            source_element, target_element, relationship_type
        )

        if not is_valid:
            logger.error(f"Relationship validation failed: {error_message}")
            return None

        try:
            relationship = ArchiMateRelationship(
                type=relationship_type,
                source_id=source_element.id,
                target_id=target_element.id,
                architecture_id=architecture_id,
            )
            if organization_id is not None:
                relationship.organization_id = organization_id
            # The relationship has no free-form properties column (passing one
            # raised, so this writer never saved anything); each property is
            # applied to the relationship column of the same name instead.
            unknown = []
            for key, value in (properties or {}).items():
                if key in cls.WRITABLE_PROPERTIES:
                    setattr(relationship, key, value)
                else:
                    unknown.append(key)
            if unknown:
                logger.warning("Ignored unknown relationship properties: %s", sorted(unknown))

            # The relationship has no free-form properties column (passing one
            # raised, so this writer never saved anything); each property is
            # applied to the relationship column of the same name instead.
            unknown = []
            for key, value in (properties or {}).items():
                if key in cls.WRITABLE_PROPERTIES:
                    setattr(relationship, key, value)
                else:
                    unknown.append(key)
            if unknown:
                logger.warning("Ignored unknown relationship properties: %s", sorted(unknown))

            db.session.add(relationship)
            db.session.flush()

            logger.info(
                f"Created {relationship_type} relationship: {source_element.name} -> {target_element.name}"
            )
            return relationship

        except Exception as e:
            logger.error(f"Failed to create relationship: {e}")
            if not rollback:
                raise
            db.session.rollback()
            return None

    @classmethod
    def get_element_relationships(
        cls, element_id: int, direction: str = "both"
    ) -> List[ArchiMateRelationship]:
        """
        Get all relationships for an element.

        Args:
            element_id: Element ID
            direction: 'incoming', 'outgoing', or 'both'

        Returns:
            List of ArchiMateRelationship objects
        """
        query = ArchiMateRelationship.query

        if direction == "outgoing":
            query = query.filter_by(source_id=element_id)
        elif direction == "incoming":
            query = query.filter_by(target_id=element_id)
        else:  # both
            query = query.filter(
                (ArchiMateRelationship.source_id == element_id)
                | (ArchiMateRelationship.target_id == element_id)
            )

        return query.all()

    @classmethod
    def delete_relationship(cls, relationship_id: int) -> bool:
        """
        Delete a relationship.

        Args:
            relationship_id: Relationship ID

        Returns:
            True if successful, False otherwise
        """
        try:
            relationship = ArchiMateRelationship.query.get(relationship_id)
            if relationship:
                db.session.delete(relationship)
                db.session.commit()
                logger.info(f"Deleted relationship {relationship_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete relationship: {e}")
            db.session.rollback()
            return False

    # -----------------------------------------------------------------
    # Relationship Suggestion Management (BPP-006)
    # -----------------------------------------------------------------

    @classmethod
    def get_pending_suggestions(cls, limit: int = 50, min_confidence: float = 0.3):
        """Return pending relationship suggestions above confidence threshold.

        Args:
            limit: Maximum number of suggestions to return.
            min_confidence: Minimum confidence score (0.0-1.0).

        Returns:
            List of RelationshipSuggestion records ordered by confidence desc.
        """
        from app.models.archimate_core import RelationshipSuggestion

        return (
            RelationshipSuggestion.query
            .filter_by(status="pending")
            .filter(RelationshipSuggestion.confidence >= min_confidence)
            .order_by(RelationshipSuggestion.confidence.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def accept_suggestion(cls, suggestion_id: int, user_id: int):
        """Accept a suggestion: create ArchiMateRelationship and mark accepted.

        Args:
            suggestion_id: RelationshipSuggestion.id
            user_id: The EA user accepting.

        Returns:
            The created ArchiMateRelationship, or None on error.

        Raises:
            ValueError: If suggestion not found or already processed.
        """
        from datetime import datetime
        from app.models.archimate_core import RelationshipSuggestion

        suggestion = RelationshipSuggestion.query.get(suggestion_id)
        if suggestion is None:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        if suggestion.status != "pending":
            raise ValueError(
                f"Suggestion {suggestion_id} already {suggestion.status}"
            )

        # Create the actual relationship
        relationship = ArchiMateRelationship(
            source_id=suggestion.source_element_id,
            target_id=suggestion.target_element_id,
            type=suggestion.relationship_type,
        )
        db.session.add(relationship)

        # Mark suggestion as accepted
        suggestion.status = "accepted"
        suggestion.reviewed_by_id = user_id
        suggestion.reviewed_at = datetime.utcnow()

        db.session.commit()
        logger.info(
            "Accepted suggestion %d: %d -%s-> %d (by user %d)",
            suggestion_id,
            suggestion.source_element_id,
            suggestion.relationship_type,
            suggestion.target_element_id,
            user_id,
        )
        return relationship

    @classmethod
    def reject_suggestion(cls, suggestion_id: int, user_id: int):
        """Reject a suggestion without creating a relationship.

        Args:
            suggestion_id: RelationshipSuggestion.id
            user_id: The EA user rejecting.

        Raises:
            ValueError: If suggestion not found or already processed.
        """
        from datetime import datetime
        from app.models.archimate_core import RelationshipSuggestion

        suggestion = RelationshipSuggestion.query.get(suggestion_id)
        if suggestion is None:
            raise ValueError(f"Suggestion {suggestion_id} not found")
        if suggestion.status != "pending":
            raise ValueError(
                f"Suggestion {suggestion_id} already {suggestion.status}"
            )

        suggestion.status = "rejected"
        suggestion.reviewed_by_id = user_id
        suggestion.reviewed_at = datetime.utcnow()

        db.session.commit()
        logger.info(
            "Rejected suggestion %d (by user %d)", suggestion_id, user_id
        )

    @classmethod
    def bulk_accept(cls, min_confidence: float, user_id: int) -> int:
        """Accept all pending suggestions above a confidence threshold.

        Args:
            min_confidence: Minimum confidence score (0.0-1.0).
            user_id: The EA user accepting.

        Returns:
            Number of suggestions accepted.
        """
        suggestions = cls.get_pending_suggestions(
            limit=100, min_confidence=min_confidence
        )
        count = 0
        for s in suggestions:
            try:
                cls.accept_suggestion(s.id, user_id)
                count += 1
            except (ValueError, Exception) as e:
                logger.warning("Could not accept suggestion %d: %s", s.id, e)
        return count
