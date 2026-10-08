"""
Salesforce Code Generation Service

Adapted from MDD flask-base-master for archie integration.
Extends the base code generation service with Salesforce-specific generation logic.
Uses templates for deterministic structural code and LLM for complex business logic.
"""

import logging
import os
from typing import Dict, List, Optional

from flask import current_app
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.llm_service import LLMService
from app.services.mdd_code_generation_service import CodeArtifact, TechnologyStack, UMLElement
from app.services.salesforce_validator import SalesforceValidator

logger = logging.getLogger(__name__)


class SalesforceCodeGenerator:
    """
    Generates Salesforce code (Apex, LWC, metadata) from models.
    """

    def __init__(self, technology_stack: TechnologyStack):
        """
        Initialize Salesforce code generator.

        Args:
            technology_stack: Technology stack configuration
        """
        self.technology_stack = technology_stack
        self.validator = SalesforceValidator()
        self.template_env = self._get_template_environment()

    def _get_template_environment(self) -> Environment:
        """
        Gets Jinja2 template environment for Salesforce templates.
        """
        templates_path = os.path.join(
            current_app.root_path, "services", "code_templates", "salesforce"
        )

        os.makedirs(templates_path, exist_ok=True)

        env = Environment(
            autoescape=select_autoescape(("html.j2", "htm.j2"), default_for_string=False, default=False),
            loader=FileSystemLoader(templates_path), trim_blocks=True, lstrip_blocks=True
        )

        return env

    def generate_apex_class(
        self,
        uml_element: UMLElement,
        class_type: str = "Service",
        business_logic: Optional[Dict[str, str]] = None,
    ) -> CodeArtifact:
        """
        Generates an Apex class from UML element.

        Args:
            uml_element: UMLElement instance
            class_type: Type of Apex class (Service, Controller, Trigger, etc.)
            business_logic: Optional business logic for methods

        Returns:
            CodeArtifact with Apex code
        """
        template_name = f"apex_{class_type.lower()}.j2"

        try:
            template = self.template_env.get_template(template_name)
        except Exception:
            logger.warning(f"Template {template_name} not found, using LLM generation")
            return self._generate_apex_with_llm(uml_element, class_type)

        apex_code = template.render(
            class_name=uml_element.name,
            object_name=uml_element.name,
            sharing_mode="with sharing",
            api_version="59.0",
            attributes=uml_element.attributes.all(),
            methods=uml_element.methods.all(),
            business_logic=business_logic or {},
        )

        violations = self.validator.validate_apex_code(apex_code)

        if violations and any(v.get("severity") == "critical" for v in violations):
            logger.error(f"Critical violations in generated Apex: {violations}")
            raise ValueError(f"Generated Apex has critical violations: {violations}")

        artifact = CodeArtifact(
            name=f"{uml_element.name}.cls",
            type="apex_class",
            language="salesforce",
            content=apex_code,
        )

        artifact.metadata["validation"] = {
            "validated": True,
            "violations": violations,
            "source": "template",
        }

        return artifact

    def generate_apex_trigger(
        self, object_name: str, trigger_events: List[str] = None
    ) -> CodeArtifact:
        """
        Generates an Apex trigger for a Salesforce object.

        Args:
            object_name: Salesforce object API name
            trigger_events: List of trigger events (before insert, after update, etc.)

        Returns:
            CodeArtifact with trigger code
        """
        if not trigger_events:
            trigger_events = ["before insert", "before update", "after insert", "after update"]

        try:
            template = self.template_env.get_template("apex_trigger.j2")
        except Exception:
            return self._generate_trigger_with_llm(object_name, trigger_events)

        apex_code = template.render(
            object_name=object_name, trigger_events=", ".join(trigger_events), api_version="59.0"
        )

        violations = self.validator.validate_apex_code(apex_code)

        artifact = CodeArtifact(
            name=f"{object_name}Trigger.trigger",
            type="apex_trigger",
            language="salesforce",
            content=apex_code,
        )

        artifact.metadata["validation"] = {
            "validated": True,
            "violations": violations,
            "source": "template",
        }

        return artifact

    def generate_lwc_component(
        self, component_name: str, object_api_name: str, component_type: str = "record_form"
    ) -> Dict[str, CodeArtifact]:
        """
        Generates a Lightning Web Component (HTML, JS, XML).

        Args:
            component_name: Component name (camelCase)
            object_api_name: Salesforce object API name
            component_type: Type of component (record_form, data_table, custom)

        Returns:
            Dict with HTML, JS, and XML artifacts
        """
        artifacts = {}

        try:
            html_template = self.template_env.get_template("lwc_component_html.j2")
            js_template = self.template_env.get_template("lwc_component_js.j2")
            xml_template = self.template_env.get_template("lwc_component_xml.j2")

            html_content = html_template.render(
                component_name=component_name,
                object_api_name=object_api_name,
                has_form=(component_type == "record_form"),
                has_data_table=(component_type == "data_table"),
            )

            js_content = js_template.render(
                component_name=component_name,
                object_api_name=object_api_name,
                has_data_table=(component_type == "data_table"),
            )

            xml_content = xml_template.render(
                component_name=component_name,
                object_api_name=object_api_name,
                is_exposed=True,
                targets=["lightning__RecordPage", "lightning__AppPage"],
            )

            artifacts["html"] = CodeArtifact(
                name=f"{component_name}.html",
                type="lwc_html",
                language="html",
                content=html_content,
            )

            artifacts["js"] = CodeArtifact(
                name=f"{component_name}.js",
                type="lwc_javascript",
                language="javascript",
                content=js_content,
            )

            artifacts["xml"] = CodeArtifact(
                name=f"{component_name}.js-meta.xml",
                type="lwc_metadata",
                language="xml",
                content=xml_content,
            )

        except Exception as e:
            logger.error(f"Failed to generate LWC component: {str(e)}")
            raise

        return artifacts

    def _generate_apex_with_llm(self, uml_element: UMLElement, class_type: str) -> CodeArtifact:
        """
        Generate Apex class using LLM when template not available.
        """
        prompt = f"""Generate Salesforce Apex {class_type} class for {uml_element.name}.

CRITICAL REQUIREMENTS:
- NEVER put SOQL or DML inside loops (governor limit violation)
- Always process collections (List<SObject>), not single records
- Add WITH USER_MODE to SOQL queries for security
- Use 'with sharing' for user context
- Include proper exception handling
- Add comprehensive test coverage patterns

Class: {uml_element.name}
Type: {class_type}

Attributes:
{chr(10).join([f"  - {attr.name}: {attr.data_type}" for attr in uml_element.attributes.all()])}

Methods:
{chr(10).join([f"  - {method.name}({method.parameters}): {method.return_type}" for method in uml_element.methods.all()])}

Output ONLY the Apex code, no markdown or explanations."""

        try:
            apex_code = LLMService.generate_from_prompt(prompt)
            apex_code = self._clean_llm_response(apex_code)
        except Exception as e:
            logger.error(f"LLM generation failed: {str(e)}")
            raise

        violations = self.validator.validate_apex_code(apex_code)

        artifact = CodeArtifact(
            name=f"{uml_element.name}.cls",
            type="apex_class",
            language="salesforce",
            content=apex_code,
        )

        artifact.metadata["validation"] = {
            "validated": True,
            "violations": violations,
            "source": "llm",
        }

        return artifact

    def _generate_trigger_with_llm(
        self, object_name: str, trigger_events: List[str]
    ) -> CodeArtifact:
        """Generate Apex trigger using LLM."""
        prompt = f"""Generate Salesforce Apex trigger for {object_name}.

Events: {', '.join(trigger_events)}

CRITICAL REQUIREMENTS:
- Delegate to handler class (TriggerHandler pattern)
- NEVER put business logic in trigger
- NEVER put SOQL or DML in trigger
- Keep trigger simple and clean

Output ONLY the Apex trigger code, no markdown."""

        apex_code = LLMService.generate_from_prompt(prompt)
        apex_code = self._clean_llm_response(apex_code)

        violations = self.validator.validate_apex_code(apex_code)

        artifact = CodeArtifact(
            name=f"{object_name}Trigger.trigger",
            type="apex_trigger",
            language="salesforce",
            content=apex_code,
        )

        artifact.metadata["validation"] = {
            "validated": True,
            "violations": violations,
            "source": "llm",
        }

        return artifact

    def _clean_llm_response(self, content: str) -> str:
        """Remove markdown code blocks from LLM response."""
        if content.startswith("```"):
            lines = content.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)
        return content.strip()

    def _pluralize(self, word: str) -> str:
        """Simple pluralization (English)."""
        if word.endswith("y"):
            return word[:-1] + "ies"
        elif word.endswith("s"):
            return word + "es"
        else:
            return word + "s"

    def _to_camel_case(self, text: str) -> str:
        """Converts text to camelCase."""
        text = text.replace("__c", "")
        parts = text.split("_")
        return parts[0].lower() + "".join(word.capitalize() for word in parts[1:])
