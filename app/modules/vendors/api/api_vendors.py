"""
MIGRATION: Copied from app/api_vendors.py
Changes: `from app import db` -> `from app.extensions import db`
Legacy file preserved at original location.

Vendor REST API with Swagger documentation using Flask-RESTX.
"""
from flask import Blueprint, request
from flask_login import login_required
from flask_restx import Api, Resource, fields
from sqlalchemy import desc, func

from app.extensions import db
from app.models.apqc_process import APQCProcess, CapabilityProcessMapping
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping

# Create blueprint
vendors_api_bp = Blueprint("vendors_api", __name__, url_prefix="/api/vendors")

# Initialize Flask-RESTX API
api = Api(
    vendors_api_bp,
    version="1.0",
    title="Vendor Management API",
    description="Enterprise vendor and product catalogue management API with analytics and comparison tools",
    doc="/doc",  # Swagger UI will be at /api/vendors/doc
    authorizations={"apikey": {"type": "apiKey", "in": "header", "name": "Authorization"}},
)

# Define namespaces
ns_vendors = api.namespace("vendors", description="Vendor organization operations")
ns_products = api.namespace("products", description="Vendor product operations")
ns_analytics = api.namespace("analytics", description="Vendor analytics and insights")
ns_comparison = api.namespace("comparison", description="Vendor comparison tools")
ns_apqc = api.namespace(
    "apqc", description="APQC PCF process operations and vendor-process mappings"
)

# Define API models (schemas)
vendor_model = api.model(
    "Vendor",
    {
        "id": fields.Integer(readonly=True, description="Vendor unique identifier"),
        "name": fields.String(required=True, description="Vendor organization name"),
        "display_name": fields.String(description="Display name"),
        "vendor_type": fields.String(description="Type of vendor (software, cloud, SI)"),
        "headquarters_location": fields.String(description="HQ location"),
        "website": fields.String(description="Vendor website URL"),
        "year_founded": fields.Integer(description="Year founded"),
        "employee_count": fields.Integer(description="Number of employees"),
        "strategic_tier": fields.String(description="Strategic tier (tier_1, tier_2, etc)"),
        "partnership_level": fields.String(description="Partnership level"),
        "enterprise_readiness_score": fields.Integer(description="Enterprise readiness (0 - 100)"),
        "innovation_score": fields.Integer(description="Innovation score (0 - 100)"),
        "status": fields.String(description="Vendor status"),
        "description": fields.String(description="Vendor description"),
    },
)

product_model = api.model(
    "Product",
    {
        "id": fields.Integer(readonly=True, description="Product unique identifier"),
        "vendor_id": fields.Integer(required=True, description="Vendor organization ID"),
        "product_name": fields.String(required=True, description="Product name"),
        "product_code": fields.String(description="Product SKU/code"),
        "product_family": fields.String(description="Product family (ERP, CRM, etc)"),
        "version": fields.String(description="Product version"),
        "deployment_model": fields.String(
            description="Deployment model (cloud, on-premise, hybrid)"
        ),
        "product_type": fields.String(description="Product type (suite, platform, application)"),
        "status": fields.String(description="Product status"),
    },
)

vendor_risk_model = api.model(
    "VendorRisk",
    {
        "vendor_id": fields.Integer(description="Vendor ID"),
        "vendor_name": fields.String(description="Vendor name"),
        "financial_health_score": fields.Integer(description="Financial health (0 - 100)"),
        "vendor_lock_in_risk": fields.Integer(description="Lock-in risk (1 - 10)"),
        "acquisition_risk": fields.String(description="Acquisition risk level"),
        "technology_maturity": fields.String(description="Technology maturity"),
        "overall_risk_score": fields.Float(description="Calculated overall risk score"),
        "risk_level": fields.String(description="Risk level: low, medium, high, critical"),
    },
)

analytics_summary_model = api.model(
    "AnalyticsSummary",
    {
        "total_vendors": fields.Integer(description="Total number of vendors"),
        "total_products": fields.Integer(description="Total number of products"),
        "strategic_partners": fields.Integer(description="Number of strategic partners"),
        "by_vendor_type": fields.Raw(description="Breakdown by vendor type"),
        "by_strategic_tier": fields.Raw(description="Breakdown by strategic tier"),
        "by_partnership_level": fields.Raw(description="Breakdown by partnership level"),
        "top_vendors": fields.List(
            fields.Nested(vendor_model), description="Top 5 vendors by score"
        ),
    },
)


