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
# What it enforces. A record of the estate is a name that (a) a tool returned
# this turn, (b) the system injected as structured context, or (c) exactly
# matches one of the organisation's own element or application names as a
# whole phrase. A statement (not a question) that names a record of kind (a)
# needs a [ref:<element id or answer address>] marker attached to THAT record:
# a run of markers covers the record names between it and the previous run,
# and each record must be covered by a marker whose id maps to it (an answer
# address covers every record in that answer). A record of kind (b) is
# grounded by the context the model was given. A record of kind (c) that no
# tool returned and no context carries is refused: it was not read this turn.
#
# Counts of estate things (digits or words; a 4-digit year is never a count)
# are checked only in sentences that name a record or carry a marker, or when
# the question was an estate question and a tool ran. A count must equal a
# count returned with the tool results the sentence refers to.
#
# Sentences naming no record and stating no such count are general guidance
# and are never touched. Single-word organisation names count only when
# capitalised mid-sentence or next to a marker, and names that are generic
# architecture vocabulary never trigger unless a tool or the context returned
# them as written.
#
# What it does NOT catch, on the record: a wrong or paraphrased claim about a
# REAL record that carries a valid marker, and an invented name that matches
# no record (caught only through the estate-question lookup rule, never by
# wording). It checks that a statement is anchored to a row returned this
# turn, not that it is true of that row.
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
    "total", "count", "returned", "rows_total", "explicit_count",
    "derived_count", "stale_count", "resolved_seed_count", "unresolved_count",
    "affected_count",
})
_NOT_COUNTS = frozenset({"citations_total", "rows_shown"})
_COUNT_LIKE = re.compile(r"^(?:total|count)|(?:_total|_count)$")
_ID_KEYS = frozenset({"id", "element_id", "risk_id", "root_element_id"})
_NAME_KEYS = frozenset({"name", "title"})
_LEADING = frozenset({
    "the", "a", "an", "both", "only", "all", "every", "each", "any", "some",
    "these", "those", "this", "that", "its", "their", "our", "your", "also",
    "as", "i", "said", "and", "but", "so", "then", "if", "when", "while",
})
# Architecture vocabulary that is not, on its own, a record of the estate.
_GENERIC = frozenset({
    "kubernetes", "togaf", "microservices", "microservice", "monolith", "api",
    "apis", "gateway", "api gateway", "customer", "customers", "database",
    "databases", "load balancer", "cache", "queue", "service", "services",
    "application", "applications", "server", "servers", "cloud", "platform",
    "security", "data", "network", "user", "users", "system", "systems",
    "portal", "archimate", "serving", "docker", "terraform", "linux", "web",
    "frontend", "backend", "middleware", "integration", "capability",
    "business", "technology", "strategy", "layer", "ui", "sql", "rest",
})
# A clarification is exempt only when the clause STARTS with the phrase, and
# only for records the user's own question named.
_CLARIFICATION_START = re.compile(
    r"^(?:i |we )?(?:could not|couldn't|cannot|can't|did not|didn't|was unable to|"
    r"am unable to|do not|don't) (?:find|see)\b|"
    r"^did you mean\b|^not recorded\b|^nothing (?:is )?recorded\b|"
    r"^there (?:is|are) no (?:matching |such )?(?:records?|elements?|applications?|matches)\b",
    re.IGNORECASE,
)
_NO_ROWS_OK = re.compile(
    r"\bnot recorded\b|\bnothing (?:is )?recorded\b|\bno (?:recorded |known )?"
    r"(?:rows|records|dependenc|dependents|results)\b|\bnone\b",
    re.IGNORECASE,
)
# Language that states how records relate or what happens when one fails.
_IMPACT_LANGUAGE = re.compile(
    r"\b(depends? on|depend(?:s|ed)? upon|dependent on|relies on|rely on|runs? on|"
    r"hosted on|breaks?|would break|fail|fails|failed|failure|stops?|stopped|down|"
    r"impacted|impacts?|affects?|affected|connects? to|connected to|calls?|"
    r"serves?|serving|feeds?|consumes?|uses?|used by)\b",
    re.IGNORECASE,
)
# What disqualifies a clarification: relationship language, ownership, and the
# words that continue a sentence into a claim.
_CLARIFY_RELATION = re.compile(
    _IMPACT_LANGUAGE.pattern[:-3] + r"|owned by|owns|because|so|while|contradict\w*|"
    r"stops?|fails?)\b",
    re.IGNORECASE,
)
_CONNECTIVE = re.compile(
    r"\b(?:because|so|while|and|but|though|although|since|whereas|which)\b|"
    r"[:;,\u2014\u2013]|\s-+\s",
    re.IGNORECASE,
)
# A quantity attached to an estate noun is checked only in a statement of fact
# about the estate; advice never triggers it.
_FACT_VERB = re.compile(
    r"\b(has|have|had|there (?:is|are)|downstream|upstream|are affected|is affected|"
    r"affected|impacted|depend(?:s|ent)?|"
    r"stop|stops|break|breaks)\b",
    re.IGNORECASE,
)
_ADVICE = re.compile(
    r"\b(consider|keep|use|aim|target|should|recommend\w*|prefer|avoid|try|plan|"
    r"ensure|make sure|you can|you could|you might|let us)\b",
    re.IGNORECASE,
)
# Estate nouns whose attached quantity is checked even where no record is named.
_ATTACHED_NOUNS = (
    r"(?:applications?|elements?|dependents?|dependenc(?:y|ies)|relationships?)"
)
_NOT_A_COUNT_BEFORE = frozenset({
    "tier", "level", "phase", "layer", "step", "type", "stage", "release",
    "version", "v", "number", "no", "least", "most", "about", "around",
    "roughly", "typically", "usually", "per", "each", "every", "top", "first",
    "last", "next", "category", "class", "grade", "priority", "p", "wave",
})
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_ALNUM = re.compile(r"[A-Za-z0-9]+")
_MAX_NGRAM = 6
_MAX_CANDIDATES = 1500


