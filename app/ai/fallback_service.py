"""
AI Fallback Service

Provides non-AI fallback functionality when AI features are unavailable.
"""

import logging
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


logger = logging.getLogger(__name__)

@dataclass
class FallbackResponse:
    """Represents a fallback response."""
    content: str
    confidence: float
    fallback_type: str
    metadata: Dict[str, Any]
    timestamp: datetime

class AIFallbackService:
    """
    Provides fallback functionality when AI features are degraded or unavailable.
    """
    
    def __init__(self):
        """Initialize the AI fallback service."""
        self._fallback_strategies = {}
        self._template_responses = {}
        self._rule_based_responses = {}
        
        # Initialize fallback strategies
        self._initialize_fallback_strategies()
        self._initialize_template_responses()
        self._initialize_rule_based_responses()
    
    def _initialize_fallback_strategies(self):
        """Initialize fallback strategies for different AI features."""
        self._fallback_strategies = {
            'chat_interface': self._fallback_chat,
            'document_analysis': self._fallback_document_analysis,
            'archimate_generation': self._fallback_archimate_generation,
            'gap_detection': self._fallback_gap_detection,
            'duplicate_detection': self._fallback_duplicate_detection,
            'vendor_discovery': self._fallback_vendor_discovery,
            'suggestion_engine': self._fallback_suggestion_engine,
            'workflow_automation': self._fallback_workflow_automation,
            'consolidation_analysis': self._fallback_consolidation_analysis,
            'capability_mapping': self._fallback_capability_mapping
        }
    
    def _initialize_template_responses(self):
        """Initialize template-based fallback responses."""
        self._template_responses = {
            'chat_interface': {
                'greeting': "Hello! I'm currently operating in limited mode. How can I help you today?",
                'unknown_query': "I'm sorry, I'm currently operating with limited capabilities. Please try again later or contact support for assistance.",
                'error': "I apologize, but I'm experiencing technical difficulties. Please try again later.",
                'help': "I can provide basic assistance while my AI capabilities are limited. For advanced features, please try again later."
            },
            'document_analysis': {
                'no_analysis': "Document analysis is currently unavailable. Please try again later or use manual analysis.",
                'basic_info': "Basic document information: This appears to be a document that requires AI analysis, which is currently unavailable.",
                'error': "Document analysis failed due to system limitations. Please try again later."
            },
            'archimate_generation': {
                'no_generation': "ArchiMate diagram generation is currently unavailable. Please use the manual modeling tools.",
                'basic_template': "Basic ArchiMate template: This would normally contain AI-generated architecture elements.",
                'error': "ArchiMate generation failed. Please use manual modeling tools."
            },
            'gap_detection': {
                'no_detection': "Gap detection is currently unavailable. Please perform manual gap analysis.",
                'basic_info': "Gap analysis requires AI capabilities that are currently unavailable.",
                'error': "Gap detection failed. Please perform manual analysis."
            },
            'duplicate_detection': {
                'no_detection': "Duplicate detection is currently unavailable. Please perform manual duplicate analysis.",
                'basic_info': "Duplicate analysis requires AI capabilities that are currently unavailable.",
                'error': "Duplicate detection failed. Please perform manual analysis."
            },
            'vendor_discovery': {
                'no_discovery': "Vendor discovery is currently unavailable. Please use the vendor catalog search.",
                'basic_info': "Vendor discovery requires AI capabilities that are currently unavailable.",
                'error': "Vendor discovery failed. Please use manual vendor search."
            },
            'suggestion_engine': {
                'no_suggestions': "Suggestions are currently unavailable. Please explore available options manually.",
                'basic_info': "Smart suggestions require AI capabilities that are currently unavailable.",
                'error': "Suggestion engine failed. Please explore options manually."
            },
            'workflow_automation': {
                'no_automation': "Workflow automation is currently unavailable. Please perform manual workflow steps.",
                'basic_info': "Workflow automation requires AI capabilities that are currently unavailable.",
                'error': "Workflow automation failed. Please perform manual steps."
            },
            'consolidation_analysis': {
                'no_analysis': "Consolidation analysis is currently unavailable. Please perform manual analysis.",
                'basic_info': "Consolidation analysis requires advanced capabilities that are currently unavailable.",
                'error': "Consolidation analysis failed. Please perform manual analysis."
            },
            'capability_mapping': {
                'no_mapping': "Capability mapping is currently unavailable. Please use manual mapping tools.",
                'basic_info': "Capability mapping requires AI capabilities that are currently unavailable.",
                'error': "Capability mapping failed. Please use manual mapping tools."
            }
        }
    
    def _initialize_rule_based_responses(self):
        """Initialize rule-based fallback responses."""
        self._rule_based_responses = {
            'chat_interface': {
                'greeting_patterns': [
                    r'^(hi|hello|hey|good morning|good afternoon|good evening)',
                    r'^(how are you|how do you do)'
                ],
                'help_patterns': [
                    r'^(help|what can you do|capabilities|features)',
                    r'^(how to|how do i|instructions)'
                ],
                'error_patterns': [
                    r'^(error|problem|issue|broken|not working)',
                    r'^(fail|failed|failure)'
                ]
            },
            'document_analysis': {
                'document_types': {
                    r'\.pdf$': 'PDF document',
                    r'\.(doc|docx)$': 'Word document',
                    r'\.(xls|xlsx)$': 'Excel spreadsheet',
                    r'\.(ppt|pptx)$': 'PowerPoint presentation',
                    r'\.txt$': 'Text document'
                }
            },
            'vendor_discovery': {
                'vendor_keywords': [
                    'software', 'technology', 'service', 'solution', 'platform',
                    'vendor', 'provider', 'company', 'supplier'
                ]
            }
        }
    
    def get_fallback_response(self, feature: str, input_data: Any, 
                             context: Optional[Dict[str, Any]] = None) -> FallbackResponse:
        """
        Get fallback response for an AI feature.
        
        Args:
            feature: AI feature name
            input_data: Input data for the feature
            context: Additional context
            
        Returns:
            Fallback response
        """
        try:
            # Get fallback strategy for the feature
            strategy = self._fallback_strategies.get(feature)
            if not strategy:
                return self._default_fallback(feature, input_data)
            
            # Execute fallback strategy
            return strategy(input_data, context or {})
            
        except Exception as e:
            logger.error(f"Fallback strategy failed for {feature}: {e}")
            return self._error_fallback(feature, str(e))
    
    def _fallback_chat(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for chat interface."""
        if not isinstance(input_data, str):
            input_data = str(input_data)
        
        input_lower = input_data.lower().strip()
        
        # Check for greeting patterns
        for pattern in self._rule_based_responses['chat_interface']['greeting_patterns']:
            if re.search(pattern, input_lower):
                return FallbackResponse(
                    content=self._template_responses['chat_interface']['greeting'],
                    confidence=0.8,
                    fallback_type='rule_based',
                    metadata={'pattern_matched': 'greeting'},
                    timestamp=datetime.utcnow()
                )
        
        # Check for help patterns
        for pattern in self._rule_based_responses['chat_interface']['help_patterns']:
            if re.search(pattern, input_lower):
                return FallbackResponse(
                    content=self._template_responses['chat_interface']['help'],
                    confidence=0.7,
                    fallback_type='rule_based',
                    metadata={'pattern_matched': 'help'},
                    timestamp=datetime.utcnow()
                )
        
        # Check for error patterns
        for pattern in self._rule_based_responses['chat_interface']['error_patterns']:
            if re.search(pattern, input_lower):
                return FallbackResponse(
                    content=self._template_responses['chat_interface']['error'],
                    confidence=0.6,
                    fallback_type='rule_based',
                    metadata={'pattern_matched': 'error'},
                    timestamp=datetime.utcnow()
                )
        
        # Default response
        return FallbackResponse(
            content=self._template_responses['chat_interface']['unknown_query'],
            confidence=0.5,
            fallback_type='template',
            metadata={'fallback_reason': 'no_pattern_matched'},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_document_analysis(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for document analysis."""
        # Try to extract basic information from document
        document_info = self._extract_basic_document_info(input_data)
        
        if document_info:
            content = f"Basic document information detected: {document_info}. " \
                     f"Advanced analysis is currently unavailable. Please try again later."
        else:
            content = self._template_responses['document_analysis']['no_analysis']
        
        return FallbackResponse(
            content=content,
            confidence=0.6,
            fallback_type='rule_based',
            metadata={'document_info': document_info},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_archimate_generation(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for ArchiMate generation."""
        # Extract basic elements from input
        elements = self._extract_basic_archimate_elements(input_data)
        
        if elements:
            content = f"Basic ArchiMate elements identified: {', '.join(elements)}. " \
                     f"AI-powered diagram generation is currently unavailable. " \
                     f"Please use manual modeling tools to create the diagram."
        else:
            content = self._template_responses['archimate_generation']['no_generation']
        
        return FallbackResponse(
            content=content,
            confidence=0.5,
            fallback_type='rule_based',
            metadata={'elements': elements},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_gap_detection(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for gap detection."""
        # Try to identify potential gap areas
        gap_areas = self._identify_potential_gaps(input_data)
        
        if gap_areas:
            content = f"Potential gap areas identified: {', '.join(gap_areas)}. " \
                     f"Advanced gap analysis is currently unavailable. " \
                     f"Please perform manual gap analysis for detailed insights."
        else:
            content = self._template_responses['gap_detection']['no_detection']
        
        return FallbackResponse(
            content=content,
            confidence=0.4,
            fallback_type='rule_based',
            metadata={'gap_areas': gap_areas},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_duplicate_detection(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for duplicate detection."""
        # Try basic duplicate detection using string similarity
        duplicates = self._basic_duplicate_detection(input_data)
        
        if duplicates:
            content = f"Basic duplicate analysis completed. Found {len(duplicates)} potential duplicates. " \
                     f"Advanced duplicate detection is currently unavailable. " \
                     f"Please review manually for confirmation."
        else:
            content = self._template_responses['duplicate_detection']['no_detection']
        
        return FallbackResponse(
            content=content,
            confidence=0.3,
            fallback_type='algorithmic',
            metadata={'duplicates': duplicates},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_vendor_discovery(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for vendor discovery."""
        # Extract vendor-related keywords
        vendor_terms = self._extract_vendor_terms(input_data)
        
        if vendor_terms:
            content = f"Vendor-related terms identified: {', '.join(vendor_terms)}. " \
                     f"AI-powered vendor discovery is currently unavailable. " \
                     f"Please use the vendor catalog search for manual vendor discovery."
        else:
            content = self._template_responses['vendor_discovery']['no_discovery']
        
        return FallbackResponse(
            content=content,
            confidence=0.4,
            fallback_type='rule_based',
            metadata={'vendor_terms': vendor_terms},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_suggestion_engine(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for suggestion engine."""
        # Generate basic suggestions based on context
        suggestions = self._generate_basic_suggestions(input_data, context)
        
        if suggestions:
            content = f"Basic suggestions available: {', '.join(suggestions)}. " \
                     f"AI-powered suggestions are currently unavailable. " \
                     f"Please explore available options manually."
        else:
            content = self._template_responses['suggestion_engine']['no_suggestions']
        
        return FallbackResponse(
            content=content,
            confidence=0.3,
            fallback_type='rule_based',
            metadata={'suggestions': suggestions},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_workflow_automation(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for workflow automation."""
        # Identify workflow steps
        steps = self._identify_workflow_steps(input_data)
        
        if steps:
            content = f"Workflow steps identified: {', '.join(steps)}. " \
                     f"AI-powered workflow automation is currently unavailable. " \
                     f"Please perform workflow steps manually."
        else:
            content = self._template_responses['workflow_automation']['no_automation']
        
        return FallbackResponse(
            content=content,
            confidence=0.3,
            fallback_type='rule_based',
            metadata={'steps': steps},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_consolidation_analysis(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for consolidation analysis."""
        # Basic consolidation analysis
        analysis = self._basic_consolidation_analysis(input_data)
        
        if analysis:
            content = f"Basic consolidation analysis: {analysis}. " \
                     f"Advanced consolidation analysis is currently unavailable. " \
                     f"Please perform detailed analysis manually."
        else:
            content = self._template_responses['consolidation_analysis']['no_analysis']
        
        return FallbackResponse(
            content=content,
            confidence=0.4,
            fallback_type='algorithmic',
            metadata={'analysis': analysis},
            timestamp=datetime.utcnow()
        )
    
    def _fallback_capability_mapping(self, input_data: Any, context: Dict[str, Any]) -> FallbackResponse:
        """Fallback for capability mapping."""
        # Extract capability terms
        capabilities = self._extract_capability_terms(input_data)
        
        if capabilities:
            content = f"Capability terms identified: {', '.join(capabilities)}. " \
                     f"AI-powered capability mapping is currently unavailable. " \
                     f"Please use manual mapping tools."
        else:
            content = self._template_responses['capability_mapping']['no_mapping']
        
        return FallbackResponse(
            content=content,
            confidence=0.4,
            fallback_type='rule_based',
            metadata={'capabilities': capabilities},
            timestamp=datetime.utcnow()
        )
    
    def _default_fallback(self, feature: str, input_data: Any) -> FallbackResponse:
        """Default fallback for unknown features."""
        return FallbackResponse(
            content=f"The {feature} feature is currently unavailable. Please try again later.",
            confidence=0.3,
            fallback_type='default',
            metadata={'feature': feature},
            timestamp=datetime.utcnow()
        )
    
    def _error_fallback(self, feature: str, error: str) -> FallbackResponse:
        """Error fallback for failed strategies."""
        return FallbackResponse(
            content=f"The {feature} feature encountered an error: {error}. Please try again later.",
            confidence=0.2,
            fallback_type='error',
            metadata={'feature': feature, 'error': error},
            timestamp=datetime.utcnow()
        )
    
    def _extract_basic_document_info(self, input_data: Any) -> Optional[str]:
        """Extract basic document information."""
        if isinstance(input_data, str):
            # Try to detect document type from filename or content
            for pattern, doc_type in self._rule_based_responses['document_analysis']['document_types'].items():
                if re.search(pattern, input_data.lower()):
                    return doc_type
        
        return None
    
    def _extract_basic_archimate_elements(self, input_data: Any) -> List[str]:
        """Extract basic ArchiMate elements."""
        elements = []
        
        if isinstance(input_data, str):
            # Look for common ArchiMate element keywords
            archimate_keywords = [
                'application', 'business', 'technology', 'process', 'service',
                'function', 'interface', 'component', 'node', 'artifact'
            ]
            
            for keyword in archimate_keywords:
                if keyword.lower() in input_data.lower():
                    elements.append(keyword.capitalize())
        
        return elements
    
    def _identify_potential_gaps(self, input_data: Any) -> List[str]:
        """Identify potential gap areas."""
        gaps = []
        
        if isinstance(input_data, str):
            # Look for gap-related keywords
            gap_keywords = [
                'missing', 'lacking', 'absent', 'gap', 'shortfall', 'deficiency',
                'incomplete', 'uncovered', 'unaddressed'
            ]
            
            for keyword in gap_keywords:
                if keyword.lower() in input_data.lower():
                    gaps.append(keyword)
        
        return gaps
    
    def _basic_duplicate_detection(self, input_data: Any) -> List[str]:
        """Basic duplicate detection using string similarity."""
        duplicates = []
        
        if isinstance(input_data, list):
            # Simple duplicate detection in lists
            seen = set()
            for item in input_data:
                if isinstance(item, str) and item in seen:
                    duplicates.append(item)
                seen.add(item)
        
        return duplicates
    
    def _extract_vendor_terms(self, input_data: Any) -> List[str]:
        """Extract vendor-related terms."""
        terms = []
        
        if isinstance(input_data, str):
            vendor_keywords = self._rule_based_responses['vendor_discovery']['vendor_keywords']
            
            for keyword in vendor_keywords:
                if keyword.lower() in input_data.lower():
                    terms.append(keyword)
        
        return terms
    
    def _generate_basic_suggestions(self, input_data: Any, context: Dict[str, Any]) -> List[str]:
        """Generate basic suggestions."""
        suggestions = []
        
        # Context-based suggestions
        if 'feature' in context:
            feature = context['feature']
            if feature == 'applications':
                suggestions.extend(['Review application details', 'Check vendor information', 'Analyze capabilities'])
            elif feature == 'vendors':
                suggestions.extend(['Search vendor catalog', 'Review vendor products', 'Check vendor ratings'])
        
        return suggestions
    
    def _identify_workflow_steps(self, input_data: Any) -> List[str]:
        """Identify workflow steps."""
        steps = []
        
        if isinstance(input_data, str):
            # Look for step-related keywords
            step_keywords = [
                'create', 'review', 'approve', 'implement', 'deploy', 'monitor',
                'analyze', 'design', 'test', 'validate', 'document'
            ]
            
            for keyword in step_keywords:
                if keyword.lower() in input_data.lower():
                    steps.append(keyword.capitalize())
        
        return steps
    
    def _basic_consolidation_analysis(self, input_data: Any) -> Optional[str]:
        """Basic consolidation analysis."""
        if isinstance(input_data, list) and len(input_data) > 1:
            return f"Found {len(input_data)} items that may be candidates for consolidation"
        
        return None
    
    def _extract_capability_terms(self, input_data: Any) -> List[str]:
        """Extract capability terms."""
        terms = []
        
        if isinstance(input_data, str):
            # Look for capability-related keywords
            capability_keywords = [
                'capability', 'ability', 'skill', 'competence', 'function',
                'process', 'service', 'activity', 'task', 'operation'
            ]
            
            for keyword in capability_keywords:
                if keyword.lower() in input_data.lower():
                    terms.append(keyword)
        
        return terms
    
    def is_fallback_available(self, feature: str) -> bool:
        """
        Check if fallback is available for a feature.
        
        Args:
            feature: AI feature name
            
        Returns:
            True if fallback is available, False otherwise
        """
        return feature in self._fallback_strategies
    
    def get_fallback_capabilities(self) -> Dict[str, Any]:
        """
        Get information about fallback capabilities.
        
        Returns:
            Fallback capabilities summary
        """
        return {
            'available_features': list(self._fallback_strategies.keys()),
            'fallback_types': ['rule_based', 'template', 'algorithmic', 'default', 'error'],
            'template_responses': {k: list(v.keys()) for k, v in self._template_responses.items()},
            'rule_based_patterns': {k: list(v.keys()) for k, v in self._rule_based_responses.items()},
            'timestamp': datetime.utcnow().isoformat()
        }

# Global AI fallback service instance
ai_fallback_service = AIFallbackService()