@ns_vendors.route("/")
class VendorList(Resource):
    @api.doc("list_vendors")
    @login_required
    @api.marshal_list_with(vendor_model)
    def get(self):
        """List all vendor organizations"""
        vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()
        return [
            {
                "id": v.id,
                "name": v.name,
                "display_name": v.display_name,
                "vendor_type": v.vendor_type,
                "headquarters_location": v.headquarters_location,
                "website": v.website,
                "year_founded": v.year_founded,
                "employee_count": v.employee_count,
                "strategic_tier": v.strategic_tier,
                "partnership_level": v.partnership_level,
                "enterprise_readiness_score": v.enterprise_readiness_score,
                "innovation_score": v.innovation_score,
                "status": v.status,
                "description": v.description,
            }
            for v in vendors
        ]


@ns_vendors.route("/<int:id>")
@api.response(404, "Vendor not found")
@api.param("id", "The vendor identifier")
class Vendor(Resource):
    @api.doc("get_vendor")
    @login_required
    @api.marshal_with(vendor_model)
    def get(self, id):
        """Get a vendor by ID"""
        vendor = VendorOrganization.query.get_or_404(id)
        return {
            "id": vendor.id,
            "name": vendor.name,
            "display_name": vendor.display_name,
            "vendor_type": vendor.vendor_type,
            "headquarters_location": vendor.headquarters_location,
            "website": vendor.website,
            "year_founded": vendor.year_founded,
            "employee_count": vendor.employee_count,
            "strategic_tier": vendor.strategic_tier,
            "partnership_level": vendor.partnership_level,
            "enterprise_readiness_score": vendor.enterprise_readiness_score,
            "innovation_score": vendor.innovation_score,
            "status": vendor.status,
            "description": vendor.description,
        }


@ns_vendors.route("/<int:id>/applications")
@api.response(404, "Vendor not found")
@api.param("id", "The vendor identifier")
class VendorApplications(Resource):
    @api.doc("get_vendor_applications")
    @login_required
    def get(self, id):
        """Get all applications using products from this vendor"""
        from app.models.application_portfolio import ApplicationComponent

        vendor = VendorOrganization.query.get_or_404(id)

        products = VendorProduct.query.filter_by(vendor_organization_id=id).all()
        product_ids = [p.id for p in products]

        if not product_ids:
            return {
                "vendor_id": id,
                "vendor_name": vendor.name,
                "applications": [],
                "total_count": 0,
            }

        applications = (
            ApplicationComponent.query.filter(
                ApplicationComponent.vendor_product_id.in_(product_ids)
            )
            .order_by(ApplicationComponent.name)
            .all()
        )

        return {
            "vendor_id": id,
            "vendor_name": vendor.name,
            "applications": [
                {
                    "id": app.id,
                    "name": app.name,
                    "application_code": app.application_code,
                    "application_type": app.application_type,
                    "deployment_model": app.deployment_model,
                    "business_criticality": app.business_criticality,
                    "status": app.deployment_status,
                    "vendor_product_id": app.vendor_product_id,
                }
                for app in applications
            ],
            "total_count": len(applications),
        }


@ns_products.route("/")
class ProductList(Resource):
    @api.doc(
        "list_products",
        params={
            "vendor_id": "Filter by vendor organization ID",
            "category": "Filter by product category/family",
            "strategic_tier": "Filter by strategic tier (tier_1, tier_2, etc)",
            "limit": "Maximum results to return (default: 50)",
        },
    )
    @login_required
    @api.marshal_list_with(product_model)
    def get(self):
        """List and filter vendor products"""
        query = VendorProduct.query

        vendor_id = request.args.get("vendor_id", type=int)
        if vendor_id:
            query = query.filter_by(vendor_organization_id=vendor_id)

        product_family = request.args.get("product_family")
        if product_family:
            query = query.filter_by(product_family=product_family)

        deployment_model = request.args.get("deployment_model")
        if deployment_model:
            query = query.filter_by(deployment_model=deployment_model)

        search = request.args.get("search")
        if search:
            query = query.filter(VendorProduct.name.ilike(f"%{search}%"))

        products = query.order_by(VendorProduct.name).all()

        return [
            {
                "id": p.id,
                "vendor_id": p.vendor_organization_id,
                "product_name": p.name,
                "product_code": p.product_code,
                "product_family": p.product_family_name,
                "version": p.version,
                "deployment_model": p.deployment_model,
                "product_type": p.product_type,
                "status": p.status,
            }
            for p in products
        ]


@ns_products.route("/<int:id>")
@api.response(404, "Product not found")
@api.param("id", "The product identifier")
class Product(Resource):
    @api.doc("get_product")
    @login_required
    @api.marshal_with(product_model)
    def get(self, id):
        """Get a product by ID"""
        product = VendorProduct.query.get_or_404(id)
        return {
            "id": product.id,
            "vendor_id": product.vendor_organization_id,
            "product_name": product.name,
            "product_code": product.product_code,
            "product_family": product.product_family_name,
            "version": product.version,
            "deployment_model": product.deployment_model,
            "product_type": product.product_type,
            "status": product.status,
        }


