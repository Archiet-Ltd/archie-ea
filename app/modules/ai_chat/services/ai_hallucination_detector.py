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
_MAX_DISTINCT_PHRASES = 20000


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
        # Genome slices read this turn (ADR-genome-01 decision 3): every node id
        # a slice returned, with the node, and each slice's domain, hash and
        # completeness. Nodes a slice dropped to fit its budget are not here.
        self.genome_nodes: Dict[str, dict] = {}
        self.genome_slices: List[dict] = []

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
        self._add_genome_slice(result.get("result"))
        facts = _facts_of(result)
        self.results.append(facts)
        self.names.update(facts.names)
        self.refs.update(facts.refs)
        self.rows_returned += facts.rows
        self.subject_names.update(facts.subjects)
        for ident, names in facts.ref_names.items():
            self.ref_names.setdefault(ident, set()).update(names)


    def _add_genome_slice(self, top) -> None:
        """Put a genome slice's node ids, and the element ids they carry, in the
        turn's ledger so a sentence may cite them."""
        if not (isinstance(top, dict) and isinstance(top.get("nodes"), list)
                and top.get("domain") and top.get("spec_hash") is not None):
            return
        self.genome_slices.append({
            "domain": top["domain"],
            "spec_hash": top["spec_hash"],
            "complete": bool(top.get("complete", True)),
            "nodes_dropped": int((top.get("truncated") or {}).get("nodes_dropped") or 0),
            "as_of": top.get("as_of"),
        })
        for node in top["nodes"]:
            ident = node.get("id") if isinstance(node, dict) else None
            if not isinstance(ident, str):
                continue
            self.genome_nodes[ident] = node
            self.refs.add(ident)
            name = node.get("name")
            if isinstance(name, str) and phrase_key(name):
                self.ref_names.setdefault(ident, set()).add(phrase_key(name))
            element_id = (node.get("provenance") or {}).get("archimate_element_id")
            if _is_int(element_id):
                self.refs.add(str(element_id))
                self.genome_nodes.setdefault(str(element_id), node)
                if isinstance(name, str) and phrase_key(name):
                    self.ref_names.setdefault(str(element_id), set()).add(phrase_key(name))


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


class CandidateOverflow(Exception):
    """The text has too many distinct phrases to look up; the caller fails
    closed for an estate answer rather than skipping any."""


