"""
chat_service.py  –  v3  (with Guards)
======================================
Thay đổi so với v2:
  - Guard 1 (pre-query)  : Intent classification via guard_service.check_intent()
  - Guard 2 (pre-LLM)    : PII scan & redact trên retrieved chunks
  - Guard 3 (post-LLM)   : PII + business secret scan trên LLM response
  - Stream mode cũng được guard đầy đủ

Vị trí guard trong flow:
  user query
    → [G1] intent check  →  BLOCK: trả về ngay / REWRITE: đổi query / ALLOW: tiếp tục
    → retrieval_service.retrieve()
    → [G2] scan chunks    →  redact PII trong document_text trước khi đưa vào prompt
    → llm_service.generate()  /  generate_stream()
    → [G3] scan response  →  log PII/secret, redact nếu cần trước khi trả về user
"""
from __future__ import annotations
import threading

import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.trace import Trace
from app.models.message_source import MessageSource
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.trace_repository import TraceRepository
from app.repositories.message_source_repository import MessageSourceRepository
from app.services.retrieval_service import retrieval_service
from app.services.answer_service import answer_service
from app.services.audit_service import audit_service
from app.services.llm_service import llm_service
from app.services.memory_service import memory_service
from app.services.guard_service import guard_service        # ← NEW
from app.services.intent_classifier import intent_classifier
from app.services.policy_agent import policy_contract_agent
from app.utils.status_answer import is_no_answer
from app.core.config import settings
from app.repositories.system_setting_repository import system_setting_repository

GUARDS_ENABLED = False
POLICY_ENABLED = True  # Set True to enable policy enforcement
DONE_STATUSES = {"success", "fallback", "no_answer", "llm_error", "blocked"}

logger = logging.getLogger(__name__)

CHATBOT_SYSTEM_PROMPT = """
Bạn là trợ lý AI thông minh, thân thiện như ChatGPT, Gemini, Claude.
Trả lời mọi câu hỏi của người dùng một cách tự nhiên, chính xác và hữu ích.
""".strip()

_BLOCK_MESSAGES = {
    "PROMPT_INJECTION":  "Câu hỏi của bạn có dấu hiệu cố tình can thiệp vào hệ thống. Vui lòng đặt câu hỏi bình thường.",
    "JAILBREAK":         "Câu hỏi của bạn vi phạm chính sách sử dụng. Vui lòng thử lại với câu hỏi khác.",
    "HARMFUL_INTENT":    "Yêu cầu này không thể được xử lý do vi phạm chính sách an toàn.",
    "DATA_EXFILTRATION": "Không thể thực hiện yêu cầu trích xuất dữ liệu hàng loạt.",
    "OFF_TOPIC":         "Câu hỏi này nằm ngoài phạm vi hỗ trợ của hệ thống. Vui lòng hỏi về các tài liệu nội bộ của công ty.",
    "_DEFAULT":          "Không thể xử lý yêu cầu này. Vui lòng thử lại với câu hỏi khác.",
}


def _block_message(class_: str) -> str:
    return _BLOCK_MESSAGES.get(class_, _BLOCK_MESSAGES["_DEFAULT"])