@ns_analytics.route("/summary")
class AnalyticsSummary(Resource):
    @api.doc("get_analytics_summary")
    @login_required
    @api.marshal_with(analytics_summary_model)
    def get(self):
        """Get vendor portfolio analytics summary"""
        total_vendors = VendorOrganization.query.count()
        total_products = VendorProduct.query.count()
        strategic_partners = VendorOrganization.query.filter_by(
            partnership_level="strategic_partner"
        ).count()

        vendor_types = (
            db.session.query(VendorOrganization.vendor_type, func.count(VendorOrganization.id))
            .group_by(VendorOrganization.vendor_type)
            .all()
        )

        strategic_tiers = (
            db.session.query(VendorOrganization.strategic_tier, func.count(VendorOrganization.id))
            .group_by(VendorOrganization.strategic_tier)
            .all()
        )

        partnership_levels = (
            db.session.query(
                VendorOrganization.partnership_level, func.count(VendorOrganization.id)
            )
            .group_by(VendorOrganization.partnership_level)
            .all()
        )

        top_vendors = (
            VendorOrganization.query.order_by(desc(VendorOrganization.enterprise_readiness_score))
            .limit(5)
            .all()
        )

        return {
            "total_vendors": total_vendors,
            "total_products": total_products,
            "strategic_partners": strategic_partners,
            "by_vendor_type": {vt[0]: vt[1] for vt in vendor_types if vt[0]},
            "by_strategic_tier": {st[0]: st[1] for st in strategic_tiers if st[0]},
            "by_partnership_level": {pl[0]: pl[1] for pl in partnership_levels if pl[0]},
            "top_vendors": [
                {
                    "id": v.id,
                    "name": v.name,
                    "enterprise_readiness_score": v.enterprise_readiness_score,
                    "innovation_score": v.innovation_score,
                }
                for v in top_vendors
            ],
        }


@ns_analytics.route("/risk-assessment")
class RiskAssessment(Resource):
    @api.doc("get_risk_assessment")
    @login_required
    @api.marshal_list_with(vendor_risk_model)
    def get(self):
        """Get vendor risk assessment for all vendors"""
        vendors = VendorOrganization.query.all()
        risk_data = []

        for v in vendors:
            financial_risk = (100 - (v.financial_health_score or 50)) * 0.4
            lockin_risk = (v.vendor_lock_in_risk or 5) * 10 * 0.3
            acquisition_risk_scores = {"low": 10, "medium": 50, "high": 90}
            acquisition_risk_val = acquisition_risk_scores.get(v.acquisition_risk, 50) * 0.3
            overall_risk = financial_risk + lockin_risk + acquisition_risk_val

            if overall_risk < 30:
                risk_level = "low"
            elif overall_risk < 60:
                risk_level = "medium"
            elif overall_risk < 80:
                risk_level = "high"
            else:
                risk_level = "critical"

            risk_data.append(
                {
                    "vendor_id": v.id,
                    "vendor_name": v.name,
                    "financial_health_score": v.financial_health_score,
                    "vendor_lock_in_risk": v.vendor_lock_in_risk,
                    "acquisition_risk": v.acquisition_risk,
                    "technology_maturity": v.technology_maturity,
                    "overall_risk_score": round(overall_risk, 1),
                    "risk_level": risk_level,
                }
            )

        risk_data.sort(key=lambda x: x["overall_risk_score"], reverse=True)
        return risk_data


@ns_comparison.route("/")
class VendorComparison(Resource):
    @api.doc(
        "compare_vendors",
        params={"vendor_ids": "Comma-separated vendor IDs to compare (e.g., 1,2,3)"},
    )
    @login_required
    def get(self):
        """Compare multiple vendors side-by-side"""
        vendor_ids = request.args.get("vendor_ids", "")
        if not vendor_ids:
            return {"error": "vendor_ids parameter required (e.g., ?vendor_ids=1,2,3)"}, 400

        try:
            ids = [int(id.strip()) for id in vendor_ids.split(",")]
        except ValueError:
            return {"error": "Invalid vendor_ids format"}, 400

        vendors = VendorOrganization.query.filter(VendorOrganization.id.in_(ids)).all()

        if not vendors:
            return {"error": "No vendors found"}, 404

        comparison = []
        for v in vendors:
            product_count = VendorProduct.query.filter_by(vendor_organization_id=v.id).count()

            comparison.append(
                {
                    "vendor_id": v.id,
                    "name": v.name,
                    "vendor_type": v.vendor_type,
                    "headquarters_location": v.headquarters_location,
                    "year_founded": v.year_founded,
                    "employee_count": v.employee_count,
                    "strategic_tier": v.strategic_tier,
                    "partnership_level": v.partnership_level,
                    "enterprise_readiness_score": v.enterprise_readiness_score,
                    "innovation_score": v.innovation_score,
                    "financial_health_score": v.financial_health_score,
                    "vendor_lock_in_risk": v.vendor_lock_in_risk,
                    "product_count": product_count,
                    "strengths": v.get_strengths() if hasattr(v, "get_strengths") else [],
                    "weaknesses": v.get_weaknesses() if hasattr(v, "get_weaknesses") else [],
                }
            )

        return {"comparison": comparison, "vendor_count": len(comparison)}


