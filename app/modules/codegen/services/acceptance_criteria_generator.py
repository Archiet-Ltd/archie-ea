"""Generate acceptance criteria from ArchiMate Motivation elements + Business Rules.

Infers testable, structured acceptance criteria from:
1. Goals → measurable outcomes with pass/fail thresholds
2. Requirements → GIVEN/WHEN/THEN behavioral criteria
3. Constraints → compliance verification checks
4. Business Rules (SolutionRule JSON IR) → behavioral criteria from trigger/condition/action

Output format per criterion:
{
    "id": "AC-001",
    "title": "Order approval threshold",
    "source": {"type": "requirement", "name": "...", "element_id": 123},
    "given": "Order amount is $15,000 (above $10,000 threshold)",
    "when": "Order is submitted",
    "then": "Approval request is created and assigned to manager role",
    "verification": "API: POST /api/orders with amount=15000 → 202, GET /api/approval-requests → contains order_id",
    "priority": "MUST",
    "category": "business_rule",  # business_rule | performance | security | compliance | data_quality
}

These criteria are:
- Written for BAs (human-readable, no code)
- Structured for ScenarioGenerator (machine-parseable for Playwright tests)
- Exported to ARCHITECTURE.md (context for LLMs working on the code)
"""
import logging
import re
from app.models.archimate_element_types import application_kind_types

logger = logging.getLogger(__name__)


