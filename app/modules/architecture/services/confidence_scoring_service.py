"""
Confidence Scoring Service

Provides confidence scores with uncertainty quantification for extracted elements.
Features:
- Multi-factor confidence calculation
- Uncertainty quantification
- Quality indicators
- Confidence propagation through relationships
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional
from app.models.archimate_element_types import StandardTypeTable

logger = logging.getLogger(__name__)


class ConfidenceLevel(Enum):
    """Confidence level categories."""

    VERY_HIGH = "very_high"  # 0.9 - 1.0
    HIGH = "high"  # 0.75 - 0.9
    MEDIUM = "medium"  # 0.5 - 0.75
    LOW = "low"  # 0.25 - 0.5
    VERY_LOW = "very_low"  # 0.0 - 0.25


@dataclass
class ConfidenceScore:
    """Confidence score with uncertainty quantification."""

    score: float  # 0.0 to 1.0
    uncertainty: float  # 0.0 to 1.0 (uncertainty in the score)
    level: ConfidenceLevel
    factors: Dict[str, float]  # Individual factor scores
    quality_indicators: List[str]  # Quality flags
    explanation: str  # Human-readable explanation

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "score": self.score,
            "uncertainty": self.uncertainty,
            "level": self.level.value,
            "factors": self.factors,
            "quality_indicators": self.quality_indicators,
            "explanation": self.explanation,
        }


class ConfidenceScoringService:
    """
    Service for calculating confidence scores with uncertainty quantification.
    """

    # Factor weights for confidence calculation
    FACTOR_WEIGHTS = {
        "name_quality": 0.20,  # How clear/complete is the name
        "description_quality": 0.15,  # Quality of description
        "type_confidence": 0.25,  # Confidence in ArchiMate type assignment
        "context_support": 0.15,  # How well context supports the element
        "extraction_method": 0.10,  # Quality of extraction method
        "validation_pass": 0.10,  # Whether validation passed
        "database_match": 0.05,  # Whether matched existing database entity
    }

    def __init__(self):
        """Initialize confidence scoring service."""
        pass

    def score_element(
        self,
        element: Dict,
        context: Optional[str] = None,
        extraction_method: str = "llm",
        validation_result: Optional[Dict] = None,
        database_match: Optional[Dict] = None,
    ) -> ConfidenceScore:
        """
        Calculate confidence score for an extracted element.

        Args:
            element: The extracted element dictionary
            context: Optional context text from document
            extraction_method: Method used ('llm', 'pattern', 'graph', etc.)
            validation_result: Optional validation result
            database_match: Optional database match result

        Returns:
            ConfidenceScore object
        """
        factors = {}

        # Factor 1: Name quality
        name = element.get("name", "")
        factors["name_quality"] = self._score_name_quality(name)

        # Factor 2: Description quality
        description = element.get("description", "")
        factors["description_quality"] = self._score_description_quality(description)

        # Factor 3: Type confidence
        element_type = element.get("type", "")
        layer = element.get("layer", "")
        factors["type_confidence"] = self._score_type_confidence(element_type, layer)

        # Factor 4: Context support
        factors["context_support"] = (
            self._score_context_support(element, context) if context else 0.5
        )  # Neutral if no context

        # Factor 5: Extraction method
        factors["extraction_method"] = self._score_extraction_method(extraction_method)

        # Factor 6: Validation pass
        factors["validation_pass"] = (
            self._score_validation(validation_result) if validation_result else 0.5
        )

        # Factor 7: Database match
        factors["database_match"] = self._score_database_match(database_match)

        # Calculate weighted score
        weighted_score = sum(
            factors[factor] * weight
            for factor, weight in self.FACTOR_WEIGHTS.items()
            if factor in factors
        )

        # Calculate uncertainty
        uncertainty = self._calculate_uncertainty(factors, weighted_score)

        # Determine confidence level
        level = self._determine_level(weighted_score)

        # Quality indicators
        quality_indicators = self._get_quality_indicators(factors, element)

        # Explanation
        explanation = self._generate_explanation(factors, weighted_score, level)

        return ConfidenceScore(
            score=weighted_score,
            uncertainty=uncertainty,
            level=level,
            factors=factors,
            quality_indicators=quality_indicators,
            explanation=explanation,
        )

    def _score_name_quality(self, name: str) -> float:
        """Score name quality (0.0 to 1.0)."""
        if not name or len(name.strip()) == 0:
            return 0.0

        name = name.strip()
        score = 0.5  # Base score

        # Length check (too short or too long reduces confidence)
        if 3 <= len(name) <= 100:
            score += 0.2
        elif len(name) < 3:
            score -= 0.3
        elif len(name) > 100:
            score -= 0.2

        # Check for placeholder names
        placeholders = ["unknown", "n/a", "tbd", "todo", "placeholder", "temp"]
        if any(p in name.lower() for p in placeholders):
            score -= 0.4

        # Check for generic names
        generic = ["system", "application", "component", "service", "module"]
        if name.lower() in generic:
            score -= 0.2

        # Capitalization (proper names should be capitalized)
        if name[0].isupper() or name.isupper():
            score += 0.1

        return max(0.0, min(1.0, score))

    def _score_description_quality(self, description: str) -> float:
        """Score description quality (0.0 to 1.0)."""
        if not description:
            return 0.2  # Low score for missing description

        desc_len = len(description.strip())
        score = 0.3  # Base score

        # Length check
        if 20 <= desc_len <= 500:
            score += 0.4
        elif desc_len < 20:
            score += 0.1
        elif desc_len > 500:
            score += 0.2  # Very long descriptions can be good

        # Check for meaningful content (not just placeholder text)
        if any(word in description.lower() for word in ["lorem", "ipsum", "placeholder", "todo"]):
            score -= 0.3

        return max(0.0, min(1.0, score))

    def _score_type_confidence(self, element_type: str, layer: str) -> float:
        """Score confidence in ArchiMate type assignment."""
        if not element_type:
            return 0.0

        score = 0.7  # Base confidence in type assignment

        # Check if type matches layer
        type_to_layer = StandardTypeTable({
            "ApplicationComponent": "application",
            "ApplicationInterface": "application",
            "BusinessActor": "business",
            "BusinessProcess": "business",
            "TechnologyService": "technology",
            "Node": "technology",
        })

        expected_layer = type_to_layer.get(element_type, "")
        if expected_layer and layer.lower() == expected_layer.lower():
            score += 0.2
        elif layer and expected_layer and layer.lower() != expected_layer.lower():
            score -= 0.3

        return max(0.0, min(1.0, score))

    def _score_context_support(self, element: Dict, context: str) -> float:
        """Score how well context supports the element."""
        if not context:
            return 0.5

        name = element.get("name", "").lower()
        description = element.get("description", "").lower()
        context_lower = context.lower()

        score = 0.5  # Base score

        # Check if name appears in context
        if name in context_lower:
            score += 0.3

        # Check if description keywords appear in context
        if description:
            desc_words = set(description.split()[:10])  # First 10 words
            context_words = set(context_lower.split())
            overlap = len(desc_words & context_words)
            if overlap > 0:
                score += min(0.2, overlap * 0.05)

        return max(0.0, min(1.0, score))

    def _score_extraction_method(self, method: str) -> float:
        """Score extraction method quality."""
        method_scores = {
            "llm": 0.8,  # LLM extraction is generally good
            "pattern": 0.7,  # Pattern matching is reliable
            "graph": 0.75,  # Graph-based is good
            "semantic": 0.7,  # Semantic similarity
            "manual": 0.95,  # Manual entry is highest confidence
            "rule": 0.6,  # Rule-based is moderate
            "hybrid": 0.85,  # Hybrid methods are best
        }
        return method_scores.get(method.lower(), 0.5)

    def _score_validation(self, validation_result: Dict) -> float:
        """Score based on validation result."""
        if not validation_result:
            return 0.5

        is_valid = validation_result.get("valid", False)
        errors = validation_result.get("errors", [])
        warnings = validation_result.get("warnings", [])

        if is_valid and not errors:
            if not warnings:
                return 1.0
            else:
                return 0.8  # Valid but with warnings
        elif errors:
            return 0.3  # Has errors
        else:
            return 0.5  # Unknown

    def _score_database_match(self, database_match: Optional[Dict]) -> float:
        """Score based on database match."""
        if not database_match:
            return 0.5  # Neutral if no match attempted

        confidence = database_match.get("confidence", 0.0)
        return confidence

    def _calculate_uncertainty(self, factors: Dict, score: float) -> float:
        """Calculate uncertainty in the confidence score."""
        # Uncertainty is higher when factors disagree
        factor_values = list(factors.values())
        if not factor_values:
            return 1.0

        # Calculate variance (spread of factor scores)
        mean = sum(factor_values) / len(factor_values)
        variance = sum((v - mean) ** 2 for v in factor_values) / len(factor_values)
        std_dev = variance**0.5

        # Uncertainty is proportional to standard deviation
        uncertainty = min(1.0, std_dev * 2)  # Scale to 0 - 1

        # Also consider if score is in middle range (more uncertain)
        if 0.4 <= score <= 0.6:
            uncertainty += 0.2

        return min(1.0, uncertainty)

    def _determine_level(self, score: float) -> ConfidenceLevel:
        """Determine confidence level from score."""
        if score >= 0.9:
            return ConfidenceLevel.VERY_HIGH
        elif score >= 0.75:
            return ConfidenceLevel.HIGH
        elif score >= 0.5:
            return ConfidenceLevel.MEDIUM
        elif score >= 0.25:
            return ConfidenceLevel.LOW
        else:
            return ConfidenceLevel.VERY_LOW

    def _get_quality_indicators(self, factors: Dict, element: Dict) -> List[str]:
        """Get quality indicator flags."""
        indicators = []

        if factors.get("name_quality", 0) < 0.5:
            indicators.append("weak_name")
        if factors.get("description_quality", 0) < 0.5:
            indicators.append("missing_description")
        if factors.get("type_confidence", 0) < 0.5:
            indicators.append("uncertain_type")
        if factors.get("validation_pass", 0) < 0.5:
            indicators.append("validation_issues")
        if factors.get("database_match", 0) > 0.8:
            indicators.append("database_matched")
        if factors.get("extraction_method", 0) > 0.8:
            indicators.append("high_quality_extraction")

        return indicators

    def _generate_explanation(self, factors: Dict, score: float, level: ConfidenceLevel) -> str:
        """Generate human-readable explanation."""
        parts = []

        parts.append(f"Confidence: {level.value.replace('_', ' ').title()} ({score:.2%})")

        # Highlight strongest and weakest factors
        sorted_factors = sorted(factors.items(), key=lambda x: x[1])
        weakest = sorted_factors[0]
        strongest = sorted_factors[-1]

        if weakest[1] < 0.5:
            parts.append(f"Weakest factor: {weakest[0]} ({weakest[1]:.2%})")
        if strongest[1] > 0.7:
            parts.append(f"Strongest factor: {strongest[0]} ({strongest[1]:.2%})")

        return ". ".join(parts)

    def score_relationship(
        self,
        relationship: Dict,
        source_element: Optional[Dict] = None,
        target_element: Optional[Dict] = None,
    ) -> ConfidenceScore:
        """Calculate confidence for a relationship."""
        factors = {}

        # Relationship type confidence
        rel_type = relationship.get("type", "")
        factors["type_confidence"] = 0.8 if rel_type else 0.3

        # Source/target element confidence
        if source_element and target_element:
            source_conf = source_element.get("confidence", {}).get("score", 0.5)
            target_conf = target_element.get("confidence", {}).get("score", 0.5)
            factors["element_confidence"] = (source_conf + target_conf) / 2
        else:
            factors["element_confidence"] = 0.5

        # Description quality
        description = relationship.get("description", "")
        factors["description_quality"] = self._score_description_quality(description)

        # Calculate weighted score
        score = (
            factors["type_confidence"] * 0.4
            + factors["element_confidence"] * 0.4
            + factors["description_quality"] * 0.2
        )

        uncertainty = self._calculate_uncertainty(factors, score)
        level = self._determine_level(score)

        return ConfidenceScore(
            score=score,
            uncertainty=uncertainty,
            level=level,
            factors=factors,
            quality_indicators=self._get_quality_indicators(factors, relationship),
            explanation=f"Relationship confidence: {level.value} ({score:.2%})",
        )
