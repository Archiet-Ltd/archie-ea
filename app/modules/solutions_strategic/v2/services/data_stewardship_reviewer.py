"""Data Architecture Stewardship Reviewer (AI-5).

The AI Data Architect, estate-wide: it reviews the data layer for the
three things a data architect owns — a coherent CANONICAL model, proper
CLASSIFICATION of sensitive data, and traceable LINEAGE — and returns
ranked findings, each sourced with the page to action it.

Deterministic + sourced (Rule 11): name-normalisation heuristics and
coverage rules over live data, no LLM — fast, free, always current. Every
section is fault-tolerant. Findings never fabricate a count or a name.

Severity: 'critical' | 'high' | 'info'. flagged = critical + high.
"""

import logging
import re
from typing import Any, Callable, Dict, List

from sqlalchemy import func

from app import db

logger = logging.getLogger(__name__)

def _n(count, singular, plural=None):
    """Big-4 copy: '3 findings' / '1 finding' — never 'finding(s)'."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"


# tokens dropped when normalising a data-entity name to its canonical core
_STOPWORDS = {
    "data", "object", "record", "records", "master", "table", "info",
    "information", "details", "detail", "store", "repository", "pack",
    "framework", "log", "logs", "the", "a", "an", "of", "for", "list",
    "set", "entity", "model", "db", "dataset",
}
# name fragments that suggest personal / sensitive data
_PII_TERMS = (
    "customer", "client", "person", "people", "contact", "name", "email",
    "address", "phone", "account", "payment", "card", "bank", "ssn",
    "social security", "dob", "birth", "salary", "compensation", "employee",
    "patient", "passport", "national id", "tax", "credential", "user profile",
)


def _safe(name: str, fn: Callable[[], List[Dict]]) -> List[Dict]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.debug("data-stewardship check %s unavailable: %s", name, exc)
        return []


def _norm(raw: str) -> str:
    """Canonical key for a data-entity name (lowercased, stopwords removed,
    trivial plurals singularised, tokens sorted)."""
    toks = re.findall(r"[a-z0-9]+", (raw or "").lower())
    out = []
    for t in toks:
        if t in _STOPWORDS:
            continue
        if t.endswith("s") and len(t) > 3:
            t = t[:-1]
        out.append(t)
    return " ".join(sorted(set(out)))


# Semantic canonical detection (sentence-transformers, free local model).
# Catches synonyms the lexical key misses (Customer ~ Client ~ Account Holder).
_SEMANTIC_THRESHOLD = 0.58   # tuned: Customer~Client≈0.61, Customer~Invoice≈0.42
_SEMANTIC_MAX = 250          # bound the cost; the catalogue is ~100 today
_semantic_cache: Dict[tuple, list] = {}  # cache-tenancy-ok: holds only name-pair similarity scores, found by the exact set of names asked about  # {frozenset(names): [(a, b, sim), ...]}


def _semantic_pairs(names: List[str]) -> List[tuple]:
    """Return (name_a, name_b, similarity) pairs above threshold whose
    normalised keys DIFFER (semantic-only matches, complementary to lexical).
    Memoised by the name set; falls back to [] on any failure."""
    import math

    uniq = sorted({n for n in names if n})
    if not uniq or len(uniq) > _SEMANTIC_MAX:
        return []
    key = frozenset(uniq)
    if key in _semantic_cache:
        return _semantic_cache[key]

    from app.services.pgvector_embedding_service import get_pgvector_service

    embs = get_pgvector_service().generate_embeddings_batch(uniq)
    if not embs or len(embs) != len(uniq):
        return []

    def _cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    pairs = []
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            if _norm(uniq[i]) == _norm(uniq[j]):
                continue  # already caught lexically
            sim = _cos(embs[i], embs[j])
            if sim >= _SEMANTIC_THRESHOLD:
                pairs.append((uniq[i], uniq[j], round(sim, 3)))
    pairs.sort(key=lambda p: -p[2])
    _semantic_cache[key] = pairs
    return pairs


class DataStewardshipReviewer:
    """Estate-wide review of the data layer."""

    @classmethod
    def review(cls) -> Dict[str, Any]:
        findings: List[Dict[str, Any]] = []
        findings += _safe("canonical", cls._canonical_findings)
        findings += _safe("semantic", cls._semantic_findings)
        findings += _safe("pii", cls._pii_findings)
        findings += _safe("classification", cls._classification_findings)
        findings += _safe("lineage", cls._lineage_findings)

        rank = {"critical": 0, "high": 1, "info": 2}
        findings.sort(key=lambda f: rank.get(f.get("severity", "info"), 3))
        flagged = sum(1 for f in findings if f.get("severity") in ("critical", "high"))

        if not findings:
            summary = "The data layer is coherent — no canonical, classification or lineage gaps found."
        else:
            cats = sorted({f["category"] for f in findings})
            summary = (
                f"{_n(len(findings), 'data-stewardship finding')} ({flagged} needing "
                f"attention) across {', '.join(cats)}. Reviewed live across the "
                "DataObject catalogue, application classifications and integration flows."
            )
        return {"success": True, "findings": findings, "flagged": flagged,
                "finding_count": len(findings), "summary": summary}

    # ------------------------------------------------------------------ #
    # Write-back: PII-aware baseline classification (flag -> fix)         #
    # ------------------------------------------------------------------ #

    @classmethod
    def apply_baseline_classification(cls, user_id: int) -> Dict[str, Any]:
        """Give every unclassified application a baseline data classification:
        'Confidential' if its name suggests PII, else 'Internal'. Audited and
        reversible. The Data Architect proposes; the human triggers this."""
        from app.models.application_portfolio import ApplicationComponent

        apps = ApplicationComponent.query.filter(
            db.or_(
                ApplicationComponent.data_classification.is_(None),
                ApplicationComponent.data_classification == "",
            )
        ).all()
        if not apps:
            return {"success": True, "classified": 0, "confidential": 0, "internal": 0}

        confidential = internal = 0
        for app in apps:
            is_pii = any(t in (app.name or "").lower() for t in _PII_TERMS)
            new_value = "Confidential" if is_pii else "Internal"
            app.data_classification = new_value
            if is_pii:
                confidential += 1
            else:
                internal += 1
            try:
                from app.models.audit_log import AuditLog
                AuditLog.log(
                    table_name="application_components",
                    record_id=app.id,
                    action="update",
                    old_value={"data_classification": None},
                    new_value={"data_classification": new_value},
                    user_id=user_id,
                )
            except Exception as exc:  # noqa: BLE001 — audit must not block the fix
                logger.debug("classification audit log failed for app %s: %s", app.id, exc)

        db.session.commit()
        logger.info(
            "Baseline classification applied: %d apps (%d confidential, %d internal)",
            len(apps), confidential, internal,
        )
        return {"success": True, "classified": len(apps),
                "confidential": confidential, "internal": internal}

    # ------------------------------------------------------------------ #
    # Checks                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _canonical_findings() -> List[Dict]:
        from app.models.archimate_core import ArchiMateElement

        rows = (
            db.session.query(ArchiMateElement.id, ArchiMateElement.name)
            .filter(ArchiMateElement.type == "DataObject")
            .all()
        )
        groups: Dict[str, List[str]] = {}
        for _id, name in rows:
            key = _norm(name)
            if not key:
                continue
            groups.setdefault(key, [])
            if name not in groups[key]:
                groups[key].append(name)

        out = []
        for key, names in groups.items():
            if len(names) < 2:
                continue  # need ≥2 DISTINCT names sharing a canonical core
            out.append({
                "category": "canonical",
                "severity": "high" if len(names) >= 3 else "info",
                "title": f"{len(names)} data objects likely model one entity: {names[0]}",
                "detail": (
                    "These data objects share a canonical core and probably "
                    "represent the same business entity: "
                    + ", ".join(f'"{n}"' for n in names[:6])
                    + ". Consolidate onto a single canonical DataObject to avoid "
                    "a fragmented data model."
                ),
                "evidence": "DataObject catalogue · normalised-name match",
                "action_label": "Open element catalogue",
                "action_url": "/architecture/dashboard",
            })
        # surface the worst first (more aliases = more fragmentation)
        out.sort(key=lambda f: -len(f["detail"]))
        return out

    @staticmethod
    def _semantic_findings() -> List[Dict]:
        """Semantic-similarity canonical candidates the lexical key missed
        (synonyms like Customer ~ Client). Additive; empty if embeddings
        are unavailable (the lexical check still stands)."""
        from app.models.archimate_core import ArchiMateElement

        names = [
            n for (n,) in db.session.query(ArchiMateElement.name)
            .filter(ArchiMateElement.type == "DataObject").all() if n
        ]
        pairs = _semantic_pairs(names)
        out = []
        for a, b, sim in pairs[:6]:
            # A high-confidence semantic match is the same risk as a lexical
            # one (fragmented canonical model) — severity must track the
            # stated risk, not default to info regardless of confidence.
            severity = "high" if sim >= 0.60 else "info"
            out.append({
                "category": "canonical",
                "severity": severity,
                "title": f'"{a}" and "{b}" may be the same entity (semantic match)',
                "detail": (
                    f'These data objects are not lexically similar but are '
                    f'semantically close (similarity {sim}). They likely model '
                    f'the same concept under different names — review for '
                    f'consolidation onto one canonical DataObject.'
                ),
                "evidence": f"Semantic embedding similarity ({sim})",
                "action_label": "Open element catalogue",
                "action_url": "/architecture/dashboard",
            })
        return out

    @staticmethod
    def _pii_findings() -> List[Dict]:
        from app.models.archimate_core import ArchiMateElement

        rows = (
            db.session.query(ArchiMateElement.name)
            .filter(ArchiMateElement.type == "DataObject").all()
        )
        pii = [n for (n,) in rows if n and any(t in n.lower() for t in _PII_TERMS)]
        if not pii:
            return []
        # Unclassified PII is a compliance/DPIA risk regardless of how many
        # objects carry it — one unclassified customer record is enough to
        # fail a DPIA. Escalate to critical once the exposure is broad.
        severity = "critical" if len(pii) >= 5 else "high"
        return [{
            "category": "classification",
            "severity": severity,
            "title": f"{_n(len(pii), 'data object')} likely hold{'s' if len(pii) == 1 else ''} personal/sensitive data",
            "detail": (
                "These data objects carry names suggesting PII and should be "
                "reviewed for a data classification and protection controls: "
                + ", ".join(f'"{n}"' for n in pii[:8])
                + ("…" if len(pii) > 8 else "")
                + ". Unclassified PII is a compliance and DPIA risk."
            ),
            "evidence": "DataObject catalogue · PII-term name match",
            "action_label": "Open element catalogue",
            "action_url": "/architecture/dashboard",
        }]

    @staticmethod
    def _classification_findings() -> List[Dict]:
        from app.models.application_portfolio import ApplicationComponent

        total = db.session.query(func.count(ApplicationComponent.id)).scalar() or 0
        if not total:
            return []
        classified = db.session.query(func.count(ApplicationComponent.id)).filter(
            ApplicationComponent.data_classification.isnot(None),
            ApplicationComponent.data_classification != "",
        ).scalar() or 0
        if classified == total:
            return []
        unclassified = total - classified
        pct = round(classified / total * 100)
        return [{
            "category": "classification",
            "severity": "high" if pct < 25 else "info",
            "title": f"Data classification on {classified}/{total} applications ({pct}%)",
            "detail": (
                f"{_n(unclassified, 'application')} ha{'s' if unclassified == 1 else 've'} no data classification. Without "
                "it, sensitive-data handling, retention and access policies cannot "
                "be enforced or audited."
            ),
            "evidence": "Application portfolio · data_classification coverage",
            "action_label": "Open applications",
            "action_url": "/applications/",
            # flag-to-fix: the Data Architect can apply a PII-aware baseline
            "writeback": "classify_baseline",
        }]

    @staticmethod
    def _lineage_findings() -> List[Dict]:
        from app.models.archimate_core import ArchiMateElement
        from app.models.solution_sad_models import SolutionIntegrationFlow

        data_objects = db.session.query(func.count(ArchiMateElement.id)).filter(
            ArchiMateElement.type == "DataObject"
        ).scalar() or 0
        flows = db.session.query(func.count(SolutionIntegrationFlow.id)).scalar() or 0
        if data_objects == 0:
            return []
        if flows == 0:
            return [{
                "category": "lineage",
                # Zero lineage across the whole data layer, not a partial gap —
                # impact analysis and DPIA scoping are unreliable estate-wide.
                "severity": "high",
                "title": f"No integration flows model data lineage for {_n(data_objects, 'data object')}",
                "detail": (
                    "The data layer has DataObjects but no integration flows to trace "
                    "where data is mastered, copied and consumed. Lineage gaps make "
                    "impact analysis and DPIA scoping unreliable."
                ),
                "evidence": "Integration flows · count = 0",
                "action_label": "Open solutions",
                "action_url": "/solutions/",
            }]
        return []
