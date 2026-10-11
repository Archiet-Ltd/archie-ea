"""
Import Audit Trail Models

Provides comprehensive audit logging for import operations with before/after snapshots
and rollback capabilities.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from flask import current_app
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from app import db

Base = declarative_base()


class ImportSessionLog(db.Model):
    """
    Comprehensive audit log for import operations.
    
    Tracks all import activities with before/after snapshots,
    user attribution, and rollback capabilities.
    """
    __tablename__ = 'import_audit_log'
    
    # Primary identification
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)  # UUID for import session
    operation_type = Column(String(50), nullable=False)  # 'create', 'update', 'merge', 'delete'
    
    # User and context
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    user = relationship('User', foreign_keys=[user_id], backref='import_audit_logs')
    
    # Import details
    import_source = Column(String(100), nullable=False)  # 'unified_applications', 'batch_import'
    filename = Column(String(255), nullable=True)  # Original filename
    file_hash = Column(String(64), nullable=True)  # File hash for integrity
    
    # Timing and status
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default='in_progress')  # 'in_progress', 'completed', 'failed', 'rolled_back'
    
    # Statistics
    records_processed = Column(Integer, nullable=False, default=0)
    records_created = Column(Integer, nullable=False, default=0)
    records_updated = Column(Integer, nullable=False, default=0)
    records_skipped = Column(Integer, nullable=False, default=0)
    records_failed = Column(Integer, nullable=False, default=0)
    
    # Change tracking
    changes_summary = Column(JSON, nullable=True)  # Summary of all changes
    detailed_changes = Column(JSON, nullable=True)  # Detailed before/after for each record
    rollback_data = Column(JSON, nullable=True)  # Data needed for rollback
    
    # Metadata
    duplicate_mode = Column(String(20), nullable=True)  # 'skip', 'merge', 'update', 'create'
    error_message = Column(Text, nullable=True)
    processing_time_seconds = Column(Integer, nullable=True)
    
    # Rollback tracking
    is_rolled_back = Column(Boolean, nullable=False, default=False)
    rolled_back_at = Column(DateTime, nullable=True)
    rolled_back_by_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    rolled_back_by = relationship('User', foreign_keys=[rolled_back_by_id])
    rollback_reason = Column(Text, nullable=True)

    # Organisation the import ran in, and the snapshot taken before it wrote
    # (what it created, and the earlier state of what it changed). Both are
    # nullable so rows written before restore existed stay valid; a row with
    # no organisation is never offered as a restore point.
    organization_id = Column(Integer, ForeignKey('organizations.id'), nullable=True, index=True)
    snapshot_data = Column(JSON, nullable=True)

    def __repr__(self):
        return f'<ImportSessionLog {self.id}: {self.operation_type} by {self.user_id} at {self.started_at}>'
    
    @property
    def duration(self) -> Optional[float]:
        """Calculate processing duration in seconds."""
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    @property
    def success_rate(self) -> Optional[float]:
        """Calculate success rate as percentage."""
        total = self.records_processed
        if total > 0:
            successful = self.records_created + self.records_updated
            return (successful / total) * 100
        return None
    
    def add_record_change(self, record_id: int, record_type: str, 
                        operation: str, before_data: Dict[str, Any], 
                        after_data: Dict[str, Any], changed_fields: List[str]):
        """
        Add a detailed record change to this audit log.
        
        Args:
            record_id: ID of the affected record
            record_type: Type of record (e.g., 'ApplicationComponent')
            operation: Type of operation ('create', 'update', 'merge')
            before_data: Record state before change
            after_data: Record state after change
            changed_fields: List of field names that changed
        """
        if not self.detailed_changes:
            self.detailed_changes = []
        
        change_entry = {
            'record_id': record_id,
            'record_type': record_type,
            'operation': operation,
            'timestamp': datetime.utcnow().isoformat(),
            'before': before_data,
            'after': after_data,
            'changed_fields': changed_fields,
            'field_changes': {}
        }
        
        # Track specific field changes
        for field in changed_fields:
            if field in before_data or field in after_data:
                change_entry['field_changes'][field] = {
                    'before': before_data.get(field),
                    'after': after_data.get(field)
                }
        
        self.detailed_changes.append(change_entry)
        
        # Update rollback data if this is an update operation
        if operation in ['update', 'merge'] and before_data:
            if not self.rollback_data:
                self.rollback_data = {}
            self.rollback_data[str(record_id)] = before_data
    
    def complete_import(self, status: str = 'completed', error_message: str = None):
        """
        Mark the import as completed with final statistics.
        
        Args:
            status: Final status ('completed', 'failed')
            error_message: Error message if failed
        """
        self.status = status
        self.completed_at = datetime.utcnow()
        if error_message:
            self.error_message = error_message
        
        # Calculate processing time
        if self.started_at:
            self.processing_time_seconds = int((self.completed_at - self.started_at).total_seconds())
        
        # Update summary statistics
        if self.detailed_changes:
            created_count = len([c for c in self.detailed_changes if c['operation'] == 'create'])
            updated_count = len([c for c in self.detailed_changes if c['operation'] in ['update', 'merge']])
            self.records_created = created_count
            self.records_updated = updated_count
    
    def rollback_import(self, user_id: int, reason: str):
        """
        Mark this import as rolled back.
        
        Args:
            user_id: ID of user performing rollback
            reason: Reason for rollback
        """
        self.is_rolled_back = True
        self.rolled_back_at = datetime.utcnow()
        self.rolled_back_by_id = user_id
        self.rollback_reason = reason
    
    def get_rollback_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Get data needed for rollback operations.
        
        Returns:
            Dict mapping record IDs to their original state
        """
        return self.rollback_data or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert audit log to dictionary for API responses."""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'operation_type': self.operation_type,
            'user_id': self.user_id,
            'user_email': getattr(self.user, 'email', None) if self.user else None,
            'import_source': self.import_source,
            'filename': self.filename,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'status': self.status,
            'records_processed': self.records_processed,
            'records_created': self.records_created,
            'records_updated': self.records_updated,
            'records_skipped': self.records_skipped,
            'records_failed': self.records_failed,
            'success_rate': self.success_rate,
            'duration_seconds': self.duration,
            'duplicate_mode': self.duplicate_mode,
            'error_message': self.error_message,
            'is_rolled_back': self.is_rolled_back,
            'rolled_back_at': self.rolled_back_at.isoformat() if self.rolled_back_at else None,
            'rolled_back_by': getattr(self.rolled_back_by, 'email', None) if self.rolled_back_by else None,
            'rollback_reason': self.rollback_reason,
            'change_count': len(self.detailed_changes) if self.detailed_changes else 0
        }


class ImportAuditService:
    """
    Service for managing import audit logs and rollback operations.
    """
    
    @staticmethod
    def create_audit_session(user_id: int, import_source: str, 
                           filename: str = None, operation_type: str = 'import',
                           duplicate_mode: str = None) -> ImportSessionLog:
        """
        Create a new audit session for an import operation.
        
        Args:
            user_id: ID of user performing import
            import_source: Source of import ('unified_applications', 'batch_import')
            filename: Original filename (optional)
            operation_type: Type of operation ('create', 'update', 'merge')
            duplicate_mode: How duplicates are handled
            
        Returns:
            New ImportSessionLog instance
        """
        import uuid
        
        audit_log = ImportSessionLog(
            session_id=str(uuid.uuid4()),
            operation_type=operation_type,
            user_id=user_id,
            import_source=import_source,
            filename=filename,
            duplicate_mode=duplicate_mode,
            status='in_progress'
        )
        
        db.session.add(audit_log)
        db.session.flush()  # Get ID without committing
        
        current_app.logger.info(f"Created audit session {audit_log.session_id} for user {user_id}")
        
        return audit_log
    
    @staticmethod
    def get_import_history(user_id: Optional[int] = None, 
                          import_source: Optional[str] = None,
                          limit: int = 100, 
                          offset: int = 0) -> List[ImportSessionLog]:
        """
        Get import history with optional filtering.
        
        Args:
            user_id: Filter by user ID (optional)
            import_source: Filter by import source (optional)
            limit: Maximum number of records to return
            offset: Number of records to skip
            
        Returns:
            List of ImportSessionLog instances
        """
        query = ImportSessionLog.query
        
        if user_id:
            query = query.filter(ImportSessionLog.user_id == user_id)
        
        if import_source:
            query = query.filter(ImportSessionLog.import_source == import_source)
        
        return query.order_by(ImportSessionLog.started_at.desc()).offset(offset).limit(limit).all()
    
    @staticmethod
    def get_audit_session(session_id: str) -> Optional[ImportSessionLog]:
        """
        Get a specific audit session by ID.
        
        Args:
            session_id: Session UUID
            
        Returns:
            ImportSessionLog instance or None
        """
        return ImportSessionLog.query.filter_by(session_id=session_id).first()
    
    @staticmethod
    def rollback_import(session_id: str, user_id: int, reason: str) -> Dict[str, Any]:
        """
        Rollback an import operation using stored audit data.
        
        Args:
            session_id: Session UUID to rollback
            user_id: ID of user performing rollback
            reason: Reason for rollback
            
        Returns:
            Dict with rollback results
        """
        audit_log = ImportAuditService.get_audit_session(session_id)
        
        if not audit_log:
            raise ValueError(f"Audit session {session_id} not found")
        
        if audit_log.is_rolled_back:
            raise ValueError(f"Import session {session_id} already rolled back")
        
        if not audit_log.rollback_data:
            raise ValueError(f"No rollback data available for session {session_id}")
        
        rollback_results = {
            'records_restored': 0,
            'records_failed': 0,
            'errors': []
        }
        
        try:
            # Get rollback data
            rollback_data = audit_log.get_rollback_data()
            
            # Restore each record to its original state
            from app.models.application import ApplicationComponent
            
            for record_id_str, original_data in rollback_data.items():
                try:
                    record_id = int(record_id_str)
                    app = ApplicationComponent.query.get(record_id)
                    
                    if app:
                        # Restore original values
                        for field, value in original_data.items():
                            if hasattr(app, field):
                                setattr(app, field, value)
                        
                        rollback_results['records_restored'] += 1
                    else:
                        rollback_results['records_failed'] += 1
                        rollback_results['errors'].append(f"Record {record_id} not found")
                
                except Exception as e:
                    rollback_results['records_failed'] += 1
                    rollback_results['errors'].append(f"Failed to restore record {record_id_str}: {str(e)}")
            
            # Commit rollback changes
            if rollback_results['records_restored'] > 0:
                db.session.commit()
            
            # Mark audit log as rolled back
            audit_log.rollback_import(user_id, reason)
            db.session.commit()
            
            current_app.logger.info(f"Rolled back import session {session_id}: {rollback_results['records_restored']} records restored")
            
            return rollback_results
        
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Rollback failed for session {session_id}: {str(e)}")
            raise
    
    @staticmethod
    def get_import_statistics(days: int = 30) -> Dict[str, Any]:
        """
        Get import statistics for the specified time period.
        
        Args:
            days: Number of days to look back
            
        Returns:
            Dict with import statistics
        """
        from sqlalchemy import func, and_
        
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        stats = db.session.query(
            func.count(ImportSessionLog.id).label('total_imports'),
            func.sum(ImportSessionLog.records_processed).label('total_records'),
            func.sum(ImportSessionLog.records_created).label('total_created'),
            func.sum(ImportSessionLog.records_updated).label('total_updated'),
            func.sum(ImportSessionLog.records_failed).label('total_failed'),
            func.avg(ImportSessionLog.processing_time_seconds).label('avg_processing_time')
        ).filter(
            ImportSessionLog.started_at >= cutoff_date
        ).first()
        
        return {
            'period_days': days,
            'total_imports': stats.total_imports or 0,
            'total_records': stats.total_records or 0,
            'total_created': stats.total_created or 0,
            'total_updated': stats.total_updated or 0,
            'total_failed': stats.total_failed or 0,
            'avg_processing_time_seconds': float(stats.avg_processing_time) if stats.avg_processing_time else 0,
            'success_rate': ((stats.total_created + stats.total_updated) / stats.total_records * 100) if stats.total_records else 0
        }
