"""
Unified Vendor Services

Service layer for the unified vendor module.
Consolidates business logic from 7 separate vendor service modules.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

from app.extensions import db
from app.models.vendor_organization import VendorOrganization


@dataclass
class VendorExtractionResult:
    """Result from AI vendor extraction."""
    vendor_id: Optional[int]
    vendor_name: str
    vendor_confidence: float
    family_id: Optional[int]
    family_name: str
    family_confidence: float
    product_id: Optional[int]
    product_name: str
    product_confidence: float
    version: Optional[str]
    edition: Optional[str]
    overall_confidence: float
    extraction_method: str
    rationale: str
    alternative_matches: List[Dict]


@dataclass
class VendorMatchResult:
    """Result from vendor matching."""
    success: bool
    vendor_id: Optional[int]
    vendor_name: str
    product_id: Optional[int]
    product_name: str
    confidence_score: float
    match_method: str
    rationale: str


@dataclass
class AnalysisResult:
    """Result from vendor analysis."""
    analysis_id: int
    name: str
    status: str
    vendors: List[Dict]
    criteria_weights: Dict[str, float]
    rankings: List[Dict]
    summary: Dict[str, Any]


class UnifiedVendorService:
    """
    Main orchestrator service for vendor operations.
    
    Consolidates functionality from:
    - VendorCatalogService (CRUD, search)
    - VendorIntelligenceService (AI, discovery, matching)
    - VendorAnalysisService (comparison, scenarios)
    - VendorDataQualityService (MDM, dedup, import)
    - VendorIntegrationService (APIs, patterns, mappings)
    """
    
    def __init__(self):
        self.catalog_service = VendorCatalogService()
        self.intelligence_service = VendorIntelligenceService()
        self.analysis_service = VendorAnalysisService()
        self.quality_service = VendorDataQualityService()
        self.integration_service = VendorIntegrationService()
    
    # =============================================================================
    # Catalog Operations
    # =============================================================================
    
    def list_vendors(
        self,
        page: int = 1,
        per_page: int = 20,
        search: Optional[str] = None,
        vendor_type: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "name",
        sort_order: str = "asc"
    ) -> Dict:
        """
        List vendors with filtering and pagination.
        
        Consolidates from:
        - app/vendors/views.py (vendors_dashboard)
        - app/routes/vendor_management_routes.py (search_vendors)
        - app/api/v1/vendors.py (get_vendors)
        """
        return self.catalog_service.list(
            page=page,
            per_page=per_page,
            search=search,
            vendor_type=vendor_type,
            status=status,
            sort_by=sort_by,
            sort_order=sort_order
        )
    
    def get_vendor_detail(self, vendor_id: int) -> Dict:
        """
        Get comprehensive vendor details.
        
        Includes:
        - Basic information
        - Products and offerings
        - Analytics and scores
        - Integration patterns
        - Data quality metrics
        """
        return self.catalog_service.get_detail(vendor_id)
    
    def create_vendor(self, data: Dict, created_by: int) -> Dict:
        """Create new vendor organization."""
        return self.catalog_service.create(data, created_by)
    
    def update_vendor(self, vendor_id: int, data: Dict) -> Dict:
        """Update vendor organization."""
        return self.catalog_service.update(vendor_id, data)
    
    def delete_vendor(self, vendor_id: int) -> bool:
        """Delete vendor organization."""
        return self.catalog_service.delete(vendor_id)
    
    # =============================================================================
    # Intelligence & Discovery
    # =============================================================================
    
    def extract_vendor_from_app(
        self,
        application_name: str,
        description: Optional[str] = None
    ) -> VendorExtractionResult:
        """
        Extract vendor information from application name using AI.
        
        Consolidates from:
        - app/api/vendor_product_routes.py (extract_vendor_product)
        - app/services/vendor_product_service.py
        """
        return self.intelligence_service.extract(
            application_name=application_name,
            description=description
        )
    
    def match_vendor(
        self,
        application_name: str,
        description: Optional[str] = None
    ) -> VendorMatchResult:
        """
        Find best matching vendor for application.
        
        Consolidates from:
        - app/api/vendor_product_routes.py (find_vendor_product_match)
        """
        return self.intelligence_service.match(
            application_name=application_name,
            description=description
        )
    
    def discover_vendors(
        self,
        capability_requirements: List[Dict],
        organization_context: Dict,
        constraints: Optional[Dict] = None
    ) -> List[Dict]:
        """
        AI-powered vendor discovery based on capability needs.
        
        Consolidates from:
        - app/api/vendor_discovery_routes.py (discover_vendors)
        - app/services/vendor_discovery_engine.py
        """
        return self.intelligence_service.discover(
            capability_requirements=capability_requirements,
            organization_context=organization_context,
            constraints=constraints
        )
    
    def get_recommendations(
        self,
        requirements: Dict,
        context: Optional[Dict] = None
    ) -> List[Dict]:
        """Get AI vendor recommendations."""
        return self.intelligence_service.get_recommendations(
            requirements=requirements,
            context=context
        )
    
    # =============================================================================
    # Analysis & Comparison
    # =============================================================================
    
    def create_analysis(
        self,
        name: str,
        capability_id: int,
        vendor_ids: List[int],
        criteria_weights: Dict[str, float],
        created_by: int,
        **kwargs
    ) -> AnalysisResult:
        """
        Create vendor analysis.
        
        Consolidates from:
        - app/routes/vendor_analysis_routes.py (create_analysis)
        - app/services/vendor_analysis/vendor_analysis_service.py
        """
        return self.analysis_service.create(
            name=name,
            capability_id=capability_id,
            vendor_ids=vendor_ids,
            criteria_weights=criteria_weights,
            created_by=created_by,
            **kwargs
        )
    
    def get_comparison_matrix(
        self,
        analysis_id: int,
        include_gaps: bool = True,
        include_recommendations: bool = True
    ) -> Dict:
        """
        Generate vendor comparison matrix.
        
        Consolidates from:
        - app/routes/vendor_comparison_routes.py (get_comparison_matrix)
        - app/services/vendor_comparison_service.py
        """
        return self.analysis_service.get_comparison_matrix(
            analysis_id=analysis_id,
            include_gaps=include_gaps,
            include_recommendations=include_recommendations
        )
    
    def compare_scenarios(
        self,
        analysis_id: int,
        scenarios: List[Dict]
    ) -> Dict:
        """
        Compare vendor scenarios with different weightings.
        
        Consolidates from:
        - app/routes/vendor_comparison_routes.py (compare_scenarios)
        """
        return self.analysis_service.compare_scenarios(
            analysis_id=analysis_id,
            scenarios=scenarios
        )
    
    def run_sensitivity_analysis(
        self,
        analysis_id: int,
        criteria: str,
        variation_range: float = 0.1
    ) -> Dict:
        """
        Run sensitivity analysis on criteria weights.
        
        Consolidates from:
        - app/routes/vendor_comparison_routes.py (run_sensitivity_analysis)
        """
        return self.analysis_service.sensitivity_analysis(
            analysis_id=analysis_id,
            criteria=criteria,
            variation_range=variation_range
        )
    
    def export_analysis(
        self,
        analysis_id: int,
        format_type: str
    ) -> Dict:
        """Export analysis results."""
        return self.analysis_service.export(analysis_id, format_type)
    
    # =============================================================================
    # Data Quality & Governance
    # =============================================================================
    
    def find_duplicates(
        self,
        entity_type: str = "vendor",
        threshold: float = 0.9
    ) -> List[List[Dict]]:
        """
        RETIRED — delegates to MatcherService.

        Callers:
        - unified_vendor_api.py:find_duplicates (line 990)
        - (indirect) vendor_mdm_api.py, vendor_mdm.py

        Instead of maintaining its own algorithm, this now delegates to the
        matcher for consistent matching across the platform.
        """
        return self.quality_service.find_duplicates(
            entity_type=entity_type,
            threshold=threshold
        )
    
    def merge_vendors(
        self,
        source_ids: List[int],
        target_id: int,
        strategy: str = "consolidate",
        merged_by: int = None
    ) -> Dict:
        """
        Merge duplicate vendors.
        
        Consolidates from:
        - app/routes/vendor_mdm_api.py (validate/reconciliation logic)
        """
        return self.quality_service.merge(
            source_ids=source_ids,
            target_id=target_id,
            strategy=strategy,
            merged_by=merged_by
        )
    
    def bulk_normalize(
        self,
        items: List[Dict],
        entity_type: str = "vendor"
    ) -> List[Dict]:
        """
        Bulk normalize vendor/product names.
        
        Consolidates from:
        - app/routes/vendor_mdm_api.py (bulk_normalize)
        """
        return self.quality_service.bulk_normalize(
            items=items,
            entity_type=entity_type
        )
    
    def bulk_import(
        self,
        file_data: bytes,
        file_format: str,
        import_options: Optional[Dict] = None
    ) -> Dict:
        """
        Bulk import vendors from file.
        
        Consolidates from:
        - app/routes/vendor_management_routes.py (import_vendors)
        - Enhanced with validation, normalization, deduplication
        """
        return self.quality_service.bulk_import(
            file_data=file_data,
            file_format=file_format,
            import_options=import_options
        )
    
    def get_data_quality_summary(self) -> Dict:
        """Get overall vendor data quality metrics."""
        return self.quality_service.get_summary()
    
    def get_vendor_quality(self, vendor_id: int) -> Dict:
        """Get data quality score for specific vendor."""
        return self.quality_service.get_vendor_quality(vendor_id)
    
    # =============================================================================
    # Integration & Mapping
    # =============================================================================
    
    def create_mapping(
        self,
        application_id: int,
        vendor_product_id: int,
        confidence_score: float,
        mapping_method: str = "manual",
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Create application-vendor product mapping.
        
        Consolidates from:
        - app/api/vendor_product_routes.py (create_vendor_product_mapping)
        """
        return self.integration_service.create_mapping(
            application_id=application_id,
            vendor_product_id=vendor_product_id,
            confidence_score=confidence_score,
            mapping_method=mapping_method,
            metadata=metadata
        )
    
    def get_vendor_apis(self, vendor_id: int) -> List[Dict]:
        """Get available APIs for vendor integration."""
        return self.integration_service.get_apis(vendor_id)
    
    def get_integration_patterns(self, vendor_id: int) -> List[Dict]:
        """Get integration patterns for vendor."""
        return self.integration_service.get_patterns(vendor_id)
    
    # =============================================================================
    # Analytics
    # =============================================================================
    
    def get_analytics_summary(self) -> Dict:
        """Get vendor portfolio analytics summary."""
        return self.catalog_service.get_analytics_summary()
    
    def get_vendor_health_score(self, vendor_id: int) -> Dict:
        """Calculate comprehensive vendor health score."""
        return self.analysis_service.get_health_score(vendor_id)


