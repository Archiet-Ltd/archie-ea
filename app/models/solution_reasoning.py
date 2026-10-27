"""
Solution AI Reasoning State Model

Persistent storage of AI reasoning decisions for audit trail, learning, and transparency.

Enables:
- Users to understand WHY AI suggested something
- ARB reviewers to see AI reasoning for governance decisions
- AI system to learn from user feedback
- Compliance audit trail for all AI-assisted choices
"""

from datetime import datetime
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text

from app import db
from app.models.mixins import TenantMixin


class SolutionAIReasoningState(TenantMixin, db.Model):
    """
    Persistent AI reasoning state per solution + ADM phase.
    
    Records what context AI examined, how it reasoned, what it suggested,
    and what feedback the user provided.
    
    Example:
        User creates solution "Customer 360 Platform"
        → AI analyzes requirements
        → AI suggests vendors: SAP (92%), Oracle (87%), Salesforce (84%)
        → AI suggests risks: data migration, user adoption
        → User accepts SAP recommendation
        → Feedback recorded: user_feedback='accept'
        
        Next time user creates similar solution:
        → AI learns: "This user prefers SAP for enterprise CRM"
        → Suggestions re-ranked with SAP higher confidence
    """
    
    __tablename__ = 'solution_ai_reasoning_states'
    __tenant_parent__ = (('solution_id', 'solutions'),)
    
    # Primary Key
    id = Column(Integer, primary_key=True)
    
    # Foreign Keys
    solution_id = Column(
        Integer,
        ForeignKey('solutions.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )
    
    # ADM Phase this reasoning is for
    adm_phase = Column(
        String(1),
        nullable=False,
        default='A'
    )  # A|B|C|D|E|F|G|H
    
    # =========================================================================
    # AI CONTEXT & REASONING
    # =========================================================================
    
    # What AI examined (snapshot of input context)
    context_snapshot = Column(JSON, nullable=True)
    # {
    #   'solution_name': 'Customer 360 Platform',
    #   'business_domain': 'Customer Management',
    #   'complexity': 'Enterprise',
    #   'budget': 5000000,
    #   'description': 'Integrate CRM, analytics, support...',
    #   'requirements': ['Real-time data', 'Mobile support', ...],
    #   'constraints': ['Must use existing infrastructure', ...]
    # }
    
    # How AI reasoned (decision trace)
    reasoning_trace = Column(JSON, nullable=True)
    # {
    #   'steps': [
    #     {
    #       'step': 'Extract requirements',
    #       'result': 'Found 5 key requirements',
    #       'timestamp': '2026-03-02T21:00:00'
    #     },
    #     {
    #       'step': 'Match vendors',
    #       'result': 'Matched against 47 vendors in database',
    #       'timestamp': '2026-03-02T21:00:05'
    #     },
    #     {
    #       'step': 'Rank by fit',
    #       'result': 'Top 3: SAP (0.92), Oracle (0.87), Salesforce (0.84)',
    #       'timestamp': '2026-03-02T21:00:08'
    #     }
    #   ],
    #   'total_steps': 8,
    #   'confidence': 0.87,
    #   'execution_time_ms': 3200
    # }
    
    # Suggestions AI made (ranked options)
    suggestions = Column(JSON, nullable=True)
    # {
    #   'vendors': [
    #     {
    #       'id': 'vendor-1',
    #       'name': 'SAP',
    #       'product': 'S/4HANA',
    #       'confidence': 0.92,
    #       'rationale': 'Best fit for enterprise-scale CRM + analytics',
    #       'capability_coverage': 0.95,
    #       'estimated_cost': 4200000,
    #       'implementation_weeks': 24,
    #       'pros': ['Comprehensive', 'Proven', 'Strong analytics'],
    #       'cons': ['Complex', 'Expensive', 'Steep learning curve']
    #     },
    #     ...
    #   ],
    #   'archimate_elements': [
    #     {
    #       'id': 'elem-1',
    #       'type': 'ApplicationComponent',
    #       'name': 'CRM Core Engine',
    #       'confidence': 0.95,
    #       'layer': 'application',
    #       'rationale': 'Typical component for customer 360 solution'
    #     },
    #     ...
    #   ],
    #   'risks': [
    #     {
    #       'id': 'risk-1',
    #       'risk': 'Data migration complexity',
    #       'severity': 'HIGH',
    #       'likelihood': 'MEDIUM',
    #       'mitigation': 'Use enterprise data migration tool',
    #       'owner': 'Data Engineering Lead'
    #     },
    #     ...
    #   ]
    # }
    
    # =========================================================================
    # USER FEEDBACK (FOR LEARNING)
    # =========================================================================
    
    # Which suggestion did user select?
    selected_suggestion_id = Column(String(255), nullable=True)
    # e.g., 'vendor-1' (if user selected first vendor suggestion)
    
    # What was the user's feedback?
    user_feedback = Column(String(20), nullable=True)
    # Enum: 'accept' | 'reject' | 'modify' | null (no feedback yet)
    
    # Why did user accept/reject?
    feedback_reason = Column(Text, nullable=True)
    # e.g., "Cost too high", "Missing required integration", "Not enough reporting"
    
    # =========================================================================
    # EXPLAINABILITY LAYER (NEW IN PHASE 6)
    # =========================================================================
    
    # Why was this recommendation chosen?
    recommendation_reasoning = Column(JSON, nullable=True)
    # {
    #   'vendor': {
    #     'id': 'vendor-1',
    #     'name': 'SAP',
    #     'reasoning_chain': [
    #       'Enterprise scale requirement → Need vendor with 5000+ implementations',
    #       'Real-time data need → SAP S/4HANA has real-time capabilities',
    #       'Integration complexity → SAP has extensive middleware',
    #       'Cost vs capability ratio → Best fit at this budget level'
    #     ]
    #   },
    #   'cost_estimate': {...},
    #   'timeline': {...}
    # }
    
    # Numeric confidence (0.0-1.0)
    confidence_score_pct = Column(db.Float, nullable=True, default=0.0)
    
    # Confidence intervals (±% range)
    confidence_intervals = Column(JSON, nullable=True)
    # {
    #   'vendor_fit': {'lower': 0.88, 'upper': 0.96, 'certainty': 'HIGH'},
    #   'cost_estimate': {'lower': 3800000, 'upper': 4600000, 'certainty': 'MEDIUM'},
    #   'timeline': {'lower': 20, 'upper': 28, 'certainty': 'LOW'}
    # }
    
    # What data informed this decision?
    data_sources_used = Column(JSON, nullable=True)
    # {
    #   'requirements_analysis': {
    #     'source': 'solution.description + ai_analysis',
    #     'data_points': ['real-time', 'enterprise scale', 'mobile support'],
    #     'weight': 0.3
    #   },
    #   'vendor_database': {
    #     'source': 'vendors table + capabilities',
    #     'records_examined': 47,
    #     'weight': 0.4
    #   },
    #   'historical_projects': {
    #     'source': 'similar solutions + outcomes',
    #     'sample_size': 12,
    #     'weight': 0.3
    #   }
    # }
    
    # What alternatives were considered?
    alternative_options_considered = Column(JSON, nullable=True)
    # {
    #   'vendors': [
    #     {
    #       'id': 'vendor-2',
    #       'name': 'Oracle',
    #       'fit_score': 0.87,
    #       'reasons_rejected': [
    #         'Higher cost (+$400K)',
    #         'Slower implementation timeline (+4 weeks)',
    #         'More complex licensing model'
    #       ]
    #     },
    #     {
    #       'id': 'vendor-3',
    #       'name': 'Salesforce',
    #       'fit_score': 0.84,
    #       'reasons_rejected': [
    #         'Limited enterprise analytics',
    #         'Weaker backend integration capabilities',
    #         'Not suitable for data volumes'
    #       ]
    #     }
    #   ]
    # }
    
    # What could make this recommendation wrong?
    uncertainty_factors = Column(JSON, nullable=True)
    # {
    #   'high_impact': [
    #     {
    #       'factor': 'Requirements interpretation',
    #       'assumption': 'Assumed real-time means <1s latency',
    #       'if_wrong': 'SAP S/4HANA might not meet SLA',
    #       'likelihood': 'LOW',
    #       'mitigation': 'Validate latency requirements in Phase B'
    #     },
    #     {
    #       'factor': 'Budget stability',
    #       'assumption': 'Budget remains $5M ±10%',
    #       'if_wrong': 'May need to downgrade vendor or scope',
    #       'likelihood': 'MEDIUM',
    #       'mitigation': 'Get budget sign-off from CFO'
    #     }
    #   ],
    #   'low_impact': [...]
    # }
    
    # What were our key assumptions?
    model_assumptions = Column(JSON, nullable=True)
    # {
    #   'technical_assumptions': [
    #     'Cloud deployment is acceptable (not on-premise only)',
    #     'Existing infrastructure can be retired or repurposed',
    #     'Team has basic cloud experience'
    #   ],
    #   'business_assumptions': [
    #     'Budget is fixed at $5M',
    #     'Timeline is 24 months max',
    #     'Single vendor preferred (no best-of-breed)'
    #   ],
    #   'organizational_assumptions': [
    #     'Change management team available',
    #     'No competing initiatives',
    #     'Executive sponsorship committed'
    #   ]
    # }
    
    # =========================================================================
    # METADATA
    # =========================================================================
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # =========================================================================
    # RELATIONSHIPS
    # =========================================================================
    
    solution = db.relationship(
        'Solution',
        backref=db.backref('ai_reasoning_states', lazy='dynamic', cascade='all, delete-orphan')
    )
    
    # =========================================================================
    # PROPERTIES & METHODS
    # =========================================================================
    
    @property
    def confidence_score(self) -> float:
        """Get confidence score (from new field or reasoning trace for backward compat)."""
        if self.confidence_score_pct is not None:
            return self.confidence_score_pct
        if self.reasoning_trace and 'confidence' in self.reasoning_trace:
            return self.reasoning_trace['confidence']
        return 0.0
    
    @property
    def vendor_suggestions(self) -> list:
        """Get vendor suggestions from suggestions JSON."""
        if self.suggestions and 'vendors' in self.suggestions:
            return self.suggestions['vendors']
        return []
    
    @property
    def archimate_suggestions(self) -> list:
        """Get ArchiMate element suggestions."""
        if self.suggestions and 'archimate_elements' in self.suggestions:
            return self.suggestions['archimate_elements']
        return []
    
    @property
    def risk_suggestions(self) -> list:
        """Get risk suggestions."""
        if self.suggestions and 'risks' in self.suggestions:
            return self.suggestions['risks']
        return []
    
    @property
    def has_feedback(self) -> bool:
        """Whether user has provided feedback on suggestions."""
        return self.user_feedback is not None
    
    @property
    def feedback_summary(self) -> str:
        """Human-readable feedback summary."""
        if not self.has_feedback:
            return "No feedback yet"
        
        feedback_map = {
            'accept': f"✓ User accepted {self.selected_suggestion_id}",
            'reject': f"✗ User rejected {self.selected_suggestion_id}",
            'modify': f"⚙ User modified {self.selected_suggestion_id}"
        }
        
        summary = feedback_map.get(self.user_feedback, 'Unknown feedback')
        if self.feedback_reason:
            summary += f" ({self.feedback_reason})"
        return summary
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for API responses."""
        return {
            'id': self.id,
            'solution_id': self.solution_id,
            'adm_phase': self.adm_phase,
            'context_snapshot': self.context_snapshot,
            'reasoning_trace': self.reasoning_trace,
            'suggestions': self.suggestions,
            'selected_suggestion_id': self.selected_suggestion_id,
            'user_feedback': self.user_feedback,
            'feedback_reason': self.feedback_reason,
            'feedback_summary': self.feedback_summary,
            'confidence_score': self.confidence_score,
            # Explainability layer fields
            'recommendation_reasoning': self.recommendation_reasoning,
            'confidence_intervals': self.confidence_intervals,
            'data_sources_used': self.data_sources_used,
            'alternative_options_considered': self.alternative_options_considered,
            'uncertainty_factors': self.uncertainty_factors,
            'model_assumptions': self.model_assumptions,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
    
    def __repr__(self):
        return f"<SolutionAIReasoningState solution_id={self.solution_id} phase={self.adm_phase} confidence={self.confidence_score:.0%}>"
