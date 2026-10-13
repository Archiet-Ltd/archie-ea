"""Technical Architecture Conformance Reviewer (AI-4).

The AI Technical Architect as a reviewer: it reads a solution's design and
checks it against the platform's technical POLICY — which exists as data
(the integration-pattern catalogue's approval status, the clean-core
weighting, the ArchiMate technology layer, deployment models) — and
returns ranked findings, each with the violated policy, the evidence, and
the concrete fix.

Deterministic and sourced (Rule 11): the checks are rules over live data,
not generation — so a review is fast, free, and always reflects the
current state. Each section is fault-tolerant. Findings never fabricate.

Severity: 'critical' | 'high' | 'info'. A conformance score starts at 100
and is debited per finding by severity, floored at 0.

Interface-level checks (``check_interfaces`` / ``interface_breaches``): each
integration interface of a solution is checked against the catalogue pattern
it names, for protocol, security (authentication, encryption) and the data it
carries (format, personal data). Every breach names the rule it violates. A
value the interface does not record is reported as not recorded, with the
reason, never guessed. The last check is stored on the interface so it is
still there after a reload, and it clears when a fixed interface is checked
again.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import func

from app import db

logger = logging.getLogger(__name__)

def _n(count, singular, plural=None):
    """Big-4 copy: '3 findings' / '1 finding' — never 'finding(s)'."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"


_DEBIT = {"critical": 25, "high": 12, "info": 0}
# clean-core eroding fit types (custom build / heavy customization)
_EROSION_FITS = {"custom", "customization", "custom_development"}

# Tables whose rows carry data-architecture content. A solution linking any of
# these has said something about the data it touches; one linking none has not.
# ArchiMate puts Data Object on the Application layer and Business Object on the
# Business layer, so this cannot be answered from layer_type alone.
_DATA_TABLES = {
    "application_data_objects",
    "data_objects",
    "data_entities",
    "data_stores",
    "business_objects",
    "archimate_representations",
    "representations",
}


def _safe(name: str, fn: Callable[[], List[Dict]], checks_run: List[Dict]) -> List[Dict]:
    try:
        findings = fn()
        checks_run.append({"check": name, "available": True})
        return findings
    except Exception as exc:  # noqa: BLE001 — one bad check can't break the review
        logger.debug("conformance check %s unavailable: %s", name, exc)
        checks_run.append({"check": name, "available": False})
        return []