class ChatService:
    def __init__(self):
        self.convs       = ConversationRepository()
        self.msgs        = MessageRepository()
        self.traces      = TraceRepository()
        self.msg_sources = MessageSourceRepository()

    # ------------------------------------------------------------------
    # Helpers 
    # ------------------------------------------------------------------

    def create_conversation(self, db: Session, user, title: str | None) -> Conversation:
        conv = Conversation(user_id=user.id, title=title)
        self.convs.create(db, conv)
        db.commit()
        db.refresh(conv)
        return conv

    def _get_trace_id(self, trace_id: str | None) -> str:
        return trace_id or uuid.uuid4().hex

    def _safe_score(self, item: dict) -> float | None:
        score = item.get("score")
        if score is None:
            score = item.get("relevance")
        try:
            return float(score) if score is not None else None
        except Exception:
            return None

    def _normalize_retrieved(
        self, retrieved: list[dict], limit: int = 5, min_score: float = 0.0
    ) -> list[dict]:
        cleaned: list[dict] = []
        for r in retrieved or []:
            doc_text = (r.get("document_text") or "").strip()
            if not doc_text:
                continue
            score = self._safe_score(r)
            if min_score > 0.0 and score is not None and score < min_score:
                continue
            md = r.get("metadata") or {}
            cleaned.append({
                "chunk_id":       r.get("chunk_id"),
                "document_text":  doc_text,
                "metadata":       md,
                "score":          score,
                "semantic_score": r.get("semantic_score"),
                "keyword_score":  r.get("keyword_score"),
                "distance":       r.get("distance"),
            })
        cleaned.sort(
            key=lambda x: (x["score"] is not None, x["score"] if x["score"] is not None else -1.0),
            reverse=True,
        )
        return cleaned[:limit]

    def _build_sources_from_retrieved(self, retrieved: list[dict]) -> list[dict]:
        sources: list[dict] = []
        for r in retrieved or []:
            md = r.get("metadata", {}) or {}
            sources.append({
                "documentId":    md.get("document_id"),
                "documentTitle": md.get("document_title") or md.get("document_id"),
                "versionId":     md.get("document_version_id"),
                "sectionPath":   md.get("section_heading"),
                "relevance":     r.get("score") if r.get("score") is not None else r.get("relevance"),
                "excerpt":       r.get("document_text") or md.get("excerpt"),
            })
        return sources

    def _load_history(self, db: Session, conversation_id: str, query: str) -> list[dict]:
        try:
            fresh_db = SessionLocal()
            try:
                return memory_service.load_history(fresh_db, conversation_id, query)
            finally:
                fresh_db.close()
        except Exception:
            logger.exception("memory_service.load_history failed, returning empty")
            return []

    def _persist_sources(self, db: Session, assistant_message_id: str, sources: list[dict]) -> None:
        for s in sources or []:
            src = MessageSource(
                message_id=assistant_message_id,
                document_id=s.get("documentId"),
                document_title=s.get("documentTitle"),
                version_id=s.get("versionId"),
                section_path=s.get("sectionPath"),
                relevance=s.get("relevance"),
                excerpt=s.get("excerpt"),
            )
            self.msg_sources.create(db, src)

    def _generalize_chunk(self, chunk: dict) -> dict:
        """Dùng LLM để khái quát hóa nội dung chunk dựa trên contract phân quyền."""
        original = chunk.get("document_text", "")
        if not original:
            return chunk

        contract = chunk.get("_generalize_contract", {})
        max_detail          = (contract.get("max_detail") or "department").lower()
        numeric_granularity = (contract.get("numeric_granularity") or "aggregated").lower()
        allowed_entities    = contract.get("allowed_entities") or []
        print(f"[GENERALIZE] chunk={chunk.get('chunk_id','?')[:8]} max_detail={max_detail} numeric={numeric_granularity} allowed={allowed_entities}")

        # Map entity type code (tiếng Anh) → nhãn tiếng Việt để LLM nhận diện đúng
        _ENTITY_VI = {
            "full_name":        "họ và tên (tên người)",
            "name":             "tên",
            "national_id":      "số CCCD / hộ chiếu / số căn cước",
            "phone":            "số điện thoại",
            "email":            "địa chỉ email",
            "address":          "địa chỉ (thường trú, tạm trú, nơi ở)",
            "dob":              "ngày sinh / năm sinh",
            "employee_id":      "mã nhân viên",
            "job_title":        "chức danh / vị trí công việc",
            "department":       "phòng ban / bộ phận",
            "salary":           "mức lương / thu nhập",
            "social_insurance": "số bảo hiểm xã hội",
            "bank_account":     "tài khoản ngân hàng",
            "company_name":     "tên công ty / tổ chức",
            "tax_id":           "mã số thuế",
            "contract_id":      "số hợp đồng",
        }

        _detail_desc = {
            "company":    "ẩn thông tin chi tiết dưới cấp công ty (phòng ban, cá nhân, ...)",
            "branch":     "ẩn thông tin chi tiết dưới cấp chi nhánh (phòng ban, cá nhân, ...)",
            "department": "ẩn thông tin chi tiết dưới cấp phòng ban (cá nhân cụ thể, ...)",
            "team":       "ẩn thông tin chi tiết dưới cấp nhóm/tổ",
            "project":    "ẩn thông tin cá nhân, giữ thông tin cấp dự án",
            "individual": "có thể giữ thông tin đến cấp cá nhân",
        }
        _numeric_desc = {
            "hidden":     "ẩn hoàn toàn tất cả số liệu (lương, ngân sách, KPI, mã số, ...)",
            "aggregated": "chỉ dùng số liệu tổng hợp/ước lượng (ví dụ: 'khoảng vài triệu')",
            "range_only": "chỉ dùng dạng khoảng (ví dụ: '10–20 triệu')",
            "exact":      "giữ nguyên số liệu chính xác",
        }

        detail_hint  = _detail_desc.get(max_detail, f"ẩn chi tiết vượt cấp '{max_detail}'")
        numeric_hint = _numeric_desc.get(numeric_granularity, f"xử lý số liệu theo mức '{numeric_granularity}'")

        if allowed_entities:
            vi_labels = [_ENTITY_VI.get(e.lower(), e) for e in allowed_entities]
            exempt_str = ", ".join(vi_labels)
            prompt = (
                "Bạn là agent viết lại nội dung theo chính sách phân quyền dữ liệu.\n\n"
                "## QUY TẮC BẮT BUỘC (ưu tiên tuyệt đối)\n"
                f"Các loại thông tin sau PHẢI GIỮ NGUYÊN giá trị thực tế, TUYỆT ĐỐI không được ẩn, "
                f"thay thế hay khái quát hóa:\n  {exempt_str}\n\n"
                "## QUY TẮC KHÁI QUÁT HÓA (áp dụng cho thông tin KHÔNG trong danh sách trên)\n"
                f"- Độ chi tiết: {detail_hint}.\n"
                f"- Số liệu: {numeric_hint}.\n\n"
                "Chỉ trả về đoạn văn đã viết lại, không giải thích.\n\n"
                f"Đoạn văn gốc:\n{original[:1500]}"
            )
        else:
            prompt = (
                "Bạn là agent viết lại nội dung theo chính sách phân quyền dữ liệu.\n\n"
                "## QUY TẮC KHÁI QUÁT HÓA\n"
                f"- Độ chi tiết: {detail_hint}.\n"
                f"- Số liệu: {numeric_hint}.\n"
                "- Khái quát hóa tất cả thông tin cụ thể nhạy cảm: "
                "định danh cá nhân, số liệu tài chính, thông tin liên lạc, mã số, dữ liệu nội bộ.\n\n"
                "Chỉ trả về đoạn văn đã viết lại, không giải thích.\n\n"
                f"Đoạn văn gốc:\n{original[:1500]}"
            )
        try:
            text, _, _ = llm_service.generate(prompt=prompt, max_tokens=1024, temperature=0.0)
            if text and text.strip():
                result = dict(chunk)
                result["document_text"] = text.strip()
                return result
        except Exception as exc:
            logger.warning("Generalize chunk failed chunk=%s: %s", chunk.get("chunk_id"), exc)
        # fallback nếu LLM lỗi
        result = dict(chunk)
        result["document_text"] = "[Nội dung đã được khái quát hóa theo chính sách phân quyền.]"
        return result

    def _anonymize_chunk(self, chunk: dict) -> dict:
        """Thay thế định danh cá nhân bằng alias nhất quán (Nhân viên A, Phòng X, ...)."""
        original = chunk.get("document_text", "")
        if not original:
            return chunk
        contract = chunk.get("_generalize_contract", {})
        allowed_entities = contract.get("allowed_entities") or []
        numeric_granularity = (contract.get("numeric_granularity") or "aggregated").lower()
        _NUMERIC_DESC = {
            "hidden":     "ẩn hoàn toàn tất cả số liệu",
            "aggregated": "chỉ dùng số liệu tổng hợp/ước lượng",
            "range_only": "chỉ dùng dạng khoảng",
            "exact":      "giữ nguyên số liệu chính xác",
        }
        _ENTITY_VI = {
            "full_name": "họ và tên", "national_id": "số CCCD/hộ chiếu",
            "phone": "số điện thoại", "email": "địa chỉ email",
            "address": "địa chỉ", "dob": "ngày sinh", "employee_id": "mã nhân viên",
            "job_title": "chức danh", "department": "phòng ban",
            "social_insurance": "số bảo hiểm xã hội", "bank_account": "tài khoản ngân hàng",
            "company_name": "tên công ty", "tax_id": "mã số thuế",
        }
        numeric_hint = _NUMERIC_DESC.get(numeric_granularity, f"xử lý số liệu theo mức '{numeric_granularity}'")
        if allowed_entities:
            vi_labels = [_ENTITY_VI.get(e.lower(), e) for e in allowed_entities]
            exempt_str = ", ".join(vi_labels)
            prompt = (
                "Bạn là agent ẩn danh hóa dữ liệu theo chính sách phân quyền.\n\n"
                "## QUY TẮC BẮT BUỘC\n"
                f"Các loại thông tin sau PHẢI GIỮ NGUYÊN: {exempt_str}.\n\n"
                "## QUY TẮC ẨN DANH HÓA (cho thông tin KHÔNG trong danh sách trên)\n"
                "- Thay thế tên người bằng alias nhất quán (VD: 'Nhân viên A', 'Nhân viên B').\n"
                "- Thay thế mã số, số tài khoản bằng dạng 'XXX-###' nhất quán.\n"
                f"- Số liệu: {numeric_hint}.\n\n"
                "Chỉ trả về đoạn văn đã viết lại, không giải thích.\n\n"
                f"Đoạn văn gốc:\n{original[:1500]}"
            )
        else:
            prompt = (
                "Bạn là agent ẩn danh hóa dữ liệu.\n"
                "Thay thế TẤT CẢ định danh cá nhân và mã số cụ thể bằng alias nhất quán "
                "(VD: tên người → 'Nhân viên A', mã số → 'ID-001', ...).\n"
                f"Số liệu: {numeric_hint}.\n"
                "Chỉ trả về đoạn văn đã viết lại, không giải thích.\n\n"
                f"Đoạn văn gốc:\n{original[:1500]}"
            )
        try:
            text, _, _ = llm_service.generate(prompt=prompt, max_tokens=1024, temperature=0.0)
            if text and text.strip():
                result = dict(chunk)
                result["document_text"] = text.strip()
                return result
        except Exception as exc:
            logger.warning("Anonymize chunk failed chunk=%s: %s", chunk.get("chunk_id"), exc)
        result = dict(chunk)
        result["document_text"] = "[Nội dung đã được ẩn danh hóa theo chính sách phân quyền.]"
        return result

    def _summarize_chunk(self, chunk: dict) -> dict:
        """Tóm tắt chunk thành 1-2 câu, không tiết lộ giá trị cụ thể."""
        original = chunk.get("document_text", "")
        if not original:
            return chunk
        prompt = (
            "Bạn là agent tóm tắt nội dung theo chính sách phân quyền dữ liệu.\n"
            "Hãy tóm tắt đoạn văn sau thành 1-2 câu ngắn gọn, "
            "chỉ nêu chủ đề và loại thông tin có trong đoạn. "
            "TUYỆT ĐỐI không tiết lộ giá trị cụ thể (tên người, số liệu, mã số, địa chỉ, ...).\n"
            "Ví dụ đầu ra: 'Thông tin nhân viên bao gồm thông tin cá nhân và liên hệ.'\n"
            "Chỉ trả về câu tóm tắt, không giải thích.\n\n"
            f"Đoạn văn:\n{original[:1500]}"
        )
        try:
            text, _, _ = llm_service.generate(prompt=prompt, max_tokens=256, temperature=0.0)
            if text and text.strip():
                result = dict(chunk)
                result["document_text"] = text.strip()
                return result
        except Exception as exc:
            logger.warning("Summarize chunk failed chunk=%s: %s", chunk.get("chunk_id"), exc)
        result = dict(chunk)
        result["document_text"] = "[Nội dung đã được tóm tắt theo chính sách phân quyền.]"
        return result

    def _apply_transforms(self, chunks: list[dict]) -> list[dict]:
        """Áp dụng các transformation đã được đánh dấu bởi policy (GENERALIZE/ANONYMIZE/SUMMARIZE)."""
        result = []
        for c in chunks:
            if c.get("_needs_generalize"):
                result.append(self._generalize_chunk(c))
            elif c.get("_needs_anonymize"):
                result.append(self._anonymize_chunk(c))
            elif c.get("_needs_summarize"):
                result.append(self._summarize_chunk(c))
            else:
                result.append(c)
        return result

    def _apply_policy_contracts(
        self,
        db: Session,
        chunks: list[dict],
        user,
        raw_query: str,
        intent_class: str,
    ) -> tuple[list[dict], list[dict]]:
        """
        Run policy-contract agent per chunk.
        Returns (approved_chunks, contracts).
        DENY       → chunk removed
        REDACT     → chunk text replaced with policy notice
        GENERALIZE → chunk marked for LLM generalization
        ALLOW / ALLOW_WITH_WATERMARK → pass through
        """
        approved: list[dict] = []
        contracts: list[dict] = []

        declared_sensitivity = 2

        user_positions: list[dict] = []
        for up in (getattr(user, "oui_positions", None) or []):
            pos = getattr(up, "position", None)
            if pos and up.oui_id:
                user_positions.append({
                    "oui_id":    up.oui_id,
                    "clearance": pos.clearance,
                })

        for chunk in chunks:
            md = chunk.get("metadata") or {}
            chunk_id = chunk.get("chunk_id") or "unknown"
            chunk_text = chunk.get("document_text") or ""
            chunk_sensitivity = int(md.get("sensitivity") or declared_sensitivity)

            try:
                contract = policy_contract_agent.generate_contract(
                    chunk_id=chunk_id,
                    chunk_text=chunk_text,
                    chunk_metadata=md,
                    declared_sensitivity=chunk_sensitivity,
                    user_role=getattr(user, "role", "Employee"),
                    user_level=getattr(user, "clearance_level", 1),
                    user_department=getattr(user, "department", ""),
                    user_id=user.id,
                    intent_class=intent_class,
                    raw_query=raw_query,
                    user_positions=user_positions,
                    db=db,
                )
                contracts.append(contract)

                decision = contract.get("decision", "ALLOW")
                _ctr = {
                    "max_detail":          contract.get("max_detail", "generalize"),
                    "numeric_granularity": contract.get("numeric_granularity", "aggregated"),
                    "allowed_entities":    contract.get("allowed_entities", []),
                }
                if decision == "DENY":
                    logger.info("Policy DENY chunk=%s", chunk_id)
                    continue
                elif decision == "REDACT":
                    chunk = dict(chunk)
                    chunk["document_text"] = "[Nội dung này đã được ẩn theo chính sách phân quyền.]"
                    logger.info("Policy REDACT chunk=%s", chunk_id)
                elif decision == "ANONYMIZE":
                    chunk = dict(chunk)
                    chunk["_needs_anonymize"] = True
                    chunk["_generalize_contract"] = _ctr
                    logger.info("Policy ANONYMIZE chunk=%s", chunk_id)
                elif decision == "GENERALIZE":
                    chunk = dict(chunk)
                    chunk["_needs_generalize"] = True
                    chunk["_generalize_contract"] = _ctr
                    logger.info("Policy GENERALIZE chunk=%s", chunk_id)
                elif decision == "SUMMARIZE":
                    chunk = dict(chunk)
                    chunk["_needs_summarize"] = True
                    logger.info("Policy SUMMARIZE chunk=%s", chunk_id)
                # ALLOW / ALLOW_WITH_WATERMARK → pass through

                approved.append(chunk)
            except Exception as exc:
                logger.warning("Policy contract failed chunk=%s: %s", chunk_id, exc)
                approved.append(chunk)

        return approved, contracts

    def _update_summary_background(self, conversation_id: str) -> None:
        try:
            with SessionLocal() as db:
                memory_service.update_summary(db, conversation_id)
                db.commit()
        except Exception:
            logger.exception("Background summary update failed conv_id=%s", conversation_id)
            
    def _extract_cited_indices(self, answer_text: str) -> set[int]:
        """Lấy các số N từ các marker [N] xuất hiện trong câu trả lời của LLM."""
        if not answer_text:
            return set()
        found = re.findall(r"\[(\d+)\]", answer_text)
        return {int(n) for n in found}

    @staticmethod
    def _make_source_entry(r: dict) -> dict:
        md = r.get("metadata", {}) or {}
        return {
            "documentId":    md.get("document_id"),
            "documentTitle": md.get("document_title") or md.get("document_id"),
            "versionId":     md.get("document_version_id"),
            "sectionPath":   md.get("section_heading"),
            "relevance":     r.get("score") if r.get("score") is not None else r.get("relevance"),
            "excerpt":       r.get("document_text") or md.get("excerpt"),
            "docRestricted": r.get("doc_restricted", False),
        }

    def _normalize_citations(
        self,
        answer_text: str | None,
        retrieved: list[dict],
    ) -> tuple[str | None, list[dict]]:
        """Normalize [N] citation markers in LLM answer to be sequential (1-based)
        and return the matching sources list in that order.

        Rules:
          - No [N] found → answer unchanged; sources = restricted chunks only (or [])
          - [N] found, e.g. [1],[3],[5] → renumber to [1],[2],[3] in answer text;
            sources[0]=chunk_1, sources[1]=chunk_3, sources[2]=chunk_5
        """
        if not answer_text:
            return answer_text, []

        cited = self._extract_cited_indices(answer_text)

        if not cited:
            # LLM cited nothing — surface only restricted chunks so the user
            # knows to request access for potentially relevant content.
            seen: set[str] = set()
            restricted: list[dict] = []
            for r in (retrieved or []):
                if not r.get("doc_restricted", False):
                    continue
                md = r.get("metadata", {}) or {}
                doc_id = md.get("document_id")
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                entry = self._make_source_entry(r)
                entry["docRestricted"] = True
                restricted.append(entry)
            return answer_text, restricted

        # Sort cited indices → stable sequential mapping: old→new
        sorted_cited = sorted(cited)
        old_to_new = {old: new for new, old in enumerate(sorted_cited, start=1)}

        # Renumber every [N] in the answer text
        def _replace(m: re.Match) -> str:
            new_n = old_to_new.get(int(m.group(1)))
            return f"[{new_n}]" if new_n is not None else m.group(0)

        normalized_text = re.sub(r"\[(\d+)\]", _replace, answer_text)

        # Build sources in sorted citation order (exactly one entry per cited chunk)
        retrieved_list = list(retrieved or [])
        sources: list[dict] = []
        for old_idx in sorted_cited:
            if 1 <= old_idx <= len(retrieved_list):
                sources.append(self._make_source_entry(retrieved_list[old_idx - 1]))

        return normalized_text, sources

    def _build_sources_from_retrieved(self, retrieved: list[dict], answer_text: str | None = None) -> list[dict]:
        """Legacy wrapper — prefer _normalize_citations for LLM answers."""
        _, sources = self._normalize_citations(answer_text, retrieved)
        return sources

    # ------------------------------------------------------------------
    # Guard helpers
    # ------------------------------------------------------------------

    def _make_blocked_assistant_message(
        self,
        db: Session,
        conversation_id: str,
        user_msg: Message,
        block_text: str,
        trace: Trace,
        tid: str,
        user,
    ) -> tuple[Message, Message, list]:
        """Tạo assistant message trả lời khi bị Guard 1 BLOCK."""
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=block_text,
            status="blocked",
            trace_id=tid,
            parent_message_id=user_msg.id,
        )
        self.msgs.create(db, assistant_msg)
        db.flush()
        db.refresh(assistant_msg)

        trace.assistant_output_summary = block_text
        trace.status = "blocked"

        audit_service.log_action(
            db, trace_id=tid, user_id=user.id,
            action="chat.message.blocked", resource_type="conversation",
            resource_id=conversation_id, decision="deny",
            input_json={"message": user_msg.content},
            output_json={"reason": block_text},
        )

        db.commit()
        db.refresh(assistant_msg)
        return user_msg, assistant_msg, []

    # ------------------------------------------------------------------
    # post_message  (non-streaming)
    # ------------------------------------------------------------------

    def post_message(
        self,
        db: Session,
        user,
        conversation_id: str,
        content: str,
        client_message_id: str | None,
        trace_id: str,
    ):
        tid = self._get_trace_id(trace_id)

        if client_message_id:
            existing = self.msgs.find_by_client_id(db, conversation_id, client_message_id)
            if existing:
                return existing, None, None

        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=content,
            client_message_id=client_message_id,
            trace_id=tid,
        )
        self.msgs.create(db, user_msg)
        db.flush()
        db.refresh(user_msg)

        tr = Trace(
            trace_id=tid,
            conversation_id=conversation_id,
            message_id=user_msg.id,
            user_id=user.id,
            user_input=content[:2000],
            status="running",
        )
        self.traces.create(db, tr)
        db.flush()

        # ── [GUARD 1] Intent classification ───────────────────────────
        if GUARDS_ENABLED:
            intent = guard_service.check_intent(content)
            if intent.blocked:
                block_text = _block_message(intent.class_)
                return self._make_blocked_assistant_message(
                    db, conversation_id, user_msg, block_text, tr, tid, user,
                )
            effective_query = intent.rewrite if intent.should_rewrite else content
        else:
            effective_query = content

        # ── Retrieval ─────────────────────────────────────────────────
        _top_k   = int(system_setting_repository.get(db, "rag.top_k") or 5)
        _min_score = float(system_setting_repository.get(db, "rag.similarity_threshold") or 0.0)
        retrieved_raw = retrieval_service.retrieve(query=effective_query, user=user, top_k=_top_k, db=db)
        retrieved     = self._normalize_retrieved(retrieved_raw, limit=_top_k, min_score=_min_score)
        # Fallback: nếu threshold lọc hết, chỉ lấy 1 chunk tốt nhất (best-effort)
        if not retrieved and _min_score > 0.0:
            retrieved = self._normalize_retrieved(retrieved_raw, limit=1, min_score=0.0)
            if retrieved:
                logger.info("Retrieval fallback best-effort: top1 score=%.3f", retrieved[0].get("score") or 0)

        # ── [GUARD 2] PII scan trên retrieved chunks ───────────────────
        logger.info("USER ROLE: %s USER ID: %s", getattr(user, "role", None), getattr(user, "id", None))
        if GUARDS_ENABLED:
            retrieved = guard_service.scan_chunks(retrieved, user=user)

        # ── [POLICY] Policy-contract enforcement ──────────────────────
        policy_contracts: list[dict] = []
        has_watermark = False
        if POLICY_ENABLED and retrieved:
            query_intent = intent_classifier.classify(effective_query)
            retrieved, policy_contracts = self._apply_policy_contracts(
                db, retrieved, user, effective_query, query_intent
            )
            has_watermark = any(c.get("decision") == "ALLOW_WITH_WATERMARK" for c in policy_contracts)
            print(f"[POLICY] intent={query_intent} approved={len(retrieved)}/{len(retrieved_raw)} watermark={has_watermark}")
            for _c in policy_contracts:
                rules = [r.get("rule_code") for r in _c.get("applied_rules", [])]
                domains = [d.get("code") for d in _c.get("domains", [])]
                print(f"[POLICY] chunk={_c.get('chunk_id','?')[:8]} domains={domains} decision={_c.get('decision')} rules={rules}")
            retrieved = self._apply_transforms(retrieved)

        # ── Phát hiện REDACT/DENY để chặn memory leak ─────────────────
        _POLICY_NOTICE = "[Nội dung này đã được ẩn theo chính sách phân quyền.]"
        has_restricted = bool(retrieved and any(
            c.get("document_text") == _POLICY_NOTICE for c in retrieved
        )) or bool(
            policy_contracts and any(c.get("decision") in ("DENY", "REDACT") for c in policy_contracts)
        )
        all_restricted = bool(retrieved and all(
            c.get("document_text") == _POLICY_NOTICE for c in retrieved
        ))

        history = self._load_history(db, conversation_id, effective_query)
        # Khi có REDACT/DENY: không truyền history để tránh memory leak từ session cũ
        safe_history: list = [] if has_restricted else history

        answer_text: str | None = None
        llm_raw: Any = None
        prompt: str | None = None
        sources: list[dict] = []
        assistant_status = "fallback"
        llm_text: str | None = None

        # Nếu TẤT CẢ chunk đều bị REDACT → trả thông báo trực tiếp, không dùng LLM
        if all_restricted:
            answer_text = "Thông tin này bị hạn chế theo chính sách phân quyền của hệ thống và không thể hiển thị."
            sources = []
            assistant_status = "success"

        elif llm_service.is_configured():
            try:
                _has_transform = bool(policy_contracts and any(
                    c.get("decision") in ("ANONYMIZE", "GENERALIZE", "SUMMARIZE")
                    for c in policy_contracts
                ))
                _extra_instructions = (
                    "Dữ liệu trong context đã được ẩn danh hóa theo chính sách phân quyền "
                    "(tên người thay bằng alias như 'Nhân viên A', mã số thay bằng ID đại diện, "
                    "số liệu thể hiện dạng khoảng hoặc tổng hợp). "
                    "Hãy trả lời dựa trên thông tin ẩn danh đó. "
                    "Không xác định danh tính cụ thể. "
                    "Không nói 'không có trong tài liệu' nếu thông tin liên quan (dù đã ẩn danh) thực sự có trong context."
                ) if _has_transform else (
                    "Nếu câu hỏi là dạng hỏi trực tiếp về một người, "
                    "hãy trả lời đúng thông tin đó, ngắn gọn, không kèm dữ liệu thừa."
                )
                prompt = llm_service.build_prompt(
                    question=effective_query,
                    contexts=retrieved,
                    chat_history=safe_history,
                    extra_instructions=_extra_instructions,
                )
                print(f"[LLM PROMPT]\n{'='*60}\n{prompt}\n{'='*60}")
                llm_text, llm_raw, _ = llm_service.generate(
                    prompt=prompt, max_tokens=512, temperature=0.0,
                )
                if llm_text and llm_text.strip():
                    answer_text = llm_text.strip()
                assistant_status = "no_answer" if is_no_answer(answer_text) else "success"
                if assistant_status == "success":
                    answer_text, sources = self._normalize_citations(answer_text, retrieved)
            except Exception:
                logger.exception("LLM generation failed trace_id=%s", tid)
                answer_text = None
                assistant_status = "llm_error"

        if not answer_text:
            answer_text, sources = answer_service.generate(user_input=effective_query, retrieved=retrieved)
            assistant_status = "fallback"
            if not sources:
                answer_text, sources = self._normalize_citations(answer_text, retrieved)

        # ── [GUARD 3] PII + Secret scan trên LLM response ─────────────
        if GUARDS_ENABLED and answer_text:
            post_scan = guard_service.scan_response(answer_text, user=user)

            if post_scan.judge and post_scan.judge.should_block:
                logger.warning("Guard3b BLOCK: reason=%s trace_id=%s", post_scan.judge.reason, tid)
                answer_text = "Xin lỗi, nội dung này không thể hiển thị do vi phạm chính sách bảo mật."

            elif post_scan.has_pii:
                logger.warning("Guard3 REDACT: entities=%s trace_id=%s",
                            [e.entity_type for e in post_scan.entities], tid)
                answer_text = post_scan.redacted_text

            if post_scan.has_pii or post_scan.has_secret:
                logger.warning("Guard3 POST-LLM: has_pii=%s has_secret=%s trace_id=%s",
                            post_scan.has_pii, post_scan.has_secret, tid)

        # ── Watermark notice ──────────────────────────────────────────
        if has_watermark and answer_text:
            answer_text = (
                answer_text
                + "\n\n---\nNội dung này được truy cập theo điều kiện kiểm soát phân quyền. Hoạt động truy vấn đã được ghi nhận."
            )

        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer_text,
            status=assistant_status,
            trace_id=tid,
            parent_message_id=user_msg.id,
        )
        self.msgs.create(db, assistant_msg)
        db.flush()
        db.refresh(assistant_msg)

        self._persist_sources(db, assistant_msg.id, sources)

        tr.assistant_output_summary = answer_text[:2000] if answer_text else None
        tr.retrieved_sources = retrieved_raw
        tr.llm_prompt  = prompt
        tr.llm_response = {
            "text":        llm_text,
            "response_id": getattr(llm_raw, "id", None),
            "model":       getattr(llm_raw, "model", None),
        }
        tr.status = "completed"

        audit_service.log_action(
            db, trace_id=tid, user_id=user.id,
            action="chat.message", resource_type="conversation",
            resource_id=conversation_id, decision="allow",
            input_json={"message": content, "effective_query": effective_query},
            output_json={"assistant_message": answer_text, "sources": sources},
        )

        db.commit()
        db.refresh(assistant_msg)

        threading.Thread(
            target=self._update_summary_background,
            args=(conversation_id,),
            daemon=True,
        ).start()

        return user_msg, assistant_msg, sources

    # ------------------------------------------------------------------
    # list_messages_flat  (giữ nguyên)
    # ------------------------------------------------------------------

    def list_messages_flat(self, db: Session, conversation_id: str, limit: int = 1000) -> list[dict]:
        from app.models.message import Message as MsgModel

        user_msgs = (
            db.query(MsgModel)
            .filter(
                MsgModel.conversation_id == conversation_id,
                MsgModel.role == "user",
            )
            .order_by(MsgModel.created_at.asc())
            .limit(limit)
            .all()
        )

        assistant_msgs_raw = (
            db.query(MsgModel)
            .filter(
                MsgModel.conversation_id == conversation_id,
                MsgModel.role == "assistant",
                MsgModel.status.in_(DONE_STATUSES),
                MsgModel.content.isnot(None),
                MsgModel.content != "",
            )
            .order_by(MsgModel.created_at.asc())
            .all()
        )

        # Dùng parent_message_id làm khóa nối, lấy cái mới nhất nếu retry
        assistant_by_parent: dict[str, MsgModel] = {}
        for a in assistant_msgs_raw:
            if a.parent_message_id:
                assistant_by_parent[a.parent_message_id] = a

        out = []
        for user_msg in user_msgs:
            assistant_msg = assistant_by_parent.get(user_msg.id)
            srcs = self.msg_sources.list_by_message(db, assistant_msg.id) if assistant_msg else []

            out.append({
                "conversationId": conversation_id,
                "messageId": user_msg.id,
                "content": user_msg.content,
                "createdAt": user_msg.created_at,
                "traceId": (assistant_msg.trace_id if assistant_msg else None) or user_msg.trace_id,
                "assistantMessage": {
                    "id": assistant_msg.id,
                    "content": assistant_msg.content,
                    "status": assistant_msg.status,
                    "createdAt": assistant_msg.created_at,
                } if assistant_msg else None,
                "sources": [
                    {
                        "documentId": s.document_id,
                        "documentTitle": s.document_title,
                        "versionId": s.version_id,
                        "sectionPath": s.section_path,
                        "relevance": s.relevance,
                        "excerpt": s.excerpt,
                        "surroundingContext": s.surrounding_context,
                    }
                    for s in (srcs or [])
                ],
            })

        return out

    # ------------------------------------------------------------------
    # post_message_stream  (với Guards)
    # ------------------------------------------------------------------

    def post_message_stream(
        self,
        db: Session,
        user,
        conversation_id: str,
        content: str,
        client_message_id: str | None,
        trace_id: str,
        oui_ids: list[str] | None = None,
        mode: str = "rag",
        chat_source="rag",
        file_content: str | None = None,
        file_name: str | None = None,
    ):
        tid = self._get_trace_id(trace_id)
        llm_extra_context = ""
        if file_content:
            label = file_name or "file"
            llm_extra_context = f"[Tài liệu đính kèm: {label}]\n\n{file_content[:40000]}\n\n---\n"

        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=content,
            client_message_id=client_message_id,
            trace_id=tid,
        )
        tr = Trace(
            trace_id=tid,
            conversation_id=conversation_id,
            message_id=user_msg.id,
            user_id=user.id,
            user_input=content[:2000],
            status="running",
        )
        self.traces.create(db, tr)
        self.msgs.create(db, user_msg)
        db.flush()
        db.refresh(user_msg)

        # ── [GUARD 1] Intent classification (stream) ───────────────────
        if GUARDS_ENABLED:
            intent = guard_service.check_intent(content)

            if intent.blocked:
                # Tạo assistant message blocked ngay, không cần tạo streaming message trước
                block_text = _block_message(intent.class_)

                assistant_msg = Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=block_text,
                    status="blocked",
                    trace_id=tid,
                    parent_message_id=user_msg.id,
                )
                self.msgs.create(db, assistant_msg)
                db.flush()
                db.refresh(assistant_msg)

                tr.assistant_output_summary = block_text
                tr.status = "blocked"

                audit_service.log_action(
                    db, trace_id=tid, user_id=user.id,
                    action="chat.message.blocked", resource_type="conversation",
                    resource_id=conversation_id, decision="deny",
                    input_json={"message": content},
                    output_json={"reason": block_text},
                )
                db.commit()

                # Emit stream events rồi done
                yield {"type": "message_start", "messageId": assistant_msg.id, "userMessageId": user_msg.id}
                yield {"type": "token", "text": block_text}
                yield {"type": "done", "content": block_text, "sources": [], "messageId": assistant_msg.id, "blocked": True, "blockClass": intent.class_}
                return

            # Dùng rewrite nếu có
            is_corp = getattr(user, "is_corp_member", False)
            max_clearance = getattr(user, "max_clearance", 1)
            is_privileged = is_corp and max_clearance >= 4
            effective_query = content
            if intent.should_rewrite and not is_privileged:
                effective_query = intent.rewrite
            
        else:
            effective_query = content

        # Tạo assistant message placeholder cho stream
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content="",
            status="streaming",
            trace_id=tid,
            parent_message_id=user_msg.id,
        )
        self.msgs.create(db, assistant_msg)
        db.flush()
        db.refresh(assistant_msg)
        db.commit()

        yield {"type": "message_start", "messageId": assistant_msg.id, "userMessageId": user_msg.id}

        full_text = ""
        sources   = []
        retrieved_raw: list[dict] = []

        # ── CHATBOT MODE ──────────────────────────────────────────────
        if mode == "chatbot":
            if llm_service.is_configured():
                try:
                    history = self._load_history(db, conversation_id, effective_query)
                    history_messages = [h for h in history if h["role"] in ("user", "assistant")]
                    summary_items    = [h for h in history if h["role"] == "system"]
                    summary_note     = summary_items[0]["content"] if summary_items else ""

                    prompt = ""
                    if summary_note:
                        prompt += f"{summary_note}\n\n"
                    if history_messages:
                        history_text = "\n".join(
                            f"{h['role']}: {h['content']}" for h in history_messages
                        )
                        prompt += f"LỊCH SỬ HỘI THOẠI\n{history_text}\n\n"
                    prompt += f"Người dùng: {(llm_extra_context + effective_query).strip()}"

                    for token in llm_service.generate_stream(
                        prompt=prompt,
                        max_tokens=1024,
                        temperature=0.7,
                        system=CHATBOT_SYSTEM_PROMPT,
                    ):
                        full_text += token
                        yield {"type": "token", "text": token}

                    full_text = re.sub(r"<think>.*?</think>", "", full_text, flags=re.DOTALL).strip()

                except Exception:
                    logger.exception("Chatbot stream failed")
                    full_text = "Xin lỗi, đã có lỗi xảy ra. Vui lòng thử lại."
                    yield {"type": "token", "text": full_text}
            else:
                full_text = "LLM chưa được cấu hình."
                yield {"type": "token", "text": full_text}

        # ── RAG MODE ──────────────────────────────────────────────────
        else:
            _top_k     = int(system_setting_repository.get(db, "rag.top_k") or 5)
            _min_score = float(system_setting_repository.get(db, "rag.similarity_threshold") or 0.0)
            try:
                retrieved_raw = retrieval_service.retrieve(
                    query=effective_query,
                    user=user,
                    top_k=_top_k,
                    oui_ids=oui_ids,
                    chat_mode=chat_source,
                    db=db,
                )
            except Exception:
                logger.exception("Retrieval failed trace_id=%s chat_source=%s", tid, chat_source)
                retrieved_raw = []

            logger.info("STREAM RETRIEVED COUNT: %d", len(retrieved_raw))

            retrieved = self._normalize_retrieved(retrieved_raw, limit=_top_k, min_score=_min_score)
            # Fallback: nếu threshold lọc hết, chỉ lấy 1 chunk tốt nhất (best-effort)
            if not retrieved and _min_score > 0.0:
                retrieved = self._normalize_retrieved(retrieved_raw, limit=1, min_score=0.0)
                if retrieved:
                    logger.info("Retrieval fallback best-effort: top1 score=%.3f", retrieved[0].get("score") or 0)

            # ── [GUARD 2] PII scan chunks ──────────────────────────────
            logger.info("Guard2 stream: user_role=%s user_id=%s",
            getattr(user, "role", None), getattr(user, "id", None))
            if GUARDS_ENABLED:
                retrieved = guard_service.scan_chunks(retrieved, user=user)

            # ── [POLICY] Policy-contract enforcement ──────────────────
            stream_has_watermark = False
            if POLICY_ENABLED and retrieved:
                query_intent = intent_classifier.classify(effective_query)
                retrieved, stream_policy_contracts = self._apply_policy_contracts(
                    db, retrieved, user, effective_query, query_intent
                )
                stream_has_watermark = any(c.get("decision") == "ALLOW_WITH_WATERMARK" for c in stream_policy_contracts)
                print(f"[POLICY STREAM] intent={query_intent} approved={len(retrieved)} watermark={stream_has_watermark}")
                for _c in stream_policy_contracts:
                    rules = [r.get("rule_code") for r in _c.get("applied_rules", [])]
                    domains = [d.get("code") for d in _c.get("domains", [])]
                    print(f"[POLICY STREAM] chunk={_c.get('chunk_id','?')[:8]} domains={domains} decision={_c.get('decision')} rules={rules}")
                retrieved = self._apply_transforms(retrieved)

            # ── Phát hiện REDACT/DENY để chặn memory leak (stream) ────
            _POLICY_NOTICE = "[Nội dung này đã được ẩn theo chính sách phân quyền.]"
            _spc = locals().get("stream_policy_contracts") or []
            stream_has_restricted = bool(retrieved and any(
                c.get("document_text") == _POLICY_NOTICE for c in retrieved
            )) or bool(any(c.get("decision") in ("DENY", "REDACT") for c in _spc))
            stream_all_restricted = bool(retrieved and all(
                c.get("document_text") == _POLICY_NOTICE for c in retrieved
            ))

            history = self._load_history(db, conversation_id, effective_query)
            safe_history = [] if stream_has_restricted else history

            if not retrieved:
                full_text = "Xin lỗi, không tìm thấy thông tin liên quan. Vui lòng thử diễn đạt lại câu hỏi."
                for token in full_text:
                    yield {"type": "token", "text": token}

            elif stream_all_restricted:
                full_text = "Thông tin này bị hạn chế theo chính sách phân quyền của hệ thống và không thể hiển thị."
                for token in full_text:
                    yield {"type": "token", "text": token}

            elif llm_service.is_configured():
                try:
                    _stream_has_transform = bool(_spc and any(
                        c.get("decision") in ("ANONYMIZE", "GENERALIZE", "SUMMARIZE")
                        for c in _spc
                    ))
                    _stream_extra = (
                        "Dữ liệu trong context đã được ẩn danh hóa theo chính sách phân quyền "
                        "(tên người thay bằng alias như 'Nhân viên A', mã số thay bằng ID đại diện, "
                        "số liệu thể hiện dạng khoảng hoặc tổng hợp). "
                        "Hãy trả lời dựa trên thông tin ẩn danh đó. "
                        "Không xác định danh tính cụ thể. "
                        "Không nói 'không có trong tài liệu' nếu thông tin liên quan (dù đã ẩn danh) thực sự có trong context."
                    ) if _stream_has_transform else None
                    prompt = llm_service.build_prompt(
                        question=llm_extra_context + effective_query,
                        contexts=retrieved,
                        chat_history=safe_history,
                        extra_instructions=_stream_extra,
                    )
                    print(f"[LLM STREAM PROMPT]\n{'='*60}\n{prompt}\n{'='*60}")
                    logger.info("LLM stream prompt trace_id=%s", tid)

                    for token in llm_service.generate_stream(prompt=prompt, max_tokens=2048):
                        full_text += token
                        yield {"type": "token", "text": token}

                    full_text = re.sub(r"<think>.*?</think>", "", full_text, flags=re.DOTALL).strip()
                    logger.info("LLM stream result len=%d", len(full_text))

                except Exception:
                    logger.exception("LLM stream failed")
                    full_text = ""

            if not full_text:
                full_text, _ = answer_service.generate(user_input=effective_query, retrieved=retrieved)

            # ── Watermark notice (stream) ──────────────────────────────
            if stream_has_watermark and full_text:
                watermark_text = "\n\n---\n⚠️ Nội dung này được truy cập theo điều kiện kiểm soát phân quyền. Hoạt động truy vấn đã được ghi nhận."
                yield {"type": "token", "text": watermark_text}
                full_text += watermark_text

            full_text, sources = self._normalize_citations(full_text, retrieved)

        # ── [GUARD 3] PII + Secret scan trên response (stream) ─────────
        if GUARDS_ENABLED and full_text:
            post_scan = guard_service.scan_response(full_text, user=user)

            if post_scan.judge and post_scan.judge.should_block:
                # Judge quyết định BLOCK
                logger.warning(
                    "Guard3b BLOCK stream: reason=%s trace_id=%s",
                    post_scan.judge.reason, tid,
                )
                full_text = "Xin lỗi, nội dung này không thể hiển thị do vi phạm chính sách bảo mật."

            elif post_scan.has_pii:
                # Judge REDACT hoặc không chạy judge nhưng 3a detect PII
                logger.warning(
                    "Guard3 REDACT stream: entities=%s trace_id=%s",
                    [e.entity_type for e in post_scan.entities], tid,
                )
                full_text = post_scan.redacted_text

            if post_scan.has_pii or post_scan.has_secret:
                logger.warning(
                    "Guard3 stream POST-LLM: has_pii=%s entities=%s has_secret=%s secrets=%s trace_id=%s",
                    post_scan.has_pii,
                    [e.entity_type for e in post_scan.entities],
                    post_scan.has_secret,
                    post_scan.secret_keywords_found[:5],
                    tid,
                )

        # ── Persist ───────────────────────────────────────────────────
        assistant_msg.content = full_text
        assistant_msg.status  = "success"
        self._persist_sources(db, assistant_msg.id, sources)
        tr.assistant_output_summary = full_text[:2000] if full_text else None
        tr.retrieved_sources = [] if mode == "chatbot" else retrieved_raw
        tr.llm_response      = {"text": full_text}
        tr.status            = "completed"
        db.commit()

        threading.Thread(
            target=self._update_summary_background,
            args=(conversation_id,),
            daemon=True,
        ).start()

        yield {"type": "done", "content": full_text, "sources": sources, "messageId": assistant_msg.id}


chat_service = ChatService()