# The remaining RESTX resource classes (APQC, analytics, etc.) are identical to the legacy file.
# Due to size, only the key resources are shown above. The full file includes all
# namespace resources from the legacy app/api_vendors.py.

@ns_analytics.route("/product-categories")
class ProductCategories(Resource):
    @api.doc("get_product_categories")
    @login_required
    def get(self):
        """Get product breakdown by category/family"""
        categories = (
            db.session.query(
                VendorProduct.product_family_name, func.count(VendorProduct.id).label("count")
            )
            .filter(VendorProduct.product_family_name.isnot(None))
            .group_by(VendorProduct.product_family_name)
            .order_by(desc("count"))
            .all()
        )

        return {
            "categories": [{"name": cat[0], "product_count": cat[1]} for cat in categories],
            "total_categories": len(categories),
        }


# ============================================================================
# APQC PCF API Models
# ============================================================================

apqc_process_model = api.model(
    "APQCProcess",
    {
        "id": fields.Integer(readonly=True, description="Process unique identifier"),
        "process_code": fields.String(description="APQC process code (e.g., 8.1.2)"),
        "process_name": fields.String(description="Process name"),
        "process_description": fields.String(description="Process description"),
        "category_level_1": fields.String(description="Level 1 category"),
        "category_level_2": fields.String(description="Level 2 category"),
        "process_category": fields.String(
            description="Category type (Strategic, Operational, Support)"
        ),
        "industry_domain": fields.String(description="Industry domain"),
        "apqc_level": fields.Integer(description="Hierarchy level (1 - 5)"),
        "archimate_mapping_level": fields.String(description="ArchiMate element type"),
        "process_maturity": fields.Integer(description="Maturity level (1 - 5)"),
    },
)

vendor_apqc_mapping_model = api.model(
    "VendorAPQCMapping",
    {
        "id": fields.Integer(readonly=True),
        "vendor_product_id": fields.Integer(description="Vendor product ID"),
        "vendor_product_name": fields.String(description="Product name"),
        "vendor_name": fields.String(description="Vendor organization name"),
        "apqc_process_id": fields.Integer(description="APQC process ID"),
        "process_code": fields.String(description="APQC process code"),
        "process_name": fields.String(description="APQC process name"),
        "relevance_score": fields.Integer(description="Relevance score (0 - 100)"),
        "coverage_level": fields.String(description="Coverage level"),
        "coverage_percentage": fields.Integer(description="Coverage percentage (0 - 100)"),
        "automation_capability": fields.Integer(description="Automation capability (0 - 100)"),
        "out_of_box_fit": fields.Integer(description="Out-of-box fit (0 - 100)"),
        "integration_complexity": fields.String(description="Integration complexity"),
        "confidence_level": fields.String(description="Confidence level"),
    },
)

apqc_comparison_model = api.model(
    "APQCComparison",
    {
        "process_code": fields.String(description="APQC process code"),
        "process_name": fields.String(description="APQC process name"),
        "vendor_coverage": fields.Raw(description="Coverage by each vendor"),
        "best_vendor": fields.String(description="Best vendor for this process"),
        "coverage_gap": fields.Boolean(description="Whether there is a coverage gap"),
    },
)

capability_process_model = api.model(
    "CapabilityProcess",
    {
        "id": fields.Integer(readonly=True),
        "capability_id": fields.Integer(description="Business capability ID"),
        "capability_name": fields.String(description="Business capability name"),
        "apqc_process_id": fields.Integer(description="APQC process ID"),
        "process_code": fields.String(description="APQC process code"),
        "process_name": fields.String(description="APQC process name"),
        "relationship_type": fields.String(description="Relationship type"),
        "relationship_strength": fields.Integer(description="Strength (1 - 5)"),
        "impact_level": fields.String(description="Impact level"),
        "process_contribution": fields.Integer(description="Process contribution (0 - 100)"),
    },
)