class AcceptanceCriteriaGenerator:
    """Generate acceptance criteria from architecture motivation + business rules."""

    def generate(self, solution_id: int) -> list[dict]:
        """Generate all acceptance criteria for a solution.

        Pulls from: ArchiMate motivation elements, SolutionRule definitions,
        solution NFRs, and ACM property constraints.
        """
        criteria = []
        ac_counter = [0]  # mutable counter for closure

        def _next_id():
            ac_counter[0] += 1
            return f"AC-{ac_counter[0]:03d}"

        # 1. From ArchiMate Motivation elements
        criteria.extend(self._from_motivation_elements(solution_id, _next_id))

        # 2. From Business Rules (SolutionRule JSON IR)
        criteria.extend(self._from_business_rules(solution_id, _next_id))

        # 3. From NFR properties on architecture elements
        criteria.extend(self._from_nfr_properties(solution_id, _next_id))

        return criteria

    def _from_motivation_elements(self, solution_id: int, next_id) -> list[dict]:
        """Infer ACs from Goals, Requirements, Constraints."""
        try:
            from app.models.solution_archimate_element import SolutionArchiMateElement
            from app.models.archimate_core import ArchiMateElement

            links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
            element_ids = [link.element_id for link in links]
            if not element_ids:
                return []

            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids)
            ).all()
            spec_map = {link.element_id: (link.spec_data or {}) for link in links}

            criteria = []
            for e in elements:
                spec = spec_map.get(e.id, {})

                if e.type == "Goal":
                    criteria.append(self._goal_to_ac(e, spec, next_id))

                elif e.type == "Outcome":
                    criteria.append(self._outcome_to_ac(e, spec, next_id))

                elif e.type == "Requirement":
                    criteria.append(self._requirement_to_ac(e, spec, next_id))

                elif e.type == "Constraint":
                    criteria.append(self._constraint_to_ac(e, spec, next_id))

                elif e.type == "Principle":
                    criteria.append(self._principle_to_ac(e, spec, next_id))

            return [c for c in criteria if c]  # filter None

        except Exception as exc:
            logger.warning("Failed to generate ACs from motivation elements: %s", exc)
            return []

    def _goal_to_ac(self, element, spec, next_id) -> dict:
        """Goal → measurable acceptance criterion."""
        name = element.name or ""
        desc = element.description or ""

        # Try to extract numeric targets from goal name/description
        numbers = re.findall(r'(\d+(?:\.\d+)?)\s*(%|min|sec|hour|day|ms)', name + " " + desc)
        if numbers:
            metric_value, metric_unit = numbers[0]
            verification = f"Measure: {name}. Target: {metric_value}{metric_unit}. Run under realistic load for 5 minutes."
        else:
            verification = f"Manual verification: confirm that '{name}' is achieved. Document evidence."

        return {
            "id": next_id(),
            "title": name,
            "source": {"type": "goal", "name": name, "element_id": element.id},
            "given": "System is deployed and operational with production-like data",
            "when": f"The goal '{name}' is evaluated",
            "then": desc if desc else f"'{name}' is demonstrably achieved",
            "verification": verification,
            "priority": "SHOULD",
            "category": "performance" if numbers else "business_rule",
        }

    def _outcome_to_ac(self, element, spec, next_id) -> dict:
        """Outcome → measurable result criterion."""
        name = element.name or ""
        return {
            "id": next_id(),
            "title": f"Outcome: {name}",
            "source": {"type": "outcome", "name": name, "element_id": element.id},
            "given": "Solution has been in use for the measurement period",
            "when": "Outcome metrics are collected",
            "then": element.description or f"Outcome '{name}' is measurable and trending positive",
            "verification": f"Dashboard or report showing '{name}' metric over time",
            "priority": "SHOULD",
            "category": "business_rule",
        }

    def _requirement_to_ac(self, element, spec, next_id) -> dict:
        """Requirement → GIVEN/WHEN/THEN behavioral criterion."""
        name = element.name or ""
        desc = element.description or ""
        priority_raw = spec.get("priority")
        if isinstance(priority_raw, dict):
            priority = priority_raw.get("value", "MUST")
        elif isinstance(priority_raw, str):
            priority = priority_raw
        else:
            priority = "MUST"

        # Try to infer GIVEN/WHEN/THEN from the requirement text
        given, when, then = self._infer_gwt(name, desc)

        # G12: emit a pytest test stub instead of human prose so verification strings
        # are actually runnable. Prose "Functional test: verify X end-to-end" is never
        # executed; a stub with given/when/then structure can be implemented and run.
        import re as _re
        _snake = _re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:60]
        _verification_stub = (
            f"def test_{_snake}(client):\n"
            f"    # GIVEN {given}\n"
            f"    # WHEN {when}\n"
            f"    # THEN {then}\n"
            f"    raise NotImplementedError('AC not yet implemented')\n"
        )

        return {
            "id": next_id(),
            "title": name,
            "source": {"type": "requirement", "name": name, "element_id": element.id},
            "given": given,
            "when": when,
            "then": then,
            "verification": _verification_stub,
            "priority": priority.upper() if priority else "MUST",
            "category": "business_rule",
        }

    def _constraint_to_ac(self, element, spec, next_id) -> dict:
        """Constraint → compliance verification criterion."""
        name = element.name or ""
        desc = element.description or ""

        # Categorize constraint
        name_lower = name.lower()
        if any(kw in name_lower for kw in ("gdpr", "hipaa", "sox", "pci", "compliance", "regulation")):
            category = "compliance"
            verification = f"Compliance check: verify '{name}' controls are implemented. Document evidence for audit."
        elif any(kw in name_lower for kw in ("encrypt", "auth", "access", "security", "password", "token")):
            category = "security"
            verification = f"Security test: verify '{name}' is enforced. Attempt bypass — must fail."
        elif any(kw in name_lower for kw in ("budget", "cost", "price", "resource")):
            category = "business_rule"
            verification = f"Verify '{name}' is respected in all configurations."
        else:
            category = "compliance"
            verification = f"Verify constraint '{name}' cannot be violated. Test both valid and invalid scenarios."

        return {
            "id": next_id(),
            "title": f"Constraint: {name}",
            "source": {"type": "constraint", "name": name, "element_id": element.id},
            "given": "System is deployed and accessible",
            "when": f"An action that could violate '{name}' is attempted",
            "then": f"The system prevents the violation. {desc}" if desc else f"The system enforces '{name}'",
            "verification": verification,
            "priority": "MUST",
            "category": category,
        }

    def _principle_to_ac(self, element, spec, next_id) -> dict:
        """Principle → architecture compliance criterion."""
        name = element.name or ""
        return {
            "id": next_id(),
            "title": f"Principle: {name}",
            "source": {"type": "principle", "name": name, "element_id": element.id},
            "given": "Codebase is reviewed",
            "when": f"Architecture principle '{name}' is evaluated",
            "then": element.description or f"All components adhere to '{name}'",
            "verification": f"Code review: verify all components follow '{name}'. No exceptions without documented ADR.",
            "priority": "SHOULD",
            "category": "compliance",
        }

    def _from_business_rules(self, solution_id: int, next_id) -> list[dict]:
        """Generate ACs from SolutionRule JSON IR definitions."""
        try:
            from app.modules.codegen.models import SolutionRule

            rules = SolutionRule.query.filter_by(
                solution_id=solution_id, is_active=True
            ).all()

            criteria = []
            for rule in rules:
                rd = rule.rule_definition or {}
                trigger = rd.get("trigger", {})
                conditions = rd.get("conditions", [])
                actions = rd.get("actions", [])

                entity = trigger.get("entity", "record")
                event = trigger.get("event", "action")

                # Build GIVEN from conditions
                given_parts = []
                for cond in conditions:
                    field = cond.get("field", "field")
                    op = cond.get("operator", "matches")
                    val = cond.get("value", "condition")
                    if op == "greater_than":
                        given_parts.append(f"{entity}.{field} > {val}")
                    elif op == "is_empty":
                        given_parts.append(f"{entity}.{field} is empty")
                    elif op == "equals":
                        given_parts.append(f"{entity}.{field} = '{val}'")
                    else:
                        given_parts.append(f"{entity}.{field} {op} {val}")

                given = ", ".join(given_parts) if given_parts else f"A {entity} exists"

                # Build WHEN from trigger
                event_map = {
                    "before_create": f"a new {entity} is being created",
                    "after_create": f"a new {entity} has been created",
                    "before_update": f"an existing {entity} is being updated",
                    "after_update": f"an existing {entity} has been updated",
                    "on_schedule": f"the scheduled check runs for {entity}",
                }
                when = event_map.get(event, f"{event} occurs on {entity}")

                # Build THEN from actions
                then_parts = []
                for action in actions:
                    atype = action.get("type", "")
                    if atype == "block":
                        msg = action.get("message", "operation blocked")
                        then_parts.append(f"the operation is blocked with error: '{msg}'")
                    elif atype == "require_approval":
                        role = action.get("role", "approver")
                        then_parts.append(f"an approval request is created for {role} role")
                    elif atype == "notify":
                        channel = action.get("channel", "email")
                        role = action.get("role", "user")
                        then_parts.append(f"{channel} notification is sent to {role}")
                    elif atype == "update_field":
                        field = action.get("field", "field")
                        value = action.get("value", "value")
                        then_parts.append(f"{entity}.{field} is set to '{value}'")
                    elif atype == "compute":
                        target = action.get("target_field", "field")
                        then_parts.append(f"{entity}.{target} is recalculated")
                    else:
                        then_parts.append(f"action '{atype}' is executed")

                then = " AND ".join(then_parts) if then_parts else "the rule action fires"

                # Verification — API-level test
                if actions and actions[0].get("type") == "block":
                    verification = f"API: POST /api/{entity.lower()}s with condition met → 422 or 400 with error message"
                elif actions and actions[0].get("type") == "require_approval":
                    verification = f"API: POST /api/{entity.lower()}s → 202, GET /api/approval-requests → contains new entry"
                elif actions and actions[0].get("type") == "notify":
                    verification = "API: trigger action, verify notification log/webhook fired"
                else:
                    verification = f"API: trigger {event} on {entity}, verify {then}"

                criteria.append({
                    "id": next_id(),
                    "title": rule.name,
                    "source": {"type": "business_rule", "name": rule.name, "rule_id": rule.id},
                    "given": given,
                    "when": when,
                    "then": then,
                    "verification": verification,
                    "priority": "MUST",
                    "category": "business_rule",
                })

                # Also generate the negative case (condition NOT met)
                if conditions:
                    neg_given_parts = []
                    for cond in conditions:
                        field = cond.get("field", "field")
                        op = cond.get("operator", "")
                        val = cond.get("value", "")
                        if op == "greater_than":
                            neg_given_parts.append(f"{entity}.{field} <= {val}")
                        elif op == "is_empty":
                            neg_given_parts.append(f"{entity}.{field} has a value")
                        elif op == "equals":
                            neg_given_parts.append(f"{entity}.{field} != '{val}'")
                        else:
                            neg_given_parts.append(f"{entity}.{field} does NOT {op} {val}")

                    criteria.append({
                        "id": next_id(),
                        "title": f"{rule.name} — negative case",
                        "source": {"type": "business_rule", "name": rule.name, "rule_id": rule.id},
                        "given": ", ".join(neg_given_parts),
                        "when": when,
                        "then": "the operation succeeds normally (rule does not fire)",
                        "verification": f"API: POST /api/{entity.lower()}s with condition NOT met → 201 success",
                        "priority": "MUST",
                        "category": "business_rule",
                    })

            return criteria

        except Exception as exc:
            logger.warning("Failed to generate ACs from business rules: %s", exc)
            return []

    def _from_nfr_properties(self, solution_id: int, next_id) -> list[dict]:
        """Generate ACs from NFR-related ACM properties (availability, scalability, etc.)."""
        try:
            from app.models.solution_archimate_element import SolutionArchiMateElement
            from app.models.archimate_core import ArchiMateElement

            links = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).all()
            element_ids = [link.element_id for link in links]
            if not element_ids:
                return []

            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids),
                ArchiMateElement.type.in_(application_kind_types()),
            ).all()

            criteria = []
            for e in elements:
                acm = e.acm_properties or {}

                # Availability target
                avail = acm.get("availability_target")
                if avail:
                    val = avail.get("value", avail) if isinstance(avail, dict) else avail
                    if val and val != "TBD":
                        criteria.append({
                            "id": next_id(),
                            "title": f"{e.name}: Availability {val}",
                            "source": {"type": "nfr", "name": e.name, "element_id": e.id},
                            "given": f"{e.name} is deployed to production",
                            "when": "Availability is measured over a 30-day period",
                            "then": f"Uptime is >= {val}",
                            "verification": f"Monitoring: {e.name} health endpoint returns 200 for >= {val} of measurement window",
                            "priority": "MUST",
                            "category": "performance",
                        })

                # Data classification
                data_class = acm.get("data_classification")
                if data_class:
                    val = data_class.get("value", data_class) if isinstance(data_class, dict) else data_class
                    if val and val != "TBD" and val in ("confidential", "restricted"):
                        criteria.append({
                            "id": next_id(),
                            "title": f"{e.name}: {val} data handling",
                            "source": {"type": "nfr", "name": e.name, "element_id": e.id},
                            "given": f"{e.name} handles {val} data",
                            "when": "Data is stored or transmitted",
                            "then": f"All {val} data is encrypted at rest (AES-256) and in transit (TLS 1.2+)",
                            "verification": f"Security scan: verify encryption on {e.name} storage + API endpoints use HTTPS",
                            "priority": "MUST",
                            "category": "security",
                        })

            return criteria

        except Exception as exc:
            logger.warning("Failed to generate ACs from NFR properties: %s", exc)
            return []

    @staticmethod
    def _infer_gwt(name: str, description: str) -> tuple[str, str, str]:
        """Try to infer Given/When/Then from requirement text."""
        text = f"{name}. {description}".strip()

        # Look for "when X then Y" patterns
        when_match = re.search(r'[Ww]hen\s+(.+?)(?:\s*,\s*|\s+then\s+)', text)
        then_match = re.search(r'[Tt]hen\s+(.+?)(?:\.|$)', text)

        if when_match and then_match:
            return ("Preconditions are met", when_match.group(1).strip(), then_match.group(1).strip())

        # Look for "must/should" patterns
        must_match = re.search(r'(?:must|should|shall)\s+(.+?)(?:\.|$)', text, re.IGNORECASE)
        if must_match:
            action = must_match.group(1).strip()
            return ("System is operational", f"The requirement '{name}' is evaluated", action)

        # Default
        return (
            "System is operational and accessible",
            f"A user interacts with the feature: '{name}'",
            description if description else f"'{name}' works as specified",
        )