def _clean_variants(text: str) -> List[str]:
    """The text with Markdown, HTML, typographic quotes, brackets (ordinary and
    fullwidth), dashes and zero-width characters removed or normalised.
    Variants: possessives stripped and as written (curly and straight
    apostrophes folded to one), brackets kept (a name may carry them,
    O365 (Office)) and dropped, slashes split (A/B names two records),
    ampersand as and."""
    t = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("\u00a0", " ")
    t = re.sub(r"<[^>]+>", " ", t)                                 # HTML tags
    t = re.sub(_ZERO_WIDTH, "", t)
    t = REF_MARKER.sub(" ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                 # [text](url) -> text
    t = re.sub("[\u2014\u2013\u2012\u2015]|--+", " ", t)           # dashes
    t = t.replace("\u2019", "'").replace("\u2018", "'")              # one apostrophe
    out: List[str] = []
    for possessive in (False, True):
        u = t
        if possessive:
            u = re.sub("(?<=\\w)'s(?!\\w)", "", u)                 # strip 's
        u = re.sub(_QUOTES, " ", u)                                # typographic quotes
        u = re.sub("(?<!\\w)'|'(?!\\w)", " ", u)                   # quote marks at edges
        u = re.sub("[`*~]+", " ", u)                               # emphasis, code
        u = re.sub("(?<!\\w)_+|_+(?!\\w)", " ", u)                 # _emphasis_
        for open_, close in zip(_FULLWIDTH_OPEN, _FULLWIDTH_CLOSE):
            u = u.replace(open_, "(").replace(close, ")")
        out.extend([
            u,
            re.sub("[\\[\\](){}]", " ", u),
            u.replace("/", " "),
            u.replace("&", " and "),
        ])
    return out


def candidate_phrases(text: str) -> set:
    """Every distinct folded phrase of *text* worth looking up against the
    organisation's names: runs of two to six words and capitalised single
    words, punctuation a name may carry (S/4HANA, E-Commerce, Moody's) kept,
    folded with the platform's one normaliser. Nothing is capped. For
    pathological input (more than 20000 distinct phrases) the phrases that
    contain a capitalised word are looked up and the rest are dropped; if
    even those exceed the bound, CandidateOverflow is raised."""
    from app.utils.duplicate_guard import normalize_name

    everything: set = set()
    capitalised: set = set()
    for variant in _clean_variants(text):
        tokens = variant.split()
        if len(tokens) > 4 * _MAX_DISTINCT_PHRASES:
            raise CandidateOverflow("text too long to look up")
        for size in range(1, _MAX_NGRAM + 1):
            for start in range(0, len(tokens) - size + 1):
                window = tokens[start:start + size]
                raw = " ".join(window)
                gram = _trim_phrase(raw)
                if not gram:
                    continue
                if size == 1 and not gram[:1].isupper():
                    continue
                keys = []
                key = normalize_name(gram)
                if key and key not in _LEADING:
                    keys.append(key)
                # A name that ends in a full stop (Acme Inc.) is also looked
                # up as written.
                dotted = raw.strip(",;:!?\"'")
                if dotted.endswith(".") and len(dotted) > 2:
                    keys.append(normalize_name(dotted))
                # A name stored with a typographic apostrophe.
                keys += [k.replace("'", "\u2019") for k in keys if "'" in k]
                has_cap = any(w[:1].isupper() for w in window)
                for k in keys:
                    everything.add(k)
                    if has_cap:
                        capitalised.add(k)
    if len(everything) <= _MAX_DISTINCT_PHRASES:
        return everything
    if len(capitalised) <= _MAX_DISTINCT_PHRASES:
        return capitalised
    raise CandidateOverflow("too many distinct phrases to look up")


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
    ordered = sorted(phrases)
    for model in models:
        for offset in range(0, len(ordered), 500):
            rows = db.session.execute(
                db.select(model.name).where(
                    model.organization_id == organization_id,
                    folded_column(model.name).in_(ordered[offset:offset + 500]),
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
                    question_names, estate_question, zero_rows):
    """The unsupported parts of one statement (empty when it is supported)."""
    missing: List[str] = []
    markers = list(REF_MARKER.finditer(sentence))
    ids = [m.group(1) for m in markers]

    for ident in ids:
        if ident not in grounding.refs and ident not in grounding.ref_names:
            missing.append("[ref:%s]" % ident)

    check_generic = bool(estate_question and grounding.tool_results > 0)
    mentions = _mentions(sentence, tool_keys, context_keys, org_keys, markers, check_generic)
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
            # A marker after a context record must map to THAT record's id.
            # Without one, the context grounds the record when the sentence
            # carries no marker at all (every record in it is then one the
            # model was handed); a sentence with markers needs each record to
            # have its own.
            run = next((r for r in runs if r["start"] >= mention["end"]), None)
            if run is not None:
                if not any(key in grounding.ref_names.get(i, ()) for i in run["ids"]):
                    missing.append("marker does not belong to record: " + key)
            elif markers and key not in subject_keys:
                missing.append("[ref:] marker for " + key)
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
    """A sentence is exempt only if it starts with a clarification phrase,
    names AT MOST ONE record (a relationship needs two), that record is one
    the user's own question named, it has no digit and no marker, and it is
    at most 15 words."""
    text = sentence.strip()
    if not _CLARIFICATION_START.search(text):
        return False
    if len(text.split()) > 15 or re.search(r"\d", text) or REF_MARKER.search(text):
        return False
    mentions = _mentions(text, tool_keys, context_keys, org_keys, [])
    if len(mentions) > 1:
        return False
    return all(m["key"] in question_names for m in mentions)


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

    uncited: List[Dict] = []
    for sentence in statements:
        if _clarification_exempt(sentence, tool_keys, context_keys, org_keys, question_names):
            continue
        missing = _check_sentence(
            sentence, grounding, tool_keys, context_keys, org_keys,
            question_names, estate_question, zero_rows,
        )
        if missing:
            uncited.append({"claim": sentence[:300], "unsupported": missing})
    return uncited


# --------------------------------------------------------------------------- #
# The genome citation post-check (ADR-genome-01 decision 3, LG-2)              #
# --------------------------------------------------------------------------- #
#
# Run on an answer whose turn read a genome slice, before it is shown. It only
# removes: a sentence that cites an id outside this turn's ledger is dropped
# whole (its id came from another organisation, an earlier turn or nowhere), and
# a model-written address or image is stripped, because every link the person
# sees is built by the server from a cited id. It never adds or rewrites a fact.
# If no cited sentence survives, the answer is the plain "not recorded" line
# naming the parts of the model that were searched.

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_BARE_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
NOT_RECORDED_YET = "Your model does not record enough to answer this yet."


def _strip_model_links(line: str) -> str:
    line = _MD_IMAGE.sub("", line)
    line = _MD_LINK.sub(lambda m: m.group(1), line)
    return _BARE_URL.sub("", line)


def genome_post_check(response_text: str, grounding: Grounding) -> Dict:
    """``{"text", "removed", "cited"}``: *response_text* with every sentence that
    cites an id outside the turn's ledger removed. ``removed`` lists the ids that
    were outside it (never shown); ``cited`` the ledger ids the kept text cites."""
    ledger = set(grounding.refs)
    removed: List[str] = []
    cited: List[str] = []
    out_lines: List[str] = []
    for raw_line in (response_text or "").split("\n"):
        line = _strip_model_links(raw_line)
        if not line.strip():
            out_lines.append("")
            continue
        kept = []
        for sentence in _sentences(line):
            ids = [m.group(1) for m in REF_MARKER.finditer(sentence)]
            outside = [i for i in ids if i not in ledger]
            if outside:
                removed.extend(outside)
                continue
            cited.extend(i for i in ids if i not in cited)
            kept.append(sentence)
        if kept:
            lead = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            out_lines.append(lead + " ".join(kept))
    text = "\n".join(out_lines).strip()
    if not cited:
        searched = ", ".join(sorted({s["domain"] for s in grounding.genome_slices})) or "none"
        text = "%s Searched: %s." % (NOT_RECORDED_YET, searched)
    return {"text": text, "removed": removed, "cited": cited}