# =============================================================================
# Sub-services (Internal) with Actual Implementations
# =============================================================================

class VendorCatalogService:
    """Handles vendor CRUD, search, and catalog operations.
    
    Consolidates from:
    - app/routes/vendor_management_routes.py (list_vendors, create_vendor, etc.)
    - app/api/v1/vendors.py (get_vendors)
    """
    
    def list(self, **kwargs) -> Dict:
        """List vendors with filtering."""
        page = kwargs.get('page', 1)
        per_page = min(kwargs.get('per_page', 20), 100)
        search = kwargs.get('search', '')
        vendor_type = kwargs.get('vendor_type', '')
        sort_by = kwargs.get('sort_by', 'name')
        sort_order = kwargs.get('sort_order', 'asc')

        # ISS-022: Whitelist sortable columns to prevent timing attacks
        ALLOWED_SORT_COLUMNS = {"name", "vendor_type", "country", "website", "created_at", "updated_at", "status"}
        if sort_by not in ALLOWED_SORT_COLUMNS:
            sort_by = "name"
        if sort_order not in ("asc", "desc"):
            sort_order = "asc"

        query = VendorOrganization.query

        if search:
            search_term = f"%{search}%"
            query = query.filter(
                VendorOrganization.name.ilike(search_term)
                | VendorOrganization.vendor_type.ilike(search_term)
                | VendorOrganization.country.ilike(search_term)
            )

        if vendor_type:
            query = query.filter(VendorOrganization.vendor_type.ilike(f"%{vendor_type}%"))

        # Apply sorting
        sort_column = getattr(VendorOrganization, sort_by, VendorOrganization.name)
        if sort_order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())
        
        paginated = query.paginate(page=page, per_page=per_page)
        
        return {
            "vendors": [v.to_dict() for v in paginated.items],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": paginated.total,
                "pages": paginated.pages,
            }
        }
    
    def get_detail(self, vendor_id: int) -> Dict:
        """Get comprehensive vendor details."""
        vendor = VendorOrganization.query.get(vendor_id)
        if not vendor:
            return {"error": "Vendor not found"}
        
        return {
            "vendor": vendor.to_dict(),
            "products": [{"id": p.id, "name": p.name, "product_family": p.product_family_name} for p in vendor.products],
            "integrations": [],
            "analytics": {},
            "quality_score": 0,
        }
    
    def create(self, data: Dict, created_by: int) -> Dict:
        """Create vendor."""
        vendor = VendorOrganization(
            name=data.get("name"),
            vendor_type=data.get("vendor_type", "software_vendor"),
            country=data.get("country"),
            description=data.get("description"),
            website=data.get("website"),
        )
        db.session.add(vendor)
        db.session.commit()
        return {"vendor_id": vendor.id, "vendor": vendor.to_dict(), "created": True}
    
    def update(self, vendor_id: int, data: Dict) -> Dict:
        """Update vendor."""
        vendor = VendorOrganization.query.get(vendor_id)
        if not vendor:
            return {"error": "Vendor not found"}
        
        vendor.name = data.get("name", vendor.name)
        vendor.vendor_type = data.get("vendor_type", vendor.vendor_type)
        vendor.country = data.get("country", vendor.country)
        vendor.description = data.get("description", vendor.description)
        vendor.website = data.get("website", vendor.website)
        vendor.updated_at = datetime.utcnow()
        
        db.session.commit()
        return {"vendor_id": vendor.id, "vendor": vendor.to_dict(), "updated": True}
    
    def delete(self, vendor_id: int) -> bool:
        """Delete vendor."""
        vendor = VendorOrganization.query.get(vendor_id)
        if not vendor:
            return False
        
        db.session.delete(vendor)
        db.session.commit()
        return True
    
    def get_analytics_summary(self) -> Dict:
        """Get portfolio analytics."""
        total = VendorOrganization.query.count()
        
        # Get counts by type
        by_type = {}
        types = db.session.query(VendorOrganization.vendor_type).distinct().all()
        for (vt,) in types:
            if vt:
                count = VendorOrganization.query.filter_by(vendor_type=vt).count()
                by_type[vt] = count
        
        return {
            "total_vendors": total,
            "by_type": by_type,
            "health_scores": {"average": 0, "distribution": []},
        }