@ns_apqc.route("/processes")
class APQCProcessList(Resource):
    @api.doc("list_apqc_processes", params={"category": "Filter by APQC category", "level": "Filter by process level (1-3)", "limit": "Max results"})
    @login_required
    @api.marshal_list_with(apqc_process_model)
    def get(self):
        """List all APQC PCF processes with optional filtering"""
        query = APQCProcess.query
        level = request.args.get("level", type=int)
        category = request.args.get("category")
        search = request.args.get("search")

        if category:
            query = query.filter(APQCProcess.process_category == category)
        if search:
            query = query.filter(APQCProcess.process_name.ilike(f"%{search}%"))

        processes = query.order_by(APQCProcess.process_code).all()
        if level:
            processes = [p for p in processes if p.apqc_level == level]

        return [p.to_dict() for p in processes]


@ns_apqc.route("/processes/<int:id>")
@api.response(404, "Process not found")
class APQCProcessDetail(Resource):
    @api.doc("get_apqc_process")
    @login_required
    @api.marshal_with(apqc_process_model)
    def get(self, id):
        """Get an APQC process by ID"""
        process = APQCProcess.query.get_or_404(id)
        return process.to_dict()


@ns_apqc.route("/processes/<int:id>/vendors")
@api.response(404, "Process not found")
class APQCProcessVendors(Resource):
    @api.doc("get_process_vendors")
    @login_required
    def get(self, id):
        """Get all vendor products supporting a specific APQC process"""
        process = APQCProcess.query.get_or_404(id)
        mappings = (
            VendorProductAPQCMapping.query.filter_by(apqc_process_id=id)
            .order_by(desc(VendorProductAPQCMapping.relevance_score))
            .all()
        )
        vendors = []
        for m in mappings:
            product = VendorProduct.query.get(m.vendor_product_id)
            if product:
                vendor = VendorOrganization.query.get(product.vendor_organization_id)
                vendors.append({
                    "mapping_id": m.id, "vendor_product_id": product.id,
                    "vendor_product_name": product.name,
                    "vendor_id": vendor.id if vendor else None,
                    "vendor_name": vendor.name if vendor else "Unknown",
                    "relevance_score": m.relevance_score, "coverage_level": m.coverage_level,
                    "coverage_percentage": m.coverage_percentage,
                    "automation_capability": m.automation_capability,
                    "out_of_box_fit": m.out_of_box_fit,
                    "integration_complexity": m.integration_complexity,
                    "confidence_level": m.confidence_level,
                    "gaps": m.gaps, "workarounds": m.workarounds,
                })
        return {"process": process.to_dict(), "vendors": vendors, "vendor_count": len(vendors)}


@ns_apqc.route("/processes/by-code/<string:code>")
class APQCProcessByCode(Resource):
    @api.doc("get_process_by_code")
    @login_required
    def get(self, code):
        """Get an APQC process by its code (e.g., 8.1.2)"""
        process = APQCProcess.query.filter_by(process_code=code).first()
        if not process:
            return {"error": f"Process with code {code} not found"}, 404
        return process.to_dict()


@ns_products.route("/<int:id>/apqc-processes")
@api.response(404, "Product not found")
class ProductAPQCProcesses(Resource):
    @api.doc("get_product_apqc_processes")
    @login_required
    def get(self, id):
        """Get all APQC processes supported by a vendor product"""
        product = VendorProduct.query.get_or_404(id)
        vendor = VendorOrganization.query.get(product.vendor_organization_id)
        mappings = (
            VendorProductAPQCMapping.query.filter_by(vendor_product_id=id)
            .order_by(desc(VendorProductAPQCMapping.relevance_score))
            .all()
        )
        processes = []
        for m in mappings:
            apqc = APQCProcess.query.get(m.apqc_process_id)
            if apqc:
                processes.append({
                    "mapping_id": m.id, "process_id": apqc.id,
                    "process_code": apqc.process_code, "process_name": apqc.process_name,
                    "category": apqc.process_category, "apqc_level": apqc.apqc_level,
                    "relevance_score": m.relevance_score, "coverage_level": m.coverage_level,
                    "coverage_percentage": m.coverage_percentage,
                    "automation_capability": m.automation_capability,
                    "out_of_box_fit": m.out_of_box_fit,
                    "integration_complexity": m.integration_complexity,
                    "confidence_level": m.confidence_level,
                    "gaps": m.gaps, "missing_features": m.missing_features,
                })
        by_category = {}
        for p in processes:
            cat = p.get("category", "Other")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(p)
        return {
            "product": {"id": product.id, "name": product.name, "vendor_name": vendor.name if vendor else "Unknown"},
            "processes": processes, "by_category": by_category, "process_count": len(processes),
            "coverage_summary": {
                "avg_coverage": sum(p["coverage_percentage"] for p in processes) / len(processes) if processes else 0,
                "avg_automation": sum(p["automation_capability"] for p in processes) / len(processes) if processes else 0,
                "full_coverage_count": sum(1 for p in processes if p["coverage_level"] == "full"),
            },
        }