def _words(value) -> List[str]:
    """Words of *value* after the platform's one name normaliser (case and
    whitespace, ``duplicate_guard.normalize_name``) with punctuation removed."""
    from app.utils.duplicate_guard import normalize_name

    return _NON_WORD.sub(" ", normalize_name(value)).split()


def phrase_key(value) -> str:
    """Comparison key of a name: folded, punctuation-free, without leading
    articles and quantifiers ('The', 'Both', 'Only', ...)."""
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


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _ResultFacts:
    """Names, citable ids and counts that came back with ONE tool result."""

    def __init__(self) -> None:
        self.names: set = set()
        self.refs: set = set()
        self.ref_names: Dict[str, set] = {}
        self.counts: set = set()
        self.rows = 0
        self.subjects: set = set()
        self.row_counts: set = set()   # counts of this result's own answer rows


def _facts_of(result: dict) -> _ResultFacts:
    facts = _ResultFacts()

    def visit(node: dict) -> None:
        key_name = None
        for key in _NAME_KEYS:
            if isinstance(node.get(key), str) and node[key].strip():
                key_name = phrase_key(node[key])
                break
        if key_name:
            facts.names.add(key_name)
        for key, value in node.items():
            if key in _ID_KEYS and _is_int(value):
                facts.refs.add(str(value))
                if key_name:
                    facts.ref_names.setdefault(str(value), set()).add(key_name)
            elif key == "address" and isinstance(value, str):
                facts.refs.add(value)
            elif key in ("elements", "subject_elements") and isinstance(value, dict):
                for ident, element in value.items():
                    facts.refs.add(str(ident))
                    if isinstance(element, dict):
                        nm = element.get("name")
                        if isinstance(nm, str) and nm.strip():
                            facts.ref_names.setdefault(str(ident), set()).add(phrase_key(nm))
            elif key in _NOT_COUNTS:
                continue
            elif (key in _COUNT_KEYS or _COUNT_LIKE.search(str(key))) and _is_int(value):
                facts.counts.add(value)
            elif key == "rows" and isinstance(value, list):
                facts.counts.add(len(value))
            elif key in ("seed_element_ids", "element_ids") and isinstance(value, list):
                facts.refs.update(str(v) for v in value if _is_int(v))

    _walk(result, visit)
    facts.names.discard("")
    top = result.get("result") if isinstance(result, dict) else None
    if isinstance(top, dict) and isinstance(top.get("subject_elements"), dict):
        for element in top["subject_elements"].values():
            if isinstance(element, dict) and isinstance(element.get("name"), str):
                facts.subjects.add(phrase_key(element["name"]))
    if isinstance(top, dict) and isinstance(top.get("rows"), list):
        facts.rows = len(top["rows"])
        facts.row_counts.add(len(top["rows"]))
        for holder in (top, top.get("summary")):
            if isinstance(holder, dict) and _is_int(holder.get("total")):
                facts.row_counts.add(holder["total"])
    elif isinstance(top, list):
        facts.rows = len(top)
        facts.counts.add(len(top))
    # An answer address covers every record in that answer.
    address = top.get("address") if isinstance(top, dict) else None
    if isinstance(address, str):
        facts.ref_names[address] = set(facts.names)
    return facts


