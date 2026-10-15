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
# What it enforces. A sentence that names a record of the estate (a name found
# in this turn's tool results, or an exact normalised match against the
# organisation's own element and application names) and is a statement, not a
# question, must carry a structured marker [ref:<element id or answer
# address>] whose every id was returned by a tool this turn. A stated count
# of estate things (digits or words) must equal a count a tool returned this
# turn. A sentence that names no record and states no such count is general
# guidance and is never touched.
#
# What it does NOT catch, by design and on the record: a wrong or paraphrased
# claim about a REAL record that carries a valid marker, and an invented name
# that matches no record and is not backed by a lookup (that case is closed
# upstream: an estate question that reached its final answer with no tool
# result is refused through ``estate_question`` below, after one nudge to look
# it up).
#
# Grounding is only (i) tool results returned in this turn and (ii) the
# structured context the system itself injected. The user's words and earlier
# assistant turns never ground a claim about the estate.

REF_MARKER = re.compile(r"\[ref:([^\]\s]+)\]", re.IGNORECASE)
_NOUNS = (
    r"(?:applications?|elements?|systems?|services?|components?|capabilit(?:y|ies)|"
    r"risks?|dependenc(?:y|ies)|dependents?|relationships?|interfaces?|servers?|"
    r"databases?|processes|owners?|solutions?|vendors?|contracts?|rows?|records?)"
)
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_TENS = {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_VAGUE = ("dozens", "dozen", "hundreds", "thousands", "scores")
_NUM_WORD = "|".join(
    sorted(list(_UNITS) + list(_TENS) + ["hundred"] + list(_VAGUE), key=len, reverse=True)
)
_COUNT_CLAIM = re.compile(
    r"\b(\d[\d,]*|(?:" + _NUM_WORD + r")(?:[ -](?:"
    + "|".join(list(_UNITS) + list(_TENS))
    + r"))?)\s+(?:\w+\s+){0,2}?" + _NOUNS + r"\b",
    re.IGNORECASE,
)
_COUNT_KEYS = frozenset({
    "total", "count", "returned", "rows_total", "rows_shown", "explicit_count",
    "derived_count", "stale_count", "citations_total", "resolved_seed_count",
    "unresolved_count", "affected_count",
})
_ID_KEYS = frozenset({"id", "element_id", "risk_id", "root_element_id"})
_LEADING = frozenset({
    "the", "a", "an", "both", "only", "all", "every", "each", "any", "some",
    "these", "those", "this", "that", "its", "their", "our", "your", "also",
    "as", "i", "said", "and", "but", "so", "then", "if", "when", "while",
})
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_MAX_NGRAM = 6
_MAX_CANDIDATES = 3000


def _words(value) -> List[str]:
    """Words of *value* after the platform's one name normaliser (case and
    whitespace, ``duplicate_guard.normalize_name``) with punctuation removed."""
    from app.utils.duplicate_guard import normalize_name

    return _NON_WORD.sub(" ", normalize_name(value)).split()


def phrase_key(value) -> str:
    """Lower-cased, punctuation-free name with leading articles and quantifiers
    ('The', 'Both', 'Only', ...) removed, for exact comparison."""
    words = _words(value)
    while words and words[0] in _LEADING:
        words.pop(0)
    return " ".join(words)


def _walk(value, visit) -> None:
    if isinstance(value, dict):
        visit(value)
        for v in value.values():
            _walk(v, visit)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, visit)


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
    """What one assistant turn may state about the estate: the tool results of
    this turn and the structured context the system injected. Nothing the user
    typed and nothing an earlier turn said."""

    def __init__(self) -> None:
        self._parts: List[str] = []
        self.tool_results = 0
        self.names: set = set()          # normalised record names a read returned
        self.receipt_names: set = set()  # names a write of this turn created or changed
        self.refs: set = set()           # ids and addresses a marker may cite
        self.counts: set = set()         # counts a tool stated

    def add_context(self, value) -> None:
        """Structured context the system injected (live data blocks)."""
        pieces: list = []
        _walk_text(value, pieces)
        self._parts.append(" ".join(pieces).lower())

    def add_tool_result(self, result, mutates: bool = False) -> None:
        if not (isinstance(result, dict) and result.get("success")):
            return
        self.tool_results += 1
        names: set = set()

        def visit(node: dict) -> None:
            name = node.get("name")
            if isinstance(name, str) and name.strip():
                names.add(phrase_key(name))
            for key, value in node.items():
                is_int = isinstance(value, int) and not isinstance(value, bool)
                if key in _ID_KEYS and is_int:
                    self.refs.add(str(value))
                elif key == "address" and isinstance(value, str):
                    self.refs.add(value)
                elif key == "elements" and isinstance(value, dict):
                    self.refs.update(str(k) for k in value)
                elif key in _COUNT_KEYS and is_int:
                    self.counts.add(value)
                elif key in ("rows", "result", "citations") and isinstance(value, list):
                    self.counts.add(len(value))
                elif key in ("seed_element_ids", "element_ids") and isinstance(value, list):
                    self.refs.update(str(v) for v in value if isinstance(v, int))

        _walk(result, visit)
        names.discard("")
        if mutates:
            self.receipt_names.update(names)
        else:
            self.names.update(names)

    @property
    def text(self) -> str:
        return " \n ".join(self._parts)


