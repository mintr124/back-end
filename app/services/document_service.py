from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

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


class DocumentService:
    def __init__(self):
        self.docs = DocumentRepository()
        self.versions = VersionRepository()
        self.chunks = ChunkRepository()
        self.departments = DepartmentRepository()
        self.projects = ProjectRepository()

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
        dept = self._resolve_department(db, payload.department_id)
        ok, reason = permission_service.can_create_document(user, dept.id)
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

        proj = self._resolve_project(db, payload.project_id, dept.id) if payload.project_id else None

        doc = Document(
            title=payload.title,
            description=payload.description,
            department_id=dept.id,
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
        return doc

    def update_document(self, db: Session, user: User, doc_id: str, payload: DocumentUpdateRequest, trace_id: str) -> Document:
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

        if (payload.department_id or payload.project_id) and user.role not in {"director", "admin_auditor"}:
            raise HTTPException(status_code=403, detail="Only director/admin_auditor can move department/project")

        if payload.department_id:
            dept = self._resolve_department(db, payload.department_id)
            doc.department_id = dept.id
            if payload.project_id:
                proj = self._resolve_project(db, payload.project_id, dept.id)
                doc.project_id = proj.id
            else:
                doc.project_id = None

        if payload.project_id and not payload.department_id:
            proj = self._resolve_project(db, payload.project_id, doc.department_id)
            doc.project_id = proj.id

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
        # Only update current_version_id here ? do not modify other Document fields

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


document_service = DocumentService()
