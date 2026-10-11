"""Cross-tenant isolation for document_chunk_embeddings.

Before TenantMixin, this table (the actual text content of every tenant's
uploaded documents, chunked for RAG retrieval) had no tenant boundary at all.
Its only read path (retrieve_document_chunks, document_processing_service.py)
has no live caller today, but a document_id-less call there does a cosine-
similarity search across every organisation's chunks with no filter -- fixed
at the source rather than trusting a future caller to remember to scope it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_chunk(db_session, org_id, document_id, text):
    from app.models.vector_embeddings import DocumentChunkEmbedding

    row = DocumentChunkEmbedding(
        document_id=document_id, chunk_index=0, chunk_text=text,
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_document_chunk_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """org A must not see org B's uploaded-document chunk content."""
    from app.models.vector_embeddings import DocumentChunkEmbedding

    org_a, org_b = make_org("a"), make_org("b")
    _make_chunk(db_session, org_a.id, 1, "org A's confidential contract text")
    b_chunk = _make_chunk(db_session, org_b.id, 2, "org B's confidential contract text")

    with tenant_ctx(org_a.id):
        visible_ids = {c.id for c in DocumentChunkEmbedding.query.all()}

    assert b_chunk.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's uploaded document chunk content."
    )


def test_chunk_and_embed_inherits_current_org(db_session, make_org, tenant_ctx):
    """chunk_and_embed() (called synchronously from the upload route, in
    request context) must stamp organization_id automatically, matching how
    it's actually invoked in document_routes.py -- no explicit org passed."""
    from app.models.ai_chat_document import AIChatDocumentUpload
    from app.modules.ai_chat.services.document_processing_service import (
        DocumentProcessingService,
    )

    from app.models.user import User

    org = make_org("a")
    with tenant_ctx(org.id):
        user = User(
            email="uploader@example.com", first_name="U", last_name="Loader",
            organization_id=org.id,
        )
        db_session.add(user)
        db_session.flush()

        upload = AIChatDocumentUpload(
            file_name="contract.pdf", original_filename="contract.pdf",
            uploaded_by_id=user.id,
        )
        db_session.add(upload)
        db_session.flush()

        svc = DocumentProcessingService()
        svc.chunk_and_embed(upload.id, "Some extracted document text to chunk.")

        from app.models.vector_embeddings import DocumentChunkEmbedding

        chunks = DocumentChunkEmbedding.query.filter_by(document_id=upload.id).all()
        assert chunks, "chunk_and_embed produced no rows"
        assert all(c.organization_id == org.id for c in chunks), (
            "a chunk created by chunk_and_embed() in request context must "
            "inherit g.current_org_id automatically via TenantMixin"
        )
