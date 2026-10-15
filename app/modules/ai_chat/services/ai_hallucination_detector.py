"""
-> app.modules.ai_chat.services.ai_analysis_service

AI Hallucination Detector Service

Detects and flags potential hallucinations in AI-generated outputs.
Validates recommendations against known patterns and knowledge base.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AIHallucinationDetector:
    """
    Detects potential hallucinations in AI-generated architecture guidance.
    
    Checks:
    - Claims against pattern library
    - Citation verification
    - Fact consistency
    - Confidence calibration
    """
    
    # Patterns that may indicate hallucination
    HALLUCINATION_INDICATORS = [
        r"\b(I know|I'm certain|I am sure|definitely|absolutely|certainly)\b",
        r"\b(always|never|all|none|every|only)\b",
        r"\b(research shows|studies prove|data indicates)\b.*\b(not|no)\s+(citation|reference|source)",
    ]
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def analyze_response(
        self,
        response_text: str,
        domain: str,
        context_entities: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Analyze AI response for potential hallucinations.
        
        Args:
            response_text: The AI-generated response
            domain: Domain of the query
            context_entities: Known entities from context
            
        Returns:
            Hallucination detection result
        """
        issues = []
        confidence_penalty = 0.0
        
        # Check for absolute statements without citations
        for pattern in self.HALLUCINATION_INDICATORS:
            matches = re.finditer(pattern, response_text, re.IGNORECASE)
            for match in matches:
                issues.append({
                    "type": "absolute_statement",
                    "text": match.group(0),
                    "position": match.start(),
                    "suggestion": "Add citation or qualify with uncertainty",
                })
                confidence_penalty += 0.1
        
        # Check for invented entities (not in context)
        if context_entities:
            entity_names = {e.get("name", "").lower() for e in context_entities}
            # Extract potential entity mentions
            words = re.findall(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b', response_text)
            for word in words:
                if len(word) > 3 and word.lower() not in entity_names:
                    # Potentially invented entity
                    if self._is_likely_entity(word, domain):
                        issues.append({
                            "type": "unverified_entity",
                            "entity": word,
                            "suggestion": "Verify entity exists in knowledge base",
                        })
        
        # Check for numeric claims without context
        numeric_claims = re.findall(r'\b(\d+%?|\$[\d,]+|\d+\s+(million|billion|thousand))\b', response_text)
        for claim in numeric_claims:
            # Check if claim has supporting context
            surrounding_text = self._get_surrounding_text(response_text, claim[0], 100)
            if not self._has_citation(surrounding_text):
                issues.append({
                    "type": "unsourced_statistic",
                    "value": claim[0],
                    "suggestion": "Add source or calculation basis",
                })
        
        # Calculate hallucination risk score
        risk_score = min(len(issues) * 0.15 + confidence_penalty, 1.0)
        
        return {
            "has_hallucination_risk": risk_score > 0.3,
            "risk_score": round(risk_score, 2),
            "risk_level": self._get_risk_level(risk_score),
            "issues": issues,
            "requires_human_review": risk_score > 0.5,
            "recommendation": self._get_recommendation(risk_score),
        }
    
    def _is_likely_entity(self, word: str, domain: str) -> bool:
        """Check if a word is likely to be an entity reference."""
        # Common architecture-related terms that might be entities
        architecture_terms = [
            "system", "service", "application", "component", "module",
            "platform", "framework", "database", "api", "gateway",
        ]
        return any(term in word.lower() for term in architecture_terms)
    
    def _get_surrounding_text(self, text: str, target: str, window: int) -> str:
        """Get text surrounding a target string."""
        pos = text.find(target)
        if pos == -1:
            return ""
        start = max(0, pos - window)
        end = min(len(text), pos + len(target) + window)
        return text[start:end]
    
    def _has_citation(self, text: str) -> bool:
        """Check if text contains a citation or source reference."""
        citation_patterns = [
            r'\[\d+\]',  # [1], [2], etc.
            r'\([^)]*\d{4}[^)]*\)',  # (Author, 2024)
            r'according to|as per|based on|source:|reference:',
            r'https?://',  # URL
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in citation_patterns)
    
    def _get_risk_level(self, score: float) -> str:
        """Convert score to risk level."""
        if score < 0.2:
            return "low"
        elif score < 0.4:
            return "medium"
        elif score < 0.6:
            return "high"
        else:
            return "critical"
    
    def _get_recommendation(self, score: float) -> str:
        """Get recommendation based on risk score."""
        if score < 0.2:
            return "No action needed"
        elif score < 0.4:
            return "Review flagged issues"
        elif score < 0.6:
            return "Human review recommended"
        else:
            return "Mandatory human review required"
    
    def validate_citations(self, response_text: str, knowledge_base: Optional[List] = None) -> Dict:
        """Validate that citations in the response exist in knowledge base."""
        # Extract citation references
        citations = re.findall(r'\[(\d+)\]', response_text)
        
        if not knowledge_base:
            return {
                "citations_found": len(citations),
                "validated": 0,
                "unvalidated": len(citations),
                "note": "Knowledge base not available for validation",
            }
        
        validated = 0
        for citation in citations:
            # Citation numbers are 1-based; validate against knowledge_base length
            try:
                if 1 <= int(citation) <= len(knowledge_base):
                    validated += 1
            except (ValueError, TypeError):
                pass  # Non-numeric citation reference — count as unvalidated
        
        return {
            "citations_found": len(citations),
            "validated": validated,
            "unvalidated": len(citations) - validated,
        }


# --------------------------------------------------------------------------- #
# Citation gate                                                                #
# --------------------------------------------------------------------------- #
#
# analyze_response above scores prose for hedging patterns; it cannot tell a
# real record from an invented one. The gate below answers the narrower
# question the assistant has to be held to: does each sentence that states a
# fact about the estate rest on something a tool returned (or the context the
# model was given) this turn?
#
# It is deterministic and conservative in the direction of refusing. A sentence
# is a claim when it states a relation or a count about the estate. It is cited
# when every named record and every count in it appears in the grounding text
# (the tool results, the context and the user's own words). A claim sentence
# that names nothing is cited only if some tool returned rows this turn.

_CLAIM_VERB = re.compile(
    r"\b(depends? on|depend(?:s|ed)? upon|dependent on|is owned by|owned by|owns|"
    r"runs? on|hosted on|hosts|breaks?|would break|will break|fails?|is impacted|"
    r"are impacted|impacts?|affects?|affected|supports?|integrates? with|"
    r"connects? to|connected to|calls?|serves?|serving|relies on|uses?|"
    r"is used by|are used by|is end[- ]of[- ]life|reaches|exposes?)\b",
    re.IGNORECASE,
)
_COUNT_CLAIM = re.compile(
    r"\b(\d[\d,]*)\s+(?:\w+\s+){0,2}?(applications?|elements?|systems?|services?|"
    r"components?|capabilit(?:y|ies)|risks?|dependenc(?:y|ies)|dependents?|"
    r"relationships?|interfaces?|servers?|databases?|processes|owners?|solutions?|"
    r"vendors?|contracts?|rows?|records?)\b",
    re.IGNORECASE,
)
_ENTITY_PHRASE = re.compile(r"\b[A-Z][\w&./+-]*(?:\s+[A-Z][\w&./+-]*)*")
# Capitalised words that are not estate records.
_NOT_AN_ENTITY = frozenset(
    {
        "i", "the", "a", "an", "it", "this", "that", "these", "those", "there", "if",
        "but", "and", "so", "in", "on", "for", "to", "yes", "no", "note", "also",
        "archimate", "togaf", "business", "application", "technology", "motivation",
        "strategy", "implementation", "physical", "layer", "answer", "not recorded",
        "recorded", "ask", "impact", "drawn", "derived", "monday", "tuesday",
        "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
        "march", "april", "may", "june", "july", "august", "september", "october",
        "november", "december", "copilot", "entelim", "archie",
    }
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _walk_text(value, out: list) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k))
            _walk_text(v, out)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            _walk_text(v, out)
    elif value is not None:
        out.append(str(value))


