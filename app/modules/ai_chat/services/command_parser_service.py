"""
Slash command parser and executor for AI Chat.

Provides /help, /compare, /analyze, /arb, /health, /gaps, and /portfolio-check
commands that return real data from the platform database.
"""

import logging

from app import db

logger = logging.getLogger(__name__)


class CommandParserService:
    """Parse and execute slash commands in AI chat messages."""

    # Every entry states whether the command writes ("mutates"). A command with
    # no such key is treated as a write, so a new write command is gated by
    # default and a read-only one has to say so.
    COMMANDS = {
        "/help": {"description": "Show available commands", "args": None, "mutates": False},
        "/compare": {
            "description": "Compare two solutions",
            "args": "<solution1> <solution2>",
            "mutates": False,
        },
        "/analyze": {
            "description": "Switch domain and get summary",
            "args": "<domain>",
            "mutates": False,
        },
        "/arb": {
            "description": "Check ARB readiness for solution",
            "args": "<solution_name>",
            "mutates": False,
        },
        "/health": {
            "description": "Show portfolio health summary",
            "args": None,
            "mutates": False,
        },
        "/gaps": {
            "description": "Run gap analysis (by domain or solution name)",
            "args": None,
            "mutates": False,
        },
        "/portfolio-check": {
            "description": "Impact propagation across ArchiMate elements",
            "args": "<element_name>",
            "mutates": False,
        },
        "/generate-from-capabilities": {
            "description": "Generate ArchiMate elements from a solution's linked capabilities",
            "args": "<solution_name>",
            "mutates": True,
        },
        "/link-capability": {
            "description": "Link a capability to a solution",
            "args": "<capability-name> to <solution-name>",
            "mutates": True,
        },
        "/quality": {
            "description": "Show ArchiMate quality score for a solution",
            "args": "<solution_name>",
            "mutates": False,
        },
        "/dependencies": {
            "description": "Show what depends on an element (downstream impact)",
            "args": "<element_name>",
            "mutates": False,
        },
    }

    def is_command(self, message):
        """Check if message starts with /."""
        if not message or not isinstance(message, str):
            return False
        stripped = message.strip()
        return stripped.startswith("/") and len(stripped) > 1

    def parse(self, message):
        """Parse command and args. Returns {command, args, valid, error}."""
        stripped = message.strip()
        parts = stripped.split(None, 1)  # Split on first whitespace
        command = parts[0].lower()
        raw_args = parts[1] if len(parts) > 1 else ""

        if command not in self.COMMANDS:
            return {
                "command": command,
                "args": [],
                "valid": False,
                "error": (
                    f"Unknown command: `{command}`. "
                    f"Type `/help` to see available commands."
                ),
            }

        # Parse arguments based on command
        args = [a.strip() for a in raw_args.split() if a.strip()] if raw_args else []

        # Validate required args
        cmd_def = self.COMMANDS[command]
        if cmd_def["args"] is not None and not args:
            return {
                "command": command,
                "args": [],
                "valid": False,
                "error": (
                    f"Command `{command}` requires arguments: "
                    f"`{command} {cmd_def['args']}`"
                ),
            }

        return {
            "command": command,
            "args": args,
            "valid": True,
            "error": None,
        }

    def execute(self, command, args, user_id, domain):
        """Execute command and return response dict with {response, domain, command_type}."""
        handlers = {
            "/help": self._handle_help,
            "/compare": self._handle_compare,
            "/analyze": self._handle_analyze,
            "/arb": self._handle_arb,
            "/health": self._handle_health,
            "/gaps": self._handle_gaps,
            "/portfolio-check": self._handle_portfolio_check,
            "/generate-from-capabilities": self._handle_generate_from_capabilities,
            "/link-capability": self._handle_link_capability,
            "/quality": self._handle_quality,
            "/dependencies": self._handle_dependencies,
        }

        handler = handlers.get(command)
        if handler and self.is_mutating(command):
            refusal = self._refuse_write(command, args, user_id, domain)
            if refusal:
                return refusal
        if not handler:
            return {
                "response": f"Unknown command: `{command}`",
                "domain": domain,
                "command_type": "error",
            }

        try:
            return handler(args, user_id, domain)
        except Exception as e:
            logger.error("Error executing command %s: %s", command, e, exc_info=True)
            return {
                "response": f"Error executing `{command}`: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    def is_mutating(self, command):
        """Whether a command writes. One that does not say otherwise is a write."""
        return bool(self.COMMANDS.get(command, {}).get("mutates", True))

    def _refuse_write(self, command, args, user_id, domain):
        """The write-permission gate every mutating command passes first.

        The approval-queue exemption for slash commands is unchanged: a person
        typed the command, so it is not queued. It is still that person's write,
        so it needs the same write access every other write surface requires.
        Returns None when allowed, else the refusal response.
        """
        from app.modules.ai_chat.tools.executor import refuse_unless_can_write

        refused = refuse_unless_can_write(
            user_id, command.lstrip("/"), {"args": " ".join(args or [])},
            via="slash_command",
        )
        if refused is None:
            return None
        return {
            "response": refused["error"],
            "domain": domain,
            "command_type": "permission_denied",
            "success": False,
            "permission_denied": True,
        }

    def get_help_text(self):
        """Return formatted help text listing all commands."""
        lines = ["**Available Slash Commands**\n"]
        for cmd, info in self.COMMANDS.items():
            usage = f"`{cmd}`" if info["args"] is None else f"`{cmd} {info['args']}`"
            lines.append(f"- {usage} — {info['description']}")
        lines.append("\nType a command to get started.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _handle_help(self, args, user_id, domain):
        return {
            "response": self.get_help_text(),
            "domain": domain,
            "command_type": "help",
        }

    def _handle_compare(self, args, user_id, domain):
        """Compare two solutions by name (partial match)."""
        if len(args) < 2:
            return {
                "response": "Usage: `/compare <solution1> <solution2>`\nProvide two solution names to compare.",
                "domain": domain,
                "command_type": "error",
            }

        name1 = args[0]
        name2 = args[1]

        try:
            from app.models.solution_models import Solution

            sol1 = Solution.query.filter(
                Solution.name.ilike(f"%{name1}%")
            ).first()
            sol2 = Solution.query.filter(
                Solution.name.ilike(f"%{name2}%")
            ).first()

            if not sol1 and not sol2:
                return {
                    "response": f"No solutions found matching `{name1}` or `{name2}`.",
                    "domain": domain,
                    "command_type": "compare",
                }
            if not sol1:
                return {
                    "response": f"No solution found matching `{name1}`.",
                    "domain": domain,
                    "command_type": "compare",
                }
            if not sol2:
                return {
                    "response": f"No solution found matching `{name2}`.",
                    "domain": domain,
                    "command_type": "compare",
                }

            def _sol_row(sol):
                return (
                    f"| {sol.name} | {sol.adm_phase or 'N/A'} | "
                    f"{sol.status or 'N/A'} | {sol.complexity_level or 'N/A'} | "
                    f"{sol.business_domain or 'N/A'} |"
                )

            table = (
                "**Solution Comparison**\n\n"
                "| Property | Solution 1 | Solution 2 |\n"
                "|----------|-----------|------------|\n"
                f"| **Name** | {sol1.name} | {sol2.name} |\n"
                f"| **ADM Phase** | {sol1.adm_phase or 'N/A'} | {sol2.adm_phase or 'N/A'} |\n"
                f"| **Status** | {sol1.status or 'N/A'} | {sol2.status or 'N/A'} |\n"
                f"| **Type** | {sol1.solution_type or 'N/A'} | {sol2.solution_type or 'N/A'} |\n"
                f"| **Complexity** | {sol1.complexity_level or 'N/A'} | {sol2.complexity_level or 'N/A'} |\n"
                f"| **Domain** | {sol1.business_domain or 'N/A'} | {sol2.business_domain or 'N/A'} |\n"
                f"| **Owner** | {sol1.solution_owner or 'N/A'} | {sol2.solution_owner or 'N/A'} |"
            )

            return {
                "response": table,
                "domain": domain,
                "command_type": "compare",
            }
        except Exception as e:
            logger.error("Compare command error: %s", e, exc_info=True)
            return {
                "response": f"Error comparing solutions: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    def _handle_analyze(self, args, user_id, domain):
        """Switch domain and return summary metrics."""
        target_domain = args[0].lower() if args else "general"

        valid_domains = {
            "general": "General — cross-domain overview",
            "architecture": "Architecture — ArchiMate elements and relationships",
            "technology": "Technology — application portfolio and tech stack",
            "business_capability": "Business Capability — capability model and maturity",
            "gap_analysis": "Gap Analysis — current vs. target state gaps",
            "vendor_intelligence": "Vendor Intelligence — vendor assessments and contracts",
            "compliance": "Compliance — regulatory and standards alignment",
        }

        if target_domain not in valid_domains:
            domains_list = "\n".join(
                f"- `{k}` — {v}" for k, v in valid_domains.items()
            )
            return {
                "response": (
                    f"Unknown domain: `{target_domain}`.\n\n"
                    f"**Available domains:**\n{domains_list}"
                ),
                "domain": domain,
                "command_type": "analyze",
            }

        # Gather metrics for the target domain
        metrics = self._get_domain_metrics(target_domain)

        response_lines = [
            f"**Domain: {valid_domains[target_domain]}**\n",
            "| Metric | Count |",
            "|--------|-------|",
        ]
        for label, count in metrics.items():
            response_lines.append(f"| {label} | {count} |")

        response_lines.append(f"\nChat domain switched to `{target_domain}`.")

        return {
            "response": "\n".join(response_lines),
            "domain": target_domain,
            "command_type": "analyze",
        }

    def _handle_arb(self, args, user_id, domain):
        """Check ARB readiness for a solution."""
        solution_name = " ".join(args)

        try:
            from app.models.solution_models import Solution

            sol = Solution.query.filter(
                Solution.name.ilike(f"%{solution_name}%")
            ).first()

            if not sol:
                return {
                    "response": f"No solution found matching `{solution_name}`.",
                    "domain": domain,
                    "command_type": "arb",
                }

            # Run phase gate validation on current phase
            current_phase = sol.adm_phase or "A"
            validation = sol.validate_phase_gate(current_phase)

            status_icon = "PASS" if validation.get("valid") else "FAIL"
            errors = validation.get("errors", [])
            warnings = validation.get("warnings", [])

            lines = [
                f"**ARB Readiness Check: {sol.name}**\n",
                f"- **Current Phase:** {current_phase}",
                f"- **Status:** {sol.status or 'N/A'}",
                f"- **Phase Gate:** {status_icon}",
            ]

            if errors:
                lines.append("\n**Blocking Issues:**")
                for err in errors:
                    lines.append(f"- {err}")

            if warnings:
                lines.append("\n**Warnings:**")
                for warn in warnings:
                    lines.append(f"- {warn}")

            if not errors and not warnings:
                lines.append(
                    "\nAll phase gate requirements met. "
                    "Solution is ready for ARB review."
                )

            # CAP-018: Include ARB readiness checks table
            try:
                readiness = sol.arb_readiness
                lines.append("\n**Readiness Checks:**\n")
                lines.append("| Check | Status | Required |")
                lines.append("|-------|--------|----------|")
                for check in readiness.get("checks", []):
                    chk_status = "PASS" if check["passed"] else "FAIL"
                    req_label = "Yes" if check.get("required") else "No"
                    label = check["label"]
                    msg = check.get("message")
                    if msg and not check["passed"]:
                        label = f"{label} - {msg}"
                    lines.append(f"| {label} | {chk_status} | {req_label} |")
                pct = readiness.get("percentage", 0)
                can_submit = readiness.get("can_submit", False)
                lines.append(
                    f"\n**Readiness Score:** {pct}% "
                    f"({readiness.get('score', 0)}/{readiness.get('max_score', 0)} mandatory) "
                    f"— {'Ready for ARB' if can_submit else 'Not ready'}"
                )
            except Exception:
                logger.debug("Could not render readiness checks table")

            return {
                "response": "\n".join(lines),
                "domain": domain,
                "command_type": "arb",
            }
        except Exception as e:
            logger.error("ARB command error: %s", e, exc_info=True)
            return {
                "response": f"Error checking ARB readiness: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    def _handle_health(self, args, user_id, domain):
        """Show portfolio health summary from real DB counts."""
        stats = {}

        try:
            from app.models.application_portfolio import ApplicationComponent

            stats["Applications"] = ApplicationComponent.query.count()
        except Exception:
            stats["Applications"] = "unavailable"

        try:
            from app.models.unified_capability import UnifiedCapability

            stats["Capabilities"] = UnifiedCapability.query.count()
        except Exception:
            stats["Capabilities"] = "unavailable"

        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            stats["Vendors"] = VendorOrganization.query.count()
        except Exception:
            stats["Vendors"] = "unavailable"

        try:
            from app.models.solution_models import Solution

            total_solutions = Solution.query.count()
            stats["Solutions"] = total_solutions

            # Phase distribution
            phase_counts = {}
            solutions = Solution.query.with_entities(
                Solution.adm_phase, db.func.count(Solution.id)
            ).group_by(Solution.adm_phase).all()
            for phase, count in solutions:
                phase_counts[phase or "N/A"] = count
            stats["Solutions by Phase"] = (
                ", ".join(f"{p}: {c}" for p, c in sorted(phase_counts.items()))
                if phase_counts
                else "N/A"
            )
        except Exception:
            stats["Solutions"] = "unavailable"

        try:
            from app.models.archimate_core import ArchiMateElement

            stats["ArchiMate Elements"] = ArchiMateElement.query.count()
        except Exception:
            try:
                from app.models.models import ArchiMateElement

                stats["ArchiMate Elements"] = ArchiMateElement.query.count()
            except Exception:
                stats["ArchiMate Elements"] = "unavailable"

        lines = ["**Portfolio Health Summary**\n"]
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for label, value in stats.items():
            lines.append(f"| {label} | {value} |")

        return {
            "response": "\n".join(lines),
            "domain": domain,
            "command_type": "health",
        }

    def _handle_gaps(self, args, user_id, domain):
        """Return gap analysis summary using existing data.

        Usage:
        - /gaps              — overall gap summary across all capabilities
        - /gaps <solution>   — weighted gap analysis for a specific solution's capabilities
        """
        solution_name = " ".join(args) if args else None

        if solution_name:
            return self._handle_gaps_for_solution(solution_name, domain)

        try:
            from app.models.capability_gap_analysis import (
                CapabilityGapAnalysis,
                CapabilityGapDetail,
            )

            total_analyses = CapabilityGapAnalysis.query.count()
            total_gaps = CapabilityGapDetail.query.count()

            # Severity breakdown
            severity_counts = {}
            severity_rows = CapabilityGapDetail.query.with_entities(
                CapabilityGapDetail.gap_severity,
                db.func.count(CapabilityGapDetail.id),
            ).group_by(CapabilityGapDetail.gap_severity).all()
            for severity, count in severity_rows:
                severity_counts[severity or "unclassified"] = count

            lines = [
                "**Gap Analysis Summary**\n",
                f"- **Total Analyses:** {total_analyses}",
                f"- **Total Gaps Identified:** {total_gaps}",
            ]

            if severity_counts:
                lines.append("\n**By Severity:**")
                lines.append("| Severity | Count |")
                lines.append("|----------|-------|")
                for sev, cnt in sorted(
                    severity_counts.items(),
                    key=lambda x: x[1],
                    reverse=True,
                ):
                    lines.append(f"| {sev} | {cnt} |")

            if total_gaps == 0:
                lines.append(
                    "\nNo gaps recorded yet. Run a gap analysis from the "
                    "Capability Planning module to populate this data."
                )

            return {
                "response": "\n".join(lines),
                "domain": domain,
                "command_type": "gaps",
            }
        except Exception as e:
            logger.error("Gaps command error: %s", e, exc_info=True)
            return {
                "response": f"Error retrieving gap analysis data: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    def _handle_gaps_for_solution(self, solution_name, domain):
        """Run weighted gap analysis scoped to a solution's linked capabilities."""
        try:
            from app.models.solution_models import Solution, SolutionCapabilityMapping
            from app.models.capability_gap_analysis import CapabilityGapDetail
            from app.models.business_capabilities import BusinessCapability

            solution = Solution.query.filter(
                Solution.name.ilike(f"%{solution_name}%")
            ).first()

            if not solution:
                return {
                    "response": f"No solution found matching `{solution_name}`.",
                    "domain": domain,
                    "command_type": "gaps",
                }

            cap_mappings = SolutionCapabilityMapping.query.filter(
                SolutionCapabilityMapping.solution_id == solution.id
            ).all()

            if not cap_mappings:
                return {
                    "response": (
                        f"**Gap Analysis: {solution.name}**\n\n"
                        f"No capability mappings found for this solution. "
                        f"Map capabilities first via the Solution Detail page."
                    ),
                    "domain": domain,
                    "command_type": "gaps",
                }

            capability_ids = list({m.capability_id for m in cap_mappings})

            capabilities = {
                c.id: c
                for c in BusinessCapability.query.filter(
                    BusinessCapability.id.in_(capability_ids)
                ).all()
            }

            gap_details = CapabilityGapDetail.query.filter(
                CapabilityGapDetail.capability_id.in_(capability_ids)
            ).all()

            severity_weights = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            level_multipliers = {1: 3.0, 2: 2.0, 3: 1.0, 4: 0.75, 5: 0.5}
            importance_multipliers = {
                "critical": 2.0, "high": 1.5, "medium": 1.0, "low": 0.5,
            }

            gap_rows = []
            for gap in gap_details:
                cap = capabilities.get(gap.capability_id)
                cap_level = cap.level if cap else 3
                cap_importance = getattr(cap, "strategic_importance", None) or "medium"
                cap_name = cap.name if cap else f"Capability #{gap.capability_id}"

                base = severity_weights.get(gap.gap_severity or "medium", 2)
                level_mult = level_multipliers.get(cap_level, 1.0)
                importance_mult = importance_multipliers.get(cap_importance, 1.0)
                weighted = round(base * level_mult * importance_mult, 2)

                gap_rows.append({
                    "capability_name": cap_name,
                    "capability_level": f"L{cap_level}",
                    "strategic_importance": cap_importance,
                    "gap_severity": gap.gap_severity or "medium",
                    "weighted_severity": weighted,
                    "coverage_pct": gap.coverage_percentage or 0,
                })

            gap_rows.sort(key=lambda r: r["weighted_severity"], reverse=True)

            critical = [r for r in gap_rows if r["capability_level"] in ("L1", "L2")]
            refinement = [r for r in gap_rows if r["capability_level"] not in ("L1", "L2")]

            lines = [
                f"**Gap Analysis: {solution.name}**\n",
                f"- **Mapped Capabilities:** {len(capability_ids)}",
                f"- **Gaps Found:** {len(gap_details)}",
            ]

            def _render_group(title, rows):
                if not rows:
                    return
                lines.append(f"\n**{title}**")
                lines.append(
                    "| Capability | Level | Importance | Severity | Weighted | Coverage |"
                )
                lines.append(
                    "|------------|-------|------------|----------|----------|----------|"
                )
                for r in rows:
                    lines.append(
                        f"| {r['capability_name'][:30]} "
                        f"| {r['capability_level']} "
                        f"| {r['strategic_importance']} "
                        f"| {r['gap_severity']} "
                        f"| {r['weighted_severity']} "
                        f"| {r['coverage_pct']}% |"
                    )

            _render_group("Critical Gaps (address immediately)", critical)
            _render_group("Refinement Opportunities", refinement)

            if not gap_rows:
                lines.append(
                    "\nNo gap details recorded for these capabilities. "
                    "Run a gap analysis from the Capability Planning module."
                )

            return {
                "response": "\n".join(lines),
                "domain": domain,
                "command_type": "gaps",
            }
        except Exception as e:
            logger.error("Gaps for solution error: %s", e, exc_info=True)
            return {
                "response": f"Error running gap analysis: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    def _handle_portfolio_check(self, args, user_id, domain):
        """Show impact propagation for an ArchiMate element across the portfolio.

        Finds the element by fuzzy name match, traces 1st and 2nd degree
        relationships, and groups impacted elements by ArchiMate layer.
        """
        element_name = " ".join(args)

        try:
            from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

            # Fuzzy search by name (case-insensitive LIKE)
            element = ArchiMateElement.query.filter(
                ArchiMateElement.name.ilike(f"%{element_name}%")
            ).first()

            if not element:
                return {
                    "response": f"No ArchiMate element found matching `{element_name}`.",
                    "domain": domain,
                    "command_type": "portfolio-check",
                }

            # --- 1st degree: direct relationships ---
            outgoing_rels = ArchiMateRelationship.query.filter_by(
                source_id=element.id
            ).all()
            incoming_rels = ArchiMateRelationship.query.filter_by(
                target_id=element.id
            ).all()

            first_degree_ids = set()
            rel_type_counts = {}
            for rel in outgoing_rels:
                first_degree_ids.add(rel.target_id)
                rtype = rel.type or "Unknown"
                rel_type_counts[rtype] = rel_type_counts.get(rtype, 0) + 1
            for rel in incoming_rels:
                first_degree_ids.add(rel.source_id)
                rtype = rel.type or "Unknown"
                rel_type_counts[rtype] = rel_type_counts.get(rtype, 0) + 1

            # Remove self if present
            first_degree_ids.discard(element.id)

            # --- 2nd degree: relationships of 1st-degree elements ---
            second_degree_ids = set()
            if first_degree_ids:
                second_rels = ArchiMateRelationship.query.filter(
                    db.or_(
                        ArchiMateRelationship.source_id.in_(first_degree_ids),
                        ArchiMateRelationship.target_id.in_(first_degree_ids),
                    )
                ).all()
                for rel in second_rels:
                    second_degree_ids.add(rel.source_id)
                    second_degree_ids.add(rel.target_id)
                # Remove the root element and all 1st-degree elements
                second_degree_ids -= first_degree_ids
                second_degree_ids.discard(element.id)

            # --- Load impacted elements and group by layer ---
            all_impacted_ids = first_degree_ids | second_degree_ids
            layer_groups = {}  # layer -> list of (name, type, degree)
            if all_impacted_ids:
                impacted_elements = ArchiMateElement.query.filter(
                    ArchiMateElement.id.in_(all_impacted_ids)
                ).all()
                for el in impacted_elements:
                    layer = el.layer or "Unknown"
                    degree = 1 if el.id in first_degree_ids else 2
                    layer_groups.setdefault(layer, []).append(
                        (el.name, el.type or "N/A", degree)
                    )

            # --- Build response ---
            lines = [
                f"**Portfolio Impact Check: {element.name}**\n",
                f"- **Element Type:** {element.type or 'N/A'}",
                f"- **Layer:** {element.layer or 'N/A'}",
                f"- **Outgoing Relationships:** {len(outgoing_rels)}",
                f"- **Incoming Relationships:** {len(incoming_rels)}",
                f"- **1st Degree Impacted:** {len(first_degree_ids)}",
                f"- **2nd Degree Impacted:** {len(second_degree_ids)}",
                f"- **Total Impacted Elements:** {len(all_impacted_ids)}",
            ]

            # Relationship type breakdown
            if rel_type_counts:
                lines.append("\n**Relationship Types:**")
                lines.append("| Type | Count |")
                lines.append("|------|-------|")
                for rtype, cnt in sorted(
                    rel_type_counts.items(), key=lambda x: x[1], reverse=True
                ):
                    lines.append(f"| {rtype} | {cnt} |")

            # Impact by layer
            if layer_groups:
                lines.append("\n**Impact by Layer:**")
                lines.append("| Layer | Elements | 1st Degree | 2nd Degree |")
                lines.append("|-------|----------|------------|------------|")
                for layer_name in sorted(layer_groups.keys()):
                    items = layer_groups[layer_name]
                    total = len(items)
                    deg1 = sum(1 for _, _, d in items if d == 1)
                    deg2 = sum(1 for _, _, d in items if d == 2)
                    lines.append(f"| {layer_name} | {total} | {deg1} | {deg2} |")

                # Detail top impacted elements per layer (up to 5 per layer)
                lines.append("\n**Impacted Elements Detail:**")
                for layer_name in sorted(layer_groups.keys()):
                    items = layer_groups[layer_name]
                    # Sort: 1st degree first, then by name
                    items_sorted = sorted(items, key=lambda x: (x[2], x[0]))
                    lines.append(f"\n*{layer_name} Layer:*")
                    for name, etype, degree in items_sorted[:5]:
                        deg_label = "1st" if degree == 1 else "2nd"
                        lines.append(f"- {name} ({etype}) — {deg_label} degree")
                    remaining = len(items_sorted) - 5
                    if remaining > 0:
                        lines.append(f"- ... and {remaining} more")

            if not outgoing_rels and not incoming_rels:
                lines.append(
                    "\nThis element has no relationships. "
                    "Use the ArchiMate Composer to define relationships."
                )

            return {
                "response": "\n".join(lines),
                "domain": domain,
                "command_type": "portfolio-check",
            }
        except Exception as e:
            logger.error("Portfolio-check command error: %s", e, exc_info=True)
            return {
                "response": f"Error checking portfolio impact: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    def _handle_generate_from_capabilities(self, args, user_id, domain):
        """Generate ArchiMate elements from a solution's linked capabilities."""
        refusal = self._refuse_write("/generate-from-capabilities", args, user_id, domain)
        if refusal:
            return refusal

        solution_name = " ".join(args)

        try:
            from app.models.solution_models import (
                Solution,
                SolutionCapabilityMapping,
            )
            from app.models.business_capabilities import BusinessCapability
            from app.models.archimate_core import ArchiMateElement
            from app.models.solution_archimate_element import SolutionArchiMateElement

            sol = Solution.query.filter(
                Solution.name.ilike(f"%{solution_name}%")
            ).first()

            if not sol:
                return {
                    "response": (
                        f"Solution '{solution_name}' not found. "
                        f"Try `/generate-from-capabilities <exact name>`"
                    ),
                    "domain": domain,
                    "command_type": "generate-from-capabilities",
                }

            # Load linked capabilities via direct solution_id link
            cap_mappings = SolutionCapabilityMapping.query.filter_by(
                solution_id=sol.id
            ).all()

            # Also check problem_id path if solution has analysis_session_id
            if sol.analysis_session_id:
                from app.models.solution_architect_models import (
                    SolutionProblemDefinition,
                )

                problem = SolutionProblemDefinition.query.filter_by(
                    session_id=sol.analysis_session_id
                ).first()
                if problem:
                    problem_mappings = SolutionCapabilityMapping.query.filter_by(
                        problem_id=problem.id
                    ).all()
                    # Merge, avoiding duplicates by capability_id
                    existing_cap_ids = {m.capability_id for m in cap_mappings}
                    for pm in problem_mappings:
                        if pm.capability_id not in existing_cap_ids:
                            cap_mappings.append(pm)
                            existing_cap_ids.add(pm.capability_id)

            if not cap_mappings:
                return {
                    "response": (
                        f"No capabilities linked to '{sol.name}'. "
                        f"Link capabilities first using /link-capability "
                        f"or the solution detail page."
                    ),
                    "domain": domain,
                    "command_type": "generate-from-capabilities",
                }

            # Map capability types to ArchiMate layer/type pairs
            archimate_type_map = {
                "Business": [
                    ("Business", "BusinessFunction"),
                    ("Business", "BusinessProcess"),
                ],
                "Application": [
                    ("Application", "ApplicationFunction"),
                    ("Application", "ApplicationComponent"),
                ],
                "Technology": [
                    ("Technology", "TechnologyService"),
                ],
            }

            # Governance note (reconciles config.py's REQUIRE_AI_APPROVAL default
            # and the mutating-tool approval queue in
            # app/modules/ai_chat/services/agent_runner.py): like /link-capability
            # above, /generate-from-capabilities is parsed verbatim from the
            # user's own typed chat message — deterministic, not LLM-initiated.
            # A prompt-injected document cannot type a slash command, so this
            # write proceeds directly and is deliberately exempt from the
            # approval queue regardless of REQUIRE_AI_APPROVAL.
            generated_rows = []
            for mapping in cap_mappings:
                cap = BusinessCapability.query.get(mapping.capability_id)
                if not cap:
                    continue

                # Determine layer from capability level
                cap_level = getattr(cap, "level", None) or 1
                if cap_level <= 1:
                    layer_types = archimate_type_map["Business"]
                elif cap_level == 2:
                    layer_types = archimate_type_map["Application"]
                else:
                    layer_types = archimate_type_map["Technology"]

                for layer, elem_type in layer_types:
                    element_name = f"{cap.name}"

                    # Check if element already exists
                    existing = ArchiMateElement.query.filter(
                        ArchiMateElement.name == element_name,
                        ArchiMateElement.type == elem_type,
                    ).first()

                    if existing:
                        element = existing
                    else:
                        element = ArchiMateElement(
                            name=element_name,
                            type=elem_type,
                            layer=layer,
                            description=(
                                f"Auto-generated from capability: {cap.name}"
                            ),
                        )
                        db.session.add(element)
                        db.session.flush()

                    # Link to solution if not already linked
                    existing_link = SolutionArchiMateElement.query.filter_by(
                        solution_id=sol.id,
                        element_id=element.id,
                    ).first()

                    if not existing_link:
                        link = SolutionArchiMateElement(
                            solution_id=sol.id,
                            element_id=element.id,
                            element_role="ai_derived",
                        )
                        db.session.add(link)

                    generated_rows.append(
                        (layer, elem_type, element_name, cap.name)
                    )

            db.session.commit()

            # Build markdown response
            lines = [
                f'**Generated ArchiMate Elements for "{sol.name}"**\n',
                f"From {len(cap_mappings)} linked capabilities:\n",
                "| Layer | Type | Element Name | Source Capability |",
                "|-------|------|-------------|-------------------|",
            ]
            for layer, elem_type, elem_name, cap_name in generated_rows:
                lines.append(
                    f"| {layer} | {elem_type} | {elem_name} | {cap_name} |"
                )

            lines.append(
                f"\nTotal: {len(generated_rows)} elements generated "
                f"and linked to solution."
            )

            return {
                "response": "\n".join(lines),
                "domain": domain,
                "command_type": "generate-from-capabilities",
            }
        except Exception as e:
            logger.error(
                "Generate-from-capabilities command error: %s", e, exc_info=True
            )
            db.session.rollback()
            return {
                "response": (
                    f"Error generating ArchiMate elements: {str(e)}"
                ),
                "domain": domain,
                "command_type": "error",
            }

    def _handle_link_capability(self, args, user_id, domain):
        """Link a business capability to a solution by name."""
        refusal = self._refuse_write("/link-capability", args, user_id, domain)
        if refusal:
            return refusal

        raw_input = " ".join(args)

        if " to " not in raw_input:
            return {
                "response": (
                    "Usage: `/link-capability <capability-name> to <solution-name>`\n"
                    "Example: `/link-capability Customer Management to CRM Consolidation`"
                ),
                "domain": domain,
                "command_type": "link-capability",
            }

        parts = raw_input.split(" to ", 1)
        cap_name = parts[0].strip()
        sol_name = parts[1].strip()

        if not cap_name or not sol_name:
            return {
                "response": (
                    "Usage: `/link-capability <capability-name> to <solution-name>`\n"
                    "Example: `/link-capability Customer Management to CRM Consolidation`"
                ),
                "domain": domain,
                "command_type": "link-capability",
            }

        try:
            from app.models.business_capabilities import BusinessCapability
            from app.models.solution_models import Solution, SolutionCapabilityMapping

            capabilities = BusinessCapability.query.filter(
                BusinessCapability.name.ilike(f"%{cap_name}%")
            ).limit(5).all()

            if not capabilities:
                return {
                    "response": f"No capability found matching `{cap_name}`.",
                    "domain": domain,
                    "command_type": "link-capability",
                }

            solutions = Solution.query.filter(
                Solution.name.ilike(f"%{sol_name}%")
            ).limit(5).all()

            if not solutions:
                return {
                    "response": f"No solution found matching `{sol_name}`.",
                    "domain": domain,
                    "command_type": "link-capability",
                }

            if len(capabilities) > 1:
                names = "\n".join(
                    f"{i}. {c.name}" for i, c in enumerate(capabilities[:3], 1)
                )
                return {
                    "response": (
                        f"Multiple matches found for capability `{cap_name}`. "
                        f"Did you mean:\n{names}"
                    ),
                    "domain": domain,
                    "command_type": "link-capability",
                }

            if len(solutions) > 1:
                names = "\n".join(
                    f"{i}. {s.name}" for i, s in enumerate(solutions[:3], 1)
                )
                return {
                    "response": (
                        f"Multiple matches found for solution `{sol_name}`. "
                        f"Did you mean:\n{names}"
                    ),
                    "domain": domain,
                    "command_type": "link-capability",
                }

            cap = capabilities[0]
            sol = solutions[0]

            existing = SolutionCapabilityMapping.query.filter_by(
                solution_id=sol.id, capability_id=cap.id
            ).first()

            if existing:
                return {
                    "response": f"'{cap.name}' is already linked to '{sol.name}'.",
                    "domain": domain,
                    "command_type": "link-capability",
                }

            # Governance note (reconciles config.py's REQUIRE_AI_APPROVAL default
            # and the mutating-tool approval queue in
            # app/modules/ai_chat/services/agent_runner.py): /link-capability is
            # parsed verbatim from the user's own typed chat message, not proposed
            # by the LLM. It is not an AI-initiated write — a prompt-injected
            # document cannot type a slash command — so it executes directly and
            # is deliberately exempt from the approval queue regardless of
            # REQUIRE_AI_APPROVAL. (This used to check the flag here and return a
            # "submitted for review" message without ever actually submitting
            # anything for review — a fabricated-success response with no backing
            # queue entry. Removed rather than wired up, per the exemption above.)

            mapping = SolutionCapabilityMapping(
                solution_id=sol.id,
                capability_id=cap.id,
                support_level="required",
                created_by_id=user_id,
            )
            db.session.add(mapping)
            db.session.commit()

            total = SolutionCapabilityMapping.query.filter_by(
                solution_id=sol.id
            ).count()

            return {
                "response": (
                    f"Linked '{cap.name}' to '{sol.name}'. "
                    f"Total capabilities: {total}"
                ),
                "domain": domain,
                "command_type": "link-capability",
            }
        except Exception as e:
            logger.error("Link-capability command error: %s", e, exc_info=True)
            return {
                "response": f"Error linking capability: {str(e)}",
                "domain": domain,
                "command_type": "error",
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_domain_metrics(self, target_domain):
        """Return key metrics dict for a given domain."""
        metrics = {}

        if target_domain in ("general", "technology"):
            try:
                from app.models.application_portfolio import ApplicationComponent

                metrics["Applications"] = ApplicationComponent.query.count()
            except Exception:
                metrics["Applications"] = "unavailable"

        if target_domain in ("general", "business_capability"):
            try:
                from app.models.unified_capability import UnifiedCapability

                metrics["Capabilities"] = UnifiedCapability.query.count()
            except Exception:
                metrics["Capabilities"] = "unavailable"

        if target_domain in ("general", "vendor_intelligence"):
            try:
                from app.models.vendor.vendor_organization import VendorOrganization

                metrics["Vendors"] = VendorOrganization.query.count()
            except Exception:
                metrics["Vendors"] = "unavailable"

        if target_domain in ("general", "architecture"):
            try:
                from app.models.archimate_core import ArchiMateElement

                metrics["ArchiMate Elements"] = ArchiMateElement.query.count()
            except Exception:
                try:
                    from app.models.models import ArchiMateElement

                    metrics["ArchiMate Elements"] = ArchiMateElement.query.count()
                except Exception:
                    metrics["ArchiMate Elements"] = "unavailable"

        if target_domain in ("general", "gap_analysis"):
            try:
                from app.models.capability_gap_analysis import CapabilityGapDetail

                metrics["Identified Gaps"] = CapabilityGapDetail.query.count()
            except Exception:
                metrics["Identified Gaps"] = "unavailable"

        if target_domain == "compliance":
            try:
                from app.models.compliance_models import ComplianceGap

                metrics["Compliance Gaps"] = ComplianceGap.query.count()
            except Exception:
                metrics["Compliance Gaps"] = "unavailable"

        # Always include solutions count
        try:
            from app.models.solution_models import Solution

            metrics["Solutions"] = Solution.query.count()
        except Exception:
            metrics["Solutions"] = "unavailable"

        return metrics

    # ── Wave 10: /quality — ArchiMate quality score ─────────────────────

    def _handle_quality(self, args, user_id, domain):
        """Return ArchiMate quality score for a solution."""
        if not args:
            return {"response": "Usage: `/quality <solution_name>`", "domain": domain, "command_type": "quality"}

        from app.models.solution_models import Solution

        sol = Solution.query.filter(Solution.name.ilike(f"%{args.strip()}%")).first()
        if not sol:
            return {"response": f"Solution not found: `{args}`", "domain": domain, "command_type": "quality"}

        try:
            from app.modules.solutions_strategic.v2.routes.solution_archimate_routes import _calculate_quality_score

            qs = _calculate_quality_score(sol.id)
            lines = [
                f"**ArchiMate Quality Score: {sol.name}**\n",
                "| Metric | Score |",
                "|--------|-------|",
                f"| **Overall** | **{qs['overall']}%** |",
                f"| Completeness | {qs['completeness']}% ({len(qs['layers_covered'])}/6 layers) |",
                f"| Traceability | {qs['traceability']}% (elements with relationships) |",
                f"| Validity | {qs['validity']}% ({qs['valid_relationships']} valid, {qs['invalid_relationships']} invalid) |",
                f"\n**{qs['element_count']}** elements, **{qs['relationship_count']}** relationships",
                f"\nLayers: {', '.join(qs['layers_covered'])}",
            ]
            if qs['layers_missing']:
                lines.append(f"Missing: {', '.join(qs['layers_missing'])}")
            if qs['invalid_details']:
                lines.append("\n**Invalid relationships (top 5):**")
                for d in qs['invalid_details'][:5]:
                    lines.append(f"- `{d}`")

            return {"response": "\n".join(lines), "domain": "architecture", "command_type": "quality"}
        except Exception as e:
            return {"response": f"Error calculating quality: {e}", "domain": domain, "command_type": "error"}

    # ── Wave 7: /dependencies — downstream impact analysis ──────────────

    def _handle_dependencies(self, args, user_id, domain):
        """Show downstream dependents of an element."""
        if not args:
            return {"response": "Usage: `/dependencies <element_name>`", "domain": domain, "command_type": "dependencies"}

        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app import db

        el = ArchiMateElement.query.filter(
            db.func.lower(ArchiMateElement.name) == args.strip().lower()
        ).first()
        if not el:
            # Fuzzy match
            el = ArchiMateElement.query.filter(
                ArchiMateElement.name.ilike(f"%{args.strip()}%")
            ).first()
        if not el:
            return {"response": f"Element not found: `{args}`", "domain": domain, "command_type": "dependencies"}

        # BFS traversal (3 hops)
        visited = {el.id}
        frontier = {el.id}
        dependents = []

        for hop in range(3):
            if not frontier:
                break
            rels = ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id.in_(frontier)
            ).all()
            next_frontier = set()
            for r in rels:
                if r.target_id not in visited:
                    visited.add(r.target_id)
                    next_frontier.add(r.target_id)
                    tgt = db.session.get(ArchiMateElement, r.target_id)
                    if tgt:
                        dependents.append({
                            "name": tgt.name, "type": tgt.type,
                            "layer": tgt.layer, "rel": r.type, "hop": hop + 1,
                        })
            frontier = next_frontier

        if not dependents:
            return {
                "response": f"**{el.name}** ({el.type}, {el.layer}) has no downstream dependents.",
                "domain": "architecture", "command_type": "dependencies",
            }

        lines = [
            f"**Downstream dependents of {el.name}** ({el.type}, {el.layer})\n",
            "| Element | Type | Layer | Via | Hops |",
            "|---------|------|-------|-----|------|",
        ]
        for d in dependents[:20]:
            lines.append(f"| {d['name']} | {d['type']} | {d['layer']} | {d['rel']} | {d['hop']} |")
        lines.append(f"\n**{len(dependents)}** total downstream elements across {max(d['hop'] for d in dependents)} hops")

        return {"response": "\n".join(lines), "domain": "architecture", "command_type": "dependencies"}
