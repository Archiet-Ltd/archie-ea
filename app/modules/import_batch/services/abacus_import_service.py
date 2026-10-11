"""
-> app.modules.import_batch.services.import_service

Abacus Import Service

Imports applications and capabilities from Avolution Abacus into A.R.C.I.E database.
Implements smart merge strategy that preserves A.R.C.I.E enrichments while importing Abacus data.

Key Features:
- Import applications as ApplicationComponent records
- Import capabilities as BusinessCapability records with hierarchy
- Preserve A.R.C.I.E enrichments (TCO, rationalization_score, vendor_risk)
- Merge conflict resolution using alias fields (abacus_name + name)
- Store Abacus IDs for sync reference (app_id, archimate_element_id)
- Set discovered_by_ai=False to distinguish from AI imports
- Track import statistics (created, updated, skipped, errors)
"""

import logging
import re
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from app import db
from app.config.abacus_field_mapping import normalize_lifecycle_status
from app.connectors.abacus import AbacusConnector
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.services.application_cost_accessor import set_annual_cost

try:
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
except ImportError:
    ArchiMateElement = None  # type: ignore[assignment,misc]
    ArchiMateRelationship = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


class AbacusImportService:
    """
    Service for importing Avolution Abacus data into A.R.C.I.E database.

    Implements smart merge strategy:
    - Alias fields: Both abacus_name and name fields maintained
    - Authority levels: Abacus wins, A.R.C.I.E wins, or merge
    - Preserve enrichments: TCO, rationalization, vendor analysis
    - Store Abacus IDs for incremental sync
    """

    # Fields that are enrichments in A.R.C.I.E and should NEVER be overwritten by Abacus
    ENRICHMENT_FIELDS_APPLICATION = {
        "total_cost_of_ownership",
        "license_cost",
        "maintenance_cost",
        "infrastructure_cost",
        "support_cost",
        "implementation_cost",
        "roi_score",
        "technical_risk",
        "business_risk",
        "vendor_risk",
        "obsolescence_risk",
        "rationalization_score",
        "consolidation_candidate",
        "user_satisfaction_score",
        "performance_rating",
        "availability_actual",
        "strategic_importance",  # A.R.C.I.E architects set this
        "business_value",  # A.R.C.I.E architects set this
    }

    ENRICHMENT_FIELDS_CAPABILITY = {
        "current_maturity_level",
        "target_maturity_level",
        "maturity_gap",
        "maturity_assessment_date",
        "maturity_assessment_notes",
        "strategic_importance",  # A.R.C.I.E architects set this
        "business_value",  # A.R.C.I.E architects set this
        "roi_score",
        "performance_score",
        "kpis",
    }

    def __init__(self, connector: AbacusConnector):
        """
        Initialize import service.

        Args:
            connector: Configured AbacusConnector instance
        """
        self.connector = connector
        self.stats = {
            "applications_created": 0,
            "applications_updated": 0,
            "applications_skipped": 0,
            "applications_errors": 0,
            "capabilities_created": 0,
            "capabilities_updated": 0,
            "capabilities_skipped": 0,
            "capabilities_errors": 0,
            "relationships_created": 0,
            "relationships_updated": 0,
            "relationships_errors": 0,
            "relationships_resolved_by_archimate_id": 0,
            "relationships_resolved_by_exact_name": 0,
            "relationships_resolved_by_fuzzy_match": 0,
            "relationships_ambiguous": 0,
            "relationships_unresolved": 0,
            "hierarchy_linked_from_batch": 0,
            "hierarchy_linked_from_db": 0,
            "hierarchy_orphaned": 0,
            "typed_relationships_created": 0,
            "typed_relationships_skipped_duplicate": 0,
            "typed_relationships_elements_created": 0,
            "typed_relationships_errors": 0,
        }

    async def import_all(self) -> Dict[str, int]:
        """
        Import all data types from Abacus: applications, capabilities, relationships.

        Returns:
            Dictionary with import statistics
        """
        logger.info("Starting full Abacus import...")

        try:
            # Step 1: Fetch all data from Abacus in parallel
            batch_result = await self.connector.batch_sync()

            applications = batch_result.get("applications", [])
            capabilities = batch_result.get("capabilities", [])
            relationships = batch_result.get("relationships", [])

            logger.info(
                f"Fetched from Abacus: {len(applications)} apps, "
                f"{len(capabilities)} caps, {len(relationships)} rels"
            )

            # Step 2: Import capabilities first (needed for relationships)
            await self.import_capabilities(capabilities)

            # Step 2b: Build capability hierarchy from naming convention
            self._build_capability_hierarchy()

            # Step 2c: Resolve deferred parent references that could not be
            # resolved during import_capabilities (parent arrived in later
            # batch or was only linked via naming convention hierarchy)
            self._resolve_deferred_parents()

            # Step 2d: Resolve ArchiMate element FKs by name matching
            self._resolve_archimate_elements()

            # Step 3: Import applications
            await self.import_applications(applications)

            # Step 4: Import relationships (app-capability mappings)
            await self.import_relationships(relationships)

            logger.info(f"Abacus import completed: {self.stats}")
            return self.stats

        except Exception as e:
            logger.error(f"Abacus import failed: {e}", exc_info=True)
            raise

    async def import_applications(self, applications: List[Dict]) -> None:
        """
        Import applications from Abacus as ApplicationComponent records.

        Merge strategy:
        - New records: Create with Abacus data + discovered_by_ai=False
        - Existing records (by app_id): Merge Abacus data, preserve enrichments
        - Store Abacus app_id in application_code field for sync reference

        Args:
            applications: List of application dictionaries from Abacus
        """
        logger.info(f"Importing {len(applications)} applications...")

        # Batch-load all existing Abacus applications to avoid N+1 queries in the loop
        existing_abacus_apps = ApplicationComponent.query.filter(
            ApplicationComponent.application_code.like("ABACUS-%")
        ).all()
        app_code_lookup = {a.application_code: a for a in existing_abacus_apps}

        for app_data in applications:
            try:
                # Extract Abacus EEID for matching (connector returns external_id/eeid)
                abacus_app_id = (
                    app_data.get("external_id") or app_data.get("eeid") or app_data.get("id")
                )
                if not abacus_app_id:
                    logger.warning(f"Application missing ID: {app_data.get('name')}")
                    self.stats["applications_skipped"] += 1
                    continue

                # Check if application already exists using pre-loaded lookup
                existing_app = app_code_lookup.get(f"ABACUS-{abacus_app_id}")

                if existing_app:
                    # Update existing application
                    self._update_application(existing_app, app_data)
                    self.stats["applications_updated"] += 1
                    logger.debug(f"Updated application: {existing_app.name}")
                else:
                    # Create new application
                    new_app = self._create_application(app_data, abacus_app_id)
                    db.session.add(new_app)
                    # Update lookup so subsequent iterations find this application
                    app_code_lookup[f"ABACUS-{abacus_app_id}"] = new_app
                    self.stats["applications_created"] += 1
                    logger.debug(f"Created application: {new_app.name}")

                # Commit after each application to avoid large rollbacks
                db.session.commit()

            except IntegrityError as e:
                db.session.rollback()
                logger.error(f"Integrity error importing app {app_data.get('name')}: {e}")
                self.stats["applications_errors"] += 1

            except Exception as e:
                db.session.rollback()
                logger.error(
                    f"Error importing application {app_data.get('name')}: {e}", exc_info=True
                )
                self.stats["applications_errors"] += 1

    def _create_application(self, app_data: Dict, abacus_app_id: str) -> ApplicationComponent:
        """
        Create new ApplicationComponent from Abacus data.

        Uses connector-transformed data directly (lowercase keys) rather than
        re-applying field mappings that expect raw API format (capitalized keys).

        Args:
            app_data: Connector-transformed application data (lowercase keys)
            abacus_app_id: Abacus application ID (EEID)

        Returns:
            New ApplicationComponent instance
        """
        name = app_data.get("name") or f"APP-{abacus_app_id}"
        description = app_data.get("description", "")

        # Parse user count from connector (may be a string like "500")
        raw_users = app_data.get("number_of_users", "")
        user_count_val = None
        if raw_users:
            try:
                user_count_val = int(str(raw_users).strip())
            except (ValueError, TypeError):
                pass

        app = ApplicationComponent(
            name=name,
            description=description,
            application_code=f"ABACUS-{abacus_app_id}",
            discovered_by_ai=False,
            abacus_source=True,
            lifecycle_status=(
                normalize_lifecycle_status(app_data["lifecycle_status"])
                if app_data.get("lifecycle_status")
                else None
            ),
            deployment_status=app_data.get("deployment_status") or None,
            business_owner=app_data.get("business_owner") or None,
            technical_owner=app_data.get("technical_owner") or None,
            application_owner=app_data.get("application_owner") or None,
            vendor_name=app_data.get("vendor_name") or None,
            business_criticality=app_data.get("business_criticality") or app_data.get("criticality") or None,
            business_domain=app_data.get("business_domain") or None,
            application_type=app_data.get("application_type") or None,
            application_category=app_data.get("application_category") or None,
            data_classification=app_data.get("data_classification") or None,
            technical_risk=(app_data.get("risk_level") or "").lower() or None,
            abacus_properties=app_data.get("abacus_properties") or None,
            # Financial data extracted from Abacus properties
            user_count=user_count_val,
        )

        # Set annual cost through the accessor
        abacus_cost = app_data.get("annual_cost")
        if abacus_cost is not None:
            set_annual_cost(app, Decimal(str(abacus_cost)))

        return app

    def _update_application(self, app: ApplicationComponent, app_data: Dict) -> None:
        """
        Update existing ApplicationComponent with Abacus data.

        Merge strategy:
        - Abacus authority fields: Overwrite (name, description, lifecycle_status, etc.)
        - A.R.C.I.E enrichments: Preserve (TCO, rationalization_score, vendor_risk)

        Uses connector-transformed data directly (lowercase keys).

        Args:
            app: Existing ApplicationComponent
            app_data: Connector-transformed application data (lowercase keys)
        """
        # Abacus-authoritative fields: always update
        name = app_data.get("name")
        if name:
            app.name = name

        description = app_data.get("description")
        if description:
            app.description = description

        # Parse user count from connector (may be a string like "500")
        raw_users = app_data.get("number_of_users", "")
        user_count_val = None
        if raw_users:
            try:
                user_count_val = int(str(raw_users).strip())
            except (ValueError, TypeError):
                pass

        # Update non-enrichment fields from connector data
        abacus_fields = {
            "lifecycle_status": (
                normalize_lifecycle_status(app_data["lifecycle_status"])
                if app_data.get("lifecycle_status")
                else None
            ),
            "deployment_status": app_data.get("deployment_status"),
            "business_owner": app_data.get("business_owner"),
            "technical_owner": app_data.get("technical_owner"),
            "application_owner": app_data.get("application_owner"),
            "vendor_name": app_data.get("vendor_name"),
            "business_criticality": app_data.get("business_criticality") or app_data.get("criticality"),
            "business_domain": app_data.get("business_domain"),
            "application_type": app_data.get("application_type"),
            "application_category": app_data.get("application_category"),
            "data_classification": app_data.get("data_classification"),
            "technical_risk": (app_data.get("risk_level") or "").lower() or None,
            # user_count is Abacus-authoritative — always overwrite
            "user_count": user_count_val,
        }

        for field_name, value in abacus_fields.items():
            if value and hasattr(app, field_name):
                setattr(app, field_name, value)

        # total_cost_of_ownership is an enrichment field — only backfill when blank
        # (preserve any value set manually by architects)
        abacus_cost = app_data.get("annual_cost")
        if abacus_cost is not None and not getattr(app, "total_cost_of_ownership", None):
            set_annual_cost(app, Decimal(str(abacus_cost)))

        # Always persist raw Abacus properties for future field discovery
        raw_props = app_data.get("abacus_properties")
        if raw_props and hasattr(app, "abacus_properties"):
            app.abacus_properties = raw_props

        # Update timestamp
        app.updated_at = datetime.utcnow()

    async def import_capabilities(self, capabilities: List[Dict]) -> None:
        """
        Import capabilities from Abacus as BusinessCapability records with hierarchy.

        Uses a two-pass approach to handle cross-batch parent resolution:
        1. First pass: Create/update ALL capabilities WITHOUT parent_id
        2. Second pass: Resolve parent-child relationships using batch lookup + DB fallback

        Unresolved parent references are stored in self._deferred_parents for
        later resolution after _build_capability_hierarchy runs (which may
        create the parent via naming convention).

        Hierarchy management:
        - L1 capabilities: parent_capability_id = None
        - L2 capabilities: parent_capability_id = L1 parent
        - L3 capabilities: parent_capability_id = L2 parent

        Merge strategy:
        - New records: Create with Abacus data + discovered_by_ai=False
        - Existing records (by archimate_element_id): Merge, preserve enrichments
        - Store Abacus archimate_element_id for sync reference

        Args:
            capabilities: List of capability dictionaries from Abacus
        """
        logger.info(f"Importing {len(capabilities)} capabilities...")

        # Build capability lookup by archimate_element_id for parent resolution
        capability_lookup = {}

        # Track deferred parent references for resolution after hierarchy build
        self._deferred_parents = []

        # Batch-load all existing capabilities by archimate_id to avoid N+1 queries
        all_existing_caps = BusinessCapability.query.filter(
            BusinessCapability.archimate_id.isnot(None)
        ).all()
        cap_archimate_lookup = {c.archimate_id: c for c in all_existing_caps}

        # First pass: Create/update all capabilities WITHOUT parent_id.
        # Parent resolution happens entirely in the second pass to avoid
        # ordering dependencies (child may appear before parent in the list).
        for cap_data in capabilities:
            try:
                # Extract Abacus EEID (connector returns external_id/eeid)
                abacus_archimate_element_id = (
                    cap_data.get("external_id")
                    or cap_data.get("eeid")
                    or cap_data.get("archimate_element_id")
                )
                if not abacus_archimate_element_id:
                    logger.warning(f"Capability missing ID: {cap_data.get('name')}")
                    self.stats["capabilities_skipped"] += 1
                    continue

                # Check if capability exists using pre-loaded lookup
                existing_cap = cap_archimate_lookup.get(abacus_archimate_element_id)

                if existing_cap:
                    # Update existing capability (do NOT touch parent_capability_id here)
                    self._update_capability(existing_cap, cap_data)
                    capability_lookup[abacus_archimate_element_id] = existing_cap
                    self.stats["capabilities_updated"] += 1
                    logger.debug(f"Updated capability: {existing_cap.name}")
                else:
                    # Create new capability (parent resolved in second pass)
                    new_cap = self._create_capability(cap_data, abacus_archimate_element_id)
                    db.session.add(new_cap)
                    db.session.flush()  # Get ID for parent resolution
                    capability_lookup[abacus_archimate_element_id] = new_cap
                    # Update lookup so subsequent iterations find this capability
                    cap_archimate_lookup[abacus_archimate_element_id] = new_cap
                    self.stats["capabilities_created"] += 1
                    logger.debug(f"Created capability: {new_cap.name}")

                db.session.commit()

            except IntegrityError as e:
                db.session.rollback()
                logger.error(f"Integrity error importing cap {cap_data.get('name')}: {e}")
                self.stats["capabilities_errors"] += 1

            except Exception as e:
                db.session.rollback()
                logger.error(
                    f"Error importing capability {cap_data.get('name')}: {e}", exc_info=True
                )
                self.stats["capabilities_errors"] += 1

        # Refresh capability_lookup with fresh DB references to avoid detached
        # instance errors after per-record commits above (batch query)
        archimate_ids_to_refresh = list(capability_lookup.keys())
        if archimate_ids_to_refresh:
            refreshed_caps = BusinessCapability.query.filter(
                BusinessCapability.archimate_id.in_(archimate_ids_to_refresh)
            ).all()
            capability_lookup = {c.archimate_id: c for c in refreshed_caps}
        else:
            capability_lookup = {}

        # Second pass: Resolve parent-child relationships
        # Pre-load ALL capabilities by archimate_id (includes previous syncs)
        # so DB fallback queries are not needed inside the loop.
        all_caps_with_archimate = BusinessCapability.query.filter(
            BusinessCapability.archimate_id.isnot(None)
        ).all()
        all_caps_lookup = {c.archimate_id: c for c in all_caps_with_archimate}
        # Merge batch-refreshed lookup into the full lookup
        all_caps_lookup.update(capability_lookup)
        capability_lookup = all_caps_lookup

        for cap_data in capabilities:
            try:
                abacus_archimate_element_id = (
                    cap_data.get("external_id")
                    or cap_data.get("eeid")
                    or cap_data.get("archimate_element_id")
                )
                parent_archimate_element_id = cap_data.get("parent_id") or cap_data.get(
                    "parent_archimate_element_id"
                )

                if not (abacus_archimate_element_id and parent_archimate_element_id):
                    continue

                child_cap = capability_lookup.get(abacus_archimate_element_id)
                if not child_cap:
                    continue

                # Skip if parent is already set (e.g. from a previous sync)
                if child_cap.parent_capability_id:
                    continue

                # Look up parent from current batch lookup first
                parent_cap = capability_lookup.get(parent_archimate_element_id)
                
                # If not found in current batch, check database lookup (previous syncs)
                if not parent_cap:
                    parent_cap = cap_archimate_lookup.get(parent_archimate_element_id)

                if parent_cap:
                    child_cap.parent_capability_id = parent_cap.id
                    # Track different resolution sources for statistics
                    if parent_archimate_element_id in capability_lookup:
                        self.stats["hierarchy_linked_from_batch"] += 1
                        logger.debug(
                            f"Linked {child_cap.name} (L{child_cap.level}) "
                            f"to parent {parent_cap.name} (L{parent_cap.level}) [batch]"
                        )
                    else:
                        self.stats["hierarchy_linked_from_db"] += 1
                        logger.debug(
                            f"Linked {child_cap.name} (L{child_cap.level}) "
                            f"to parent {parent_cap.name} (L{parent_cap.level}) [database]"
                        )
                else:
                        # Parent not found yet - defer for resolution after
                        # _build_capability_hierarchy (which may create the parent
                        # from naming convention or a later sync batch)
                        self._deferred_parents.append({
                            "child_archimate_id": abacus_archimate_element_id,
                            "parent_archimate_id": parent_archimate_element_id,
                        })
                        self.stats["hierarchy_orphaned"] += 1
                        logger.warning(
                            f"Deferred parent resolution: {child_cap.name} "
                            f"(archimate_id={abacus_archimate_element_id}) — "
                            f"parent {parent_archimate_element_id} not found in batch or DB"
                        )

                db.session.commit()

            except Exception as e:
                db.session.rollback()
                logger.error(f"Error linking capability hierarchy: {e}", exc_info=True)

    def _create_capability(
        self, cap_data: Dict, abacus_archimate_element_id: str
    ) -> BusinessCapability:
        """
        Create new BusinessCapability from Abacus data.

        Uses connector-transformed data directly (lowercase keys) rather than
        re-applying field mappings that expect raw API format (capitalized keys).

        Args:
            cap_data: Connector-transformed capability data (lowercase keys)
            abacus_archimate_element_id: Abacus EEID

        Returns:
            New BusinessCapability instance
        """
        name = cap_data.get("name") or f"CAP-{abacus_archimate_element_id}"
        description = cap_data.get("description", "")
        code = f"CAP-ABACUS-{abacus_archimate_element_id}"

        cap = BusinessCapability(
            name=name,
            description=description,
            code=code,
            archimate_id=abacus_archimate_element_id,
            discovered_by_ai=False,
            discovery_source="abacus",
            level=cap_data.get("level", 1),
            category=cap_data.get("category") or None,
            business_domain=cap_data.get("domain") or cap_data.get("business_domain") or None,
        )

        return cap

    def _update_capability(self, cap: BusinessCapability, cap_data: Dict) -> None:
        """
        Update existing BusinessCapability with Abacus data.

        Merge strategy:
        - Abacus authority fields: Overwrite (name, description, level)
        - A.R.C.I.E enrichments: Preserve (maturity levels, performance scores)

        Uses connector-transformed data directly (lowercase keys).

        Args:
            cap: Existing BusinessCapability
            cap_data: Connector-transformed capability data (lowercase keys)
        """
        # Abacus-authoritative fields: always update
        name = cap_data.get("name")
        if name:
            cap.name = name

        description = cap_data.get("description")
        if description:
            cap.description = description

        level = cap_data.get("level")
        if level is not None:
            cap.level = level

        # Non-enrichment optional fields
        category = cap_data.get("category")
        if category:
            cap.category = category

        domain = cap_data.get("domain") or cap_data.get("business_domain")
        if domain:
            cap.business_domain = domain

        # Update timestamp
        cap.updated_at = datetime.utcnow()

    # Pattern: "01", "02" etc = level 1 (domain)
    # Pattern: "1.1", "2.3" etc = level 2
    # Pattern: "1.1.1", "4.2.3" etc = level 3
    _LEVEL_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\s+")

    def _parse_capability_prefix(self, name: str) -> Optional[tuple]:
        """
        Parse the numeric prefix from a capability name.

        Returns (prefix_str, parts_list, level) or None if no match.
        Examples:
            "01 Product Management" -> ("01", ["01"], 1)
            "1.1 Research and develop" -> ("1.1", ["1", "1"], 2)
            "4.2.3 Production data" -> ("4.2.3", ["4", "2", "3"], 3)
        """
        match = self._LEVEL_PATTERN.match(name)
        if not match:
            return None
        prefix = match.group(1)
        parts = prefix.split(".")
        level = len(parts)
        return prefix, parts, level

    def _build_capability_hierarchy(self) -> None:
        """
        Derive parent-child hierarchy from capability naming convention.

        Abacus capabilities follow a numbered naming convention:
        - "01 Product Management" = Level 1 (domain)
        - "1.1 Research and develop products" = Level 2 (child of domain 01)
        - "4.2.3 Production data collection" = Level 3 (child of 4.2)

        This method:
        1. Parses the numeric prefix from each capability name
        2. Sets the correct `level` (1, 2, or 3)
        3. Sets `parent_capability_id` by matching prefix hierarchy
        4. Sets `business_domain` to the domain name for all children

        No data is fabricated — hierarchy is derived from the actual naming
        structure that Abacus uses.
        """
        caps = BusinessCapability.query.filter_by(discovery_source="abacus").all()
        if not caps:
            return

        # Build lookup: normalized domain number -> capability
        # "01" -> cap, "1" -> same cap (for matching children)
        domain_lookup = {}  # domain_num -> cap
        prefix_lookup = {}  # prefix -> cap

        for cap in caps:
            parsed = self._parse_capability_prefix(cap.name)
            if not parsed:
                continue
            prefix, parts, level = parsed
            prefix_lookup[prefix] = cap

            if level == 1:
                # Domain: "01" should also be findable as "1"
                domain_num = str(int(parts[0]))  # "01" -> "1"
                domain_lookup[domain_num] = cap

        # Now set levels and parent_capability_id
        updated = 0
        for cap in caps:
            parsed = self._parse_capability_prefix(cap.name)
            if not parsed:
                continue
            prefix, parts, level = parsed

            # Set level
            cap.level = level

            # Set parent
            if level == 1:
                cap.parent_capability_id = None
                cap.business_domain = cap.name  # Domain is itself
            elif level == 2:
                # Parent is the level-1 domain: "1.1" -> domain "1"
                domain_num = parts[0]
                parent = domain_lookup.get(domain_num)
                if parent:
                    cap.parent_capability_id = parent.id
                    cap.business_domain = parent.name
            elif level == 3:
                # Parent is the level-2: "4.2.3" -> parent "4.2"
                parent_prefix = ".".join(parts[:2])
                parent = prefix_lookup.get(parent_prefix)
                if parent:
                    cap.parent_capability_id = parent.id
                    # Domain is the level-1 ancestor
                    domain_num = parts[0]
                    domain = domain_lookup.get(domain_num)
                    if domain:
                        cap.business_domain = domain.name

            updated += 1

        db.session.commit()
        logger.info(
            f"Built capability hierarchy: {updated} capabilities updated with levels and parents"
        )

    def _resolve_deferred_parents(self) -> None:
        """
        Retry parent resolution for capabilities that were orphaned during import.

        Called after _build_capability_hierarchy so that parents created via
        naming convention or from previous sync batches are now available in the DB.

        This handles the cross-batch scenario where:
        - Batch 1 imports child capabilities referencing parent EEIDs
        - Parent capabilities arrive in a later batch or are only created
          via _build_capability_hierarchy naming convention
        - This method retries the deferred parent links
        """
        deferred = getattr(self, "_deferred_parents", [])
        if not deferred:
            return

        resolved_count = 0
        still_orphaned = 0

        # Batch-load all capabilities with archimate_ids to avoid N+1 in deferred loop
        all_deferred_archimate_ids = set()
        for entry in deferred:
            all_deferred_archimate_ids.add(entry["child_archimate_id"])
            all_deferred_archimate_ids.add(entry["parent_archimate_id"])
        deferred_caps = BusinessCapability.query.filter(
            BusinessCapability.archimate_id.in_(list(all_deferred_archimate_ids))
        ).all()
        deferred_cap_lookup = {c.archimate_id: c for c in deferred_caps}

        for entry in deferred:
            try:
                child_archimate_id = entry["child_archimate_id"]
                parent_archimate_id = entry["parent_archimate_id"]

                child_cap = deferred_cap_lookup.get(child_archimate_id)

                if not child_cap:
                    continue

                # Skip if parent was already resolved (e.g. by _build_capability_hierarchy)
                if child_cap.parent_capability_id:
                    resolved_count += 1
                    continue

                # Try to find parent from deferred lookup (includes current batch + DB)
                parent_cap = deferred_cap_lookup.get(parent_archimate_id)
                
                # Additional fallback: query database directly if not found
                if not parent_cap:
                    parent_cap = BusinessCapability.query.filter(
                        BusinessCapability.archimate_id == parent_archimate_id
                    ).first()

                if parent_cap:
                    child_cap.parent_capability_id = parent_cap.id
                    resolved_count += 1
                    # Adjust stats: was orphaned, now resolved
                    self.stats["hierarchy_orphaned"] = max(
                        0, self.stats["hierarchy_orphaned"] - 1
                    )
                    self.stats["hierarchy_linked_from_db"] += 1
                    # Determine resolution source for better logging
                    if parent_archimate_id in deferred_cap_lookup:
                        resolution_source = "deferred_lookup"
                    else:
                        resolution_source = "direct_db_query"
                    logger.info(
                        f"Deferred resolution: linked {child_cap.name} "
                        f"to parent {parent_cap.name} [{resolution_source}]"
                    )
                else:
                    still_orphaned += 1
                    logger.warning(
                        f"Still orphaned after hierarchy build: {child_cap.name} "
                        f"(archimate_id={child_archimate_id}) — "
                        f"parent {parent_archimate_id} not found"
                    )

            except Exception as e:
                logger.error(f"Error in deferred parent resolution: {e}", exc_info=True)

        if resolved_count > 0 or still_orphaned > 0:
            db.session.commit()
            logger.info(
                f"Deferred parent resolution: {resolved_count} resolved, "
                f"{still_orphaned} still orphaned"
            )

        # Clean up
        self._deferred_parents = []

    def _resolve_archimate_elements(self) -> None:
        """
        Resolve ArchiMateElement FKs for BusinessCapabilities by name matching.

        For capabilities that have no archimate_element_id set, attempts to find
        a matching ArchiMateElement record by name. Only sets the FK when exactly
        one match is found (to avoid ambiguity).
        """
        try:
            from app.models.models import ArchiMateElement
        except ImportError:
            logger.debug("ArchiMateElement model not available, skipping resolver")
            return

        # Check if any ArchiMateElement records exist
        element_count = ArchiMateElement.query.count()
        if element_count == 0:
            logger.debug("No ArchiMateElement records found, skipping resolver")
            return

        # Get capabilities without archimate_element_id
        unlinked = BusinessCapability.query.filter(
            BusinessCapability.archimate_element_id.is_(None),
            BusinessCapability.discovery_source == "abacus",
        ).all()

        if not unlinked:
            return

        # Pre-load all elements by lowercase name for matching
        all_elements = ArchiMateElement.query.all()
        elements_by_name = {}
        for el in all_elements:
            key = el.name.lower().strip()
            if key not in elements_by_name:
                elements_by_name[key] = el
            else:
                # Multiple elements with same name - mark as ambiguous
                elements_by_name[key] = None

        resolved = 0
        for cap in unlinked:
            # Strip numeric prefix from capability name for matching
            clean_name = self._LEVEL_PATTERN.sub("", cap.name).strip().lower()
            element = elements_by_name.get(clean_name)
            if element:
                cap.archimate_element_id = element.id
                resolved += 1

        if resolved > 0:
            db.session.commit()
            logger.info(f"Resolved {resolved} capability-ArchiMateElement links by name matching")

    def _resolve_capability_by_archimate_id(
        self, target_archimate_id: str
    ) -> Optional[BusinessCapability]:
        """
        Resolve capability using archimate_id lookup (PRIMARY method).

        Args:
            target_archimate_id: The archimate_id to look up

        Returns:
            BusinessCapability if found, None otherwise
        """
        if not target_archimate_id:
            return None

        cap = BusinessCapability.query.filter_by(archimate_id=target_archimate_id).first()
        if cap:
            logger.debug(f"✅ Capability resolved by archimate_id: {target_archimate_id}")
            self.stats["relationships_resolved_by_archimate_id"] += 1
        return cap

    def _resolve_capability_by_exact_name(self, target_name: str) -> Optional[BusinessCapability]:
        """
        Resolve capability using exact name match (SECONDARY method).

        Handles ambiguity: if multiple exact matches, logs warning and returns None.

        Args:
            target_name: The capability name to look up

        Returns:
            BusinessCapability if single match found, None otherwise
        """
        if not target_name:
            return None

        matches = BusinessCapability.query.filter_by(name=target_name).all()

        if len(matches) == 0:
            return None
        elif len(matches) == 1:
            cap = matches[0]
            logger.debug(f"✅ Capability resolved by exact name match: {target_name}")
            self.stats["relationships_resolved_by_exact_name"] += 1
            return cap
        else:
            # Ambiguous: multiple capabilities with same name
            capability_ids = [c.id for c in matches]
            logger.warning(
                f"⚠️ Ambiguous capability name '{target_name}': "
                f"Found {len(matches)} matches (IDs: {capability_ids}). Skipping relationship."
            )
            self.stats["relationships_ambiguous"] += 1
            return None

    def _resolve_capability_by_fuzzy_match(
        self, target_name: str, similarity_threshold: float = 0.90
    ) -> Optional[BusinessCapability]:
        """
        Resolve capability using fuzzy string matching (TERTIARY method).

        Only returns match if similarity > 90%.

        Args:
            target_name: The capability name to match
            similarity_threshold: Minimum similarity ratio (0-1)

        Returns:
            BusinessCapability if fuzzy match found above threshold, None otherwise
        """
        if not target_name:
            return None

        # Get all capabilities
        all_caps = BusinessCapability.query.all()
        best_match = None
        best_ratio = 0

        for cap in all_caps:
            if cap.name:
                ratio = SequenceMatcher(None, target_name.lower(), cap.name.lower()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = cap

        if best_ratio >= similarity_threshold:
            logger.info(
                f"✅ Capability resolved by fuzzy match: '{target_name}' → '{best_match.name}' "
                f"(similarity: {best_ratio:.2%})"
            )
            self.stats["relationships_resolved_by_fuzzy_match"] += 1
            return best_match

        return None

    def _resolve_capability(self, target_archimate_id: Optional[str], target_name: str) -> Optional[BusinessCapability]:
        """
        Resolve capability using fallback chain:
        1. archimate_id lookup (PRIMARY)
        2. exact name match (SECONDARY)
        3. fuzzy match >90% (TERTIARY)
        4. Log warning, return None (FAIL)

        Args:
            target_archimate_id: Abacus EEID/archimate_id if available
            target_name: Capability name from OutConnections

        Returns:
            BusinessCapability if resolved, None otherwise
        """
        # Primary: archimate_id lookup
        if target_archimate_id:
            cap = self._resolve_capability_by_archimate_id(target_archimate_id)
            if cap:
                return cap

        # Secondary: exact name match
        cap = self._resolve_capability_by_exact_name(target_name)
        if cap:
            return cap

        # Tertiary: fuzzy match (>90%)
        cap = self._resolve_capability_by_fuzzy_match(target_name)
        if cap:
            return cap

        # Fail: Unable to resolve (DEBUG level - many OutConnections are non-capability targets)
        logger.debug(
            f"Unresolved capability target: archimate_id={target_archimate_id}, "
            f"name={target_name}. No match in archimate_id, exact name, or fuzzy match."
        )
        self.stats["relationships_unresolved"] += 1
        return None

    def _extract_relationship_strength(
        self,
        connection_type: Optional[str],
        weight: Optional[float] = None,
        relationship_type: Optional[str] = None,
    ) -> Tuple[str, int]:
        """
        Extract relationship strength from Abacus OutConnections metadata.

        Maps Abacus connection metadata to support_level and coverage_percentage:
        - ConnectionType="Core" OR Weight≥0.8 → "strong", 80%
        - ConnectionType="Standard" OR Weight 0.4-0.8 → "partial", 60%
        - ConnectionType="Weak" OR Weight<0.4 → "weak", 30%
        - No data available → "partial", 50% (default fallback)

        Args:
            connection_type: From OutConnections.ConnectionTypeName
            weight: Optional connection weight/strength (0-1)
            relationship_type: Optional relationship type

        Returns:
            Tuple of (support_level, coverage_percentage)
        """
        # Normalize connection_type
        conn_type_normalized = connection_type.lower() if connection_type else ""

        # Strong: Core connections or weight ≥ 0.8
        if conn_type_normalized == "core" or (weight and weight >= 0.8):
            logger.debug(
                f"🔗 Relationship strength: STRONG "
                f"(ConnectionType={connection_type}, Weight={weight})"
            )
            return "strong", 80

        # Weak: Weak connections or weight < 0.4
        if conn_type_normalized == "weak" or (weight and weight < 0.4):
            logger.debug(
                f"🔗 Relationship strength: WEAK "
                f"(ConnectionType={connection_type}, Weight={weight})"
            )
            return "weak", 30

        # Partial: Standard connections, weight 0.4-0.8, or no data
        logger.debug(
            f"🔗 Relationship strength: PARTIAL (default) "
            f"(ConnectionType={connection_type}, Weight={weight})"
        )
        return "partial", 60


    async def import_relationships(self, relationships: List[Dict]) -> None:
        """
        Import relationships from Abacus (application-capability mappings).

        Uses intelligent capability resolution:
        - Primary: archimate_id lookup
        - Secondary: exact name match
        - Tertiary: fuzzy match (>90%)
        - Fail: log warning, skip

        Extracts relationship strength from OutConnections metadata instead of hard-coding.

        Args:
            relationships: List of relationship dictionaries from Abacus
        """
        logger.info(f"Importing {len(relationships)} relationships...")

        # Batch-load all Abacus applications and existing mappings to avoid N+1 queries
        abacus_apps = ApplicationComponent.query.filter(
            ApplicationComponent.application_code.like("ABACUS-%")
        ).all()
        rel_app_lookup = {a.application_code: a for a in abacus_apps}

        # tenant-scoping-ok: scoped via the TenantMixin FK parent
        # BusinessCapability, not ACM.organization_id -- that column is NULL
        # on every row in production (added nullable by reconcile-schema,
        # never backfilled; nothing populates it, including this importer).
        # A predicate on it would match zero rows and make dedup think no
        # mapping exists, creating a duplicate on every re-import. Joining
        # BusinessCapability (TenantMixin) lets do_orm_execute's
        # with_loader_criteria scope the join automatically; falls back to
        # unscoped only outside a request context (CLI import), where there
        # is no tenant to leak across. See e622d36 / rationalization_scoring_service.py.
        existing_mappings = (
            # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
            ApplicationCapabilityMapping.query
            .join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .all()
        )
        mapping_lookup = {
            (m.application_component_id, m.business_capability_id): m
            for m in existing_mappings
        }

        for rel_data in relationships:
            try:
                # Extract source and target IDs/names
                source_eeid = rel_data.get("source_eeid") or rel_data.get("source_id")
                rel_data.get("source_archimate_id")
                target_name = rel_data.get("target_name") or rel_data.get("target_id")
                target_archimate_id = rel_data.get("target_archimate_id")
                connection_type = rel_data.get("connection_type")
                connection_weight = rel_data.get("connection_weight")
                rel_type = rel_data.get("relationship_type", "supports")

                # Validate source
                if not source_eeid:
                    logger.warning("Skipping relationship: missing source_eeid")
                    continue

                if not target_name:
                    logger.warning(f"Skipping relationship from {source_eeid}: missing target_name")
                    continue

                # Find source application by EEID using pre-loaded lookup
                app = rel_app_lookup.get(f"ABACUS-{source_eeid}")

                if not app:
                    logger.warning(
                        f"Skipping relationship: source application not found (EEID={source_eeid})"
                    )
                    continue

                # Resolve target capability using fallback chain
                cap = self._resolve_capability(target_archimate_id, target_name)

                if not cap:
                    # Capability could not be resolved - already logged by _resolve_capability
                    continue

                # Check if relationship already exists using pre-loaded lookup
                existing_mapping = mapping_lookup.get((app.id, cap.id))

                # Extract relationship strength from metadata
                support_level, coverage_percentage = self._extract_relationship_strength(
                    connection_type, connection_weight, rel_type
                )

                if not existing_mapping:
                    # Create new mapping
                    mapping = ApplicationCapabilityMapping(
                        application_component_id=app.id,
                        business_capability_id=cap.id,
                        support_level=support_level,
                        coverage_percentage=coverage_percentage,
                        relationship_type=rel_type,
                        discovered_by_ai=False,
                        discovery_source="abacus",
                    )
                    db.session.add(mapping)
                    # Update lookup so subsequent iterations find this mapping
                    mapping_lookup[(app.id, cap.id)] = mapping
                    self.stats["relationships_created"] += 1
                    logger.debug(
                        f"✅ Created relationship: {app.name} → {cap.name} "
                        f"(strength={support_level}, coverage={coverage_percentage}%)"
                    )
                else:
                    # Update existing mapping with new strength data
                    existing_mapping.support_level = support_level
                    existing_mapping.coverage_percentage = coverage_percentage
                    existing_mapping.abacus_source = True
                    self.stats["relationships_updated"] += 1
                    logger.debug(
                        f"✅ Updated relationship: {app.name} → {cap.name} "
                        f"(strength={support_level}, coverage={coverage_percentage}%)"
                    )

                db.session.commit()

            except Exception as e:
                db.session.rollback()
                logger.error(f"Error importing relationship: {e}", exc_info=True)
                self.stats["relationships_errors"] += 1

        # Log summary statistics
        logger.info(
            f"Relationships import complete:\n"
            f"  Created: {self.stats['relationships_created']}\n"
            f"  Updated: {self.stats['relationships_updated']}\n"
            f"  Errors: {self.stats['relationships_errors']}\n"
            f"Resolution stats:\n"
            f"  By archimate_id: {self.stats['relationships_resolved_by_archimate_id']}\n"
            f"  By exact name: {self.stats['relationships_resolved_by_exact_name']}\n"
            f"  By fuzzy match: {self.stats['relationships_resolved_by_fuzzy_match']}\n"
            f"  Ambiguous (skipped): {self.stats['relationships_ambiguous']}\n"
            f"  Unresolved (skipped): {self.stats['relationships_unresolved']}"
        )



    # -----------------------------------------------------------------
    # Typed relationship import (BPP-002)
    # -----------------------------------------------------------------

    # Mapping from ArchiMate type strings used in OutConnection mappings
    # to the (element_type, layer) pairs used in archimate_elements.
    _ARCHIMATE_TYPE_TO_LAYER = {
        "ApplicationComponent": ("ApplicationComponent", "application"),
        "Node": ("Node", "technology"),
        "BusinessActor": ("BusinessActor", "business"),
        "Facility": ("Facility", "technology"),
        "ApplicationInterface": ("ApplicationInterface", "application"),
        "SystemSoftware": ("SystemSoftware", "technology"),
        "BusinessProcess": ("BusinessProcess", "business"),
        "BusinessService": ("BusinessService", "business"),
    }

    async def import_typed_relationships(
        self, typed_rels: List[Dict]
    ) -> Dict[str, int]:
        """Import typed (non-capability) connections as ArchiMateRelationship records.

        For each typed relationship from
        :meth:`AbacusConnector.extract_all_typed_connections`:

        1. Resolve source and target ArchiMate elements by name.
        2. If the target element does not exist, create it with the
           correct ``type`` and ``layer``.
        3. Create an ``ArchiMateRelationship`` record (skip duplicates).

        Args:
            typed_rels: List of typed relationship dicts from the connector,
                each containing ``source_name``, ``target_name``, ``rel_type``,
                ``source_type``, ``target_type``.

        Returns:
            Stats dict: ``{created, skipped_duplicate, created_elements, errors}``.
        """
        if ArchiMateElement is None or ArchiMateRelationship is None:
            logger.warning("ArchiMate models not available — skipping typed relationship import")
            return {
                "created": 0,
                "skipped_duplicate": 0,
                "created_elements": 0,
                "errors": 0,
            }

        logger.info("Importing %d typed relationships as ArchiMateRelationships...", len(typed_rels))

        # Pre-load element name→id lookup (case-insensitive)
        all_elements = ArchiMateElement.query.all()
        element_lookup: Dict[str, "ArchiMateElement"] = {}  # type: ignore[name-defined]
        for el in all_elements:
            element_lookup[el.name.lower().strip()] = el

        # Pre-load existing relationships for dedup
        existing_rels = ArchiMateRelationship.query.all()
        existing_rel_keys = {
            (r.source_id, r.target_id, r.type) for r in existing_rels
        }

        created = 0
        skipped = 0
        created_elements = 0
        errors = 0

        for rel in typed_rels:
            try:
                source_name = (rel.get("source_name") or "").strip()
                target_name = (rel.get("target_name") or "").strip()
                rel_type = rel.get("rel_type", "association")
                source_type_str = rel.get("source_type", "ApplicationComponent")
                target_type_str = rel.get("target_type", "ApplicationComponent")

                if not source_name or not target_name:
                    errors += 1
                    continue

                # Resolve source element
                source_el = element_lookup.get(source_name.lower())
                if source_el is None:
                    s_type, s_layer = self._ARCHIMATE_TYPE_TO_LAYER.get(
                        source_type_str, (source_type_str, "application")
                    )
                    source_el = ArchiMateElement(
                        name=source_name, type=s_type, layer=s_layer
                    )
                    db.session.add(source_el)
                    db.session.flush()
                    element_lookup[source_name.lower()] = source_el
                    created_elements += 1

                # Resolve target element
                target_el = element_lookup.get(target_name.lower())
                if target_el is None:
                    t_type, t_layer = self._ARCHIMATE_TYPE_TO_LAYER.get(
                        target_type_str, (target_type_str, "application")
                    )
                    target_el = ArchiMateElement(
                        name=target_name, type=t_type, layer=t_layer
                    )
                    db.session.add(target_el)
                    db.session.flush()
                    element_lookup[target_name.lower()] = target_el
                    created_elements += 1

                # Dedup check
                rel_key = (source_el.id, target_el.id, rel_type)
                if rel_key in existing_rel_keys:
                    skipped += 1
                    continue

                # Create relationship
                new_rel = ArchiMateRelationship(
                    source_id=source_el.id,
                    target_id=target_el.id,
                    type=rel_type,
                )
                db.session.add(new_rel)
                existing_rel_keys.add(rel_key)
                created += 1

            except IntegrityError:
                db.session.rollback()
                skipped += 1
            except Exception as e:
                db.session.rollback()
                logger.error("Error importing typed relationship: %s", e)
                errors += 1

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error("Failed to commit typed relationships: %s", e)
            errors += created
            created = 0

        self.stats["typed_relationships_created"] += created
        self.stats["typed_relationships_skipped_duplicate"] += skipped
        self.stats["typed_relationships_elements_created"] += created_elements
        self.stats["typed_relationships_errors"] += errors

        logger.info(
            "Typed relationship import complete: "
            "created=%d, skipped_duplicate=%d, elements_created=%d, errors=%d",
            created, skipped, created_elements, errors,
        )
        return {
            "created": created,
            "skipped_duplicate": skipped,
            "created_elements": created_elements,
            "errors": errors,
        }


def create_import_service(connector: AbacusConnector) -> AbacusImportService:
    """
    Factory function to create AbacusImportService instance.

    Args:
        connector: Configured AbacusConnector

    Returns:
        AbacusImportService instance
    """
    return AbacusImportService(connector)