@ns_comparison.route("/apqc")
class VendorAPQCComparison(Resource):
    @api.doc("compare_vendors_by_apqc", params={"vendor_ids": "Comma-separated vendor org IDs", "product_ids": "Comma-separated product IDs", "process_codes": "Optional APQC process codes", "category": "Optional APQC category"})
    @login_required
    def get(self):
        """Compare vendors by APQC process coverage."""
        vendor_ids = request.args.get("vendor_ids", "")
        product_ids = request.args.get("product_ids", "")
        process_codes = request.args.get("process_codes", "")
        category = request.args.get("category")

        products = []
        if product_ids:
            try:
                ids = [int(id.strip()) for id in product_ids.split(",")]
                products = VendorProduct.query.filter(VendorProduct.id.in_(ids)).all()
            except ValueError:
                return {"error": "Invalid product_ids format"}, 400
        elif vendor_ids:
            try:
                ids = [int(id.strip()) for id in vendor_ids.split(",")]
                products = VendorProduct.query.filter(VendorProduct.vendor_organization_id.in_(ids)).all()
            except ValueError:
                return {"error": "Invalid vendor_ids format"}, 400

        if not products:
            return {"error": "No products found. Provide vendor_ids or product_ids."}, 400

        process_query = APQCProcess.query
        if category:
            process_query = process_query.filter(APQCProcess.process_category == category)
        all_processes = process_query.order_by(APQCProcess.process_code).all()
        if process_codes:
            codes = [c.strip() for c in process_codes.split(",")]
            all_processes = [p for p in all_processes if p.process_code in codes]

        comparison_matrix = []
        product_summaries = {p.id: {"total_coverage": 0, "process_count": 0, "gaps": []} for p in products}

        for process in all_processes:
            row = {"process_id": process.id, "process_code": process.process_code, "process_name": process.process_name, "category": process.process_category, "apqc_level": process.apqc_level, "vendor_coverage": {}, "best_vendor": None, "best_score": 0, "coverage_gap": True}
            for product in products:
                vendor = VendorOrganization.query.get(product.vendor_organization_id)
                vendor_key = f"{vendor.name if vendor else 'Unknown'} - {product.name}"
                mapping = VendorProductAPQCMapping.query.filter_by(vendor_product_id=product.id, apqc_process_id=process.id).first()
                if mapping:
                    coverage_score = mapping.coverage_percentage * 0.4 + mapping.automation_capability * 0.3 + mapping.out_of_box_fit * 0.3
                    row["vendor_coverage"][vendor_key] = {"product_id": product.id, "coverage_level": mapping.coverage_level, "coverage_percentage": mapping.coverage_percentage, "automation_capability": mapping.automation_capability, "out_of_box_fit": mapping.out_of_box_fit, "relevance_score": mapping.relevance_score, "composite_score": round(coverage_score, 1), "gaps": mapping.gaps, "integration_complexity": mapping.integration_complexity}
                    if coverage_score > row["best_score"]:
                        row["best_score"] = coverage_score
                        row["best_vendor"] = vendor_key
                    if mapping.coverage_percentage >= 50:
                        row["coverage_gap"] = False
                    product_summaries[product.id]["total_coverage"] += mapping.coverage_percentage
                    product_summaries[product.id]["process_count"] += 1
                else:
                    row["vendor_coverage"][vendor_key] = {"product_id": product.id, "coverage_level": "none", "coverage_percentage": 0, "composite_score": 0}
                    product_summaries[product.id]["gaps"].append(process.process_code)
            comparison_matrix.append(row)

        vendor_scores = []
        for product in products:
            vendor = VendorOrganization.query.get(product.vendor_organization_id)
            summary = product_summaries[product.id]
            avg_coverage = summary["total_coverage"] / summary["process_count"] if summary["process_count"] > 0 else 0
            vendor_scores.append({"product_id": product.id, "product_name": product.name, "vendor_name": vendor.name if vendor else "Unknown", "processes_covered": summary["process_count"], "avg_coverage": round(avg_coverage, 1), "gaps": summary["gaps"], "gap_count": len(summary["gaps"])})
        vendor_scores.sort(key=lambda x: x["avg_coverage"], reverse=True)
        uncovered = [row for row in comparison_matrix if row["coverage_gap"]]
        return {"comparison_matrix": comparison_matrix, "vendor_scores": vendor_scores, "summary": {"total_processes": len(all_processes), "products_compared": len(products), "uncovered_processes": len(uncovered), "recommended_vendor": vendor_scores[0] if vendor_scores else None}, "uncovered_processes": [{"process_code": p["process_code"], "process_name": p["process_name"]} for p in uncovered]}