class Grounding:
    """What one assistant turn may state about the estate: the tool results of
    this turn and the structured context the system injected. Nothing the user
    typed and nothing an earlier turn said."""

    def __init__(self) -> None:
        self.tool_results = 0
        self.results: List[_ResultFacts] = []
        self.names: set = set()           # keys of records a read returned
        self.receipt_names: set = set()   # keys of records a write of this turn created
        self.refs: set = set()            # ids and addresses a marker may cite
        self.ref_names: Dict[str, set] = {}
        self.context_names: set = set()   # records the injected context carries
        self.context_text: str = ""       # keyed text of unstructured context
        self.context_counts: set = set()  # counts the injected context states
        self.rows_returned = 0
        self.subject_names: set = set()   # records the question itself was about

    def add_context(self, value) -> None:
        """Structured context the system injected (live data blocks): a dict
        is parsed for names and ids, a string is kept as keyed text."""
        if isinstance(value, str):
            self.context_text += " " + " ".join(_words(value)) + " "
            return
        if value is None:
            return
        facts = _facts_of({"success": True, "result": value})
        self.context_names.update(facts.names)
        self.context_counts.update(facts.counts)
        self.refs.update(facts.refs)
        for ident, names in facts.ref_names.items():
            self.ref_names.setdefault(ident, set()).update(names)

    def add_tool_result(self, result, mutates: bool = False) -> None:
        if not (isinstance(result, dict) and result.get("success")):
            return
        self.tool_results += 1
        if mutates:
            # Only the record the write itself created or changed is a
            # receipt; a name found anywhere else in the result is not.
            top = result.get("result")
            created = top if isinstance(top, dict) else {}
            name = created.get("name") if isinstance(created.get("name"), str) else None
            if name and phrase_key(name):
                self.receipt_names.add(phrase_key(name))
            return
        facts = _facts_of(result)
        self.results.append(facts)
        self.names.update(facts.names)
        self.refs.update(facts.refs)
        self.rows_returned += facts.rows
        self.subject_names.update(facts.subjects)
        for ident, names in facts.ref_names.items():
            self.ref_names.setdefault(ident, set()).update(names)


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


def _trim_phrase(phrase: str) -> str:
    phrase = phrase.strip(".,;:!?\"'")
    if phrase.endswith(")") and "(" not in phrase:
        phrase = phrase[:-1]
    if phrase.startswith("(") and ")" not in phrase:
        phrase = phrase[1:]
    return phrase


_FULLWIDTH_OPEN = "\uff08\uff3b\uff5b\u3010\u300c\u300e\u3008"
_FULLWIDTH_CLOSE = "\uff09\uff3d\uff5d\u3011\u300d\u300f\u3009"
_ZERO_WIDTH = "[\u200b\u200c\u200d\u2060\ufeff\u00ad]"
_QUOTES = "[\u201c\u201d\u201e\u201f\u2018\u2019\u201a\u201b\u00ab\u00bb\u2039\u203a\u300c\u300d`\"]"


