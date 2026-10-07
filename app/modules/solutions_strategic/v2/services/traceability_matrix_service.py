"""Traceability matrix service — end-to-end chain from drivers to technology."""
import logging


logger = logging.getLogger(__name__)


class TraceabilityMatrixService:
    """Build full traceability matrix for a solution.

    Traces the chain: Driver -> Goal -> Capability -> Requirement ->
    Application -> Vendor Product -> Technology (ArchiMate elements).
    """

    COLUMNS = [
        "Drivers",
        "Goals",
        "Capabilities",
        "Requirements",
        "Applications",
        "Vendor Products",
        "Technology",
    ]

    def get_matrix(self, solution_id):
        """Build full traceability matrix for a solution.

        Returns dict with columns, rows, coverage percentages, and gap list.
        """
        from app.models.solution_models import Solution

        solution = Solution.query.get_or_404(solution_id)

        drivers = self._get_drivers(solution)
        goals = self._get_goals(solution)
        capabilities = self._get_capabilities(solution_id)
        requirements = self._get_requirements(solution_id, solution)
        applications = self._get_applications(solution)
        vendor_products = self._get_vendor_products(solution)
        technology = self._get_technology_elements(solution_id)

        # Build rows: each row is one entity from the longest column,
        # with corresponding entities filled across.
        max_len = max(
            len(drivers),
            len(goals),
            len(capabilities),
            len(requirements),
            len(applications),
            len(vendor_products),
            len(technology),
            1,  # avoid zero
        )

        rows = []
        for i in range(max_len):
            tech_entry = technology[i] if i < len(technology) else None
            row = {
                "driver": drivers[i] if i < len(drivers) else None,
                "goal": goals[i] if i < len(goals) else None,
                "capability": capabilities[i] if i < len(capabilities) else None,
                "requirement": requirements[i] if i < len(requirements) else None,
                "application": applications[i] if i < len(applications) else None,
                "vendor_product": vendor_products[i] if i < len(vendor_products) else None,
                "technology": tech_entry,
                "archimate_element": tech_entry,
            }
            # Compute row status based on how many of the 7 real columns are populated
            # (archimate_element is an alias for technology, so exclude it)
            filled = sum(
                1 for k in ("driver", "goal", "capability", "requirement", "application", "vendor_product", "technology")
                if row.get(k) is not None
            )
            if filled >= 5:
                row["status"] = "complete"
            elif filled >= 2:
                row["status"] = "partial"
            else:
                row["status"] = "gap"
            rows.append(row)

        # Coverage: percentage of columns that have at least one entity
        total_columns = 7
        populated = sum(
            1
            for lst in [drivers, goals, capabilities, requirements, applications, vendor_products, technology]
            if len(lst) > 0
        )
        overall_coverage = round((populated / total_columns) * 100)

        coverage = {
            "drivers": 100 if drivers else 0,
            "goals": 100 if goals else 0,
            "capabilities": 100 if capabilities else 0,
            "requirements": 100 if requirements else 0,
            "applications": 100 if applications else 0,
            "vendor_products": 100 if vendor_products else 0,
            "technology": 100 if technology else 0,
            "overall": overall_coverage,
        }

        # Gaps: columns with no entities
        gaps = []
        column_map = {
            "Drivers": drivers,
            "Goals": goals,
            "Capabilities": capabilities,
            "Requirements": requirements,
            "Applications": applications,
            "Vendor Products": vendor_products,
            "Technology": technology,
        }
        for col_name, col_data in column_map.items():
            if not col_data:
                gaps.append({
                    "column": col_name,
                    "message": f"No {col_name.lower()} linked to this solution",
                })

        summary = {
            "total_entities": sum(
                len(lst)
                for lst in [drivers, goals, capabilities, requirements, applications, vendor_products, technology]
            ),
            "populated_columns": populated,
            "total_columns": total_columns,
            "drivers": len(drivers),
            "goals": len(goals),
            "capabilities": len(capabilities),
            "requirements": len(requirements),
            "applications": len(applications),
            "vendor_products": len(vendor_products),
            "archimate_elements": len(technology),
        }

        return {
            "columns": self.COLUMNS,
            "rows": rows,
            "coverage": coverage,
            "gaps": gaps,
            "summary": summary,
        }

    def _get_drivers(self, solution):
        """Get drivers linked via analysis session -> problem definition."""
        results = []
        session = getattr(solution, "analysis_session", None)
        if session:
            problem = getattr(session, "problem_definition", None)
            if problem:
                from app.models.solution_architect_models import SolutionDriver

                drivers = SolutionDriver.query.filter_by(problem_id=problem.id).all()
                for d in drivers:
                    results.append({
                        "id": d.id,
                        "name": d.name,
                        "type": d.driver_type.value if d.driver_type else None,
                        "impact_level": d.impact_level,
                    })
        return results

    def _get_goals(self, solution):
        """Get goals linked via analysis session -> problem definition."""
        results = []
        session = getattr(solution, "analysis_session", None)
        if session:
            problem = getattr(session, "problem_definition", None)
            if problem:
                from app.models.solution_architect_models import SolutionGoal

                goals = SolutionGoal.query.filter_by(problem_id=problem.id).all()
                for g in goals:
                    results.append({
                        "id": g.id,
                        "name": g.name,
                        "priority": g.priority,
                    })
        return results

    def _get_capabilities(self, solution_id):
        """Get capabilities linked via SolutionCapabilityMapping."""
        from app.models.solution_models import SolutionCapabilityMapping

        mappings = SolutionCapabilityMapping.query.filter_by(solution_id=solution_id).all()
        results = []
        for m in mappings:
            cap_name = None
            if m.capability_id:
                try:
                    from app.models.business_capability import BusinessCapability

                    cap = BusinessCapability.query.get(m.capability_id)
                    if cap:
                        cap_name = cap.name
                except Exception:
                    cap_name = f"Capability #{m.capability_id}"
            results.append({
                "id": m.id,
                "capability_id": m.capability_id,
                "name": cap_name or f"Capability #{m.capability_id}",
                "support_level": m.support_level,
                "coverage_percentage": m.coverage_percentage,
            })
        return results

    def _get_requirements(self, solution_id, solution):
        """Get requirements linked directly to solution or via analysis session."""
        from app.models.solution_architect_models import SolutionRequirement

        # Direct link via solution_id
        reqs = SolutionRequirement.query.filter(
            SolutionRequirement.solution_id == solution_id,
            SolutionRequirement.deleted_at.is_(None),
        ).all()

        # Also check via analysis session problem definition
        session = getattr(solution, "analysis_session", None)
        if session:
            problem = getattr(session, "problem_definition", None)
            if problem:
                session_reqs = SolutionRequirement.query.filter(
                    SolutionRequirement.problem_id == problem.id,
                    SolutionRequirement.deleted_at.is_(None),
                ).all()
                existing_ids = {r.id for r in reqs}
                for r in session_reqs:
                    if r.id not in existing_ids:
                        reqs.append(r)

        results = []
        for r in reqs:
            results.append({
                "id": r.id,
                "name": r.name,
                "requirement_type": r.requirement_type.value if r.requirement_type else r.req_type,
                "priority": r.priority,
                "is_mandatory": r.is_mandatory,
            })
        return results

    def _get_applications(self, solution):
        """Get applications linked via solution_applications junction."""
        results = []
        try:
            apps = solution.applications.all()
            for a in apps:
                results.append({
                    "id": a.id,
                    "name": getattr(a, "name", None) or getattr(a, "application_name", f"App #{a.id}"),
                    "status": getattr(a, "lifecycle_status", None),
                })
        except Exception as exc:
            logger.warning("Failed to load applications for solution %s: %s", solution.id, exc)
        return results

    def _get_vendor_products(self, solution):
        """Get vendor products linked via solution_vendor_products junction."""
        results = []
        try:
            products = solution.vendor_products.all()
            for vp in products:
                results.append({
                    "id": vp.id,
                    "name": getattr(vp, "name", None) or getattr(vp, "product_name", f"Product #{vp.id}"),
                    "vendor_name": getattr(vp, "vendor_name", None),
                })
        except Exception as exc:
            logger.warning("Failed to load vendor products for solution %s: %s", solution.id, exc)
        return results

    def _get_technology_elements(self, solution_id):
        """Get technology ArchiMate elements linked via SolutionArchiMateElement."""
        from app.models.solution_models import SolutionArchiMateElement

        elements = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id,
            layer_type="technology",
        ).all()
        results = []
        for e in elements:
            results.append({
                "id": e.id,
                "element_id": e.element_id,
                "name": e.element_name or f"Element #{e.element_id}",
                "element_table": e.element_table,
                "relationship_type": e.relationship_type,
            })

        # If no technology-only elements, include all ArchiMate elements
        if not results:
            all_elements = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id,
            ).all()
            for e in all_elements:
                results.append({
                    "id": e.id,
                    "element_id": e.element_id,
                    "name": e.element_name or f"Element #{e.element_id}",
                    "element_table": e.element_table,
                    "layer_type": e.layer_type,
                    "relationship_type": e.relationship_type,
                })
        return results

    @staticmethod
    def trace_from_here_url(goal_id: int) -> str:
        """Return the permanent-link URL for a goal's traceability chain.

        The "trace from here" action on the traceability matrix renders this
        URL so a strategy officer can open the full goal trace in one click.
        """
        return f"/api/v1/motivation/goals/{goal_id}/trace"
