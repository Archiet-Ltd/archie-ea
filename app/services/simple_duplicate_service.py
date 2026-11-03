"""
Simplified Duplicate Detection Service
Basic functionality for testing
"""

import logging
from datetime import datetime
from .. import db
from ..models.simple_duplicate_detection import SimpleDuplicateGroup, SimpleDetectionRun
from ..models.application_portfolio import ApplicationComponent
from app.utils.tenant_sql import current_org_id

logger = logging.getLogger(__name__)


class SimpleDuplicateService:
    """Simplified duplicate detection service"""
    
    @staticmethod
    def cleanup_stale_data():
        """
        Clean up stale duplicate detection data
        """
        try:
            from .. import db
            
            # Delete all existing groups and runs to start fresh
            db.session.execute(db.text("DELETE FROM simple_group_applications"))  # tenant-exempt: system table (detection engine cleanup)
            db.session.execute(db.text("DELETE FROM simple_duplicate_groups"))  # tenant-exempt: system table (detection engine cleanup)
            db.session.execute(db.text("DELETE FROM simple_detection_runs"))  # tenant-exempt: system table (detection engine cleanup)
            db.session.commit()
            
            return {
                'success': True,
                'message': 'Stale data cleaned up successfully'
            }
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'error': f'Cleanup failed: {str(e)}'
            }

    @staticmethod
    def run_detection(threshold=0.7):
        """Run a simple duplicate detection"""
        try:
            # Create detection run
            run = SimpleDetectionRun(
                run_name=f"Detection Run {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                status='running',
                similarity_threshold=threshold,
                started_at=datetime.utcnow()
            )
            db.session.add(run)
            db.session.commit()
            
            # Get all applications
            applications = ApplicationComponent.query.all()
            run.applications_analyzed = len(applications)
            
            # Simple similarity detection based on name similarity
            groups_created = 0
            processed_applications = set()  # Track apps already assigned to groups
            
            for i, app1 in enumerate(applications):
                if app1.id in processed_applications:
                    continue  # Skip if already assigned to a group
                    
                # Find all applications similar to app1
                similar_apps = [app1]
                for j, app2 in enumerate(applications[i+1:], i+1):
                    if app2.id in processed_applications:
                        continue  # Skip if already assigned to a group
                    
                    # Calculate both name and description similarity
                    name_similarity = SimpleDuplicateService._calculate_name_similarity(app1.name, app2.name)
                    desc_similarity = SimpleDuplicateService._calculate_description_similarity(app1.description, app2.description)
                    
                    # Use name similarity as primary, description as secondary
                    overall_similarity = name_similarity * 0.8 + desc_similarity * 0.2
                    
                    if overall_similarity >= threshold:
                        similar_apps.append(app2)
                        processed_applications.add(app2.id)
                
                # Create group if we found duplicates (more than 1 app)
                if len(similar_apps) > 1:
                    # Calculate estimated savings for this group
                    # Try to use actual costs if available, otherwise use default estimate
                    group_savings = SimpleDuplicateService._estimate_group_savings(similar_apps)
                    
                    group = SimpleDuplicateGroup(
                        name=f"Duplicate Group {groups_created + 1}",
                        description=f"Applications with similar names: {', '.join([app.name for app in similar_apps])}",
                        duplicate_type="functional",
                        overall_similarity=sum(
                            SimpleDuplicateService._calculate_combined_similarity(similar_apps[0], app) 
                            for app in similar_apps[1:]
                        ) / len(similar_apps[1:])
                    )
                    
                    # Add all similar applications to the group
                    for app in similar_apps:
                        group.applications.append(app)
                        processed_applications.add(app.id)
                    
                    db.session.add(group)
                    groups_created += 1
                else:
                    # Mark single app as processed to avoid rechecking
                    processed_applications.add(app1.id)
            
            # Update run status
            run.status = 'completed'
            run.groups_found = groups_created
            run.completed_at = datetime.utcnow()
            
            # Calculate total estimated savings from all groups
            # Sum up savings from each group (calculated when groups were created)
            total_savings = 0
            try:
                for group in SimpleDuplicateGroup.query.all():  # model-safety-ok: loop HEADER, runs once
                    try:
                        # Recalculate savings for each group based on its applications
                        group_apps = list(group.applications)
                        if len(group_apps) > 1:
                            total_savings += SimpleDuplicateService._estimate_group_savings(group_apps)
                    except Exception as e:
                        logger.warning(f"Error calculating savings for group {group.id}: {e}")
                        # Group contributes 0 — never substitute a fabricated figure
            except Exception as e:
                logger.error(f"Error calculating total savings: {e}")

            # total_savings == 0 means no real cost data exists for any duplicate
            # group. Store the honest zero; the dashboard explains coverage gaps.
            run.estimated_savings = total_savings
            
            db.session.commit()
            
            logger.info(f"Detection completed: {groups_created} groups found")
            return run
            
        except Exception as e:
            logger.error(f"Detection failed: {e}")
            if 'run' in locals():
                run.status = 'failed'
                run.completed_at = datetime.utcnow()
                db.session.commit()
            raise
    
    @staticmethod
    def _calculate_name_similarity(name1, name2):
        """Calculate name similarity using multiple algorithms"""
        from difflib import SequenceMatcher
        
        if not name1 or not name2:
            return 0.0
            
        name1_normalized = name1.lower().strip()
        name2_normalized = name2.lower().strip()
        
        # Exact match gets highest similarity
        if name1_normalized == name2_normalized:
            return 1.0
        
        # Method 1: SequenceMatcher (fuzzy string matching) - handles typos
        sequence_ratio = SequenceMatcher(None, name1_normalized, name2_normalized).ratio()
        
        # Method 2: Word overlap (Jaccard similarity) - handles word order differences
        words1 = set(name1_normalized.split())
        words2 = set(name2_normalized.split())
        
        if words1 and words2:
            intersection = words1.intersection(words2)
            union = words1.union(words2)
            jaccard_ratio = len(intersection) / len(union) if union else 0.0
        else:
            jaccard_ratio = 0.0
        
        # Method 3: Substring matching - handles partial matches
        # Check if one name contains the other (for cases like "PRS" vs "Plasterboard Recycling system (PRS)")
        contains_ratio = 0.0
        if name1_normalized in name2_normalized or name2_normalized in name1_normalized:
            shorter = min(len(name1_normalized), len(name2_normalized))
            longer = max(len(name1_normalized), len(name2_normalized))
            contains_ratio = shorter / longer if longer > 0 else 0.0
        
        # Combine methods: weighted average favoring sequence matching for typo tolerance
        # SequenceMatcher: 50% (best for typos), Jaccard: 30% (word order), Contains: 20% (partial)
        combined_score = (sequence_ratio * 0.5) + (jaccard_ratio * 0.3) + (contains_ratio * 0.2)
        
        return min(combined_score, 1.0)
    
    @staticmethod
    def _calculate_description_similarity(desc1, desc2):
        """Calculate description similarity using fuzzy matching"""
        from difflib import SequenceMatcher
        
        if not desc1 and not desc2:
            return 1.0  # Both empty - consider as perfect match
        if not desc1 or not desc2:
            return 0.0  # One empty, one not - no match
        
        desc1_normalized = desc1.lower().strip()
        desc2_normalized = desc2.lower().strip()
        
        # Exact match
        if desc1_normalized == desc2_normalized:
            return 1.0
        
        # Use SequenceMatcher for fuzzy matching (better than simple word overlap)
        sequence_ratio = SequenceMatcher(None, desc1_normalized, desc2_normalized).ratio()
        
        # Also calculate word overlap for comparison
        words1 = set(desc1_normalized.split())
        words2 = set(desc2_normalized.split())
        
        if words1 and words2:
            intersection = words1.intersection(words2)
            union = words1.union(words2)
            jaccard_ratio = len(intersection) / len(union) if union else 0.0
        else:
            jaccard_ratio = 0.0
        
        # Combine: 70% sequence (fuzzy), 30% word overlap
        combined_score = (sequence_ratio * 0.7) + (jaccard_ratio * 0.3)
        
        return min(combined_score, 1.0)
    
    @staticmethod
    def _calculate_combined_similarity(app1, app2):
        """Calculate combined similarity using name (80%) and description (20%)"""
        name_sim = SimpleDuplicateService._calculate_name_similarity(app1.name, app2.name)
        desc_sim = SimpleDuplicateService._calculate_description_similarity(app1.description, app2.description)
        
        return name_sim * 0.8 + desc_sim * 0.2
    
    @staticmethod
    def _estimate_group_savings(applications):
        """
        Estimate potential annual savings if duplicates in this group are consolidated.
        
        This calculates what COULD be saved (potential savings), not what HAS been saved.
        Assumes keeping one app and removing the others would eliminate their costs.
        
        Uses actual application cost data ONLY. Apps without cost data contribute
        zero — fabricated per-app estimates are forbidden (Rule 11).
        """
        if len(applications) <= 1:
            return 0
        
        # Calculate total annual cost of duplicate apps (all except the first one we'd keep)
        duplicate_apps = applications[1:]  # Apps that would be deleted/consolidated
        total_annual_cost = 0
        
        for app in duplicate_apps:
            # Actual costs from the ApplicationComponent model (direct access —
            # these are real columns; the old hasattr guards silently skipped
            # maintenance_cost because they probed a non-existent field name).
            app_cost = 0

            if app.total_cost_of_ownership:  # most comprehensive (annual TCO)
                app_cost = float(app.total_cost_of_ownership)
            else:
                if app.license_cost_annual or app.license_cost:
                    app_cost += float(app.license_cost_annual or app.license_cost)
                if app.maintenance_cost:
                    app_cost += float(app.maintenance_cost)
                if app.infrastructure_cost_monthly:
                    app_cost += float(app.infrastructure_cost_monthly) * 12
                elif app.infrastructure_cost:
                    app_cost += float(app.infrastructure_cost)
                if app.development_cost_annual:
                    app_cost += float(app.development_cost_annual)
            
            # No cost data -> contributes 0. Fabricating a per-app estimate here
            # inflated dashboard savings with fiction (Rule 11); savings must be
            # derived from real cost coverage only. Enrich cost data to grow it.
            total_annual_cost += app_cost
        
        # Also account for one-time consolidation costs (migration, data transfer, etc.)
        # Estimate 10% of first year savings as consolidation cost
        consolidation_cost = total_annual_cost * 0.10
        
        # Net first-year savings = annual cost savings - consolidation cost
        # For multi-year view, subsequent years would be full annual savings
        net_first_year_savings = total_annual_cost - consolidation_cost
        
        # Return annual savings (what would be saved each year after consolidation)
        # This represents potential recurring savings
        return max(0, net_first_year_savings)
    
    @staticmethod
    def _normalize_name_for_hash(name):
        """Normalize name for hash-based exact matching"""
        if not name:
            return ""
        # Remove special chars, lowercase, strip
        normalized = ''.join(c.lower() if c.isalnum() or c.isspace() else ' ' for c in name)
        # Collapse multiple spaces
        normalized = ' '.join(normalized.split())
        return normalized.strip()
    
    @staticmethod
    def run_detection_hybrid(threshold=0.7):
        """Hybrid detection: hash-based exact matching + fuzzy matching"""
        import hashlib
        
        try:
            # Create detection run
            run = SimpleDetectionRun(
                run_name=f"Hybrid Detection Run {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                status='running',
                similarity_threshold=threshold,
                started_at=datetime.utcnow()
            )
            db.session.add(run)
            db.session.commit()
            
            # Get all applications
            applications = ApplicationComponent.query.all()
            run.applications_analyzed = len(applications)
            
            # Stage 1: Hash-based exact matching (instant, 100% confidence)
            hash_groups = {}
            hash_processed = set()
            
            for app in applications:
                normalized = SimpleDuplicateService._normalize_name_for_hash(app.name)
                if not normalized:
                    continue
                name_hash = hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()
                
                if name_hash in hash_groups:
                    hash_groups[name_hash].append(app)
                else:
                    hash_groups[name_hash] = [app]
            
            # Create groups for exact hash matches (2+ apps)
            groups_created = 0
            processed_applications = set()
            
            for name_hash, hash_apps in hash_groups.items():
                if len(hash_apps) > 1:
                    # Exact match group
                    group = SimpleDuplicateGroup(
                        name=f"Duplicate Group {groups_created + 1} (Exact Match)",
                        description=f"Exact name matches: {', '.join([app.name for app in hash_apps])}",
                        duplicate_type="exact",
                        overall_similarity=1.0  # 100% for exact matches
                    )
                    for app in hash_apps:
                        group.applications.append(app)
                        processed_applications.add(app.id)
                    db.session.add(group)
                    groups_created += 1
            
            # Stage 2: Fuzzy matching for remaining apps
            remaining_apps = [app for app in applications if app.id not in processed_applications]
            
            for i, app1 in enumerate(remaining_apps):
                if app1.id in processed_applications:
                    continue
                
                similar_apps = [app1]
                for app2 in remaining_apps[i+1:]:
                    if app2.id in processed_applications:
                        continue
                    
                    name_sim = SimpleDuplicateService._calculate_name_similarity(app1.name, app2.name)
                    desc_sim = SimpleDuplicateService._calculate_description_similarity(app1.description, app2.description)
                    overall_similarity = name_sim * 0.8 + desc_sim * 0.2
                    
                    if overall_similarity >= threshold:
                        similar_apps.append(app2)
                        processed_applications.add(app2.id)
                
                # Create group if duplicates found
                if len(similar_apps) > 1:
                    group = SimpleDuplicateGroup(
                        name=f"Duplicate Group {groups_created + 1} (Fuzzy Match)",
                        description=f"Fuzzy similarity matches: {', '.join([app.name for app in similar_apps])}",
                        duplicate_type="fuzzy",
                        overall_similarity=sum(
                            SimpleDuplicateService._calculate_combined_similarity(similar_apps[0], app)
                            for app in similar_apps[1:]
                        ) / len(similar_apps[1:])
                    )
                    for app in similar_apps:
                        group.applications.append(app)
                        processed_applications.add(app.id)
                    db.session.add(group)
                    groups_created += 1
                else:
                    processed_applications.add(app1.id)
            
            # Update run status
            run.status = 'completed'
            run.groups_found = groups_created
            run.completed_at = datetime.utcnow()
            # Calculate total estimated savings from all groups
            total_savings = 0
            for group in SimpleDuplicateGroup.query.all():  # model-safety-ok: loop HEADER, runs once
                group_apps = list(group.applications)
                if len(group_apps) > 1:
                    total_savings += SimpleDuplicateService._estimate_group_savings(group_apps)
            
            # Honest zero when no real cost data exists — no fabricated fallback
            run.estimated_savings = total_savings
            
            db.session.commit()
            logger.info(f"Hybrid detection completed: {groups_created} groups found")
            return run
            
        except Exception as e:
            logger.error(f"Hybrid detection failed: {e}")
            if 'run' in locals():
                run.status = 'failed'
                run.completed_at = datetime.utcnow()
                db.session.commit()
            raise
    
    @staticmethod
    def _find_existing_group(app1, app2):
        """Find if there's already a group containing both applications"""
        # Use simple_duplicate_groups backref (renamed to avoid conflict with DuplicateGroup)
        for group in app1.simple_duplicate_groups:
            if app2 in group.applications:
                return group
        return None
    
    @staticmethod
    def get_all_groups():
        """Get all duplicate groups"""
        return SimpleDuplicateGroup.query.order_by(SimpleDuplicateGroup.overall_similarity.desc()).all()
    
    @staticmethod
    def get_recent_runs(limit=10):
        """Get recent detection runs"""
        return SimpleDetectionRun.query.order_by(SimpleDetectionRun.created_at.desc()).limit(limit).all()
    
    @staticmethod
    def get_statistics():
        """Get detection statistics"""
        total_groups = SimpleDuplicateGroup.query.count()
        latest_run = SimpleDetectionRun.query.order_by(SimpleDetectionRun.created_at.desc()).first()
        
        # Calculate total savings from runs (groups don't have estimated_savings field)
        # None when the run carries no figure: the column defaults to 0.0, which is "not estimated".
        total_savings = (latest_run.estimated_savings or None) if latest_run else None
        
        return {
            'total_groups': total_groups,
            'total_estimated_savings': total_savings,
            'latest_run': latest_run
        }
    
    @staticmethod
    def delete_duplicates_keep_one(group_id, keep_app_id):
        """
        Delete all applications in a duplicate group except the one to keep
        
        Args:
            group_id: ID of the duplicate group
            keep_app_id: ID of the application to keep
            
        Returns:
            dict with results including deleted apps count and any errors
        """
        try:
            from .. import db
            
            # Validate inputs
            try:
                group_id = int(group_id)
                keep_app_id = int(keep_app_id)
            except (ValueError, TypeError) as e:
                logger.error(f"Invalid group_id or keep_app_id: group_id={group_id}, keep_app_id={keep_app_id}, error={e}")
                return {
                    'success': False,
                    'error': 'Invalid group ID or application ID. Please refresh the page and try again.'
                }
            
            # Check if tables exist first
            try:
                # Test if the table exists by attempting a simple query
                db.session.execute(db.text("SELECT 1 FROM simple_duplicate_groups LIMIT 1"))  # tenant-exempt: system table (existence check)
            except Exception as e:
                logger.error(f"Duplicate detection tables not found: {e}")
                return {
                    'success': False,
                    'error': 'Duplicate detection tables not found. Please run the setup script first: python scripts/setup/setup_simple_duplicate.py'
                }
            
            # Get the group - try multiple query methods
            group = SimpleDuplicateGroup.query.get(group_id)
            
            # If not found, try querying by filter to see if it's a different issue
            if not group:
                # Check if any groups exist at all
                total_groups = SimpleDuplicateGroup.query.count()
                logger.warning(f"Group {group_id} not found. Total groups in database: {total_groups}")
                
                # Try to find groups with similar IDs (in case of ID mismatch)
                all_groups = SimpleDuplicateGroup.query.all()
                group_ids = [g.id for g in all_groups]
                logger.warning(f"Available group IDs: {group_ids}")
                
                return {
                    'success': False,
                    'error': f'Group {group_id} not found. The duplicate groups may have been cleared. Please run duplicate detection again to refresh the groups.'
                }
            
            # Verify the app to keep is in this group
            keep_app = None
            for app in group.applications:
                if app.id == keep_app_id:
                    keep_app = app
                    break
            
            if not keep_app:
                return {
                    'success': False,
                    'error': f'Application {keep_app_id} not found in group {group_id}'
                }
            
            # Get apps to delete (all except the one to keep)
            apps_to_delete = [app for app in group.applications if app.id != keep_app_id]
            deleted_count = 0
            errors = []
            
            # Use a much simpler approach - only clean tables we know exist
            for app in apps_to_delete:
                try:
                    app_id = app.id

                    # Ownership gate, ahead of every cascade delete below.
                    #
                    # SimpleDuplicateGroup is not a TenantMixin model, so the
                    # group lookup above is unfiltered. The delete of the
                    # application itself already carries an org predicate, but it
                    # runs LAST: without this check the child rows in
                    # application_capability_mapping and application_data_objects
                    # were already gone by the time that predicate declined to
                    # delete another organisation's application. Neither child
                    # table can be scoped on its own — application_data_objects
                    # has an organization_id column that is NULL on every row,
                    # and application_capability_mapping's is NULL too — so the
                    # check has to happen here, on the parent.
                    _org = current_org_id()
                    if _org is not None and getattr(app, "organization_id", None) != _org:
                        errors.append(
                            f'Application {app.name} does not belong to this organization'
                        )
                        continue

                    # Clean the simple duplicate group junction table first (this is the main issue)
                    try:
                        db.session.execute(  # tenant-filtered: scoped via parent FK (app_id)
                            db.text("DELETE FROM simple_group_applications WHERE application_id = :app_id"),
                            {'app_id': app_id}
                        )
                        print(f"Cleaned simple_group_applications for app {app_id}")
                    except Exception as e:
                        print(f"Warning: Could not clean simple_group_applications for app {app_id}: {e}")
                    
                    # Only clean the tables that we know exist and cause issues
                    # 1. application_capability_mapping (we know this exists and causes FK issues)
                    try:
                        db.session.execute(  # tenant-filtered: scoped via parent FK (app_id)
                            db.text("DELETE FROM application_capability_mapping WHERE application_component_id = :app_id"),
                            {'app_id': app_id}
                        )
                        print(f"Cleaned application_capability_mapping for app {app_id}")
                    except Exception as e:
                        print(f"Warning: Could not clean application_capability_mapping for app {app_id}: {e}")
                    
                    # 2. application_data_objects (we know this exists but has different column name)
                    try:
                        # tenancy-ok: gated by the app.organization_id == current_org_id
                        # check at the top of this loop. The table's own
                        # organization_id is NULL on every row, so a predicate
                        # here would delete nothing and leak the FK violation.
                        db.session.execute(
                            db.text("DELETE FROM application_data_objects WHERE application_component_id = :app_id"),
                            {'app_id': app_id}
                        )
                        print(f"Cleaned application_data_objects for app {app_id}")
                    except Exception as e:
                        print(f"Warning: Could not clean application_data_objects for app {app_id}: {e}")
                    
                    # Now try to delete the main application record
                    try:
                        # Tenant guard on the destructive delete: never remove an
                        # app outside the caller's org (raw SQL bypasses the ORM filter).
                        from flask import g as _g
                        _org = getattr(_g, "current_org_id", None)
                        _dq = "DELETE FROM application_components WHERE id = :app_id"
                        _dp = {'app_id': app_id}
                        if _org is not None:
                            _dq += " AND organization_id = :org"
                            _dp['org'] = _org
                        result = db.session.execute(db.text(_dq), _dp)
                        
                        if result.rowcount > 0:
                            deleted_count += 1
                            print(f"Successfully deleted application {app_id}")
                            db.session.commit()
                        else:
                            errors.append(f'Application {app.name} was not found or already deleted')
                            db.session.rollback()
                    
                    except Exception as e:
                        errors.append(f'Failed to delete {app.name}: {str(e)}')
                        print(f"Error deleting app {app_id}: {e}")
                        try:
                            db.session.rollback()
                        except Exception as exc:
                            logger.debug("suppressed error in SimpleDuplicateService.delete_duplicates_keep_one (app/services/simple_duplicate_service.py): %s", exc)
                    
                except Exception as e:
                    errors.append(f'Failed to delete {app.name}: {str(e)}')
                    try:
                        db.session.rollback()
                    except Exception as exc:
                        logger.debug("suppressed error in SimpleDuplicateService.delete_duplicates_keep_one (app/services/simple_duplicate_service.py): %s", exc)
            
            # Delete the duplicate group
            try:
                db.session.delete(group)
                db.session.commit()
            except Exception as e:
                print(f"Warning: Could not delete group {group_id}: {e}")
                try:
                    db.session.rollback()
                except Exception as exc:
                    logger.debug("suppressed error in SimpleDuplicateService.delete_duplicates_keep_one (app/services/simple_duplicate_service.py): %s", exc)
            
            return {
                'success': True,
                'deleted_count': deleted_count,
                'kept_app': keep_app.name,
                'errors': errors
            }
            
        except Exception as e:
            try:
                db.session.rollback()
            except Exception as exc:
                logger.debug("suppressed error in SimpleDuplicateService.delete_duplicates_keep_one (app/services/simple_duplicate_service.py): %s", exc)
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def bulk_delete_duplicates_keep_best(group_selections):
        """
        Bulk delete duplicates across multiple groups, keeping the selected app in each
        
        Args:
            group_selections: Dict mapping group_id -> keep_app_id
            
        Returns:
            dict with overall results
        """
        try:
            total_deleted = 0
            total_groups_processed = 0
            errors = []
            successful_groups = []
            
            for group_id, keep_app_id in group_selections.items():
                result = SimpleDuplicateService.delete_duplicates_keep_one(group_id, keep_app_id)
                
                if result['success']:
                    total_deleted += result['deleted_count']
                    total_groups_processed += 1
                    successful_groups.append({
                        'group_id': group_id,
                        'kept_app': result['kept_app'],
                        'deleted_count': result['deleted_count']
                    })
                else:
                    errors.append(f'Group {group_id}: {result["error"]}')
            
            return {
                'success': len(errors) == 0,
                'total_groups_processed': total_groups_processed,
                'total_deleted': total_deleted,
                'successful_groups': successful_groups,
                'errors': errors
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
