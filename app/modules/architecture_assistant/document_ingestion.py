"""Document Ingestion Service — extract architecture elements from uploaded documents.

Extracts text from documents, sends to LLM for structured extraction,
creates SolutionBlueprintProposal rows for architect review.
"""

import json
import logging
import re

from app import db

logger = logging.getLogger(__name__)

# Severity mapping used when stamping acm_properties.business_rules onto motivation-layer
# proposals at creation time. These are read by Source 1 in _get_solution_business_rules()
# before any domain promotion occurs.
_MOTIVATION_SEVERITY = {
    "Constraint": "must",
    "Goal": "should",
    "Driver": "context",
    "Requirement": "should",
}

# Fallback ACM domain assignment when the LLM doesn't return a domain field.
# Used so that proposals are never born without an acm_domain — orphaned proposals
# are invisible in Step 2 and can never be promoted to ArchiMate elements.
_VALID_ACM_DOMAINS = {"UX", "APP", "DATA", "SEC", "DEV", "AI", "COM"}
_TYPE_FALLBACK_DOMAIN = {
    "ApplicationComponent": "APP",
    "BusinessProcess": "APP",
    "Capability": "APP",
    "DataObject": "DATA",
    "Goal": "APP",
    "Driver": "APP",
    "Constraint": "SEC",
    "Requirement": "APP",
}

EXTRACTION_PROMPT = """Read this document and extract architecture-relevant elements.

For each element found, classify it as one of:
- Goal: business objectives, targets, KPIs
- Driver: business drivers, motivations, market forces
- Capability: business capabilities the solution must provide
- BusinessProcess: processes that must be supported or changed
- ApplicationComponent: systems, applications, platforms mentioned
- DataObject: data entities, datasets, data stores mentioned
- Constraint: regulatory, compliance, timeline, budget constraints

Also assign each element to exactly one of these 7 ACM architecture domains:
- UX: user experience, interfaces, accessibility, user journeys
- APP: application services, business logic, APIs, capabilities, processes
- DATA: data storage, data models, databases, data objects
- SEC: security, identity, compliance, regulatory constraints (GDPR, HIPAA, SOC2, etc.)
- DEV: DevOps, infrastructure, deployment, CI/CD, platform services
- AI: machine learning, analytics, reporting, AI/ML models
- COM: integration, messaging, events, APIs between systems

For each element provide:
- type: the ArchiMate type from the list above
- domain: one of UX / APP / DATA / SEC / DEV / AI / COM
- name: concise name (3-6 words)
- description: one sentence explaining it in context
- confidence: 0.0-1.0 how clearly this was stated in the document

Focus on elements that are architecturally significant. Skip generic statements.
Extract at most 30 elements. Prefer quality over quantity — capture every architecturally significant decision, constraint, and capability, but skip generic statements.

DOCUMENT TEXT:
{document_text}

Return ONLY valid JSON:
{{
    "elements": [
        {{"type": "Goal", "domain": "APP", "name": "Reduce fraud losses by 30%", "description": "CTO target for fiscal year", "confidence": 0.95}},
        {{"type": "Capability", "domain": "APP", "name": "Real-time transaction scoring", "description": "Score each transaction within 200ms", "confidence": 0.88}},
        {{"type": "Constraint", "domain": "SEC", "name": "GDPR compliance required", "description": "All user data must be processed per GDPR Article 6", "confidence": 0.99}}
    ],
    "summary": "Brief summary of what the document is about"
}}"""


def _parse_llm_json(raw_text):
    """Parse JSON from LLM response, stripping markdown fences."""
    if not raw_text:
        return None
    text = raw_text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)
    json_start = text.find("{")
    json_end = text.rfind("}") + 1
    if json_start >= 0 and json_end > json_start:
        return json.loads(text[json_start:json_end])
    return None