@ns_apqc.route("/capability-mappings")
class CapabilityProcessMappingList(Resource):
    @api.doc("list_capability_process_mappings", params={"capability_id": "Filter by capability ID", "process_id": "Filter by APQC process ID", "relationship_type": "Filter by relationship type"})
    @login_required
    @api.marshal_list_with(capability_process_model)
    def get(self):
        """List capability-to-process mappings"""
        from app.services.apqc_mapping_tenant_fence import (
            fenced_capability_mappings_query,
        )

        # CapabilityProcessMapping has no organization_id of its own;
        # unfenced, this returned every organisation's rows.
        query = fenced_capability_mappings_query()
        capability_id = request.args.get("capability_id", type=int)
        process_id = request.args.get("process_id", type=int)
        relationship_type = request.args.get("relationship_type")
        if capability_id:
            query = query.filter(CapabilityProcessMapping.capability_id == capability_id)
        if process_id:
            query = query.filter(CapabilityProcessMapping.apqc_process_id == process_id)
        if relationship_type:
            query = query.filter(CapabilityProcessMapping.relationship_type == relationship_type)
        mappings = query.all()
        return [m.to_dict() for m in mappings]

    @api.doc("create_capability_process_mapping")
    @api.expect(api.model("CapabilityProcessInput", {"capability_id": fields.Integer(required=True), "apqc_process_id": fields.Integer(required=True), "relationship_type": fields.String(default="enables"), "relationship_strength": fields.Integer(default=3), "impact_level": fields.String(default="medium"), "process_contribution": fields.Integer(default=50)}))
    @login_required
    def post(self):
        """Create a new capability-process mapping"""
        from app.services.apqc_mapping_tenant_fence import (
            capability_owned_by_caller,
            fenced_capability_mappings_query,
        )

        data = request.json
        # Neither the duplicate-mapping check nor the create itself was
        # fenced: the dup-check leaked another organisation's mapping id in
        # its 409 body, and the create let a caller point a new mapping at
        # another organisation's capability_id (found in this consolidation
        # sweep -- no prior review of this route had flagged it).
        capability_id = data.get("capability_id")
        if not capability_owned_by_caller(capability_id):
            return {"error": f"Capability not found: {capability_id}"}, 404
        existing = fenced_capability_mappings_query().filter(
            CapabilityProcessMapping.capability_id == capability_id,
            CapabilityProcessMapping.apqc_process_id == data.get("apqc_process_id"),
        ).first()
        if existing:
            return {"error": "Mapping already exists", "id": existing.id}, 409
        mapping = CapabilityProcessMapping(capability_id=capability_id, apqc_process_id=data.get("apqc_process_id"), relationship_type=data.get("relationship_type", "enables"), relationship_strength=data.get("relationship_strength", 3), impact_level=data.get("impact_level", "medium"), process_contribution=data.get("process_contribution", 50))
        db.session.add(mapping)
        db.session.commit()
        return {"message": "Mapping created", "id": mapping.id}, 201


@ns_apqc.route("/processes/<int:id>/capabilities")
class ProcessCapabilities(Resource):
    @api.doc("get_process_capabilities")
    @login_required
    def get(self, id):
        """Get all business capabilities linked to an APQC process"""
        from app.services.apqc_mapping_tenant_fence import (
            fenced_capability_mappings_query,
        )

        process = APQCProcess.query.get_or_404(id)
        mappings = fenced_capability_mappings_query().filter(
            CapabilityProcessMapping.apqc_process_id == id
        ).all()
        return {"process": process.to_dict(), "capabilities": [m.to_dict() for m in mappings], "capability_count": len(mappings)}