class Grounding:
    """What one assistant turn is allowed to state: everything a tool returned,
    the context it was handed and the user's own words. Text, not structure, so
    a tool the gate has never heard of is still a source."""

    def __init__(self) -> None:
        self._parts: List[str] = []
        self.tool_results = 0

    def add(self, value) -> None:
        pieces: list = []
        _walk_text(value, pieces)
        self._parts.append(" ".join(pieces).lower())

    def add_tool_result(self, result) -> None:
        if isinstance(result, dict) and result.get("success"):
            self.tool_results += 1
            self.add(result)

    @property
    def text(self) -> str:
        return " \n ".join(self._parts)


def find_uncited_claims(response_text: str, grounding: Grounding) -> List[Dict]:
    """Sentences of *response_text* that state a fact about the estate which
    nothing in *grounding* supports. An empty list means the answer may be shown."""
    if not response_text or not response_text.strip():
        return []
    haystack = grounding.text
    uncited: List[Dict] = []
    for raw in _SENTENCE_SPLIT.split(response_text):
        sentence = raw.strip(" \t-*#>|")
        if len(sentence) < 8 or sentence.endswith("?"):
            continue
        counts = _COUNT_CLAIM.findall(sentence)
        if not (_CLAIM_VERB.search(sentence) or counts):
            continue

        missing: List[str] = []
        phrases = []
        for match in _ENTITY_PHRASE.finditer(sentence):
            phrase = match.group(0).strip().rstrip(".,;:/+&-")
            first_word_alone = match.start() == 0 and " " not in phrase
            if first_word_alone or phrase.lower() in _NOT_AN_ENTITY or len(phrase) < 3:
                continue
            phrases.append(phrase)
            if phrase.lower() not in haystack:
                missing.append(phrase)
        for number, _noun in counts:
            digits = number.replace(",", "")
            if not re.search(r"(?<![\d.])%s(?![\d])" % re.escape(digits), haystack):
                missing.append(number)

        if missing:
            uncited.append({"claim": sentence[:300], "unsupported": missing})
        elif not phrases and not counts and grounding.tool_results == 0:
            uncited.append({"claim": sentence[:300], "unsupported": []})
    return uncited

