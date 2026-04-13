from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.fga.adapter import fga_adapter
from app.repositories.user_repository import UserRepository
from app.core.config import settings
from app.models.department import Department
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_version import DocumentVersion
from app.models.policy_snapshot import DocumentPolicySnapshot
from app.models.project import Project
from app.models.storage_object import StorageObject
from app.models.user import User
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.department_repository import DepartmentRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.project_repository import ProjectRepository
from app.repositories.version_repository import VersionRepository
from app.schemas.document import DocumentCreateRequest, DocumentUpdateRequest
from app.services.audit_service import audit_service
from app.services.job_service import job_service
from app.services.permission_service import permission_service
from app.services.storage_service import storage_service
from app.models.chunk_embedding import ChunkEmbedding
from app.models.policy_snapshot import DocumentPolicySnapshot
from app.models.job import Job
from app.models.job_step import JobStep
from app.services.chroma_service import chroma_service

import logging
logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self):
        self.docs = DocumentRepository()
        self.versions = VersionRepository()
        self.chunks = ChunkRepository()
        self.departments = DepartmentRepository()
        self.projects = ProjectRepository()
        self.users = UserRepository()
        
    def _sync_fga(self, db: Session, doc: Document):
        all_users  = self.users.list_active(db)
        dept_users = self.users.list_by_dept(db, doc.department_id) if doc.department_id else []
        proj_users = self.users.list_by_project(db, doc.project_id) if doc.project_id else []

        dept_id_for_managers = doc.department_id
        if doc.project_id and not dept_id_for_managers:
            proj = self.projects.get_by_id(db, doc.project_id)
            dept_id_for_managers = proj.department_id if proj else None

        dept_managers = []
        if dept_id_for_managers:
            dept_managers = [
                u for u in self.users.list_by_dept(db, dept_id_for_managers)
                if u.role == "department_manager"
            ]

        fga_adapter.sync_document_tuples(
            doc=doc,
            all_users=all_users,
            dept_users=dept_users,
            project_users=proj_users,
            dept_managers=dept_managers,
        )

    def _resolve_department(self, db: Session, id: str) -> Department:
        dept = self.departments.get_by_id(db, id)
        if not dept:
            raise HTTPException(status_code=404, detail=f"Department not found: {id}")
        return dept

    def _resolve_project(self, db: Session, project_id: str, department_id: str) -> Project:
        proj = self.projects.get_by_id(db, project_id)
        if proj and proj.department_id != department_id:
            raise HTTPException(status_code=400, detail="Project does not belong to selected department")
        if not proj:
            proj = self.projects.create(db, project_id, department_id)
        return proj

    def _policy_contract(self, doc: Document) -> dict:
        if doc.sensitivity_level in {"restricted", "top_secret"}:
            max_detail = "department"
            numeric_granularity = "aggregated"
        elif doc.sensitivity_level == "confidential":
            max_detail = "project"
            numeric_granularity = "aggregated"
        else:
            max_detail = "full"
            numeric_granularity = "full"

        return {
            "max_detail": max_detail,
            "numeric_granularity": numeric_granularity,
            "allowed_entities": ["department", "project"],
            "violation_action": "mask",
            "allowed_roles": doc.allowed_roles,
            "policy_version": settings.default_policy_version,
        } #TODO: change to policy-as-code, now is hard code

    def list_documents(self, db: Session, user: User) -> list[Document]:
        docs = self.docs.list_all(db)
        visible = []
        for doc in docs:
            ok, _ = permission_service.can_view_document(user, doc)
            if ok:
                visible.append(doc)
        return visible

    def get_document(self, db: Session, user: User, doc_id: str) -> Document:
        doc = self.docs.get_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        ok, reason = permission_service.can_view_document(user, doc)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        return doc

    def create_document(self, db: Session, user: User, payload: DocumentCreateRequest, trace_id: str) -> Document:
        # Department là optional — None = doc chung công ty
        dept = self._resolve_department(db, payload.department_id) if payload.department_id else None

        ok, reason = permission_service.can_create_document(user, dept.id if dept else None)
        if not ok:
            audit_service.log_action(
                db,
                trace_id=trace_id,
                user_id=user.id,
                action="document.create",
                resource_type="document",
                resource_id=None,
                decision="deny",
                input_json=payload.model_dump(mode="json"),
            )
            db.commit()
            raise HTTPException(status_code=403, detail=reason)

        proj = self._resolve_project(db, payload.project_id, dept.id) if payload.project_id and dept else None

        doc = Document(
            title=payload.title,
            description=payload.description,
            department_id=dept.id if dept else None,
            project_id=proj.id if proj else None,
            owner_user_id=user.id,
            document_type=payload.document_type,
            sensitivity_level=payload.sensitivity_level,
            data_type=payload.data_type,
            allowed_roles=payload.allowed_roles,
            status="draft",
        )
        self.docs.create(db, doc)

        audit_service.log_action(
            db,
            trace_id=trace_id,
            user_id=user.id,
            action="document.create",
            resource_type="document",
            resource_id=doc.id,
            decision="allow",
            input_json=payload.model_dump(mode="json"),
        )
        db.commit()
        db.refresh(doc)
        old_tuples = fga_adapter.get_document_tuples(doc.id)
        fga_adapter.delete_document_tuples(doc.id, old_tuples)
        self._sync_fga(db, doc)
        return doc

    def update_document(
        self,
        db: Session,
        user: User,
        doc_id: str,
        payload: DocumentUpdateRequest,
        trace_id: str,
    ) -> Document:
        doc = self.docs.get_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        ok, reason = permission_service.can_update_document(user, doc)
        if not ok:
            audit_service.log_action(
                db,
                trace_id=trace_id,
                user_id=user.id,
                action="document.update",
                resource_type="document",
                resource_id=doc.id,
                decision="deny",
                input_json=payload.model_dump(mode="json"),
            )
            db.commit()
            raise HTTPException(status_code=403, detail=reason)

        # Chỉ director/admin_auditor được move dept/project
        fields_set = payload.model_fields_set
        if ("department_id" in fields_set or "project_id" in fields_set) and user.role not in {"director", "admin_auditor"}:
            raise HTTPException(status_code=403, detail="Only director/admin_auditor can move department/project")

        # ── department_id ─────────────────────────────────────────────────────────
        if "department_id" in fields_set:
            if payload.department_id:
                dept = self._resolve_department(db, payload.department_id)
                doc.department_id = dept.id
                # Nếu đổi dept mà không truyền project_id → xóa project cũ
                if "project_id" not in fields_set:
                    doc.project_id = None
            else:
                # Truyền null → company-wide doc
                doc.department_id = None
                doc.project_id = None

        # ── project_id ────────────────────────────────────────────────────────────
        if "project_id" in fields_set:
            if payload.project_id:
                proj = self._resolve_project(db, payload.project_id, doc.department_id)
                doc.project_id = proj.id
            else:
                doc.project_id = None

        # ── Các field khác ────────────────────────────────────────────────────────
        if payload.title is not None:
            doc.title = payload.title
        if payload.description is not None:
            doc.description = payload.description
        if payload.document_type is not None:
            doc.document_type = payload.document_type
        if payload.sensitivity_level is not None:
            doc.sensitivity_level = payload.sensitivity_level
        if payload.data_type is not None:
            doc.data_type = payload.data_type
        if payload.allowed_roles is not None:
            doc.allowed_roles = payload.allowed_roles

        self.docs.save(db, doc)

        audit_service.log_action(
            db,
            trace_id=trace_id,
            user_id=user.id,
            action="document.update",
            resource_type="document",
            resource_id=doc.id,
            decision="allow",
            input_json=payload.model_dump(mode="json"),
        )
        db.commit()
        db.refresh(doc)

        # ── Re-sync FGA ───────────────────────────────────────────────────────────
        old_tuples = fga_adapter.get_document_tuples(doc.id)
        fga_adapter.delete_document_tuples(doc.id, old_tuples)
        self._sync_fga(db, doc)

        # ── Cập nhật Chroma metadata nếu dept/project thay đổi ───────────────────
        if "department_id" in fields_set or "project_id" in fields_set:
            all_versions = self.versions.list_by_document(db, doc.id)
            version_ids = [v.id for v in all_versions]
            chunk_ids = [
                c.id for c in db.query(DocumentChunk).filter(
                    DocumentChunk.document_version_id.in_(version_ids)
                ).all()
            ] if version_ids else []

            if chunk_ids:
                chroma_service.update_document_metadata(chunk_ids, {
                    "department_id": doc.department_id or "",
                    "project_id": doc.project_id or "",
                })

        return doc

    def get_versions(self, db: Session, user: User, doc_id: str) -> list[DocumentVersion]:
        doc = self.get_document(db, user, doc_id)
        return self.versions.list_by_document(db, doc.id)

    def create_version(
        self,
        db: Session,
        user: User,
        doc_id: str,
        *,
        raw_bytes: bytes,
        filename: str,
        content_type: str,
        trace_id: str,
    ) -> tuple[Document, DocumentVersion, object, bool]:
        doc = self.docs.get_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        ok, reason = permission_service.can_update_document(user, doc)
        if not ok:
            audit_service.log_action(
                db,
                trace_id=trace_id,
                user_id=user.id,
                action="document.version_upload",
                resource_type="document",
                resource_id=doc.id,
                decision="deny",
                input_json={"filename": filename, "content_type": content_type},
            )
            db.commit()
            raise HTTPException(status_code=403, detail=reason)

        last_no = self.docs.get_max_version_no(db, doc.id)
        version_no = last_no + 1

        checksum = storage_service.checksum(raw_bytes)
        object_key = f"documents/{doc.id}/versions/{version_no}/{Path(filename).name}"

        storage_obj = storage_service.upload_raw(
            db,
            data=raw_bytes,
            object_key=object_key,
            original_filename=Path(filename).name,
            content_type=content_type,
        )

        version = DocumentVersion(
            document_id=doc.id,
            version_no=version_no,
            file_name=Path(filename).name,
            mime_type=content_type,
            checksum=checksum,
            source_object_id=storage_obj.id,
            ingest_status="queued",
            parse_status="pending",
            chunk_status="pending",
            embed_status="pending",
            rule_version=settings.default_policy_version,
        )
        self.versions.create(db, version)

        doc.current_version_id = version.id
        doc.status = "uploaded"

        snapshot = DocumentPolicySnapshot(
            document_version_id=version.id,
            policy_version=settings.default_policy_version,
            contract_json=self._policy_contract(doc),
        )
        db.add(snapshot)

        job, created = job_service.create_or_get_ingest_job(
            db,
            trace_id=trace_id,
            document_id=doc.id,
            version_id=version.id,
            created_by_user_id=user.id,
        )

        audit_service.log_action(
            db,
            trace_id=trace_id,
            user_id=user.id,
            action="document.version_upload",
            resource_type="document_version",
            resource_id=version.id,
            decision="allow",
            input_json={"filename": filename, "content_type": content_type},
            output_json={"job_id": job.id, "version_no": version_no},
        )

        db.commit()
        db.refresh(doc)
        db.refresh(version)
        db.refresh(job)
        return doc, version, job, created

    def start_ingest(
        self,
        db: Session,
        user: User,
        doc_id: str,
        *,
        version_id: str | None,
        force_new: bool,
        trace_id: str,
    ):
        doc = self.docs.get_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        ok, reason = permission_service.can_update_document(user, doc)
        if not ok:
            audit_service.log_action(
                db,
                trace_id=trace_id,
                user_id=user.id,
                action="document.ingest_start",
                resource_type="document",
                resource_id=doc.id,
                decision="deny",
                input_json={"version_id": version_id, "force_new": force_new},
            )
            db.commit()
            raise HTTPException(status_code=403, detail=reason)

        if version_id:
            version = self.versions.get_by_id(db, version_id)
            if not version or version.document_id != doc.id:
                raise HTTPException(status_code=404, detail="Version not found for document")
        else:
            version = doc.current_version
            if not version:
                raise HTTPException(status_code=400, detail="Document has no current version")

        job, created = job_service.create_or_get_ingest_job(
            db,
            trace_id=trace_id,
            document_id=doc.id,
            version_id=version.id,
            created_by_user_id=user.id,
            force_new=force_new,
        )

        audit_service.log_action(
            db,
            trace_id=trace_id,
            user_id=user.id,
            action="document.ingest_start",
            resource_type="job",
            resource_id=job.id,
            decision="allow",
            input_json={"version_id": version.id, "force_new": force_new},
            output_json={"created": created},
        )
        db.commit()
        db.refresh(job)
        return job
    
    def delete_document(self, db: Session, user: User, doc_id: str, trace_id: str) -> None:
        doc = self.docs.get_by_id(db, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        # 1. Xóa FGA tuples
        old_tuples = fga_adapter.get_document_tuples(doc_id)
        fga_adapter.delete_document_tuples(doc_id, old_tuples)

        # 2. Lấy tất cả version_id của doc
        version_ids = [v.id for v in db.query(DocumentVersion.id).filter(
            DocumentVersion.document_id == doc_id
        ).all()]

        if version_ids:
            # 3. Lấy tất cả chunk_id
            chunk_ids = [c.id for c in db.query(DocumentChunk.id).filter(
                DocumentChunk.document_version_id.in_(version_ids)
            ).all()]

            if chunk_ids:
                # 4. Xóa ChunkEmbedding
                db.query(ChunkEmbedding).filter(
                    ChunkEmbedding.chunk_id.in_(chunk_ids)
                ).delete(synchronize_session=False)

                # 5. Xóa DocumentChunk
                db.query(DocumentChunk).filter(
                    DocumentChunk.document_version_id.in_(version_ids)
                ).delete(synchronize_session=False)

            # 6. Xóa DocumentPolicySnapshot
            db.query(DocumentPolicySnapshot).filter(
                DocumentPolicySnapshot.document_version_id.in_(version_ids)
            ).delete(synchronize_session=False)

            # 7. Xóa JobStep và Job
            job_ids = [j.id for j in db.query(Job.id).filter(
                Job.document_id == doc_id
            ).all()]
            if job_ids:
                from app.models.job_step import JobStep
                db.query(JobStep).filter(
                    JobStep.job_id.in_(job_ids)
                ).delete(synchronize_session=False)
                db.query(Job).filter(
                    Job.id.in_(job_ids)
                ).delete(synchronize_session=False)

            # 8. Null current_version_id trước khi xóa versions
            doc.current_version_id = None
            db.flush()

            # 9. Xóa DocumentVersion
            db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc_id
            ).delete(synchronize_session=False)
            
            if chunk_ids:
                try:
                    from app.services.chroma_service import chroma_service
                    chroma_service.delete_chunks(chunk_ids)
                except Exception as e:
                    logger.warning("Failed to delete chunks from Chroma for doc %s: %s", doc_id, e)

        # 10. Xóa Document
        db.delete(doc)

        audit_service.log_action(
            db,
            trace_id=trace_id,
            user_id=user.id,
            action="document.delete",
            resource_type="document",
            resource_id=doc_id,
            decision="allow",
            input_json={"document_id": doc_id},
        )
        db.commit()


document_service = DocumentService()