class ConformanceReviewer:
    """Review a solution against technical policy; return ranked findings."""

    @classmethod
    def review(cls, solution_id: int) -> Dict[str, Any]:
        """Returns {success, solution_id, score, findings, summary} or
        {success: False, error}."""
        from app.models.solution_models import Solution

        solution = db.session.get(Solution, solution_id)
        if solution is None:
            return {"success": False, "error": "Solution not found."}

        # A solution with nothing modelled has nothing to be conformant about.
        # Scoring it 100 rewards emptiness — it must be reported as unassessed,
        # never as a passing score. See M-01.
        total, _layers, _tables = cls._element_counts(solution_id)
        if total == 0:
            return {
                "success": True,
                "solution_id": solution_id,
                "solution_name": solution.name,
                "score": None,
                "unassessed": True,
                "flagged": 0,
                "findings": [],
                "summary": (
                    "Not yet assessed — the solution has no ArchiMate elements modelled, "
                    "so there is nothing to check against platform policy. This is not "
                    "the same as conformant."
                ),
            }

        findings: List[Dict[str, Any]] = []
        checks_run: List[Dict[str, Any]] = []
        findings += _safe("integration", lambda: cls._integration_findings(solution_id), checks_run)
        findings += _safe("interfaces", lambda: cls._interface_findings(solution_id), checks_run)
        findings += _safe("clean_core", lambda: cls._clean_core_findings(solution_id), checks_run)
        findings += _safe("business", lambda: cls._business_findings(solution_id), checks_run)
        findings += _safe("data", lambda: cls._data_findings(solution_id), checks_run)
        findings += _safe("technology", lambda: cls._technology_findings(solution_id), checks_run)
        findings += _safe("deployment", lambda: cls._deployment_findings(solution_id), checks_run)

        rank = {"critical": 0, "high": 1, "info": 2}
        findings.sort(key=lambda f: rank.get(f.get("severity", "info"), 3))

        unavailable_checks = [check["check"] for check in checks_run if not check["available"]]
        controls_available = not unavailable_checks
        score = None
        if controls_available:
            score = 100
            for f in findings:
                score -= _DEBIT.get(f.get("severity", "info"), 0)
            score = max(0, score)
        flagged = sum(1 for f in findings if f.get("severity") in ("critical", "high"))

        if not controls_available:
            summary = (
                "Conformance controls unavailable: "
                + ", ".join(unavailable_checks)
                + ". No score has been calculated."
            )
        elif not findings:
            summary = "No technical-conformance issues found. The design aligns with platform policy."
        else:
            summary = (
                f"{_n(len(findings), 'conformance finding')} ({flagged} needing attention). "
                "Each names the policy it breaches and the fix. Reviewed live against "
                "the integration-pattern catalogue, clean-core weighting, and coverage "
                "of the business, data and technology architectures."
            )

        return {
            "success": True,
            "solution_id": solution_id,
            "solution_name": solution.name,
            "score": score,
            "unassessed": False,
            "controls_available": controls_available,
            "unavailable_checks": unavailable_checks,
            "checks_run": checks_run,
            "flagged": flagged,
            "findings": findings,
            "summary": summary,
        }

    # ------------------------------------------------------------------ #
    # Checks                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _integration_findings(sid: int) -> List[Dict]:
        from app.models.integration_pattern import IntegrationPattern
        from app.models.solution_sad_models import SolutionIntegrationFlow

        rows = (
            db.session.query(
                IntegrationPattern.name, IntegrationPattern.approval_status,
                func.count(SolutionIntegrationFlow.id),
            )
            .join(SolutionIntegrationFlow,
                  SolutionIntegrationFlow.pattern_id == IntegrationPattern.id)
            .filter(SolutionIntegrationFlow.solution_id == sid)
            .group_by(IntegrationPattern.name, IntegrationPattern.approval_status)
            .all()
        )
        out = []
        for name, status, n in rows:
            st = (status or "").lower()
            if st == "blocked":
                out.append({
                    "category": "integration",
                    "severity": "critical",
                    "title": f"Blocked integration pattern in use: {name}",
                    "detail": (
                        f"{_n(n, 'integration flow')} use{'s' if n == 1 else ''} '{name}', which the pattern "
                        "catalogue marks BLOCKED. Replace it with an approved pattern."
                    ),
                    "evidence": "Integration-pattern catalogue · approval_status=blocked",
                    "recommendation": "Re-route these flows through an approved pattern.",
                })
            elif st in ("deprecated", "conditional"):
                out.append({
                    "category": "integration",
                    "severity": "high" if st == "deprecated" else "info",
                    "title": f"{st.title()} integration pattern: {name}",
                    "detail": (
                        f"{_n(n, 'flow')} use{'s' if n == 1 else ''} '{name}' ({st}). "
                        + ("Plan migration off it." if st == "deprecated"
                           else "Allowed only with documented justification.")
                    ),
                    "evidence": f"Integration-pattern catalogue · approval_status={st}",
                    "recommendation": ("Migrate to an approved pattern." if st == "deprecated"
                                       else "Document the conditional-use justification for the ARB."),
                })
        return out

    # ------------------------------------------------------------------ #
    # Interface-level checks against the pattern catalogue                #
    # ------------------------------------------------------------------ #

    # Rule names, as a person reads them. The key is the stable rule code
    # carried on each breach.
    INTERFACE_RULES = {
        "pattern-named": "Every interface follows a pattern from the catalogue",
        "pattern-approved": "An interface may not use a blocked or deprecated pattern",
        "pattern-protocol": "The interface uses the protocol its pattern specifies",
        "pattern-data-format": "The interface carries data in the format its pattern specifies",
        "pattern-security-auth": "The interface authenticates with a method its pattern allows",
        "pattern-security-encryption": "The interface encrypts data in transit where its pattern requires it",
        "pattern-data-personal": "Personal data travels only over a pattern that allows it",
    }

    @classmethod
    def _breach(cls, rule: str, detail: str) -> Dict[str, str]:
        return {"rule": rule, "rule_name": cls.INTERFACE_RULES[rule], "detail": detail}

    @staticmethod
    def _norm(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    @classmethod
    def interface_breaches(cls, flow, pattern) -> Dict[str, Any]:
        """Check one interface (SolutionIntegrationFlow) against its pattern.

        Returns ``{"pattern_id", "pattern_name", "breaches": [...],
        "not_recorded": [...]}``; each breach is ``{"rule", "rule_name",
        "detail"}`` and each not-recorded entry ``{"fact", "reason"}``.
        """
        breaches: List[Dict[str, str]] = []
        not_recorded: List[Dict[str, str]] = []

        if pattern is None:
            breaches.append(cls._breach(
                "pattern-named",
                "No catalogue pattern is named for this interface, so it is a "
                "point-to-point integration outside the approved patterns. Link it "
                "to an approved pattern.",
            ))
            return {"pattern_id": None, "pattern_name": None,
                    "breaches": breaches, "not_recorded": not_recorded}

        status = cls._norm(pattern.approval_status)
        if status in ("blocked", "deprecated"):
            breaches.append(cls._breach(
                "pattern-approved",
                f"'{pattern.name}' is {status} in the catalogue. Move this interface "
                "to an approved pattern.",
            ))

        if pattern.protocol:
            if not flow.protocol:
                not_recorded.append({
                    "fact": "Protocol",
                    "reason": "The interface records no protocol, so the protocol rule cannot be checked.",
                })
            elif cls._norm(flow.protocol) != cls._norm(pattern.protocol):
                breaches.append(cls._breach(
                    "pattern-protocol",
                    f"The interface uses {flow.protocol}; '{pattern.name}' specifies {pattern.protocol}.",
                ))

        if pattern.data_format:
            carried = flow.data_format or flow.message_format
            if not carried:
                not_recorded.append({
                    "fact": "Data format",
                    "reason": "The interface records no data format, so the format rule cannot be checked.",
                })
            elif cls._norm(carried) != cls._norm(pattern.data_format):
                breaches.append(cls._breach(
                    "pattern-data-format",
                    f"The interface carries {carried}; '{pattern.name}' specifies {pattern.data_format}.",
                ))

        allowed_auth = [cls._norm(a) for a in (pattern.allowed_auth_methods or []) if a]
        if allowed_auth:
            if not flow.auth_method:
                not_recorded.append({
                    "fact": "Authentication",
                    "reason": "The interface records no authentication method, so the security rule cannot be checked.",
                })
            elif cls._norm(flow.auth_method) not in allowed_auth:
                breaches.append(cls._breach(
                    "pattern-security-auth",
                    f"The interface authenticates with {flow.auth_method}; '{pattern.name}' "
                    f"allows {', '.join(pattern.allowed_auth_methods)}.",
                ))

        if pattern.requires_encryption:
            if flow.encryption_required is None:
                not_recorded.append({
                    "fact": "Encryption",
                    "reason": "The interface does not record whether it is encrypted.",
                })
            elif flow.encryption_required is False:
                breaches.append(cls._breach(
                    "pattern-security-encryption",
                    f"The interface is not encrypted; '{pattern.name}' requires encryption in transit.",
                ))

        if pattern.allows_personal_data is False and flow.contains_pii:
            breaches.append(cls._breach(
                "pattern-data-personal",
                f"The interface carries personal data; '{pattern.name}' does not allow it.",
            ))

        return {"pattern_id": pattern.id, "pattern_name": pattern.name,
                "breaches": breaches, "not_recorded": not_recorded}

    @staticmethod
    def _solution_in_tenant(solution_id: int):
        """The solution if it belongs to the caller's organisation. filter(),
        never .get(): an identity-map hit would skip the tenant predicate."""
        from app.models.solution_models import Solution

        return Solution.query.filter(Solution.id == solution_id).first()

    @staticmethod
    def _flows_with_patterns(solution_id: int, flow_ids: Optional[Iterable[int]] = None):
        from app.models.integration_pattern import IntegrationPattern
        from app.models.solution_sad_models import SolutionIntegrationFlow

        # tenant-scoping-ok: solution_id was checked against the caller's organisation by _solution_in_tenant first; flows are reached only through it
        query = SolutionIntegrationFlow.query.filter(SolutionIntegrationFlow.solution_id == solution_id)
        if flow_ids is not None:
            ids = [int(i) for i in flow_ids]
            query = query.filter(SolutionIntegrationFlow.id.in_(ids or [-1]))
        flows = query.order_by(SolutionIntegrationFlow.flow_name, SolutionIntegrationFlow.id).all()
        pattern_ids = sorted({f.pattern_id for f in flows if f.pattern_id})
        patterns = {}
        if pattern_ids:
            # tenant-scoping-ok: the pattern catalogue is shared reference data with no organisation column
            patterns = {p.id: p for p in IntegrationPattern.query.filter(IntegrationPattern.id.in_(pattern_ids)).all()}
        return flows, patterns

    @staticmethod
    def _flow_row(flow, result: Optional[Dict[str, Any]], checked_at) -> Dict[str, Any]:
        return {
            "id": flow.id,
            "name": flow.flow_name,
            "protocol": flow.protocol,
            "data_format": flow.data_format or flow.message_format,
            "auth_method": flow.auth_method,
            "contains_pii": flow.contains_pii,
            "checked_at": checked_at,
            "pattern_name": (result or {}).get("pattern_name"),
            "breaches": (result or {}).get("breaches") or [],
            "not_recorded": (result or {}).get("not_recorded") or [],
            "conforms": bool(result) and not (result or {}).get("breaches"),
        }

    @classmethod
    def check_interfaces(cls, solution_id: int, flow_ids: Optional[Iterable[int]] = None) -> Dict[str, Any]:
        """Check the chosen interfaces of a solution against the catalogue and
        store each result on the interface. ``flow_ids`` None checks them all.

        Returns ``{"success", "checked": [rows], "breaching", "conforming"}``
        or ``{"success": False, "error"}``.
        """
        if cls._solution_in_tenant(solution_id) is None:
            return {"success": False, "error": "Solution not found."}
        flows, patterns = cls._flows_with_patterns(solution_id, flow_ids)
        if not flows:
            return {"success": False, "error": "Choose at least one interface of this solution to check."}

        now = datetime.utcnow()
        rows = []
        for flow in flows:
            result = cls.interface_breaches(flow, patterns.get(flow.pattern_id))
            flow.conformance_checked_at = now
            flow.conformance_breaches = result
            rows.append(cls._flow_row(flow, result, now))
        db.session.commit()
        breaching = sum(1 for r in rows if r["breaches"])
        return {"success": True, "checked": rows, "breaching": breaching,
                "conforming": len(rows) - breaching}

    @classmethod
    def interface_results(cls, solution_id: int) -> Dict[str, Any]:
        """Every interface of a solution with its last stored check (read only).
        An interface never checked has ``checked_at`` None."""
        if cls._solution_in_tenant(solution_id) is None:
            return {"success": False, "error": "Solution not found.", "interfaces": []}
        flows, _patterns = cls._flows_with_patterns(solution_id)
        return {
            "success": True,
            "interfaces": [
                cls._flow_row(f, f.conformance_breaches if f.conformance_checked_at else None,
                              f.conformance_checked_at)
                for f in flows
            ],
        }

    @classmethod
    def _interface_findings(cls, sid: int) -> List[Dict]:
        """Live interface rules for the whole-solution review. Blocked and
        deprecated patterns are already reported by _integration_findings, so
        that rule is left out here rather than counted twice."""
        flows, patterns = cls._flows_with_patterns(sid)
        unpatterned = []
        out = []
        for flow in flows:
            result = cls.interface_breaches(flow, patterns.get(flow.pattern_id))
            rules = [b for b in result["breaches"] if b["rule"] != "pattern-approved"]
            if any(b["rule"] == "pattern-named" for b in rules):
                unpatterned.append(flow.flow_name)
                continue
            if rules:
                out.append({
                    "category": "integration",
                    "severity": "high",
                    "title": f"Interface '{flow.flow_name}' breaches its pattern",
                    "detail": " ".join(f"{b['rule_name']}: {b['detail']}" for b in rules),
                    "evidence": f"Integration-pattern catalogue · {result['pattern_name']}",
                    "recommendation": "Change the interface to meet the pattern, or request a waiver.",
                })
        if unpatterned:
            n = len(unpatterned)
            out.insert(0, {
                "category": "integration",
                "severity": "high",
                "title": f"{_n(n, 'interface')} follow{'s' if n == 1 else ''} no catalogue pattern",
                "detail": (
                    "Point-to-point integrations outside the approved patterns: "
                    + ", ".join(unpatterned[:10]) + ("…" if n > 10 else "") + "."
                ),
                "evidence": "Integration interfaces · no pattern named",
                "recommendation": "Link each interface to an approved pattern from the catalogue.",
            })
        return out

    @staticmethod
    def _clean_core_findings(sid: int) -> List[Dict]:
        from app.models.solution_models import SolutionFitGapEntry

        rows = dict(
            db.session.query(SolutionFitGapEntry.fit_type, func.count())
            .filter(SolutionFitGapEntry.solution_id == sid)
            .group_by(SolutionFitGapEntry.fit_type).all()
        )
        if not rows:
            return []
        erosion = sum(n for ft, n in rows.items() if ft and ft.lower() in _EROSION_FITS)
        total = sum(rows.values())
        if not erosion:
            return []
        pct = round(erosion / total * 100)
        return [{
            "category": "clean_core",
            "severity": "high" if pct >= 30 else "info",
            "title": f"{_n(erosion, 'fit-gap entry', 'fit-gap entries')} erode{'s' if erosion == 1 else ''} clean core",
            "detail": (
                f"{erosion} of {total} fit-gap entries ({pct}%) are custom build or "
                "heavy customization — the lowest clean-core weighting. Prefer "
                "standard/configuration or a governed extension."
            ),
            "evidence": "Fit-gap register · fit_type in (custom, customization, custom_development)",
            "recommendation": "Reclassify or redesign these toward standard/configuration/extension.",
        }]

    @staticmethod
    def _element_counts(sid: int):
        """(total elements, per-layer counter, set of element tables) for a solution."""
        from app.models.solution_models import SolutionArchiMateElement

        rows = (
            db.session.query(
                SolutionArchiMateElement.layer_type,
                SolutionArchiMateElement.element_table,
                func.count(SolutionArchiMateElement.id),
            )
            .filter(SolutionArchiMateElement.solution_id == sid)
            .group_by(
                SolutionArchiMateElement.layer_type,
                SolutionArchiMateElement.element_table,
            )
            .all()
        )
        total = sum(n for _, _, n in rows)
        # layer_type is written in both casings across the codebase, so fold it.
        layers = {(layer or "").strip().lower() for layer, _, _ in rows}
        tables = {(table or "").strip().lower() for _, table, _ in rows}
        return total, layers, tables

    @staticmethod
    def _business_findings(sid: int) -> List[Dict]:
        """TOGAF Phase B: a design must say which business behaviour it changes."""
        total, layers, _ = ConformanceReviewer._element_counts(sid)
        if total == 0:
            return []  # nothing modelled yet — not a conformance issue
        if "business" in layers:
            return []
        return [{
            "category": "business",
            "severity": "high",
            "title": "No business-layer elements — the design does not say what business behaviour changes",
            "detail": (
                f"The solution models {_n(total, 'ArchiMate element')} but none on the "
                "Business layer (process, function, service, actor, role). Without it "
                "there is no way to review which processes are affected, who owns them, "
                "or which business services degrade if the change goes wrong. TOGAF "
                "Phase B is not optional for an architecture of record."
            ),
            "evidence": "ArchiMate elements · layer_type=business = 0",
            "recommendation": (
                "Attach the business processes, functions or services this solution "
                "changes, and name their owners."
            ),
        }]

    @staticmethod
    def _data_findings(sid: int) -> List[Dict]:
        """TOGAF Phase C (Data): a design must name the data it creates and consumes."""
        total, _, tables = ConformanceReviewer._element_counts(sid)
        if total == 0:
            return []
        if tables & _DATA_TABLES:
            return []
        return [{
            "category": "data",
            "severity": "high",
            "title": "No data architecture — the design names no data object it creates or consumes",
            "detail": (
                f"The solution models {_n(total, 'ArchiMate element')} but none of them "
                "is a data object or business object. Without the data in scope, the "
                "design cannot be assessed for classification, personal data, retention "
                "or lineage — the questions a data protection review asks first."
            ),
            "evidence": "ArchiMate elements · no data-bearing element_table linked",
            "recommendation": (
                "Attach the data objects or business objects this solution creates, "
                "reads or updates, and set their classification."
            ),
        }]

    @staticmethod
    def _technology_findings(sid: int) -> List[Dict]:
        from app.models.solution_models import SolutionArchiMateElement

        total = (
            db.session.query(func.count(SolutionArchiMateElement.id))
            .filter(SolutionArchiMateElement.solution_id == sid).scalar() or 0
        )
        if total == 0:
            return []  # nothing modelled yet — not a conformance issue
        tech = (
            db.session.query(func.count(SolutionArchiMateElement.id))
            .filter(SolutionArchiMateElement.solution_id == sid)
            .filter(SolutionArchiMateElement.layer_type.ilike("technology")).scalar() or 0
        )
        if tech > 0:
            return []
        return [{
            "category": "technology",
            "severity": "high",
            "title": "No technology-layer elements — design lacks a technical underpinning",
            "detail": (
                f"The solution models {_n(total, 'ArchiMate element')} but none on the "
                "Technology layer (nodes, system software, deployment). A design "
                "without a technology underpinning is incomplete and unbuildable as-specified."
            ),
            "evidence": "ArchiMate elements · layer_type=technology = 0",
            "recommendation": "Add the technology nodes/platforms the application components run on.",
        }]

    @staticmethod
    def _deployment_findings(sid: int) -> List[Dict]:
        from app.models.application_portfolio import ApplicationComponent
        from app.models.solution_models import solution_applications

        rows = (
            db.session.query(
                ApplicationComponent.deployment_model, func.count()
            )
            .join(solution_applications,
                  solution_applications.c.application_component_id == ApplicationComponent.id)
            .filter(solution_applications.c.solution_id == sid)
            .group_by(ApplicationComponent.deployment_model)
            .all()
        )
        total = sum(n for _, n in rows)
        if total == 0:
            return []
        missing = sum(n for dm, n in rows if not dm)
        if not missing:
            return []
        return [{
            "category": "deployment",
            "severity": "info",
            "title": f"{_n(missing, 'application')} ha{'s' if missing == 1 else 've'} no recorded deployment model",
            "detail": (
                f"{missing} of {total} linked applications lack a deployment model "
                "(cloud / on-prem / hybrid). Technology governance and TCO need it."
            ),
            "evidence": "Application portfolio · deployment_model is null",
            "recommendation": "Set the deployment model on each linked application.",
        }]