@ns_apqc.route("/vendor-capability-process-matrix")
class VendorCapabilityProcessMatrix(Resource):
    @api.doc("get_vendor_capability_process_matrix", params={"vendor_id": "Filter by vendor org ID", "capability_id": "Filter by capability ID"})
    @login_required
    def get(self):
        """Get the complete Vendor -> APQC Process -> Capability matrix."""
        vendor_id = request.args.get("vendor_id", type=int)
        product_id = request.args.get("product_id", type=int)
        capability_id = request.args.get("capability_id", type=int)
        if not vendor_id and not product_id:
            return {"error": "Provide vendor_id or product_id"}, 400
        if product_id:
            products = [VendorProduct.query.get_or_404(product_id)]
        else:
            products = VendorProduct.query.filter_by(vendor_organization_id=vendor_id).all()
        matrix = []
        for product in products:
            vendor = VendorOrganization.query.get(product.vendor_organization_id)
            apqc_mappings = VendorProductAPQCMapping.query.filter_by(vendor_product_id=product.id).all()
            for apqc_map in apqc_mappings:
                process = APQCProcess.query.get(apqc_map.apqc_process_id)
                if not process:
                    continue
                from app.services.apqc_mapping_tenant_fence import (
                    fenced_capability_mappings_query,
                )

                # CapabilityProcessMapping has no organization_id of its own
                # -- fence through BusinessCapability, same pattern as the
                # two endpoints above in this file (pr306-v2 review,
                # DEFECT D1-D3 as originally found).
                cap_mappings = fenced_capability_mappings_query().filter(
                    CapabilityProcessMapping.apqc_process_id == process.id
                ).all()
                if capability_id:
                    cap_mappings = [c for c in cap_mappings if c.capability_id == capability_id]
                for cap_map in cap_mappings:
                    matrix.append({"vendor_name": vendor.name if vendor else "Unknown", "product_name": product.name, "product_id": product.id, "process_code": process.process_code, "process_name": process.process_name, "process_id": process.id, "capability_id": cap_map.capability_id, "capability_name": cap_map.capability.name if cap_map.capability else None, "vendor_coverage": apqc_map.coverage_percentage, "vendor_automation": apqc_map.automation_capability, "capability_contribution": cap_map.process_contribution, "relationship_type": cap_map.relationship_type, "combined_score": round((apqc_map.coverage_percentage * cap_map.process_contribution) / 100, 1)})
        by_capability = {}
        for item in matrix:
            cap_name = item.get("capability_name", "Unknown")
            if cap_name not in by_capability:
                by_capability[cap_name] = []
            by_capability[cap_name].append(item)
        return {"matrix": matrix, "by_capability": by_capability, "total_mappings": len(matrix), "capabilities_covered": len(by_capability)}


@ns_analytics.route("/overview")
class PortfolioOverview(Resource):
    @api.doc("get_portfolio_overview")
    @login_required
    def get(self):
        """Return vendors & products, capabilities and APQC processes."""
        result = {}
        try:
            vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()
            vendor_list = []
            for v in vendors:
                products = VendorProduct.query.filter_by(vendor_organization_id=v.id).order_by(VendorProduct.name).all()
                vendor_list.append({"id": v.id, "name": v.name, "products": [{"id": p.id, "name": p.name, "product_code": p.product_code} for p in products]})
            result["vendors"] = vendor_list
        except Exception as e:
            result["vendors_error"] = str(e)
        try:
            from app.models.unified_capability import UnifiedCapability
            app_caps_q = UnifiedCapability.query.filter_by(specialization_type="APPLICATION").order_by(UnifiedCapability.name).all()
            result["application_capabilities"] = [{"id": c.id, "name": c.name, "level": c.level} for c in app_caps_q]
        except Exception as e:
            result["application_capabilities_error"] = str(e)
        try:
            from app.models.technical_capability import TechnicalCapability
            tech_caps_q = TechnicalCapability.query.order_by(TechnicalCapability.acm_domain, TechnicalCapability.code).all()
            result["technical_capabilities"] = [t.to_dict() for t in tech_caps_q]
        except Exception as e:
            result["technical_capabilities_error"] = str(e)
        try:
            try:
                from app.models.manufacturing_capability import ManufacturingCapability
                manuf_q = ManufacturingCapability.query.order_by(ManufacturingCapability.name).all()
                result["manufacturing_capabilities"] = [{"id": c.id, "name": c.name} for c in manuf_q]
            except Exception:
                from app.models.unified_capability import UnifiedCapability
                manuf_q = UnifiedCapability.query.filter_by(specialization_type="MANUFACTURING").order_by(UnifiedCapability.name).all()
                result["manufacturing_capabilities"] = [{"id": c.id, "name": c.name} for c in manuf_q]
        except Exception as e:
            result["manufacturing_capabilities_error"] = str(e)
        try:
            from app.models.business_capabilities import BusinessCapability
            bus_q = BusinessCapability.query.order_by(BusinessCapability.name).all()
            result["business_capabilities"] = [{"id": c.id, "name": c.name} for c in bus_q]
        except Exception as e:
            result["business_capabilities_error"] = str(e)
        try:
            processes = APQCProcess.query.order_by(APQCProcess.process_code).all()
            result["apqc_processes"] = [p.to_dict() for p in processes]
        except Exception as e:
            result["apqc_processes_error"] = str(e)
        return result
