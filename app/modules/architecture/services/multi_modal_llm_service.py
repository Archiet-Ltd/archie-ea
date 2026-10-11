"""
Multi-Modal LLM Service for ArchiMate Engine

Handles vision-enabled AI analysis of documents and images.
Supports GPT - 4 Vision, Claude 3.5 Sonnet, and Gemini 2.5 models.
"""

import asyncio
import base64
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.models import LLMInteraction
from app.services.llm_service import LLMService


class MultiModalLLMService:
    """
    Service for multi-modal AI analysis (text + images)
    """

    def __init__(self):
        """Initialize multi-modal service"""
        self.llm_service = LLMService()

    def _resolve_api_key(self, provider: str) -> Optional[str]:
        """Resolve API key from database settings or environment."""
        from app.models.models import APISettings

        settings = APISettings.query.filter_by(provider=provider).first()
        if settings and settings.api_key:
            return settings.api_key

        return os.getenv(f"{provider.upper()}_API_KEY")

    def _guard_provider_call(self, provider: str, model: str | None, prompt: str | None) -> None:
        LLMService._guard_provider_call(provider, model, prompt=prompt)

    def encode_image(self, image_path: str) -> str:
        """
        Encode image to base64 for API submission

        Args:
            image_path: Path to image file

        Returns:
            Base64 - encoded image string
        """
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    def get_image_mime_type(self, image_path: str) -> str:
        """
        Get MIME type from file extension

        Args:
            image_path: Path to image file

        Returns:
            MIME type string
        """
        ext = image_path.rsplit(".", 1)[1].lower()
        mime_types = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        return mime_types.get(ext, "image/jpeg")

    async def analyze_image_with_claude(
        self, image_path: str, prompt: str, max_tokens: int = 4000, temperature: float = 0.2
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """
        Analyze image using Claude 3.5 Sonnet vision capabilities

        Args:
            image_path: Path to image file
            prompt: Analysis prompt
            max_tokens: Maximum tokens for response
            temperature: Temperature for generation

        Returns:
            (response_text, llm_interaction)
        """
        # Encode image
        image_data = self.encode_image(image_path)
        mime_type = self.get_image_mime_type(image_path)

        # Build messages with vision content
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": image_data},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        # Call Claude with vision
        result = await self.llm_service.generate_with_messages(
            messages=messages,
            model="claude - 3 - 5-sonnet - 20241022",  # Claude 3.5 Sonnet with vision
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return result

    async def analyze_image_with_gpt4v(
        self, image_path: str, prompt: str, max_tokens: int = 4000, temperature: float = 0.2
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """
        Analyze image using GPT - 4 Vision

        Args:
            image_path: Path to image file
            prompt: Analysis prompt
            max_tokens: Maximum tokens for response
            temperature: Temperature for generation

        Returns:
            (response_text, llm_interaction)
        """
        # Encode image
        image_data = self.encode_image(image_path)
        mime_type = self.get_image_mime_type(image_path)

        # Build messages with vision content
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_data}",
                            "detail": "high",  # High detail for architecture diagrams
                        },
                    },
                ],
            }
        ]

        # Call GPT - 4 Vision
        result = await self.llm_service.generate_with_messages(
            messages=messages,
            model="gpt - 4 - vision-preview",
            max_tokens=max_tokens,
            temperature=temperature,
        )

        return result

    async def analyze_image_with_gemini(
        self,
        image_path: str,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.2,
        model: str = "gemini - 1.5 - pro-latest",
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """Analyze image using Google Gemini vision models."""

        api_key = self._resolve_api_key("gemini")
        if not api_key:
            raise ValueError(
                "Gemini API key not configured. Set GEMINI_API_KEY or configure via API Settings."
            )

        image_data = self.encode_image(image_path)
        mime_type = self.get_image_mime_type(image_path)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": image_data}},
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }

        self._guard_provider_call("gemini", model, prompt)

        async def _invoke() -> Dict[str, Any]:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            return response.json()

        data = await asyncio.to_thread(_invoke)

        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini response did not include any candidates")

        response_text = ""
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                response_text += part["text"]

        usage = data.get("usageMetadata", {})
        token_input = usage.get("promptTokenCount", 0)
        token_output = usage.get("candidatesTokenCount", 0)
        if not token_output:
            total = usage.get("totalTokenCount")
            if total is not None:
                token_output = max(total - token_input, 0)

        interaction = LLMInteraction(
            model_name=model,
            provider="gemini",
            prompt=prompt[:1000],
            response=response_text[:2000],
            token_count_input=token_input,
            token_count_output=token_output,
            cost=LLMService.estimate_cost(len(prompt), len(response_text), model=model),
        )

        return response_text, interaction

    async def analyze_pdf_with_gemini(
        self,
        pdf_path: str,
        prompt: str,
        max_tokens: int = 8000,
        temperature: float = 0.2,
        model: str = "gemini - 2.5 - flash",
        use_files_api: bool = False,
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """
        Analyze PDF using Gemini's native document understanding capabilities.

        Uses Gemini's native PDF processing which understands:
        - Text, images, diagrams, charts, and tables
        - Document layout and formatting
        - Up to 1000 pages or 50MB

        Args:
            pdf_path: Path to PDF file
            prompt: Analysis prompt
            max_tokens: Maximum tokens for response
            temperature: Temperature for generation
            model: Gemini model to use (gemini - 2.5 - flash recommended for PDFs)
            use_files_api: If True, use Files API (better for large docs/multi-turn)
                          If False, use inline data (simpler, good for single requests)

        Returns:
            (response_text, llm_interaction)
        """
        api_key = self._resolve_api_key("gemini")
        if not api_key:
            raise ValueError(
                "Gemini API key not configured. Set GEMINI_API_KEY or configure via API Settings."
            )

        # Read PDF file
        with open(pdf_path, "rb") as pdf_file:
            pdf_data = pdf_file.read()

        pdf_base64 = base64.b64encode(pdf_data).decode("utf-8")

        if use_files_api:
            # Use Files API for larger documents or multi-turn conversations
            return await self._analyze_pdf_with_files_api(
                pdf_path, pdf_data, prompt, api_key, model, max_tokens, temperature
            )
        else:
            # Use inline data for simpler, single-request processing
            return await self._analyze_pdf_inline(
                pdf_base64, prompt, api_key, model, max_tokens, temperature
            )

    async def _analyze_pdf_inline(
        self,
        pdf_base64: str,
        prompt: str,
        api_key: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """Analyze PDF using inline base64 data."""
        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "application/pdf", "data": pdf_base64}},
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }

        self._guard_provider_call("gemini", model, prompt)

        def _invoke() -> Dict[str, Any]:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                json=payload,
                timeout=300,  # Longer timeout for PDF processing
            )
            response.raise_for_status()
            return response.json()

        data = await asyncio.to_thread(_invoke)

        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini response did not include any candidates")

        response_text = ""
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                response_text += part["text"]

        usage = data.get("usageMetadata", {})
        token_input = usage.get("promptTokenCount", 0)
        token_output = usage.get("candidatesTokenCount", 0)
        if not token_output:
            total = usage.get("totalTokenCount")
            if total is not None:
                token_output = max(total - token_input, 0)

        interaction = LLMInteraction(
            model_name=model,
            provider="gemini",
            prompt=prompt[:1000],
            response=response_text[:2000],
            token_count_input=token_input,
            token_count_output=token_output,
            cost=LLMService.estimate_cost(token_input, token_output, model=model),
        )

        return response_text, interaction

    async def _analyze_pdf_with_files_api(
        self,
        pdf_path: str,
        pdf_data: bytes,
        prompt: str,
        api_key: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """Analyze PDF using Gemini Files API (better for large documents)."""

        # Step 1: Upload file to Files API
        file_size = len(pdf_data)
        file_name = os.path.basename(pdf_path)

        # Start resumable upload
        self._guard_provider_call("gemini", model, prompt)
        upload_start_resp = requests.post(
            "https://generativelanguage.googleapis.com/upload/v1beta/files",
            params={"key": api_key},
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(file_size),
                "X-Goog-Upload-Header-Content-Type": "application/pdf",
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": file_name}},
            timeout=30,
        )
        upload_start_resp.raise_for_status()

        upload_url = upload_start_resp.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise ValueError("Upload URL not returned from Gemini Files API")

        # Upload the file
        upload_resp = requests.put(
            upload_url,
            headers={
                "Content-Length": str(file_size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            data=pdf_data,
            timeout=300,
        )
        upload_resp.raise_for_status()

        file_info = upload_resp.json()
        file_uri = file_info.get("file", {}).get("uri")
        if not file_uri:
            raise ValueError("File URI not returned after upload")

        try:
            # Step 2: Generate content using file URI
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"file_data": {"mime_type": "application/pdf", "file_uri": file_uri}},
                            {"text": prompt},
                        ]
                    }
                ],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
            }

            self._guard_provider_call("gemini", model, prompt)

            def _invoke() -> Dict[str, Any]:
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": api_key},
                    json=payload,
                    timeout=300,
                )
                response.raise_for_status()
                return response.json()

            data = await asyncio.to_thread(_invoke)

            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("Gemini response did not include any candidates")

            response_text = ""
            for part in candidates[0].get("content", {}).get("parts", []):
                if "text" in part:
                    response_text += part["text"]

            usage = data.get("usageMetadata", {})
            token_input = usage.get("promptTokenCount", 0)
            token_output = usage.get("candidatesTokenCount", 0)
            if not token_output:
                total = usage.get("totalTokenCount")
                if total is not None:
                    token_output = max(total - token_input, 0)

            interaction = LLMInteraction(
                model_name=model,
                provider="gemini",
                prompt=prompt[:1000],
                response=response_text[:2000],
                token_count_input=token_input,
                token_count_output=token_output,
                cost=LLMService.estimate_cost(token_input, token_output, model=model),
            )

            return response_text, interaction

        finally:
            # Step 3: Clean up - delete the uploaded file
            try:
                # Extract file name from URI (format: files/{file_name})
                if file_uri and "/" in file_uri:
                    file_name = file_uri.split("/")[-1]
                    requests.delete(
                        f"https://generativelanguage.googleapis.com/v1beta/files/{file_name}",
                        params={"key": api_key},
                        timeout=30,
                    )
            except Exception as e:
                # Log but don't fail if cleanup fails
                import logging

                logging.getLogger(__name__).warning(f"Failed to delete Gemini file {file_uri}: {e}")

    async def analyze_image(
        self,
        image_path: str,
        prompt: str,
        provider: str = "claude",  # 'claude', 'openai', or 'gemini'
        max_tokens: int = 4000,
        temperature: float = 0.2,
    ) -> Tuple[str, Optional[LLMInteraction]]:
        """
        Analyze image using specified vision model

        Args:
            image_path: Path to image file
            prompt: Analysis prompt
            provider: 'claude', 'openai', or 'gemini'
            max_tokens: Maximum tokens for response
            temperature: Temperature for generation

        Returns:
            (response_text, llm_interaction)
        """
        if provider == "claude":
            return await self.analyze_image_with_claude(image_path, prompt, max_tokens, temperature)
        elif provider == "openai":
            return await self.analyze_image_with_gpt4v(image_path, prompt, max_tokens, temperature)
        elif provider == "gemini":
            return await self.analyze_image_with_gemini(image_path, prompt, max_tokens, temperature)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    async def extract_archimate_from_diagram(
        self, image_path: str, provider: str = "claude", extra_instructions: str = ""
    ) -> Tuple[Dict, Optional[LLMInteraction]]:
        """
        Extract ArchiMate elements from architecture diagram image

        Args:
            image_path: Path to diagram image
            provider: 'claude', 'openai', or 'gemini'
            extra_instructions: Optional caller-specific guidance appended to the
                prompt — e.g. "focus on Application Layer elements" when the
                upload arrived through an application-context flow. Optional and
                defaulted so existing two-argument callers are unaffected.

        Returns:
            (parsed_json, llm_interaction) with ArchiMate elements and relationships
        """
        prompt = """
Analyze this architecture diagram and extract ArchiMate 3.2 elements and relationships.

INSTRUCTIONS:
1. Identify all architecture elements (boxes, nodes, components)
2. Classify each element by ArchiMate 3.2 type and layer:
   - Motivation: Stakeholder, Driver, Assessment, Goal, Outcome, Principle, Requirement, Constraint
   - Strategy: Resource, Capability, Course of Action, Value Stream
   - Business: Actor, Role, Collaboration, Interface, Process, Function, Interaction, Event, Service, Object, Contract, Representation, Product
   - Application: Component, Interface, Function, Interaction, Event, Service, Data Object
   - Technology: Node, Device, System Software, Technology Collaboration, Interface, Path, Communication Network, Function, Process, Interaction, Event, Service, Artifact
   - Implementation: Work Package, Deliverable, Implementation Event, Plateau, Gap

3. Identify relationships between elements:
   - Composition, Aggregation, Assignment, Realization, Serving, Access, Influence, Triggering, Flow, Specialization, Association

4. Extract element properties:
   - Name/label
   - Description (if visible)
   - Layer assignment
   - Position/grouping (if diagram shows layers/zones)

Return ONLY valid JSON in this exact format:
{
  "elements": [
    {
      "name": "Element Name",
      "type": "BusinessProcess",
      "layer": "business",
      "description": "Optional description",
      "properties": {}
    }
  ],
  "relationships": [
    {
      "source": "Element Name 1",
      "target": "Element Name 2",
      "type": "Flow",
      "description": "Optional description"
    }
  ],
  "metadata": {
    "diagram_type": "process flow / component diagram / etc",
    "confidence": "high/medium/low",
    "notes": "Any observations about the diagram"
  }
}

Be conservative - only extract elements you can clearly identify. If uncertain about element type, note it in metadata.
"""

        if extra_instructions and extra_instructions.strip():
            prompt = f"{prompt}\nADDITIONAL CONTEXT FOR THIS UPLOAD:\n{extra_instructions.strip()}\n"

        response_text, interaction = await self.analyze_image(
            image_path, prompt, provider, max_tokens=6000, temperature=0.1
        )

        # Parse JSON from response
        try:
            # Try to extract JSON if wrapped in markdown
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                json_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                json_text = response_text[json_start:json_end].strip()
            else:
                json_text = response_text

            parsed = json.loads(json_text)
            return parsed, interaction

        except json.JSONDecodeError as e:
            # Return raw response if parsing fails
            return {
                "elements": [],
                "relationships": [],
                "metadata": {
                    "error": f"JSON parsing failed: {str(e)}",
                    "raw_response": response_text,
                },
            }, interaction

    async def extract_archimate_from_multiple_images(
        self, image_paths: List[str], provider: str = "claude"
    ) -> Tuple[Dict, List[LLMInteraction]]:
        """
        Extract ArchiMate elements from multiple diagram images and merge results

        Args:
            image_paths: List of paths to diagram images
            provider: 'claude', 'openai', or 'gemini'

        Returns:
            (merged_results, list_of_interactions)
        """
        all_elements = []
        all_relationships = []
        all_metadata = []
        interactions = []

        for image_path in image_paths:
            result, interaction = await self.extract_archimate_from_diagram(image_path, provider)

            if interaction:
                interactions.append(interaction)

            all_elements.extend(result.get("elements", []))
            all_relationships.extend(result.get("relationships", []))
            all_metadata.append(result.get("metadata", {}))

        # Deduplicate elements by name and type
        unique_elements = {}
        for elem in all_elements:
            key = (elem["name"], elem["type"])
            if key not in unique_elements:
                unique_elements[key] = elem

        # Deduplicate relationships
        unique_relationships = {}
        for rel in all_relationships:
            key = (rel["source"], rel["target"], rel["type"])
            if key not in unique_relationships:
                unique_relationships[key] = rel

        merged = {
            "elements": list(unique_elements.values()),
            "relationships": list(unique_relationships.values()),
            "metadata": {
                "diagram_count": len(image_paths),
                "total_elements_extracted": len(all_elements),
                "unique_elements": len(unique_elements),
                "total_relationships_extracted": len(all_relationships),
                "unique_relationships": len(unique_relationships),
                "individual_metadata": all_metadata,
            },
        }

        return merged, interactions

    async def analyze_document_text(
        self,
        text: str,
        document_type: str,
        provider: str = "claude",
        use_enhanced_extraction: bool = True,
    ) -> Tuple[Dict, Optional[LLMInteraction]]:
        """
        Analyze text document and extract ArchiMate elements

        Args:
            text: Document text content
            document_type: 'requirements', 'technical_spec', 'business_case', etc.
            provider: 'claude', 'openai', or 'gemini'
            use_enhanced_extraction: Use multi-pass enhanced extraction (default: True)

        Returns:
            (parsed_json, llm_interaction)
        """

        if use_enhanced_extraction:
            # Use enterprise-grade enhanced extraction
            from .enhanced_archimate_extractor import EnhancedArchiMateExtractor

            extractor = EnhancedArchiMateExtractor()
            parsed = extractor.extract_comprehensive_architecture(
                document_text=text, document_type=document_type, context=f"Provider: {provider}"
            )

            # Create interaction record
            from app.models import LLMInteraction

            model = (
                "claude - 3 - 5-sonnet - 20241022"
                if provider == "claude"
                else "gpt - 4 - turbo-preview"
            )
            interaction = LLMInteraction(
                model_name=model,
                provider="claude" if "claude" in model else "openai",
                prompt=f"Enhanced extraction: {document_type}"[:1000],
                response=f"Extracted {len(parsed.get('elements', []))} elements"[:2000],
                token_count_input=len(text) // 4,
                token_count_output=500,  # Estimate
                cost=0.05,  # Multi-pass cost
            )

            return parsed, interaction

        else:
            # Original single-pass extraction
            from .archimate_prompts import GENERATE_ARCHIMATE_FROM_REQUIREMENTS

            prompt = GENERATE_ARCHIMATE_FROM_REQUIREMENTS.format(
                requirements=text, context=f"Document type: {document_type}"
            )

            # Use standard text generation
            model = (
                "claude - 3 - 5-sonnet - 20241022"
                if provider == "claude"
                else "gpt - 4 - turbo-preview"
            )

            response_text = self.llm_service.generate_from_prompt(prompt)

            # Create interaction record manually since we don't have async version
            from app.models import LLMInteraction

            interaction = LLMInteraction(
                model_name=model,
                provider="claude" if "claude" in model else "openai",
                prompt=prompt[:1000],  # Truncate for storage
                response=response_text[:2000],  # Truncate for storage
                token_count_input=len(prompt) // 4,  # Rough estimate
                token_count_output=len(response_text) // 4,  # Rough estimate
                cost=0.01,  # Placeholder cost
            )

            # Parse JSON
            try:
                if "```json" in response_text:
                    json_start = response_text.find("```json") + 7
                    json_end = response_text.find("```", json_start)
                    json_text = response_text[json_start:json_end].strip()
                elif "```" in response_text:
                    json_start = response_text.find("```") + 3
                    json_end = response_text.find("```", json_start)
                    json_text = response_text[json_start:json_end].strip()
                else:
                    json_text = response_text

                parsed = json.loads(json_text)
                return parsed, interaction

            except json.JSONDecodeError as e:
                return {
                    "elements": [],
                    "relationships": [],
                    "metadata": {
                        "error": f"JSON parsing failed: {str(e)}",
                        "raw_response": response_text,
                    },
                }, interaction
