"""
Feedback Learning Service

Implements feedback loops for learning from user corrections and improving extraction.
Features:
- Store user corrections
- Learn from corrections to improve future extractions
- Pattern recognition for common mistakes
- Adaptive prompt refinement
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.dialects.postgresql import JSON

from app import db
from app.models.mixins import TenantMixin

logger = logging.getLogger(__name__)


class ExtractionFeedback(TenantMixin, db.Model):
    """Store user feedback on extracted elements."""

    __tablename__ = "extraction_feedback"

    id = db.Column(db.Integer, primary_key=True)

    # Document context
    document_id = db.Column(db.Integer, index=True)  # Reference to document analysis
    document_hash = db.Column(db.String(64), index=True)  # For pattern matching

    # Element information
    original_element = db.Column(JSON)  # Original extracted element
    corrected_element = db.Column(JSON)  # User-corrected element
    correction_type = db.Column(db.String(50))  # 'name', 'type', 'description', 'delete', etc.

    # Feedback metadata
    user_id = db.Column(db.Integer, index=True)
    feedback_timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    confidence_before = db.Column(db.Float)  # Confidence before correction
    confidence_after = db.Column(db.Float)  # Confidence after correction

    # Learning data
    pattern_key = db.Column(db.String(200), index=True)  # For pattern matching
    learned_rule = db.Column(JSON)  # Extracted learning rule
    applied_count = db.Column(db.Integer, default=0)  # How many times rule was applied


class FeedbackLearningService:
    """
    Service for learning from user feedback and improving extraction quality.
    """

    def __init__(self):
        """Initialize feedback learning service."""
        pass

    def record_correction(
        self,
        original_element: Dict,
        corrected_element: Dict,
        document_id: Optional[int] = None,
        document_hash: Optional[str] = None,
        user_id: Optional[int] = None,
        confidence_before: Optional[float] = None,
    ) -> int:
        """
        Record a user correction for learning.

        Args:
            original_element: The originally extracted element
            corrected_element: The user-corrected element
            document_id: Optional document analysis ID
            document_hash: Optional document hash for pattern matching
            user_id: User who made the correction
            confidence_before: Confidence score before correction

        Returns:
            Feedback record ID
        """
        try:
            # Determine correction type
            correction_type = self._identify_correction_type(original_element, corrected_element)

            # Extract pattern for learning
            pattern_key = self._extract_pattern(original_element, corrected_element)

            # Calculate confidence after (assume high if user corrected)
            confidence_after = 0.95  # User corrections are high confidence

            feedback = ExtractionFeedback(
                document_id=document_id,
                document_hash=document_hash,
                original_element=original_element,
                corrected_element=corrected_element,
                correction_type=correction_type,
                user_id=user_id,
                confidence_before=confidence_before,
                confidence_after=confidence_after,
                pattern_key=pattern_key,
            )

            db.session.add(feedback)
            db.session.commit()

            # Extract learning rule
            learned_rule = self._extract_learning_rule(original_element, corrected_element)
            if learned_rule:
                feedback.learned_rule = learned_rule
                db.session.commit()

            logger.info(f"Recorded feedback correction: {feedback.id}, type: {correction_type}")
            return feedback.id

        except Exception as e:
            logger.error(f"Error recording feedback: {e}")
            db.session.rollback()
            raise

    def _identify_correction_type(self, original: Dict, corrected: Dict) -> str:
        """Identify the type of correction made."""
        if not corrected or corrected.get("deleted"):
            return "delete"

        original_name = original.get("name", "").lower()
        corrected_name = corrected.get("name", "").lower()
        original_type = original.get("type", "")
        corrected_type = corrected.get("type", "")

        if original_name != corrected_name:
            if original_type != corrected_type:
                return "name_and_type"
            return "name"
        elif original_type != corrected_type:
            return "type"
        elif original.get("description") != corrected.get("description"):
            return "description"
        elif original.get("layer") != corrected.get("layer"):
            return "layer"
        else:
            return "other"

    def _extract_pattern(self, original: Dict, corrected: Dict) -> str:
        """Extract a pattern key for matching similar corrections."""
        # Create pattern based on original element characteristics
        original_name = original.get("name", "").lower()
        original_type = original.get("type", "")
        correction_type = self._identify_correction_type(original, corrected)

        # Pattern: correction_type + original_type + first_word_of_name
        first_word = original_name.split()[0] if original_name else "unknown"
        pattern = f"{correction_type}:{original_type}:{first_word}"

        return pattern

    def _extract_learning_rule(self, original: Dict, corrected: Dict) -> Optional[Dict]:
        """Extract a learning rule from the correction."""
        rule = {}

        correction_type = self._identify_correction_type(original, corrected)

        if correction_type in ["name", "name_and_type"]:
            # Learn name mapping
            original_name = original.get("name", "")
            corrected_name = corrected.get("name", "")
            if original_name and corrected_name:
                rule["name_mapping"] = {
                    "from": original_name,
                    "to": corrected_name,
                    "context": original.get("type", ""),
                }

        if correction_type in ["type", "name_and_type"]:
            # Learn type correction
            original_type = original.get("type", "")
            corrected_type = corrected.get("type", "")
            original_name_pattern = original.get("name", "").lower()
            if original_type and corrected_type:
                rule["type_correction"] = {
                    "from": original_type,
                    "to": corrected_type,
                    "name_pattern": original_name_pattern[:20],  # First 20 chars
                }

        if correction_type == "description":
            # Learn description improvement pattern
            rule["description_pattern"] = {
                "original_length": len(original.get("description", "")),
                "corrected_length": len(corrected.get("description", "")),
            }

        return rule if rule else None

    def apply_learned_rules(
        self, elements: List[Dict], document_hash: Optional[str] = None
    ) -> List[Dict]:
        """
        Apply learned rules to improve extraction.

        Args:
            elements: List of extracted elements
            document_hash: Optional document hash for context

        Returns:
            List of improved elements
        """
        if not elements:
            return elements

        # Get relevant feedback patterns
        patterns = self._get_relevant_patterns(elements, document_hash)

        improved_elements = []
        for element in elements:
            improved = element.copy()

            # Apply name mappings
            for pattern in patterns:
                rule = pattern.get("learned_rule", {})
                name_mapping = rule.get("name_mapping", {})
                if name_mapping:
                    original_name = improved.get("name", "")
                    if name_mapping["from"].lower() == original_name.lower():
                        improved["name"] = name_mapping["to"]
                        improved["confidence"] = improved.get("confidence", {})
                        improved["confidence"]["score"] = min(
                            1.0, improved["confidence"].get("score", 0.5) + 0.2
                        )
                        improved["confidence"]["learning_applied"] = True
                        logger.info(
                            f"Applied name mapping: {name_mapping['from']} -> {name_mapping['to']}"
                        )

                # Apply type corrections
                type_correction = rule.get("type_correction", {})
                if type_correction:
                    name_pattern = type_correction.get("name_pattern", "").lower()
                    element_name = improved.get("name", "").lower()
                    if (
                        name_pattern in element_name
                        and improved.get("type") == type_correction["from"]
                    ):
                        improved["type"] = type_correction["to"]
                        improved["confidence"] = improved.get("confidence", {})
                        improved["confidence"]["score"] = min(
                            1.0, improved["confidence"].get("score", 0.5) + 0.15
                        )
                        improved["confidence"]["learning_applied"] = True
                        logger.info(
                            f"Applied type correction: {type_correction['from']} -> {type_correction['to']}"
                        )

            improved_elements.append(improved)

        return improved_elements

    def _get_relevant_patterns(
        self, elements: List[Dict], document_hash: Optional[str] = None
    ) -> List[Dict]:
        """Get relevant feedback patterns for the elements."""
        try:
            # Extract pattern keys from elements
            element_patterns = set()
            for element in elements:
                element_type = element.get("type", "")
                name = element.get("name", "").lower()
                first_word = name.split()[0] if name else "unknown"
                pattern = f"name:{element_type}:{first_word}"
                element_patterns.add(pattern)
                pattern = f"type:{element_type}:{first_word}"
                element_patterns.add(pattern)

            # Query feedback records with matching patterns
            query = ExtractionFeedback.query.filter(
                ExtractionFeedback.pattern_key.in_(element_patterns)
            ).order_by(ExtractionFeedback.applied_count.desc())

            if document_hash:
                # Prefer patterns from similar documents
                query = query.filter(
                    db.or_(
                        ExtractionFeedback.document_hash == document_hash,
                        ExtractionFeedback.document_hash.is_(None),
                    )
                )

            # Get top patterns (most applied)
            patterns = query.limit(50).all()

            return [
                {
                    "pattern_key": p.pattern_key,
                    "learned_rule": p.learned_rule,
                    "applied_count": p.applied_count,
                    "correction_type": p.correction_type,
                }
                for p in patterns
            ]

        except Exception as e:
            logger.error(f"Error getting relevant patterns: {e}")
            return []

    def get_feedback_statistics(self, user_id: Optional[int] = None) -> Dict:
        """Get statistics about feedback and learning."""
        try:
            query = ExtractionFeedback.query
            if user_id:
                query = query.filter_by(user_id=user_id)

            total_feedback = query.count()
            corrections_by_type = (
                db.session.query(
                    ExtractionFeedback.correction_type, db.func.count(ExtractionFeedback.id)
                )
                .group_by(ExtractionFeedback.correction_type)
                .all()
            )

            return {
                "total_feedback": total_feedback,
                "corrections_by_type": dict(corrections_by_type),
                "learning_rules_applied": sum(
                    p.applied_count
                    for p in ExtractionFeedback.query.filter(
                        ExtractionFeedback.learned_rule.isnot(None)
                    ).all()
                ),
            }

        except Exception as e:
            # Do not fabricate a zero-feedback stat on failure; return None so
            # the caller can render an em dash instead of an invented count.
            logger.error(f"Error getting feedback statistics: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Adaptive prompt refinement                                           #
    # ------------------------------------------------------------------ #

    @classmethod
    def get_correction_rules_for_persona(cls, persona: str, limit: int = 8) -> List[str]:
        """
        Return persona-specific correction rules derived from ExtractionFeedback patterns.

        Maps correction_type clusters to the architect personas that benefit from them:
          - data_architect: name/type corrections on DataObject/DataStore
          - technology_architect: type corrections on Node/SystemSoftware
          - solutions_architect: name corrections on ApplicationComponent/Service
          - enterprise_architect: description/delete corrections (landscape-level)

        Returns human-readable rule strings suitable for injection into persona prompts.
        """
        PERSONA_TYPE_FILTER = {
            "data_architect": ["DataObject", "DataStore", "DataComponent"],
            "technology_architect": ["Node", "SystemSoftware", "TechnologyService"],
            "solutions_architect": ["ApplicationComponent", "ApplicationService",
                                    "ApplicationFunction", "ApplicationInterface"],
            "enterprise_architect": [],  # All types — landscape-level
        }

        try:
            filters = PERSONA_TYPE_FILTER.get(persona, [])
            query = ExtractionFeedback.query.filter(
                ExtractionFeedback.learned_rule.isnot(None)
            ).order_by(ExtractionFeedback.applied_count.desc())

            records = query.limit(200).all()
            rules: List[str] = []

            for rec in records:
                rule = rec.learned_rule or {}
                if not isinstance(rule, dict):
                    continue

                nm = rule.get("name_mapping", {})
                tc = rule.get("type_correction", {})

                # Filter by element types relevant to this persona
                if filters:
                    relevant = (
                        (nm and nm.get("context", "") in filters) or
                        (tc and (tc.get("from", "") in filters or tc.get("to", "") in filters))
                    )
                    if not relevant:
                        continue

                if nm and nm.get("from") and nm.get("to"):
                    rules.append(
                        f"Name: '{nm['from']}' → '{nm['to']}'"
                        + (f" (when type={nm['context']})" if nm.get("context") else "")
                    )
                elif tc and tc.get("from") and tc.get("to"):
                    rules.append(
                        f"Type: '{tc['from']}' → '{tc['to']}'"
                        + (f" (name contains '{tc.get('name_pattern', '')}')" if tc.get("name_pattern") else "")
                    )

                if len(rules) >= limit:
                    break

            return rules

        except Exception as e:
            logger.debug("get_correction_rules_for_persona(%s) failed: %s", persona, e)
            return []

    @classmethod
    def get_all_correction_summary(cls, limit: int = 5) -> List[str]:
        """
        Return top correction rules across all personas.
        Used by portfolio_context.py _learned_rules_summary().
        """
        try:
            records = (
                ExtractionFeedback.query
                .filter(ExtractionFeedback.learned_rule.isnot(None))
                .order_by(ExtractionFeedback.applied_count.desc())
                .limit(50)
                .all()
            )
            rules = []
            for rec in records:
                rule = rec.learned_rule or {}
                if not isinstance(rule, dict):
                    continue
                nm = rule.get("name_mapping", {})
                tc = rule.get("type_correction", {})
                if nm and nm.get("from") and nm.get("to"):
                    rules.append(f"Name: '{nm['from']}' → '{nm['to']}'")
                elif tc and tc.get("from") and tc.get("to"):
                    rules.append(f"Type: '{tc['from']}' → '{tc['to']}'")
                if len(rules) >= limit:
                    break
            return rules
        except Exception as e:
            logger.debug("get_all_correction_summary failed: %s", e)
            return []