class DocumentIngestionService:
    """Extracts architecture elements from document text and creates proposals."""

    def extract_from_text(self, solution_id, document_text, source_doc_name="uploaded document"):
        """Extract architecture elements from document text via LLM.

        Args:
            solution_id: solution to attach proposals to
            document_text: raw text content of the document
            source_doc_name: filename for provenance

        Returns:
            dict with proposals_created count, summary, and proposal list
        """
        if not document_text or len(document_text.strip()) < 50:
            return {"proposals_created": 0, "errors": ["Document text too short (min 50 chars)"]}

        # Truncate very long documents to stay within LLM context
        # 40KB covers most enterprise specs (~8000 words); anything larger gets chunked
        max_chars = 40000
        was_truncated = len(document_text) > max_chars
        if was_truncated:
            document_text = document_text[:max_chars] + "\n\n[... document truncated — only first 40,000 characters processed ...]"

        prompt = EXTRACTION_PROMPT.format(document_text=document_text)

        try:
            from app.modules.ai_chat.services.llm_service import LLMService
            provider, model = LLMService._get_configured_provider()
            raw_text, _ = LLMService._call_llm(prompt=prompt, model=model, provider=provider)

            parsed = _parse_llm_json(raw_text)
            if not parsed:
                return {"proposals_created": 0, "errors": ["Failed to parse LLM extraction response"]}

            elements = parsed.get("elements", [])
            summary = parsed.get("summary", "")

            # Resolve organization_id from the solution (required by TenantMixin NOT NULL constraint)
            from app.models.solution_models import Solution
            solution = Solution.query.get(solution_id)
            org_id = solution.organization_id if solution else None

            # Create proposal rows
            from app.services.solution_blueprint_service import create_solution_blueprint_proposal

            proposals = []
            for el in elements:
                el_type = el.get("type", "Unknown")
                el_name = el.get("name", "Unnamed")
                el_desc = el.get("description", "")

                # Resolve ACM domain — prefer LLM-returned value, fall back to type mapping.
                # Proposals without acm_domain are invisible in Step 2 (domain cards only show
                # proposals with a matching acm_domain) and can never be promoted.
                raw_domain = (el.get("domain") or "").upper().strip()
                acm_domain = raw_domain if raw_domain in _VALID_ACM_DOMAINS else _TYPE_FALLBACK_DOMAIN.get(el_type)

                # Stamp motivation-layer elements with a structured business_rules entry.
                # _get_solution_business_rules() Source 1 reads acm_properties.business_rules
                # from SolutionBlueprintProposal rows — this ensures document-extracted
                # Constraints/Goals/Drivers reach the code generator immediately, even before
                # any domain promotion creates SolutionRequirement rows.
                acm_props = {}
                if el_type in _MOTIVATION_SEVERITY:
                    acm_props = {
                        "business_rules": [{
                            "name": el_name,
                            "condition": el_desc,
                            "severity": _MOTIVATION_SEVERITY[el_type],
                        }]
                    }

                proposal = create_solution_blueprint_proposal(
                    solution_id=solution_id,
                    organization_id=org_id,
                    archimate_type=el_type,
                    name=el_name,
                    description=el_desc,
                    source="document",
                    source_doc_name=source_doc_name,
                    confidence=min(1.0, max(0.0, el.get("confidence", 0.5))),
                    status="proposed",
                    acm_domain=acm_domain,
                    acm_properties=acm_props if acm_props else None,
                )
                proposals.append(proposal)

            db.session.commit()

            logger.info(
                "Extracted %d proposals from '%s' for solution %d",
                len(proposals), source_doc_name, solution_id,
            )

            warnings = []
            if was_truncated:
                warnings.append(
                    "Document exceeded 40,000 characters — only the first portion was analysed. "
                    "Split the document or reduce its size for complete extraction."
                )

            return {
                "proposals_created": len(proposals),
                "summary": summary,
                "proposals": [
                    {
                        "id": p.id,
                        "archimate_type": p.archimate_type,
                        "name": p.name,
                        "description": p.description,
                        "confidence": p.confidence,
                        "source": p.source,
                        "source_doc_name": p.source_doc_name,
                        "status": p.status,
                    }
                    for p in proposals
                ],
                "errors": [],
                "warnings": warnings,
            }

        except Exception as e:
            logger.error("Document extraction failed: %s", e)
            db.session.rollback()
            return {"proposals_created": 0, "errors": [str(e)]}

    def extract_from_file(self, solution_id, file_storage):
        """Extract from a Flask FileStorage object.

        Handles text extraction from the uploaded file, then delegates to extract_from_text.
        """
        filename = file_storage.filename or "unknown"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        try:
            if ext in ("txt", "md", "csv"):
                text = file_storage.read().decode("utf-8", errors="replace")
            elif ext == "pdf":
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(file_storage)
                    text = "\n".join(page.extract_text() or "" for page in reader.pages[:30])
                except ImportError:
                    return {"proposals_created": 0, "errors": ["pypdf not installed for PDF parsing"]}
            elif ext in ("docx",):
                try:
                    import docx
                    doc = docx.Document(file_storage)
                    text = "\n".join(p.text for p in doc.paragraphs)
                except ImportError:
                    return {"proposals_created": 0, "errors": ["python-docx not installed for DOCX parsing"]}
            else:
                # Try as plain text
                text = file_storage.read().decode("utf-8", errors="replace")

            return self.extract_from_text(solution_id, text, source_doc_name=filename)

        except Exception as e:
            logger.error("File extraction failed for %s: %s", filename, e)
            return {"proposals_created": 0, "errors": [f"Failed to read {filename}: {str(e)}"]}

    def list_proposals(self, solution_id, status=None):
        """List proposals for a solution, optionally filtered by status."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        query = SolutionBlueprintProposal.query.filter_by(solution_id=solution_id)
        if status:
            query = query.filter_by(status=status)
        query = query.order_by(SolutionBlueprintProposal.confidence.desc())

        return [
            {
                "id": p.id,
                "archimate_type": p.archimate_type,
                "name": p.name,
                "description": p.description,
                "confidence": p.confidence,
                "source": p.source,
                "source_doc_name": p.source_doc_name,
                "status": p.status,
                "capability_id": p.capability_id,
            }
            for p in query.all()
        ]

    def accept_proposal(self, proposal_id, solution_id):
        """Accept a single proposal — create ArchiMateElement + run inference chain.

        Returns dict with element_id and chain generation results.
        Null-safe: handles proposals with missing archimate_type, description, or
        no associated ArchiMate elements gracefully.
        """
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        proposal = SolutionBlueprintProposal.query.filter_by(
            id=proposal_id, solution_id=solution_id
        ).first()
        if not proposal:
            return {"error": f"Proposal {proposal_id} not found"}
        if proposal.status == "accepted":
            return {"error": "Already accepted", "element_id": None}

        # Guard: archimate_type is required for element creation
        if not proposal.archimate_type:
            proposal.status = "accepted"
            db.session.commit()
            logger.warning("Proposal %d has no archimate_type — accepted without element creation", proposal_id)
            return {
                "proposal_id": proposal_id,
                "element_id": None,
                "chain": {"elements_created": 0, "relationships_created": 0},
            }

        # Create ArchiMate element via the journey graph
        from app.modules.architecture_assistant.journey_graph import JourneyGraph
        from app.models.solution_archimate_element import SolutionArchiMateElement

        graph = JourneyGraph.resume_for_solution(solution_id)
        node = graph.facade.get_or_create_node(
            element_type=proposal.archimate_type,
            key={"name": proposal.name},
            defaults={"description": proposal.description or ""},
        )

        # Link element to solution blueprint (junction row) — idempotent
        if not SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id, element_id=node.id
        ).first():
            db.session.add(SolutionArchiMateElement(
                solution_id=solution_id,
                element_id=node.id,
                layer_type=proposal.archimate_type,
                element_table="archimate_elements",
                element_name=proposal.name,
                element_role="document_ingestion",
                is_new_element=True,
            ))

        # Run inference chain
        chain_result = {"elements_created": 0, "relationships_created": 0}
        try:
            result = graph.engine.generate_chain(node.id)
            chain_result = {
                "elements_created": len(result.elements_created) if result.elements_created else 0,
                "relationships_created": len(result.relationships_created) if result.relationships_created else 0,
            }
            # Link chain-generated elements to solution too
            if result and result.elements_created:
                from app.models.archimate_core import ArchiMateElement
                for chain_elem_id in result.elements_created:
                    if not SolutionArchiMateElement.query.filter_by(
                        solution_id=solution_id, element_id=chain_elem_id
                    ).first():
                        chain_el = ArchiMateElement.query.get(chain_elem_id)
                        chain_type = (chain_el.type if chain_el else None) or "Unknown"
                        db.session.add(SolutionArchiMateElement(
                            solution_id=solution_id,
                            element_id=chain_elem_id,
                            layer_type=chain_type,
                            element_table="archimate_elements",
                            element_name=chain_el.name if chain_el else None,
                            element_role="ai_derived",
                            is_new_element=True,
                        ))
        except Exception as e:
            logger.warning("Chain generation failed for proposal %d: %s", proposal_id, e)

        # Update proposal status
        proposal.status = "accepted"
        db.session.commit()

        return {
            "proposal_id": proposal_id,
            "element_id": node.id,
            "chain": chain_result,
        }

    def reject_proposal(self, proposal_id, solution_id):
        """Reject a proposal."""
        from app.models.solution_blueprint_proposal import SolutionBlueprintProposal

        proposal = SolutionBlueprintProposal.query.filter_by(
            id=proposal_id, solution_id=solution_id
        ).first()
        if not proposal:
            return {"error": f"Proposal {proposal_id} not found"}

        proposal.status = "rejected"
        db.session.commit()
        return {"proposal_id": proposal_id, "status": "rejected"}

    def batch_accept(self, proposal_ids, solution_id):
        """Batch accept multiple proposals.

        Creates elements, runs inference for each, deduplicates.
        Each proposal is handled independently — one failure does not block others.
        """
        results = []
        errors = []
        total_elements = 0
        total_rels = 0

        for pid in proposal_ids:
            try:
                r = self.accept_proposal(pid, solution_id)
                if "error" not in r:
                    results.append(r)
                    total_elements += r.get("chain", {}).get("elements_created", 0)
                    total_rels += r.get("chain", {}).get("relationships_created", 0)
                else:
                    errors.append({"proposal_id": pid, "error": r["error"]})
            except Exception as e:
                logger.error("batch_accept: proposal %d threw: %s", pid, e, exc_info=True)
                errors.append({"proposal_id": pid, "error": str(e)})

        return {
            "accepted": len(results),
            "errors": errors,
            "elements_created": total_elements,
            "relationships_created": total_rels,
        }