def _clean_variants(text: str) -> List[str]:
    """The text with Markdown, HTML, typographic quotes, brackets (ordinary and
    fullwidth), possessives, dashes and zero-width characters removed or
    normalised. Variants: brackets kept (a name may carry them, O365 (Office)),
    brackets dropped, slashes split (A/B names two records), ampersand as and."""
    t = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("\u00a0", " ")
    t = re.sub(r"<[^>]+>", " ", t)                                 # HTML tags
    t = re.sub(_ZERO_WIDTH, "", t)
    t = REF_MARKER.sub(" ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                 # [text](url) -> text
    t = re.sub("[\u2014\u2013\u2012\u2015]|--+", " ", t)           # dashes
    t = re.sub("(?<=\\w)['\u2019]s(?!\\w)", "", t)                # possessive 's
    t = re.sub(_QUOTES, " ", t)                                    # typographic quotes
    t = re.sub("(?<!\\w)'|'(?!\\w)", " ", t)                       # ASCII quotes
    t = re.sub("[`*~]+", " ", t)                                   # emphasis, code
    t = re.sub("(?<!\\w)_+|_+(?!\\w)", " ", t)                     # _emphasis_
    for open_, close in zip(_FULLWIDTH_OPEN, _FULLWIDTH_CLOSE):
        t = t.replace(open_, "(").replace(close, ")")
    bare = re.sub("[\\[\\](){}]", " ", t)
    slash = t.replace("/", " ")
    amp = t.replace("&", " and ")
    return [t, bare, slash, amp]


def candidate_phrases(text: str) -> set:
    """Phrases of *text* worth looking up against the organisation's names:
    every run of two to six words and every capitalised single word, with the
    punctuation a name may carry (S/4HANA, E-Commerce) kept, folded with the
    platform's one normaliser. Deduplicated and bounded."""
    from app.utils.duplicate_guard import normalize_name

    found: set = set()
    for variant in _clean_variants(text):
        tokens = variant.split()
        for size in range(1, _MAX_NGRAM + 1):
            for start in range(0, len(tokens) - size + 1):
                raw = " ".join(tokens[start:start + size])
                gram = _trim_phrase(raw)
                if not gram:
                    continue
                if size == 1 and not gram[:1].isupper():
                    continue
                key = normalize_name(gram)
                if key and key not in _LEADING:
                    found.add(key)
                # A name that ends in a full stop (Acme Inc.) is also looked
                # up as written.
                dotted = raw.strip(",;:!?\"'")
                if dotted.endswith(".") and len(dotted) > 2:
                    found.add(normalize_name(dotted))
                if len(found) >= _MAX_CANDIDATES:
                    return found
    return found


def organisation_names_in_text(text: str, organization_id) -> set:
    """Phrase keys of the organisation's element and application names that
    appear in *text* as whole phrases: one query per model, fenced to the
    organisation, with the SQL side folded by ``duplicate_guard.folded_column``
    (the one folding expression). Empty with no organisation."""
    if organization_id is None or not text:
        return set()
    phrases = candidate_phrases(text)
    if not phrases:
        return set()
    from app.extensions import db
    from app.utils.duplicate_guard import folded_column

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
    for model in models:
        rows = db.session.execute(
            db.select(model.name).where(
                model.organization_id == organization_id,
                folded_column(model.name).in_(sorted(phrases)),
            )
        ).scalars()
        found.update(phrase_key(n) for n in rows if n)
    found.discard("")
    return found


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


def _mentions(sentence: str, tool_keys: set, context_keys: set, org_keys: set,
              markers: list, check_generic: bool = False) -> List[Dict]:
    """Record mentions in *sentence*: longest phrase first, no overlaps."""
    spans = []
    for match in _ALNUM.finditer(sentence):
        spans.append((match.group(0).lower(), match.start(), match.end(), match.group(0)))
    marker_spans = [(m.start(), m.end()) for m in markers]

    def inside_marker(pos: int) -> bool:
        return any(a <= pos < b for a, b in marker_spans)

    spans = [s for s in spans if not inside_marker(s[1] - 1)]
    all_keys = tool_keys | context_keys | org_keys
    found: List[Dict] = []
    i = 0
    while i < len(spans):
        hit = None
        for size in range(min(_MAX_NGRAM, len(spans) - i), 0, -1):
            gram = spans[i:i + size]
            key = " ".join(w[0] for w in gram)
            if key not in all_keys:
                continue
            returned = key in tool_keys or key in context_keys
            if not returned:
                # An organisation name that nothing returned: generic
                # vocabulary never triggers, and one word counts only when
                # capitalised mid-sentence or next to a marker.
                if key in _GENERIC and not (check_generic and size == 1):
                    continue
                if size == 1:
                    first = gram[0]
                    capitalised = first[3][:1].isupper() and first[1] > 0
                    after = sentence[first[2]:].lstrip()
                    if not (capitalised or after.startswith("[ref:")):
                        continue
            hit = (key, gram[0][1], gram[-1][2], size)
            break
        if hit:
            found.append({"key": hit[0], "start": hit[1], "end": hit[2]})
            i += hit[3]
        else:
            i += 1
    return found


def _attached_counts(text: str):
    """Quantities directly attached to an estate noun (applications, elements,
    dependents ...), without the ones that are not counts of the estate (tier
    1, at least three, a year)."""
    for match in _COUNT_CLAIM.finditer(text):
        token = match.group(1)
        if token.lower() == "one":
            continue
        after = text[match.end(1):match.end()].split()
        if not after or not re.fullmatch(_ATTACHED_NOUNS, after[-1].strip(".,;:!?"), re.I):
            continue
        if len(after) > 2 or (len(after) == 2 and after[0].lower() not in ("of", "more", "other")):
            continue  # another word sits between quantity and noun: not "attached"
        before = text[:match.start(1)].split()
        if before and before[-1].lower().strip(".,;:()") in _NOT_A_COUNT_BEFORE:
            continue
        yield token


def _year(token: str) -> bool:
    return token.isdigit() and len(token) == 4 and 1900 <= int(token) <= 2100


def _check_sentence(sentence, grounding, tool_keys, context_keys, org_keys,
                    question_names, estate_question, zero_rows, impact_rows):
    """The unsupported parts of one statement (empty when it is supported)."""
    missing: List[str] = []
    markers = list(REF_MARKER.finditer(sentence))
    ids = [m.group(1) for m in markers]

    for ident in ids:
        if ident not in grounding.refs and ident not in grounding.ref_names:
            missing.append("[ref:%s]" % ident)

    check_generic = bool(estate_question and grounding.tool_results > 0)
    mentions = _mentions(sentence, tool_keys, context_keys, org_keys, markers, check_generic)
    blanked = REF_MARKER.sub(" ", sentence)
    for mention in sorted(mentions, key=lambda m: -m["start"]):
        blanked = blanked[:mention["start"]] + " " * (mention["end"] - mention["start"]) + blanked[mention["end"]:]
    impact_language = bool(_IMPACT_LANGUAGE.search(blanked))
    subject_keys = grounding.subject_names

    # Marker runs: consecutive markers separated only by whitespace. A run
    # covers the mentions between the previous run and itself.
    runs: List[Dict] = []
    for m in markers:
        if runs and not sentence[runs[-1]["end"]:m.start()].strip():
            runs[-1]["end"] = m.end()
            runs[-1]["ids"].append(m.group(1))
        else:
            runs.append({"start": m.start(), "end": m.end(), "ids": [m.group(1)]})

    for mention in mentions:
        key = mention["key"]
        if key in grounding.receipt_names:
            continue
        if zero_rows and key not in question_names and not (
            _NO_ROWS_OK.search(sentence) and key in question_names
        ):
            missing.append("not in the question and no rows were returned: " + key)
            continue
        if key in tool_keys:
            run = next((r for r in runs if r["start"] >= mention["end"]), None)
            covered = run is not None and any(
                key in grounding.ref_names.get(ident, ()) for ident in run["ids"]
            )
            if not covered:
                missing.append("[ref:] marker for " + key)
        elif key in context_keys:
            # Context rows ground what a record is, has or is owned by; they
            # never ground a relationship or impact statement.
            if impact_language and key not in subject_keys:
                missing.append("not in the answer rows: " + key)
        else:
            missing.append("not returned this turn: " + key)

    # Counts: a sentence that names a record or carries a marker is checked in
    # full; any other sentence only for a quantity attached to an estate noun.
    if mentions or markers:
        tokens = [
            m.group(1) for m in _COUNT_CLAIM.finditer(REF_MARKER.sub("", sentence))
            if m.group(1).lower() != "one"
            and not _is_non_count(REF_MARKER.sub("", sentence), m)
        ]
    elif (
        estate_question
        and (grounding.tool_results > 0 or grounding.context_counts)
        and _FACT_VERB.search(sentence)
        and not _ADVICE.search(sentence)
    ):
        tokens = list(_attached_counts(sentence))
    else:
        tokens = []
    if tokens:
        referenced = [
            r for r in grounding.results
            if any(m["key"] in r.names for m in mentions) or any(i in r.refs for i in ids)
        ]
        allowed: set = set(grounding.context_counts)
        for r in referenced:
            allowed |= r.counts
        if not referenced:
            # No record or marker to tie it to: a count that equals the count
            # of this turn's own answer rows is accepted.
            for r in grounding.results:
                allowed |= r.row_counts
        for token in tokens:
            if _year(token):
                continue
            value = _count_value(token)
            if value is None or value not in allowed:
                missing.append(token)
    return missing


def _is_non_count(text: str, match) -> bool:
    before = text[:match.start(1)].split()
    return bool(before and before[-1].lower().strip(".,;:()") in _NOT_A_COUNT_BEFORE)


def _clarification_exempt(sentence, tool_keys, context_keys, org_keys, question_names) -> bool:
    """A sentence is exempt only if it STARTS with a clarification phrase, has
    no relationship language, no connective leading into a record name, no
    count, and names only records the user's own question named."""
    text = sentence.strip()
    if not _CLARIFICATION_START.search(text):
        return False
    markers = list(REF_MARKER.finditer(text))
    if markers or _COUNT_CLAIM.search(text):
        return False
    mentions = _mentions(text, tool_keys, context_keys, org_keys, markers)
    blanked = text
    for mention in sorted(mentions, key=lambda m: -m["start"]):
        blanked = blanked[:mention["start"]] + " " * (mention["end"] - mention["start"]) + blanked[mention["end"]:]
    body = _CLARIFICATION_START.sub(" ", blanked, count=1)
    if _CLARIFY_RELATION.search(body):
        return False
    for mention in mentions:
        if _CONNECTIVE.search(text[:mention["start"]]):
            return False
        if mention["key"] not in question_names:
            return False
    # A connective followed by anything that looks like a new clause.
    tail = text[_CLARIFICATION_START.search(text).end():]
    if re.search(r"[:;\u2014\u2013]|\s-+\s", tail) and mentions:
        return False
    return True


def find_uncited_claims(
    response_text: str,
    grounding: Grounding,
    org_names: Optional[set] = None,
    estate_question: bool = False,
    question_names: Optional[set] = None,
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
    question_names = set(question_names or ())

    if estate_question and grounding.tool_results == 0 and statements:
        if not all(_CLARIFICATION_START.search(s) for s in statements):
            return [{
                "claim": statements[0][:300],
                "unsupported": ["no record was read for an estate question"],
            }]

    tool_keys = set(grounding.names) | set(grounding.receipt_names)
    tool_keys.discard("")
    context_keys = set(grounding.context_names)
    org_keys = set(org_names or ())
    for key in list(org_keys):
        # An organisation name the context text carries is context-grounded.
        if grounding.context_text and (" " + key + " ") in grounding.context_text:
            context_keys.add(key)
    zero_rows = bool(estate_question and grounding.tool_results > 0
                     and grounding.rows_returned == 0)
    impact_rows = bool(grounding.rows_returned > 0)

    uncited: List[Dict] = []
    for sentence in statements:
        if _clarification_exempt(sentence, tool_keys, context_keys, org_keys, question_names):
            continue
        missing = _check_sentence(
            sentence, grounding, tool_keys, context_keys, org_keys,
            question_names, estate_question, zero_rows, impact_rows,
        )
        if missing:
            uncited.append({"claim": sentence[:300], "unsupported": missing})
    return uncited