def _count_value(token: str):
    """The integer a count token states, or None for a vague quantity."""
    token = token.lower().replace(",", "")
    if token.isdigit():
        return int(token)
    if token in _VAGUE:
        return None
    total = 0
    for part in re.split(r"[ -]", token):
        if part in _UNITS:
            total += _UNITS[part]
        elif part in _TENS:
            total += _TENS[part]
        elif part == "hundred":
            total = max(total, 1) * 100
        else:
            return None
    return total


def candidate_phrases(text: str) -> set:
    """Every 1..6-word normalised phrase of *text* (bounded): the candidates
    looked up against the organisation's own names in one query."""
    words = _words(text)
    found: set = set()
    for size in range(1, _MAX_NGRAM + 1):
        for start in range(0, len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            if phrase and phrase not in _LEADING:
                found.add(phrase)
            if len(found) >= _MAX_CANDIDATES:
                return found
    return found


def organisation_names_in_text(text: str, organization_id) -> set:
    """Normalised names of the organisation's elements and applications that
    appear in *text*: one query per model, fenced to the organisation. Empty
    with no organisation."""
    if organization_id is None or not text:
        return set()
    phrases = candidate_phrases(text)
    if not phrases:
        return set()
    from sqlalchemy import func

    from app.extensions import db

    models = []
    try:
        from app.models import ArchiMateElement

        models.append(ArchiMateElement)
    except Exception:  # pragma: no cover - degraded module registration
        pass
    try:
        from app.models.application_portfolio import ApplicationComponent

        models.append(ApplicationComponent)
    except Exception:  # pragma: no cover
        pass
    found: set = set()
    # Names are compared whole and case-insensitively; punctuation is
    # normalised again on the way out.
    for model in models:
        rows = db.session.execute(
            db.select(model.name).where(
                model.organization_id == organization_id,
                func.lower(model.name).in_(list(phrases)),
            )
        ).scalars()
        found.update(phrase_key(n) for n in rows if n)
    found.discard("")
    return found


def _names_in(sentence: str, names: set) -> set:
    words = _words(sentence)
    hit: set = set()
    for size in range(1, _MAX_NGRAM + 1):
        for start in range(0, len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            if phrase in names:
                hit.add(phrase)
    return hit


def _sentences(text: str) -> List[str]:
    out: List[str] = []
    for raw in _SENTENCE_SPLIT.split(text):
        piece = raw.strip(" \t-*#>|")
        if not piece:
            continue
        # A marker placed after the full stop belongs to the sentence before.
        if out and not REF_MARKER.sub("", piece).strip(" .,;"):
            out[-1] += " " + piece
            continue
        out.append(piece)
    return out


def find_uncited_claims(
    response_text: str,
    grounding: Grounding,
    org_names: Optional[set] = None,
    estate_question: bool = False,
) -> List[Dict]:
    """Sentences of *response_text* that state a fact about the estate which a
    tool result of this turn does not support. An empty list means the answer
    may be shown. See the notes above for what is and is not caught."""
    if not response_text or not response_text.strip():
        return []
    statements = [
        s for s in _sentences(response_text)
        if len(s) >= 8 and not s.rstrip().endswith("?")
    ]

    if estate_question and grounding.tool_results == 0 and statements:
        return [{
            "claim": statements[0][:300],
            "unsupported": ["no record was read for an estate question"],
        }]

    names = set(grounding.names) | set(grounding.receipt_names) | set(org_names or ())
    names.discard("")
    uncited: List[Dict] = []
    for sentence in statements:
        refs = REF_MARKER.findall(sentence)
        clean = REF_MARKER.sub("", sentence)
        missing: List[str] = []

        for match in _COUNT_CLAIM.finditer(clean):
            if match.group(1).lower() == "one":
                # "one element provides ..." is a pronoun-like use, not a count.
                continue
            value = _count_value(match.group(1))
            if value is None or value not in grounding.counts:
                missing.append(match.group(1))

        # A sentence about records this turn's own writes created or changed
        # is a receipt of that write, not a reading of the estate.
        named = {n for n in _names_in(clean, names) if n not in grounding.receipt_names}
        if named:
            if not refs:
                missing.append("[ref:] marker for " + ", ".join(sorted(named)))
            else:
                missing.extend("[ref:%s]" % r for r in refs if r not in grounding.refs)

        if missing:
            uncited.append({"claim": sentence[:300], "unsupported": missing})
    return uncited
