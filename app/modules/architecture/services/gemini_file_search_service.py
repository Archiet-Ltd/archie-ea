"""
Gemini File Search integration for ArchiMate document ingestion.

Provides helper utilities to:
- Create a temporary File Search store
- Upload documents using resumable uploads
- Wait for indexing operations to finish
- Query Gemini models with File Search grounding
- Clean up File Search stores once processing completes

The service is designed for short-lived stores created per document upload so
we avoid accumulating state in the Gemini project. Each public method enforces
timeouts and raises descriptive exceptions so upstream code can fall back or
surface actionable errors to the user.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class GeminiExtractionResult:
    """Represents the outcome of a Gemini File Search extraction."""

    response_text: str
    prompt: str
    system_prompt: str
    model: str
    usage: Dict[str, Any]
    grounding_metadata: Optional[Dict[str, Any]]
    citation_metadata: Optional[Dict[str, Any]]


class GeminiFileSearchService:
    """Utility wrapper for Gemini File Search API workflows."""

    GENERATIVE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    UPLOAD_BASE_URL = "https://generativelanguage.googleapis.com/upload/v1beta"
    DEFAULT_MODEL = "gemini - 2.5 - pro"

    def __init__(self):
        self.api_key = self._resolve_api_key()
        if not self.api_key:
            raise ValueError(
                "Gemini API key not configured. Set GEMINI_API_KEY or add a Gemini "
                "entry under Admin -> API Settings."
            )

    @staticmethod
    def _resolve_api_key() -> Optional[str]:
        """Resolve the Gemini API key from the database or environment."""
        try:
            from app.models.models import APISettings

            settings = APISettings.query.filter_by(provider="gemini", enabled=True).first()
            if settings and settings.api_key:
                return settings.api_key
        except Exception:  # SQLAlchemy might not be ready outside app context
            logger.debug("Unable to resolve Gemini API key from database", exc_info=True)

        return os.getenv("GEMINI_API_KEY")

    def extract_archimate_elements(
        self,
        file_path: str,
        document_type: str,
        additional_context: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = 300,
    ) -> GeminiExtractionResult:
        """End-to-end extraction workflow for a single document."""

        store_display = self._build_store_display_name(file_path)
        store_name = self._create_store(store_display)

        logger.info("[Gemini] Created temporary File Search store %s", store_name)

        try:
            operation_name = self._upload_file_to_store(store_name, file_path)
            self._wait_for_operation(operation_name, timeout_seconds=timeout_seconds)

            system_prompt = self._build_system_prompt(document_type)
            user_prompt = self._build_user_prompt(document_type, additional_context)

            response = self._generate_with_file_search(
                store_name=store_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
            )

            response_text = self._collect_response_text(response)
            usage = response.get("usageMetadata", {})
            candidate = self._first_candidate(response)
            grounding = None
            citations = None
            if candidate:
                grounding = candidate.get("groundingMetadata")
                citations = candidate.get("citationMetadata")

            return GeminiExtractionResult(
                response_text=response_text,
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=model,
                usage=usage,
                grounding_metadata=grounding,
                citation_metadata=citations,
            )
        finally:
            self._cleanup_store(store_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _guard_provider_call(self, model: str | None = None, prompt: str | None = None) -> None:
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        LLMService._guard_provider_call("gemini", model, prompt=prompt)

    @staticmethod
    def _build_store_display_name(file_path: str) -> str:
        base_name = os.path.basename(file_path) or "document"
        root, _ = os.path.splitext(base_name)
        slug = re.sub(r"[^a-zA-Z0 - 9-]+", "-", root).strip("-")
        slug = slug[:48] or "archimate-document"
        unique_suffix = uuid.uuid4().hex[:6]
        return f"archimate-{slug}-{unique_suffix}"[:60]

    def _create_store(self, display_name: str) -> str:
        self._guard_provider_call(model=self.DEFAULT_MODEL, prompt=f"create store {display_name}")
        resp = requests.post(
            f"{self.GENERATIVE_BASE_URL}/fileSearchStores",
            headers=self._headers(),
            json={"displayName": display_name},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "name" not in data:
            raise ValueError("Gemini File Search store creation did not return a name")
        return data["name"]

    def _upload_file_to_store(self, store_name: str, file_path: str) -> str:
        self._guard_provider_call(model=self.DEFAULT_MODEL, prompt=f"upload file {file_path}")
        file_size = os.path.getsize(file_path)
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        metadata = {
            "displayName": os.path.basename(file_path)[:120],
        }

        start_resp = requests.post(
            f"{self.UPLOAD_BASE_URL}/{store_name}:uploadToFileSearchStore",
            params={"key": self.api_key},
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(file_size),
                "X-Goog-Upload-Header-Content-Type": mime_type,
                "Content-Type": "application/json",
            },
            json=metadata,
            timeout=30,
        )
        start_resp.raise_for_status()

        upload_url = start_resp.headers.get("x-goog-upload-url")
        if not upload_url:
            raise ValueError("Gemini File Search did not return an upload URL")

        with open(file_path, "rb") as file_handle:
            upload_resp = requests.post(
                upload_url,
                headers={
                    "Content-Length": str(file_size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                data=file_handle,
                timeout=120,
            )

        upload_resp.raise_for_status()
        operation = upload_resp.json()
        operation_name = operation.get("name")
        if not operation_name:
            raise ValueError("Gemini upload response missing operation name")
        return operation_name

    def _wait_for_operation(self, operation_name: str, timeout_seconds: int = 300) -> None:
        self._guard_provider_call(model=self.DEFAULT_MODEL, prompt=f"wait for operation {operation_name}")
        deadline = time.time() + timeout_seconds
        poll_url = f"{self.GENERATIVE_BASE_URL}/{operation_name}"

        while True:
            resp = requests.get(
                poll_url,
                headers={"x-goog-api-key": self.api_key},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("done"):
                if "error" in data:
                    error = data["error"]
                    message = error.get("message", "Gemini operation failed")
                    raise RuntimeError(message)
                return

            if time.time() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for Gemini File Search operation {operation_name}"
                )

            time.sleep(3)

    @staticmethod
    def _build_system_prompt(document_type: str) -> str:
        return (
            "You are an enterprise architect who maps complex source documents into "
            "ArchiMate 3.2 models. You read indexed evidence via Gemini File Search "
            "and respond with precise JSON only."
        )

    @staticmethod
    def _build_user_prompt(document_type: str, additional_context: Optional[str]) -> str:
        context_block = f"\nADDITIONAL CONTEXT:\n{additional_context}" if additional_context else ""
        return (
            "Analyze the uploaded document corpus and extract ArchiMate 3.2 "
            "elements plus relationships.\n\n"
            f"Document type: {document_type}."
            "\n\nReturn ONLY valid JSON in this format:\n"
            "{\n"
            '  "model_name": "Descriptive name",\n'
            '  "model_description": "1 - 2 sentence summary",\n'
            '  "elements": [\n'
            "    {\n"
            '      "name": "Element Name",\n'
            '      "type": "ArchiMateType",\n'
            '      "layer": "motivation|strategy|business|application|technology|implementation",\n'
            '      "description": "Detailed purpose (20+ words)",\n'
            '      "properties": { }\n'
            "    }\n"
            "  ],\n"
            '  "relationships": [\n'
            "    {\n"
            '      "source": "Element Name",\n'
            '      "target": "Element Name",\n'
            '      "type": "Serving|Realization|Flow|Triggering|Access|Association|Specialization|Composition|Aggregation",\n'
            '      "description": "Optional description"\n'
            "    }\n"
            "  ],\n"
            '  "metadata": {\n'
            '    "document_type": "{document_type}",\n'
            '    "confidence": "high|medium|low",\n'
            '    "notes": "Insights, risks, assumptions"\n'
            "  }\n"
            "}\n"
            "Maintain factual grounding by citing document evidence in descriptions."
            f"{context_block}\n"
        )

    def _generate_with_file_search(
        self,
        store_name: str,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> Dict[str, Any]:
        self._guard_provider_call(model=model, prompt=user_prompt)
        payload = {
            "systemInstruction": {
                "role": "system",
                "parts": [{"text": system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": user_prompt},
                    ],
                }
            ],
            "tools": [
                {
                    "file_search": {
                        "file_search_store_names": [store_name],
                    }
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 6000,
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        resp = requests.post(
            f"{self.GENERATIVE_BASE_URL}/models/{model}:generateContent",
            headers=self._headers(),
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _collect_response_text(response: Dict[str, Any]) -> str:
        candidate = GeminiFileSearchService._first_candidate(response)
        if not candidate:
            return ""

        parts = candidate.get("content", {}).get("parts", [])
        texts = [part.get("text", "") for part in parts if "text" in part]
        return "".join(texts).strip()

    @staticmethod
    def _first_candidate(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidates = response.get("candidates", [])
        return candidates[0] if candidates else None

    def _cleanup_store(self, store_name: str) -> None:
        try:
            self._guard_provider_call(model=self.DEFAULT_MODEL, prompt=f"cleanup store {store_name}")
            requests.delete(
                f"{self.GENERATIVE_BASE_URL}/{store_name}",
                headers={"x-goog-api-key": self.api_key},
                timeout=20,
            )
        except requests.HTTPError as exc:
            logger.warning("[Gemini] Failed to delete store %s: %s", store_name, exc)
        except Exception:
            logger.debug("[Gemini] Error deleting store %s", store_name, exc_info=True)