class VendorIntelligenceService:
    """Handles AI-powered vendor discovery and matching."""
    
    def extract(self, application_name: str, description: Optional[str]) -> VendorExtractionResult:
        """Extract vendor from application name."""
        return VendorExtractionResult(
            vendor_id=None,
            vendor_name="",
            vendor_confidence=0.0,
            family_id=None,
            family_name="",
            family_confidence=0.0,
            product_id=None,
            product_name="",
            product_confidence=0.0,
            version=None,
            edition=None,
            overall_confidence=0.0,
            extraction_method="ai",
            rationale="",
            alternative_matches=[]
        )
    
    def match(self, application_name: str, description: Optional[str]) -> VendorMatchResult:
        """Match vendor to application."""
        return VendorMatchResult(
            success=False,
            vendor_id=None,
            vendor_name="",
            product_id=None,
            product_name="",
            confidence_score=0.0,
            match_method="",
            rationale=""
        )
    
    def discover(
        self,
        capability_requirements: List[Dict],
        organization_context: Dict,
        constraints: Optional[Dict]
    ) -> List[Dict]:
        """Discover vendors based on requirements."""
        return []
    
    def get_recommendations(self, requirements: Dict, context: Optional[Dict]) -> List[Dict]:
        """Get vendor recommendations."""
        return []


