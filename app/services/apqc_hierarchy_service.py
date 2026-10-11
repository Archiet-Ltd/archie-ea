"""
Enhanced APQC PCF Hierarchy Browser & Intelligent Mapping Service

Provides comprehensive APQC Process Classification Framework (PCF) hierarchy navigation,
intelligent mapping with rationale, and parent process linking for enterprise architecture.

Features:
- Full 5 - level APQC hierarchy path display
- Interactive tree browser with search and filtering
- Auto-parent process linking on mapping
- Match rationale with keyword analysis
- Industry-specific APQC variants
- Process metrics and benchmarking data
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


from app import db
from app.models.apqc_process import APQCProcess

logger = logging.getLogger(__name__)


@dataclass
class APQCMatchRationale:
    """Rationale for APQC process matching."""

    primary_reason: str
    keyword_matches: List[str]
    semantic_similarity: float
    vendor_indicators: List[str]
    context_clues: List[str]
    confidence_factors: Dict[str, float]


@dataclass
class EnhancedAPQCMatch:
    """Enhanced APQC match with hierarchy and rationale."""

    process_id: int
    process_code: str
    process_name: str
    level: int

    # Hierarchy chain
    hierarchy_path: List[Dict[str, Any]]  # [{code: "10.0", name: "Marketing"}, ...]
    parent_ids: List[int]  # [parent_id, grandparent_id, ...]

    # Match explanation
    match_rationale: APQCMatchRationale
    similarity_score: float
    confidence: str

    # Process metadata
    process_category: str
    industry_domain: str
    benchmark_available: bool
    process_maturity: int

    def to_dict(self) -> Dict[str, Any]:
        """Serialize match to JSON-serializable dictionary."""
        rationale = self.match_rationale
        return {
            "process_id": self.process_id,
            "process_code": self.process_code,
            "process_name": self.process_name,
            "level": self.level,
            "hierarchy_path": self.hierarchy_path,
            "parent_ids": self.parent_ids,
            "similarity_score": self.similarity_score,
            "confidence": self.confidence,
            "process_category": self.process_category,
            "industry_domain": self.industry_domain,
            "benchmark_available": self.benchmark_available,
            "process_maturity": self.process_maturity,
            "match_rationale": {
                "primary_reason": rationale.primary_reason,
                "keyword_matches": rationale.keyword_matches,
                "semantic_similarity": rationale.semantic_similarity,
                "vendor_indicators": rationale.vendor_indicators,
                "context_clues": rationale.context_clues,
                "confidence_factors": rationale.confidence_factors,
            },
        }


class APQCHierarchyService:
    """
    Enhanced APQC PCF hierarchy browser and intelligent mapping service.

    Provides comprehensive APQC process navigation, intelligent matching with detailed
    rationale, and automatic parent process linking for enterprise architecture modeling.
    """

    def __init__(self):
        """Initialize the APQC hierarchy service."""
        self._init_keyword_mappings()
        self._init_industry_variants()

    def _init_keyword_mappings(self):
        """Initialize keyword mappings for intelligent matching."""
        self.keyword_mappings = {
            "marketing": {
                "keywords": [
                    "marketing",
                    "promotion",
                    "advertising",
                    "brand",
                    "campaign",
                    "market",
                ],
                "process_codes": ["10.0", "10.1", "10.2", "10.3", "10.4"],
                "confidence_boost": 0.15,
            },
            "finance": {
                "keywords": [
                    "finance",
                    "financial",
                    "accounting",
                    "budget",
                    "cost",
                    "revenue",
                ],
                "process_codes": ["4.0", "4.1", "4.2", "4.3", "4.4"],
                "confidence_boost": 0.15,
            },
            "hr": {
                "keywords": [
                    "hr",
                    "human resources",
                    "personnel",
                    "employee",
                    "staff",
                    "workforce",
                ],
                "process_codes": ["7.0", "7.1", "7.2", "7.3", "7.4"],
                "confidence_boost": 0.15,
            },
            "it": {
                "keywords": [
                    "it",
                    "technology",
                    "software",
                    "system",
                    "infrastructure",
                    "digital",
                ],
                "process_codes": ["6.0", "6.1", "6.2", "6.3", "6.4"],
                "confidence_boost": 0.15,
            },
            "supply_chain": {
                "keywords": [
                    "supply",
                    "chain",
                    "logistics",
                    "procurement",
                    "inventory",
                    "sourcing",
                ],
                "process_codes": ["3.0", "3.1", "3.2", "3.3", "3.4", "3.5"],
                "confidence_boost": 0.15,
            },
            "customer_service": {
                "keywords": [
                    "customer",
                    "service",
                    "support",
                    "help",
                    "call",
                    "contact",
                ],
                "process_codes": ["1.0", "1.1", "1.2", "1.3", "1.4"],
                "confidence_boost": 0.15,
            },
            "risk_compliance": {
                "keywords": [
                    "risk",
                    "compliance",
                    "governance",
                    "audit",
                    "regulatory",
                    "legal",
                ],
                "process_codes": ["8.0", "8.1", "8.2", "8.3", "8.4"],
                "confidence_boost": 0.15,
            },
            "product_development": {
                "keywords": [
                    "product",
                    "development",
                    "rd",
                    "research",
                    "innovation",
                    "design",
                ],
                "process_codes": ["2.0", "2.1", "2.2", "2.3", "2.4"],
                "confidence_boost": 0.15,
            },
        }

    def _init_industry_variants(self):
        """Initialize industry-specific APQC variants."""
        self.industry_variants = {
            "banking": {
                "process_modifications": {
                    "1.0": {
                        "name": "Customer Banking Services",
                        "specialization": "retail_banking",
                    },
                    "4.0": {
                        "name": "Financial Risk Management",
                        "specialization": "banking_risk",
                    },
                    "7.0": {
                        "name": "Banking HR Management",
                        "specialization": "banking_hr",
                    },
                },
                "additional_processes": [
                    {"code": "9.1", "name": "Credit Risk Assessment", "level": 3},
                    {"code": "9.2", "name": "Loan Processing", "level": 3},
                    {"code": "9.3", "name": "Regulatory Reporting", "level": 3},
                ],
            },
            "healthcare": {
                "process_modifications": {
                    "1.0": {
                        "name": "Patient Care Services",
                        "specialization": "healthcare_delivery",
                    },
                    "3.0": {
                        "name": "Healthcare Supply Chain",
                        "specialization": "medical_supplies",
                    },
                    "8.0": {
                        "name": "Healthcare Compliance",
                        "specialization": "hipaa_compliance",
                    },
                },
                "additional_processes": [
                    {"code": "9.1", "name": "Clinical Operations", "level": 3},
                    {"code": "9.2", "name": "Medical Records Management", "level": 3},
                    {"code": "9.3", "name": "Patient Safety", "level": 3},
                ],
            },
            "manufacturing": {
                "process_modifications": {
                    "3.0": {
                        "name": "Manufacturing Supply Chain",
                        "specialization": "production_supply",
                    },
                    "2.0": {
                        "name": "Product Manufacturing",
                        "specialization": "production_dev",
                    },
                    "6.0": {
                        "name": "Manufacturing IT",
                        "specialization": "plant_systems",
                    },
                },
                "additional_processes": [
                    {"code": "9.1", "name": "Production Planning", "level": 3},
                    {"code": "9.2", "name": "Quality Control", "level": 3},
                    {"code": "9.3", "name": "Maintenance Management", "level": 3},
                ],
            },
        }

    def get_process_details(self, process_id: int) -> Optional["APQCProcess"]:
        """
        Get an APQC process by its database ID.

        Args:
            process_id: Database ID of the APQC process

        Returns:
            APQCProcess instance or None if not found
        """
        from app.models.apqc_process import APQCProcess

        return APQCProcess.query.get(process_id)

    def get_child_processes(self, process_id: int) -> List["APQCProcess"]:
        """
        Get all direct child processes of a given process.

        Args:
            process_id: Database ID of the parent APQC process

        Returns:
            List of child APQCProcess instances
        """
        from app.models.apqc_process import APQCProcess

        return APQCProcess.query.filter_by(parent_process_id=process_id).all()

    def get_auto_link_parents(self, process_id: int) -> List["APQCProcess"]:
        """
        Get all ancestor processes by walking up the parent_process_id chain.

        Args:
            process_id: Database ID of the APQC process

        Returns:
            List of parent APQCProcess instances from immediate parent to root
        """
        from app.models.apqc_process import APQCProcess

        parents = []
        current = APQCProcess.query.get(process_id)
        if not current:
            return parents

        while current.parent_process_id:
            parent = APQCProcess.query.get(current.parent_process_id)
            if not parent:
                break
            parents.append(parent)
            current = parent

        return parents

    def get_hierarchy_path(
        self, process_code: str, industry: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get full hierarchy path for an APQC process.

        Args:
            process_code: APQC process code (e.g., "10.2.1.3")
            industry: Optional industry for variant-specific paths

        Returns:
            List of dictionaries representing the hierarchy path from Level 1 to the process
        """
        from app.models.apqc_process import APQCProcess

        hierarchy_path = []
        current_code = process_code

        # Build path from Level 1 down to the process
        while current_code:
            process = APQCProcess.query.filter_by(process_code=current_code).first()
            if not process:
                break

            # Apply industry variant if specified
            display_name = process.process_name
            if industry and industry in self.industry_variants:
                variant = self.industry_variants[industry]["process_modifications"].get(
                    current_code
                )
                if variant:
                    display_name = variant["name"]

            hierarchy_path.append(
                {
                    "code": process.process_code,
                    "name": display_name,
                    "level": process.apqc_level,
                    "description": process.process_description,
                }
            )

            # Move to parent
            current_code = process.parent_code

        # Reverse to get Level 1 → ... → Process
        return list(reversed(hierarchy_path))

    def get_parent_processes(self, process_code: str) -> List[int]:
        """
        Get all parent process IDs for a given process.

        Args:
            process_code: APQC process code

        Returns:
            List of parent process IDs in order from immediate parent to root
        """
        from app.models.apqc_process import APQCProcess

        parent_ids = []
        current_code = process_code

        while current_code:
            process = APQCProcess.query.filter_by(process_code=current_code).first()
            if not process:
                break

            if process.parent_process_id:
                parent_ids.append(process.parent_process_id)

            current_code = process.parent_code

        return parent_ids

    def search_processes(
        self,
        query: str,
        level: Optional[int] = None,
        industry: Optional[str] = None,
        limit: int = 50,
    ) -> List[EnhancedAPQCMatch]:
        """
        Search APQC processes with intelligent matching and hierarchy context.

        Args:
            query: Search query (application name, description, etc.)
            level: Optional APQC level filter (1 - 5)
            industry: Optional industry for variant-specific matching
            limit: Maximum number of results

        Returns:
            List of enhanced APQC matches with hierarchy and rationale
        """
        from app.models.apqc_process import APQCProcess

        # Base query
        base_query = APQCProcess.query

        # Apply level filter
        if level:
            # For level filtering, we need to check the apqc_level property
            all_processes = base_query.all()
            filtered_processes = [p for p in all_processes if p.apqc_level == level]
        else:
            filtered_processes = base_query.all()

        # Calculate match scores and rationale
        matches = []
        for process in filtered_processes:
            match_result = self._calculate_match_score(query, process, industry)
            if match_result["similarity_score"] > 0.3:  # Minimum threshold
                hierarchy_path = self.get_hierarchy_path(process.process_code, industry)
                parent_ids = self.get_parent_processes(process.process_code)

                enhanced_match = EnhancedAPQCMatch(
                    process_id=process.id,
                    process_code=process.process_code,
                    process_name=process.process_name,
                    level=process.apqc_level,
                    hierarchy_path=hierarchy_path,
                    parent_ids=parent_ids,
                    match_rationale=match_result["rationale"],
                    similarity_score=match_result["similarity_score"],
                    confidence=match_result["confidence"],
                    process_category=process.process_category,
                    industry_domain=process.industry_domain,
                    benchmark_available=process.benchmark_available,
                    process_maturity=process.process_maturity,
                )
                matches.append(enhanced_match)

        # Sort by similarity score and limit results
        matches.sort(key=lambda x: x.similarity_score, reverse=True)
        return matches[:limit]

    def _calculate_match_score(
        self, query: str, process: "APQCProcess", industry: str = None
    ) -> Dict[str, Any]:
        """
        Calculate intelligent match score with detailed rationale.

        Args:
            query: Search query
            process: APQC process object
            industry: Optional industry context

        Returns:
            Dictionary with similarity score, confidence, and rationale
        """
        query_lower = query.lower()
        process_name_lower = process.process_name.lower()
        process_desc_lower = (process.process_description or "").lower()

        # Initialize rationale
        rationale = APQCMatchRationale(
            primary_reason="",
            keyword_matches=[],
            semantic_similarity=0.0,
            vendor_indicators=[],
            context_clues=[],
            confidence_factors={},
        )

        score = 0.0

        # 1. Exact name match (highest weight)
        if query_lower in process_name_lower:
            score += 0.8
            rationale.primary_reason = "Exact name match"
            rationale.keyword_matches.append(query)
            rationale.confidence_factors["name_match"] = 0.8

        # 2. Keyword matching
        for domain, config in self.keyword_mappings.items():
            domain_score = 0.0
            matched_keywords = []

            for keyword in config["keywords"]:
                if keyword in query_lower:
                    domain_score += 0.1
                    matched_keywords.append(keyword)

            if domain_score > 0:
                # Check if process is in this domain
                if any(
                    code.startswith(process.process_code.split(".")[0])
                    for code in config["process_codes"]
                ):
                    domain_score += config["confidence_boost"]
                    rationale.keyword_matches.extend(matched_keywords)
                    rationale.confidence_factors[f"{domain}_match"] = domain_score
                    score += domain_score

                    if not rationale.primary_reason:
                        rationale.primary_reason = (
                            f"Keyword match in {domain.title()} domain"
                        )

        # 3. Description similarity
        if process_desc_lower:
            desc_words = set(process_desc_lower.split())
            query_words = set(query_lower.split())
            overlap = len(desc_words.intersection(query_words))
            if overlap > 0:
                desc_similarity = min(overlap / len(query_words), 0.3)
                score += desc_similarity
                rationale.semantic_similarity = desc_similarity
                rationale.confidence_factors["description_similarity"] = desc_similarity

                if not rationale.primary_reason and desc_similarity > 0.2:
                    rationale.primary_reason = "Description content match"

        # 4. Process code proximity (for similar domains)
        process_prefix = process.process_code.split(".")[0]
        for domain, config in self.keyword_mappings.items():
            if any(code.startswith(process_prefix) for code in config["process_codes"]):
                # Same domain bonus
                score += 0.05
                rationale.context_clues.append(f"Same domain as {domain.title()}")
                break

        # 5. Industry-specific boosting
        if industry and industry in self.industry_variants:
            variant = self.industry_variants[industry]["process_modifications"].get(
                process.process_code
            )
            if variant:
                score += 0.1
                rationale.context_clues.append(
                    f"Industry-specific variant for {industry}"
                )

        # Determine confidence level
        if score >= 0.8:
            confidence = "high"
        elif score >= 0.6:
            confidence = "medium"
        elif score >= 0.4:
            confidence = "low"
        else:
            confidence = "very_low"

        return {
            "similarity_score": min(score, 1.0),
            "confidence": confidence,
            "rationale": rationale,
        }

    def get_tree_structure(self, industry: str = None) -> Dict[str, Any]:
        """
        Get complete APQC hierarchy tree structure for interactive browsing.

        Args:
            industry: Optional industry for variant-specific structure

        Returns:
            Nested dictionary representing the complete hierarchy
        """
        from app.models.apqc_process import APQCProcess

        # Get all processes
        all_processes = APQCProcess.query.all()

        # Build tree structure
        tree = {}

        for process in all_processes:
            # Apply industry variant if specified
            display_name = process.process_name
            if industry and industry in self.industry_variants:
                variant = self.industry_variants[industry]["process_modifications"].get(
                    process.process_code
                )
                if variant:
                    display_name = variant["name"]

            node = {
                "id": process.id,
                "code": process.process_code,
                "name": display_name,
                "level": process.apqc_level,
                "description": process.process_description,
                "category": process.process_category,
                "benchmark_available": process.benchmark_available,
                "children": {},
            }

            # Add to tree structure
            self._add_to_tree(tree, process.process_code, node)

        # Add industry-specific additional processes
        if industry and industry in self.industry_variants:
            additional = self.industry_variants[industry]["additional_processes"]
            for proc_data in additional:
                node = {
                    "id": None,  # No database ID for additional processes
                    "code": proc_data["code"],
                    "name": proc_data["name"],
                    "level": proc_data["level"],
                    "description": f"Industry-specific process for {industry}",
                    "category": "Industry Specific",
                    "benchmark_available": False,
                    "children": {},
                }
                self._add_to_tree(tree, proc_data["code"], node)

        return tree

    def _add_to_tree(self, tree: Dict, process_code: str, node: Dict):
        """Helper method to add a node to the tree structure."""
        segments = process_code.split(".")

        current = tree
        for i, segment in enumerate(segments[:-1]):  # Navigate to parent
            parent_code = ".".join(segments[: i + 1])
            if parent_code not in current:
                current[parent_code] = {"children": {}}
            # Ensure children is a dict, not a list
            parent_node = current[parent_code]
            if isinstance(parent_node.get("children"), list):
                parent_node["children"] = {}
            current = parent_node["children"]

        # Add the node
        current[process_code] = node

    def auto_link_parent_processes(
        self, application_id: int, process_id: int, confidence_threshold: float = 0.6
    ) -> Dict[str, Any]:
        """
        Automatically create parent process mappings when mapping to child process.

        Args:
            application_id: Application component ID
            process_id: Target APQC process ID
            confidence_threshold: Minimum confidence for auto-linking

        Returns:
            Dictionary with linking results and statistics
        """
        from app.models.apqc_process import APQCProcess, ProcessApplicationMapping

        # Get the target process
        target_process = APQCProcess.query.get(process_id)
        if not target_process:
            return {"success": False, "error": "Target process not found"}

        # Get parent processes
        parent_ids = self.get_parent_processes(target_process.process_code)

        linked_count = 0
        skipped_count = 0
        errors = []

        for parent_id in parent_ids:
            try:
                parent_process = APQCProcess.query.get(parent_id)
                if not parent_process:
                    skipped_count += 1
                    continue

                # Check if mapping already exists
                existing = ProcessApplicationMapping.query.filter_by(  # model-safety-ok
                    application_id=application_id, process_id=parent_id
                ).first()

                if existing:
                    skipped_count += 1
                    continue

                # Calculate confidence for parent (lower than child)
                parent_confidence = max(0.3, confidence_threshold - 0.2)

                # Create parent mapping
                mapping = ProcessApplicationMapping(
                    application_id=application_id,
                    process_id=parent_id,
                    confidence_score=parent_confidence,
                    mapping_method="auto_parent_link",
                    created_by="system",
                )

                db.session.add(mapping)
                linked_count += 1

            except Exception as e:
                errors.append(f"Error linking parent {parent_id}: {str(e)}")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return {"success": False, "error": f"Database error: {str(e)}"}

        return {
            "success": True,
            "linked_parents": linked_count,
            "skipped_parents": skipped_count,
            "errors": errors,
            "target_process": {
                "code": target_process.process_code,
                "name": target_process.process_name,
                "level": target_process.apqc_level,
            },
        }

    def get_process_metrics(self, process_id: int) -> Dict[str, Any]:
        """
        Get comprehensive metrics and benchmarking data for a process.

        Args:
            process_id: APQC process ID

        Returns:
            Dictionary with process metrics, benchmarks, and KPIs
        """
        from app.models.apqc_process import APQCProcess, ProcessApplicationMapping

        process = APQCProcess.query.get(process_id)
        if not process:
            return {"error": "Process not found"}

        # Get application mappings. ProcessApplicationMapping has no
        # organization_id of its own (pr310-v1 review, DEFECT-5) --
        # unfenced, total_applications below counted every organisation's
        # mappings for this process.
        from app.services.apqc_mapping_tenant_fence import (
            fenced_application_mappings_query,
        )

        mappings = fenced_application_mappings_query().filter(
            ProcessApplicationMapping.apqc_process_id == process_id
        ).all()

        # Calculate metrics
        total_applications = len(mappings)
        # Use process_coverage (0 - 100) as confidence metric
        high_confidence_mappings = len(
            [m for m in mappings if (m.process_coverage or 0) >= 70]
        )
        avg_confidence = (
            sum((m.process_coverage or 0) / 100 for m in mappings) / total_applications
            if mappings
            else 0
        )

        metrics = {
            "process_info": {
                "code": process.process_code,
                "name": process.process_name,
                "level": process.apqc_level,
                "category": process.process_category,
                "industry_domain": process.industry_domain,
            },
            "mapping_metrics": {
                "total_applications": total_applications,
                "high_confidence_mappings": high_confidence_mappings,
                "average_confidence": round(avg_confidence, 3),
                "confidence_distribution": self._calculate_confidence_distribution(
                    mappings
                ),
            },
            "benchmark_data": {},
            "kpi_definitions": {},
        }

        # Parse benchmark data if available
        if process.benchmark_available and process.industry_benchmarks:
            try:
                import json

                metrics["benchmark_data"] = json.loads(process.industry_benchmarks)
            except json.JSONDecodeError:
                pass

        # Parse KPI definitions if available
        if process.kpi_definitions:
            try:
                import json

                metrics["kpi_definitions"] = json.loads(process.kpi_definitions)
            except json.JSONDecodeError:
                pass

        return metrics

    def _calculate_confidence_distribution(self, mappings: List) -> Dict[str, int]:
        """Calculate confidence score distribution."""
        distribution = {
            "very_high": 0,  # >= 0.9
            "high": 0,  # >= 0.7
            "medium": 0,  # >= 0.5
            "low": 0,  # >= 0.3
            "very_low": 0,  # < 0.3
        }

        for mapping in mappings:
            score = mapping.confidence_score
            if score >= 0.9:
                distribution["very_high"] += 1
            elif score >= 0.7:
                distribution["high"] += 1
            elif score >= 0.5:
                distribution["medium"] += 1
            elif score >= 0.3:
                distribution["low"] += 1
            else:
                distribution["very_low"] += 1

        return distribution

    def get_industry_processes(self, industry: str) -> List[Dict[str, Any]]:
        """
        Get industry-specific APQC process variants.

        Args:
            industry: Industry name (banking, healthcare, manufacturing)

        Returns:
            List of industry-specific process modifications and additions
        """
        if industry not in self.industry_variants:
            return []

        variant = self.industry_variants[industry]
        processes = []

        # Modified processes
        for code, modification in variant["process_modifications"].items():
            from app.models.apqc_process import APQCProcess

            base_process = APQCProcess.query.filter_by(process_code=code).first()  # model-safety-ok

            if base_process:
                processes.append(
                    {
                        "code": code,
                        "base_name": base_process.process_name,
                        "modified_name": modification["name"],
                        "specialization": modification["specialization"],
                        "level": base_process.apqc_level,
                        "type": "modification",
                    }
                )

        # Additional processes
        for proc_data in variant["additional_processes"]:
            processes.append(
                {
                    "code": proc_data["code"],
                    "name": proc_data["name"],
                    "level": proc_data["level"],
                    "type": "additional",
                    "description": f"Industry-specific process for {industry}",
                }
            )

        return processes

    def get_statistics(self, industry: str = None) -> Dict[str, Any]:
        """
        Get APQC process statistics.

        Args:
            industry: Optional industry filter for context

        Returns:
            Dictionary with aggregate process statistics
        """
        from app.models.apqc_process import APQCProcess

        all_processes = APQCProcess.query.all()
        total = len(all_processes)

        processes_by_level: Dict[str, int] = {}
        for p in all_processes:
            level_key = f"level_{p.apqc_level}"
            processes_by_level[level_key] = processes_by_level.get(level_key, 0) + 1

        # CapabilityProcessMapping has no organization_id of its own;
        # unfenced, this counted every organisation's mappings, leaking an
        # aggregate cross-organisation figure (pr310-v1 review, DEFECT-6).
        from app.services.apqc_mapping_tenant_fence import (
            fenced_capability_mappings_query,
        )

        total_mappings = fenced_capability_mappings_query().count()

        return {
            "total_processes": total,
            "processes_by_level": processes_by_level,
            "total_capability_mappings": total_mappings,
            "industry": industry,
        }

    def get_industry_variants(self) -> List[str]:
        """
        Get list of available industry variant names.

        Returns:
            List of industry variant keys (e.g. ['banking', 'healthcare', ...])
        """
        return list(self.industry_variants.keys())
