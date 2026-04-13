"""
ingest_pipeline_service.py  –  v2
===================================
Key improvements vs v1:
  1. Batch embedding  – all chunks embedded in one (or a few) API calls
     instead of N sequential calls  → 10-30x faster ingest
  2. Uses chunk["embed_text"]  (heading-prefixed text) for embedding
     but stores chunk["chunk_text"]  (clean text) in DB and Chroma documents
  3. Passes section_heading and position_ratio to Chroma metadata
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chunk_embedding import ChunkEmbedding
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_version import DocumentVersion
from app.services.audit_service import audit_service
from app.services.chunker_service import chunker_service
from app.services.chroma_service import chroma_service
from app.services.embedding_service import embedding_service
from app.services.job_service import job_service
from app.services.parser_service import parser_service
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)


class IngestPipelineService:

    def run(self, db: Session, job_id: str):
        job = job_service.get_job(db, job_id)
        if not job:
            return
        if job.status == "succeeded":
            return

        start_total = time.perf_counter()
        job.status   = "running"
        job.progress = 1
        db.commit()

        doc     = db.get(Document,        job.document_id)
        version = db.get(DocumentVersion, job.document_version_id)
        if not doc or not version:
            job.status        = "failed"
            job.error_message = "Document/version missing"
            db.commit()
            return

        try:
            doc.status            = "processing"
            version.ingest_status = "running"
            version.parse_status  = "running"
            version.chunk_status  = "pending"
            version.embed_status  = "pending"
            db.commit()

            # ── clean re-run ──────────────────────────────────────────
            db.query(DocumentChunk).filter(
                DocumentChunk.document_version_id == version.id
            ).delete(synchronize_session=False)
            db.commit()

            # ── download ──────────────────────────────────────────────
            raw_step = job_service.add_step(
                db, job_id=job.id, step_name="download_raw",
                detail_json={
                    "bucket":     version.source_object.bucket,
                    "object_key": version.source_object.object_key,
                },
            )
            t0        = time.perf_counter()
            raw_bytes = storage_service.download(
                version.source_object.bucket, version.source_object.object_key
            )
            job_service.finish_step(
                db, raw_step,
                detail_json={
                    "size_bytes": len(raw_bytes),
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                },
            )
            db.commit()

            # ── parse ─────────────────────────────────────────────────
            parse_step = job_service.add_step(
                db, job_id=job.id, step_name="parse",
                detail_json={"filename": version.file_name},
            )
            t0     = time.perf_counter()
            parsed = parser_service.parse(raw_bytes, version.file_name, version.mime_type)
            job_service.finish_step(
                db, parse_step,
                detail_json={
                    "pages":      len(parsed.pages),
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                },
            )
            version.parse_status = "completed"
            db.commit()

            normalized_key = (
                f"documents/{doc.id}/versions/{version.version_no}/normalized.txt"
            )
            normalized_obj = storage_service.upload_processed_text(
                db,
                text=parsed.full_text,
                object_key=normalized_key,
                original_filename=f"{Path(version.file_name).stem}.normalized.txt",
            )
            version.normalized_object_id = normalized_obj.id
            db.commit()

            # ── chunk ─────────────────────────────────────────────────
            chunk_step = job_service.add_step(
                db, job_id=job.id, step_name="chunk", detail_json={}
            )
            t0     = time.perf_counter()
            chunks = chunker_service.chunk(parsed)   # list[dict]

            chunk_models: list[DocumentChunk] = []
            for c in chunks:
                chunk = DocumentChunk(
                    document_version_id=version.id,
                    chunk_index=c["chunk_index"],
                    chunk_text=c["chunk_text"],
                    page_start=c["page_start"],
                    page_end=c["page_end"],
                    token_count=c["token_count"],
                    metadata_json=c["metadata_json"],
                    chunk_hash=c["chunk_hash"],
                )
                db.add(chunk)
                db.flush()
                chunk_models.append(chunk)

            job_service.finish_step(
                db, chunk_step,
                detail_json={
                    "chunk_count": len(chunk_models),
                    "latency_ms":  int((time.perf_counter() - t0) * 1000),
                },
            )
            version.chunk_status = "completed"
            db.commit()

            # ── embed  (BATCH) ────────────────────────────────────────
            embed_step = job_service.add_step(
                db, job_id=job.id, step_name="embed",
                detail_json={"dimensions": embedding_service.dimensions},
            )
            t0 = time.perf_counter()

            # Use embed_text (heading-prefixed) for richer embeddings;
            # store chunk_text (clean) in Chroma documents field.
            embed_texts = [c.get("embed_text") or c["chunk_text"] for c in chunks]
            vectors     = embedding_service.embed_many(embed_texts)

            for chunk_model, chunk_dict, vector in zip(chunk_models, chunks, vectors):
                meta_json = chunk_dict.get("metadata_json") or {}
                metadata  = {
                    "document_id":         doc.id,
                    "document_version_id": version.id,
                    "department_id":       doc.department_id,
                    "document_title":      doc.title,
                    "project_id":          doc.project_id,
                    "document_type":       doc.document_type,
                    "sensitivity_level":   doc.sensitivity_level,
                    "data_type":           doc.data_type,
                    "allowed_roles":       ",".join(doc.allowed_roles or []),
                    "chunk_index":         chunk_model.chunk_index,
                    "page_start":          chunk_model.page_start,
                    "page_end":            chunk_model.page_end,
                    "chunk_hash":          chunk_model.chunk_hash,
                    # New fields from v2 chunker
                    "section_heading":     meta_json.get("section_heading", ""),
                    "section_index":       meta_json.get("section_index", 0),
                    "position_ratio":      meta_json.get("position_ratio", 0.0),
                }
                chroma_service.upsert_chunk(
                    chunk_id=chunk_model.id,
                    document_text=chunk_dict["chunk_text"],   # clean text
                    embedding=vector,
                    metadata=metadata,
                )
                db.add(
                    ChunkEmbedding(
                        chunk_id=chunk_model.id,
                        vector_db="chroma",
                        collection_name=settings.chroma_collection,
                        vector_id=chunk_model.id,
                        embedding_model=embedding_service.model_name,
                        dimensions=embedding_service.dimensions,
                        embedding_status="completed",
                    )
                )

            job_service.finish_step(
                db, embed_step,
                detail_json={
                    "embedded_chunks": len(chunk_models),
                    "latency_ms":      int((time.perf_counter() - t0) * 1000),
                },
            )
            version.embed_status  = "completed"
            version.ingest_status = "succeeded"

            creator = job.created_by
            logger.info(
                "DEBUG creator_id=%s creator=%s role=%s doc_status_before=%s",
                job.created_by_user_id, creator,
                creator.role if creator else None, doc.status,
            )
            if creator and creator.role in {"admin_auditor", "director"}:
                doc.status = "ready"
            elif doc.status not in {"ready", "approved"}:
                doc.status = "review"
            logger.info("DEBUG doc_status_after=%s", doc.status)

            doc.current_version_id = version.id

            audit_service.emit_event(
                db,
                event_type="document.embedded",
                aggregate_type="document_version",
                aggregate_id=version.id,
                payload_json={
                    "document_id":         doc.id,
                    "document_version_id": version.id,
                    "chunk_count":         len(chunk_models),
                    "collection":          settings.chroma_collection,
                },
            )
            audit_service.log_action(
                db,
                trace_id=job.trace_id,
                user_id=job.created_by_user_id,
                action="document.ingest.completed",
                resource_type="document_version",
                resource_id=version.id,
                decision="allow",
                job_id=job.id,
                output_json={
                    "chunk_count":     len(chunk_models),
                    "embedding_model": embedding_service.model_name,
                },
                latency_ms=int((time.perf_counter() - start_total) * 1000),
            )

            job.status   = "succeeded"
            job.progress = 100
            db.commit()

        except Exception as exc:
            db.rollback()
            job     = job_service.get_job(db, job.id)
            doc     = db.get(Document,        job.document_id)         if job else None
            version = db.get(DocumentVersion, job.document_version_id) if job else None

            if job:
                job.status        = "failed"
                job.error_message = str(exc)
                job.progress      = min(job.progress or 0, 99)
            if doc:
                doc.status = "failed"
            if version:
                version.ingest_status = "failed"
                version.error_message = str(exc)
                version.parse_status  = version.parse_status or "failed"
                version.chunk_status  = "failed"
                version.embed_status  = "failed"

            job_service.add_step(
                db,
                job_id=job.id if job else job_id,
                step_name="failed",
                detail_json={"error": str(exc)},
            )
            audit_service.log_action(
                db,
                trace_id=job.trace_id         if job     else "unknown",
                user_id=job.created_by_user_id if job     else None,
                action="document.ingest.failed",
                resource_type="document_version",
                resource_id=version.id         if version else None,
                decision="deny",
                job_id=job.id                  if job     else None,
                output_json={"error": str(exc)},
            )
            db.commit()
            raise


ingest_pipeline_service = IngestPipelineService()