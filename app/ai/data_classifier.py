"""
AI Data Classifier

Provides comprehensive data classification and filtering for AI features.
"""

import logging
import re
from datetime import datetime
from typing import Dict, Any
from dataclasses import dataclass
from enum import Enum
import threading

from flask import current_app

logger = logging.getLogger(__name__)

class DataClassification(Enum):
    """Data classification levels."""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    SENSITIVE_PERSONAL = "sensitive_personal"

class DataRisk(Enum):
    """Data risk levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class DataPattern:
    """Represents a data pattern for classification."""
    name: str
    pattern: str
    classification: DataClassification
    risk: DataRisk
    description: str

class AIDataClassifier:
    """
    Classifies and filters data for AI features to prevent sensitive data exposure.
    """
    
    def __init__(self):
        """Initialize the AI data classifier."""
        self._patterns = []
        self._blocked_domains = set()
        self._allowed_domains = set()
        self._lock = threading.Lock()
        
        # Initialize default patterns
        self._initialize_default_patterns()
        
        # Load configuration from environment
        self._load_configuration()
    
    def _initialize_default_patterns(self):
        """Initialize default data classification patterns."""
        self._patterns = [
            # Personal Identifiable Information (PII)
            DataPattern(
                name="email_address",
                pattern=r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                classification=DataClassification.SENSITIVE_PERSONAL,
                risk=DataRisk.HIGH,
                description="Email addresses"
            ),
            DataPattern(
                name="phone_number",
                pattern=r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b',
                classification=DataClassification.SENSITIVE_PERSONAL,
                risk=DataRisk.HIGH,
                description="Phone numbers (US format)"
            ),
            DataPattern(
                name="ssn",
                pattern=r'\b\d{3}-\d{2}-\d{4}\b',
                classification=DataClassification.RESTRICTED,
                risk=DataRisk.CRITICAL,
                description="Social Security Numbers"
            ),
            DataPattern(
                name="credit_card",
                pattern=r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3[0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b',
                classification=DataClassification.RESTRICTED,
                risk=DataRisk.CRITICAL,
                description="Credit card numbers"
            ),
            
            # Financial information
            DataPattern(
                name="bank_account",
                pattern=r'\b\d{9,18}\b',
                classification=DataClassification.RESTRICTED,
                risk=DataRisk.CRITICAL,
                description="Bank account numbers"
            ),
            DataPattern(
                name="salary_amount",
                pattern=r'\$\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b',
                classification=DataClassification.CONFIDENTIAL,
                risk=DataRisk.HIGH,
                description="Salary amounts"
            ),
            
            # Health information
            DataPattern(
                name="medical_terms",
                pattern=r'\b(?:diabetes|cancer|HIV|AIDS|depression|anxiety|bipolar|schizophrenia|medication|prescription|diagnosis|treatment)\b',
                classification=DataClassification.SENSITIVE_PERSONAL,
                risk=DataRisk.HIGH,
                description="Medical terms and conditions"
            ),
            
            # Authentication credentials
            DataPattern(
                name="password",
                pattern=r'\b(?:password|passwd|pwd)\s*[:=]\s*[^\s]+\b',
                classification=DataClassification.RESTRICTED,
                risk=DataRisk.CRITICAL,
                description="Password fields"
            ),
            DataPattern(
                name="api_key",
                pattern=r'\b(?:api[_-]?key|apikey|secret|token)\s*[:=]\s*[A-Za-z0-9+/]{20,}\b',
                classification=DataClassification.RESTRICTED,
                risk=DataRisk.CRITICAL,
                description="API keys and secrets"
            ),
            
            # Business confidential information
            DataPattern(
                name="internal_project",
                pattern=r'\b(?:project|initiative|program)\s+(?:codename|code[-\s]?name|alias)[:\s]+[A-Z][A-Za-z0-9]+\b',
                classification=DataClassification.CONFIDENTIAL,
                risk=DataRisk.MEDIUM,
                description="Internal project codenames"
            ),
            DataPattern(
                name="financial_forecast",
                pattern=r'\b(?:revenue|profit|earnings|forecast|projection|budget)\s*(?:\$\s*)?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b',
                classification=DataClassification.CONFIDENTIAL,
                risk=DataRisk.HIGH,
                description="Financial forecasts and projections"
            ),
            
            # Legal and compliance
            DataPattern(
                name="legal_case",
                pattern=r'\b(?:case|docket|lawsuit|litigation)\s*(?:no\.?|#)\s*\d+\b',
                classification=DataClassification.CONFIDENTIAL,
                risk=DataRisk.HIGH,
                description="Legal case numbers"
            ),
            DataPattern(
                name="contract_terms",
                pattern=r'\b(?:contract|agreement|NDA|non[-\s]?disclosure)\s*(?:amount|value|price)\s*(?:\$\s*)?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b',
                classification=DataClassification.CONFIDENTIAL,
                risk=DataRisk.HIGH,
                description="Contract terms and values"
            )
        ]
    
    def _load_configuration(self):
        """Load configuration from environment variables."""
        # Load blocked domains
        blocked_domains = current_app.config.get('AI_BLOCKED_DOMAINS', '')
        if blocked_domains:
            self._blocked_domains.update(domain.strip() for domain in blocked_domains.split(','))
        
        # Load allowed domains (whitelist)
        allowed_domains = current_app.config.get('AI_ALLOWED_DOMAINS', '')
        if allowed_domains:
            self._allowed_domains.update(domain.strip() for domain in allowed_domains.split(','))
    
    def classify_text(self, text: str) -> Dict[str, Any]:
        """
        Classify text content and identify sensitive data patterns.
        
        Args:
            text: Text content to classify
            
        Returns:
            Classification results with detected patterns
        """
        if not text:
            return {
                'classification': DataClassification.PUBLIC.value,
                'risk': DataRisk.LOW.value,
                'patterns_found': [],
                'safe_for_ai': True
            }
        
        patterns_found = []
        highest_risk = DataRisk.LOW
        highest_classification = DataClassification.PUBLIC
        
        with self._lock:
            for pattern in self._patterns:
                matches = re.finditer(pattern.pattern, text, re.IGNORECASE)
                for match in matches:
                    pattern_info = {
                        'name': pattern.name,
                        'classification': pattern.classification.value,
                        'risk': pattern.risk.value,
                        'description': pattern.description,
                        'match': match.group(),
                        'position': match.span(),
                        'confidence': self._calculate_confidence(match.group(), pattern)
                    }
                    patterns_found.append(pattern_info)
                    
                    # Update highest risk and classification
                    if pattern.risk.value > highest_risk.value:
                        highest_risk = pattern.risk
                    if self._compare_classification(pattern.classification, highest_classification) > 0:
                        highest_classification = pattern.classification
        
        # Determine if safe for AI
        safe_for_ai = self._is_safe_for_ai(highest_classification, highest_risk)
        
        return {
            'classification': highest_classification.value,
            'risk': highest_risk.value,
            'patterns_found': patterns_found,
            'safe_for_ai': safe_for_ai,
            'text_length': len(text),
            'analysis_timestamp': datetime.utcnow().isoformat()
        }
    
    def _calculate_confidence(self, match: str, pattern: DataPattern) -> float:
        """Calculate confidence score for a pattern match."""
        confidence = 0.5  # Base confidence
        
        # Increase confidence for exact matches
        if len(match) >= len(pattern.pattern) * 0.8:
            confidence += 0.3
        
        # Increase confidence for structured patterns
        if pattern.name in ['email_address', 'phone_number', 'ssn', 'credit_card']:
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _compare_classification(self, cls1: DataClassification, cls2: DataClassification) -> int:
        """Compare two classifications (higher = more restrictive)."""
        classification_order = [
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
            DataClassification.CONFIDENTIAL,
            DataClassification.SENSITIVE_PERSONAL,
            DataClassification.RESTRICTED
        ]
        
        try:
            idx1 = classification_order.index(cls1)
            idx2 = classification_order.index(cls2)
            return idx1 - idx2
        except ValueError:
            return 0
    
    def _is_safe_for_ai(self, classification: DataClassification, risk: DataRisk) -> bool:
        """Determine if content is safe for AI processing."""
        # Define safety thresholds
        safe_classifications = [DataClassification.PUBLIC, DataClassification.INTERNAL]
        safe_risks = [DataRisk.LOW, DataRisk.MEDIUM]
        
        return classification in safe_classifications and risk in safe_risks
    
    def filter_text(self, text: str, mode: str = 'redact') -> Dict[str, Any]:
        """
        Filter text to remove or redact sensitive information.
        
        Args:
            text: Text to filter
            mode: Filtering mode ('redact', 'remove', 'mask')
            
        Returns:
            Filtered text and filtering summary
        """
        classification_result = self.classify_text(text)
        
        if classification_result['safe_for_ai']:
            return {
                'filtered_text': text,
                'original_text': text,
                'filtering_applied': False,
                'patterns_filtered': 0,
                'classification': classification_result
            }
        
        filtered_text = text
        patterns_filtered = 0
        
        # Sort patterns by position (reverse order to avoid index shifting)
        patterns_sorted = sorted(classification_result['patterns_found'], 
                              key=lambda x: x['position'][0], reverse=True)
        
        for pattern in patterns_sorted:
            start, end = pattern['position']
            original = pattern['match']
            
            if mode == 'redact':
                replacement = '[REDACTED]'
            elif mode == 'remove':
                replacement = ''
            elif mode == 'mask':
                replacement = original[0] + '*' * (len(original) - 2) + original[-1] if len(original) > 2 else '*'
            else:
                replacement = '[FILTERED]'
            
            filtered_text = filtered_text[:start] + replacement + filtered_text[end:]
            patterns_filtered += 1
        
        return {
            'filtered_text': filtered_text,
            'original_text': text,
            'filtering_applied': True,
            'filtering_mode': mode,
            'patterns_filtered': patterns_filtered,
            'classification': classification_result
        }
    
    def check_domain_safety(self, domain: str) -> bool:
        """
        Check if a domain is safe for AI processing.
        
        Args:
            domain: Domain to check
            
        Returns:
            True if domain is safe, False otherwise
        """
        domain = domain.lower().strip()
        
        # Check blocked domains first
        if domain in self._blocked_domains:
            return False
        
        # If allowed domains are specified, check against whitelist
        if self._allowed_domains:
            return domain in self._allowed_domains
        
        # Default to safe if not explicitly blocked
        return True
    
    def get_classification_summary(self) -> Dict[str, Any]:
        """
        Get summary of classification patterns.
        
        Returns:
            Classification patterns summary
        """
        with self._lock:
            patterns_by_classification = {}
            patterns_by_risk = {}
            
            for pattern in self._patterns:
                # Group by classification
                cls = pattern.classification.value
                if cls not in patterns_by_classification:
                    patterns_by_classification[cls] = []
                patterns_by_classification[cls].append({
                    'name': pattern.name,
                    'description': pattern.description,
                    'risk': pattern.risk.value
                })
                
                # Group by risk
                risk = pattern.risk.value
                if risk not in patterns_by_risk:
                    patterns_by_risk[risk] = []
                patterns_by_risk[risk].append({
                    'name': pattern.name,
                    'description': pattern.description,
                    'classification': pattern.classification.value
                })
        
        return {
            'total_patterns': len(self._patterns),
            'patterns_by_classification': patterns_by_classification,
            'patterns_by_risk': patterns_by_risk,
            'blocked_domains': list(self._blocked_domains),
            'allowed_domains': list(self._allowed_domains),
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def add_custom_pattern(self, name: str, pattern: str, classification: DataClassification, 
                          risk: DataRisk, description: str):
        """
        Add a custom classification pattern.
        
        Args:
            name: Pattern name
            pattern: Regex pattern
            classification: Data classification
            risk: Risk level
            description: Pattern description
        """
        try:
            # Validate regex pattern
            re.compile(pattern)
            
            new_pattern = DataPattern(
                name=name,
                pattern=pattern,
                classification=classification,
                risk=risk,
                description=description
            )
            
            with self._lock:
                self._patterns.append(new_pattern)
            
            logger.info(f"Added custom classification pattern: {name}")
            
        except re.error as e:
            logger.error(f"Invalid regex pattern for {name}: {e}")
            raise ValueError(f"Invalid regex pattern: {e}")
    
    def remove_pattern(self, name: str) -> bool:
        """
        Remove a classification pattern.
        
        Args:
            name: Pattern name to remove
            
        Returns:
            True if pattern was removed, False if not found
        """
        with self._lock:
            original_length = len(self._patterns)
            self._patterns = [p for p in self._patterns if p.name != name]
            
            if len(self._patterns) < original_length:
                logger.info(f"Removed classification pattern: {name}")
                return True
            else:
                logger.warning(f"Pattern not found for removal: {name}")
                return False
    
    def update_pattern(self, name: str, **kwargs):
        """
        Update an existing classification pattern.
        
        Args:
            name: Pattern name to update
            **kwargs: Fields to update
        """
        with self._lock:
            for pattern in self._patterns:
                if pattern.name == name:
                    if 'pattern' in kwargs:
                        # Validate new regex pattern
                        re.compile(kwargs['pattern'])
                        pattern.pattern = kwargs['pattern']
                    if 'classification' in kwargs:
                        pattern.classification = kwargs['classification']
                    if 'risk' in kwargs:
                        pattern.risk = kwargs['risk']
                    if 'description' in kwargs:
                        pattern.description = kwargs['description']
                    
                    logger.info(f"Updated classification pattern: {name}")
                    return
            
            logger.warning(f"Pattern not found for update: {name}")

# Global AI data classifier instance
ai_data_classifier = AIDataClassifier()