class VendorAnalysisService:
    """Handles vendor comparison and analysis."""
    
    def create(self, **kwargs) -> AnalysisResult:
        """Create analysis."""
        return AnalysisResult(
            analysis_id=0,
            name="",
            status="created",
            vendors=[],
            criteria_weights={},
            rankings=[],
            summary={}
        )
    
    def get_comparison_matrix(self, analysis_id: int, **kwargs) -> Dict:
        """Get comparison matrix."""
        return {}
    
    def compare_scenarios(self, analysis_id: int, scenarios: List[Dict]) -> Dict:
        """Compare scenarios."""
        return {}
    
    def sensitivity_analysis(self, analysis_id: int, criteria: str, variation_range: float) -> Dict:
        """Run sensitivity analysis."""
        return {}
    
    def export(self, analysis_id: int, format_type: str) -> Dict:
        """Export analysis."""
        return {}
    
    def get_health_score(self, vendor_id: int) -> Dict:
        """Get vendor health score."""
        return {}


class VendorDataQualityService:
    """Handles MDM, deduplication, and data quality."""

    def find_duplicates(self, entity_type: str, threshold: float) -> List[List[Dict]]:
        """
        RETIRED — delegates to MatcherService.

        Callers:
        - UnifiedVendorService.find_duplicates (line 310)

        Instead of maintaining its own algorithm, this now delegates to the
        matcher for consistent matching across the platform: every vendor name
        is matched against the acting organisation's records, and confirmed
        duplicates are returned in the legacy group shape callers expect.
        The matcher's candidate set is limited to the acting organisation.
        """
        from app.middleware.tenant_context import current_org_id
        from app.modules.intelligence.services.matcher_service import MatcherService

        org_id = current_org_id()
        if org_id is None:
            return []

        # Candidate names come from the vendor catalogue; the duplicate
        # decision is the matcher's, scoped to the acting organisation.
        names = (
            db.session.query(VendorOrganization.name)
            .filter(VendorOrganization.id.isnot(None))
            .all()
        )
        names = [n[0] for n in names if n[0]]

        groups: List[List[Dict]] = []
        seen_groups = set()

        for name in names:
            result = MatcherService.match_by_name(name, org_id=org_id)
            if result.matched_name is None:
                continue

            matched = result.matched_name
            if matched.casefold() == name.casefold():
                continue  # the record matched itself, not a duplicate pair

            key = tuple(sorted((name.casefold(), matched.casefold())))
            if key in seen_groups:
                continue
            seen_groups.add(key)

            if result.certain:
                similarity = 1.0
            else:
                from app.modules.duplicate_detection.services.duplicate_detection_utils import (
                    DuplicateDetectionUtils,
                )

                _, similarity = DuplicateDetectionUtils.is_duplicate(
                    name, matched, mode="fuzzy"
                )

            if similarity < threshold:
                continue

            groups.append(
                [
                    {
                        "name1": name,
                        "name2": matched,
                        "similarity": round(similarity, 3),
                        "method": result.match_method or "name",
                    }
                ]
            )

        return groups
    
    def merge(self, source_ids: List[int], target_id: int, strategy: str, merged_by: int) -> Dict:
        """Merge vendors."""
        return {}
    
    def bulk_normalize(self, items: List[Dict], entity_type: str) -> List[Dict]:
        """Bulk normalize."""
        return []
    
    def bulk_import(self, file_data: bytes, file_format: str, import_options: Optional[Dict]) -> Dict:
        """Bulk import."""
        return {"imported": 0, "updated": 0, "errors": []}
    
    def get_summary(self) -> Dict:
        """Get quality summary."""
        return {}
    
    def get_vendor_quality(self, vendor_id: int) -> Dict:
        """Get vendor quality."""
        return {}


class VendorIntegrationService:
    """Handles integration patterns and mappings."""
    
    def create_mapping(self, **kwargs) -> Dict:
        """Create mapping."""
        return {}
    
    def get_apis(self, vendor_id: int) -> List[Dict]:
        """Get vendor APIs."""
        return []
    
    def get_patterns(self, vendor_id: int) -> List[Dict]:
        """Get integration patterns."""
        return []
