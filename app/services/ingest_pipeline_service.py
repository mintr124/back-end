"""
ingest_pipeline_service.py  –  v3
===================================
Thay đổi so với v2:
  - Đọc chunk_config_json từ DocumentVersion để lấy ChunkConfig
  - Truyền config vào chunker_service.chunk(parsed, config=cfg)
  - Mọi thứ khác giữ nguyên
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
from app.schemas.document import ChunkingConfig
from app.services.audit_service import audit_service
from app.services.chunker_service import ChunkConfig, chunker_service
from app.services.chroma_service import chroma_service
from app.services.embedding_service import embedding_service
from app.services.entity_extractor import run_pipeline as detect_entities
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

            # ── Đọc chunking config từ version ────────────────────────
            # version.chunk_config_json được lưu lúc upload
            # Nếu None (version cũ) → dùng legacy defaults
            chunking_cfg = ChunkConfig(
                **ChunkingConfig.from_json(version.chunk_config_json).model_dump(
                    exclude={"embed_model"}  # ChunkingConfig không có field này
                ),
                embed_model="sentence-transformers/all-MiniLM-L6-v2",
            )
            logger.info(
                "Ingest job=%s mode=%s max_tokens=%d overlap=%d ocr=%s",
                job_id, chunking_cfg.mode, chunking_cfg.max_tokens,
                chunking_cfg.overlap_tokens, chunking_cfg.ocr,
            )

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
                db, job_id=job.id, step_name="chunk",
                detail_json={"mode": chunking_cfg.mode},
            )
            t0 = time.perf_counter()

            # ← THAY ĐỔI DUY NHẤT: truyền config vào
            chunks = chunker_service.chunk(parsed, config=chunking_cfg)

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
                    "mode":        chunking_cfg.mode,
                    "latency_ms":  int((time.perf_counter() - t0) * 1000),
                },
            )
            version.chunk_status = "completed"
            db.commit()

            # ── entity detection ──────────────────────────────────────
            entity_step = job_service.add_step(
                db, job_id=job.id, step_name="entity_detection",
                detail_json={"chunk_count": len(chunk_models)},
            )
            t0 = time.perf_counter()
            entity_results: list[dict] = []
            for chunk_model in chunk_models:
                try:
                    result = detect_entities(chunk_model.chunk_text, db=db)
                    entity_results.append(result)
                    # Merge entity data into chunk metadata_json
                    existing_meta = chunk_model.metadata_json or {}
                    existing_meta["entities"]     = result["entities"]
                    existing_meta["entity_labels"] = result["labels"]
                    existing_meta["entity_types"] = ",".join(result["entity_types"])
                    chunk_model.metadata_json = existing_meta
                except Exception as exc:
                    logger.warning("Entity detection failed for chunk %s: %s", chunk_model.id, exc)
                    entity_results.append({"entities": [], "labels": {}, "entity_types": []})
            job_service.finish_step(
                db, entity_step,
                detail_json={"latency_ms": int((time.perf_counter() - t0) * 1000)},
            )
            db.commit()

            # ── embed  (BATCH) ────────────────────────────────────────
            embed_step = job_service.add_step(
                db, job_id=job.id, step_name="embed",
                detail_json={"dimensions": embedding_service.dimensions},
            )
            t0 = time.perf_counter()

            embed_texts = [c.get("embed_text") or c["chunk_text"] for c in chunks]
            vectors     = embedding_service.embed_many(embed_texts)

            for chunk_model, chunk_dict, vector, entity_result in zip(
                chunk_models, chunks, vectors, entity_results
            ):
                meta_json    = chunk_dict.get("metadata_json") or {}
                entity_labels = entity_result.get("labels") or {}
                metadata = {
                    "document_id":         doc.id,
                    "document_version_id": version.id,
                    "document_title":      doc.title,
                    "oui_ids":             ",".join(sorted([o.id for o in (doc.ouis or [])])),
                    "document_type":       doc.document_type,
                    "sensitivity":         doc.sensitivity,
                    "data_type":           doc.data_type,
                    "chunk_index":         chunk_model.chunk_index,
                    "page_start":          chunk_model.page_start,
                    "page_end":            chunk_model.page_end,
                    "chunk_hash":          chunk_model.chunk_hash,
                    "section_heading":     meta_json.get("section_heading", ""),
                    "section_index":       meta_json.get("section_index", 0),
                    "position_ratio":      meta_json.get("position_ratio", 0.0),
                    "chunker_mode":        meta_json.get("chunker_mode", "legacy"),
                    # Entity detection results
                    "entity_types":        ",".join(entity_result.get("entity_types") or []),
                    "has_pii":             entity_labels.get("has_pii", False),
                    "has_number":          entity_labels.get("has_number", False),
                    "has_credential":      entity_labels.get("has_credential", False),
                    "has_legal":           entity_labels.get("has_legal", False),
                    "has_strategic":       entity_labels.get("has_strategic", False),
                }
                chroma_service.upsert_chunk(
                    chunk_id=chunk_model.id,
                        document_text=chunk_dict.get("embed_text") or chunk_dict["chunk_text"],  
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
            from app.services.user_service import user_service as _user_service
            if creator:
                from app.services.user_service import user_service as _user_service
                from app.db.session import SessionLocal
                with SessionLocal() as tmp_db:
                    creator_resp = _user_service.build_user_response(tmp_db, creator)
                    if creator_resp.is_corp_member:
                        doc.status = "approved"
                    elif doc.status not in {"approved", "ready"}:
                        doc.status = "review"
            else:
                if doc.status not in {"approved", "ready"}:
                    doc.status = "review"

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
                    "chunker_mode":        chunking_cfg.mode,
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
                    "chunker_mode":    chunking_cfg.mode,
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