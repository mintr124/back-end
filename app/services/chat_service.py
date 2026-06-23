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
        DENY   → chunk removed
        REDACT → chunk text replaced with policy notice
        ALLOW / ALLOW_WITH_WATERMARK → pass through
        """
        approved: list[dict] = []
        contracts: list[dict] = []

        declared_sensitivity = 2  # default Internal; overridden per-chunk from metadata

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
                    db=db,
                )
                contracts.append(contract)

                decision = contract.get("decision", "ALLOW")
                if decision == "DENY":
                    logger.info("Policy DENY chunk=%s domain=%s", chunk_id, contract.get("domains"))
                    continue
                elif decision == "REDACT":
                    chunk = dict(chunk)
                    chunk["document_text"] = "[Nội dung này đã được ẩn theo chính sách phân quyền.]"
                    logger.info("Policy REDACT chunk=%s", chunk_id)
                # ALLOW / ALLOW_WITH_WATERMARK → pass through

                approved.append(chunk)
            except Exception as exc:
                logger.warning("Policy contract failed chunk=%s: %s", chunk_id, exc)
                approved.append(chunk)  # fail-open: pass through on error

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

    def _build_sources_from_retrieved(self, retrieved: list[dict], answer_text: str | None = None) -> list[dict]:
        cited = self._extract_cited_indices(answer_text) if answer_text else None
        no_citations = cited is not None and not cited

        sources: list[dict] = []
        seen_docs: set[str] = set()
        for r in retrieved or []:
            md = r.get("metadata", {}) or {}
            doc_id = md.get("document_id")

            if no_citations:
                # LLM answered but cited nothing (e.g. all chunks redacted):
                # deduplicate by document — one entry per document is enough
                if doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
            # When LLM cited specific sources, keep ALL chunks in original order
            # so that [n] in the answer text maps correctly to source n in the panel.
            # Do NOT filter by cited indices — filtering shifts indices and breaks mapping.

            sources.append({
                "documentId":    doc_id,
                "documentTitle": md.get("document_title") or doc_id,
                "versionId":     md.get("document_version_id"),
                "sectionPath":   md.get("section_heading"),
                "relevance":     r.get("score") if r.get("score") is not None else r.get("relevance"),
                "excerpt":       r.get("document_text") or md.get("excerpt"),
            })
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
        retrieved_raw = retrieval_service.retrieve(query=effective_query, user=user, top_k=_top_k)
        retrieved     = self._normalize_retrieved(retrieved_raw, limit=_top_k, min_score=_min_score)

        # ── [GUARD 2] PII scan trên retrieved chunks ───────────────────
        logger.info("USER ROLE: %s USER ID: %s", getattr(user, "role", None), getattr(user, "id", None))
        if GUARDS_ENABLED:
            retrieved = guard_service.scan_chunks(retrieved, user=user)

        # ── [POLICY] Policy-contract enforcement ──────────────────────
        policy_contracts: list[dict] = []
        if POLICY_ENABLED and retrieved:
            query_intent = intent_classifier.classify(effective_query)
            retrieved, policy_contracts = self._apply_policy_contracts(
                db, retrieved, user, effective_query, query_intent
            )
            logger.info("Policy: intent=%s approved=%d/%d", query_intent, len(retrieved), len(retrieved_raw))

        history = self._load_history(db, conversation_id, effective_query)

        answer_text: str | None = None
        llm_raw: Any = None
        prompt: str | None = None
        sources: list[dict] = []
        assistant_status = "fallback"
        llm_text: str | None = None

        if llm_service.is_configured():
            try:
                prompt = llm_service.build_prompt(
                    question=effective_query,
                    contexts=retrieved,
                    chat_history=history,
                    extra_instructions=(
                        "Nếu câu hỏi là dạng hỏi trực tiếp về một người, "
                        "hãy trả lời đúng thông tin đó, ngắn gọn, không kèm dữ liệu thừa."
                    ),
                )
                llm_text, llm_raw, _ = llm_service.generate(
                    prompt=prompt, max_tokens=512, temperature=0.0,
                )
                if llm_text and llm_text.strip():
                    answer_text = llm_text.strip()
                assistant_status = "no_answer" if is_no_answer(answer_text) else "success"
                sources = self._build_sources_from_retrieved(retrieved, answer_text) if assistant_status == "success" else []
            except Exception:
                logger.exception("LLM generation failed trace_id=%s", tid)
                answer_text = None
                assistant_status = "llm_error"

        if not answer_text:
            answer_text, sources = answer_service.generate(user_input=effective_query, retrieved=retrieved)
            assistant_status = "fallback"
            if not sources:
                sources = self._build_sources_from_retrieved(retrieved, answer_text)

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
                )
            except Exception:
                logger.exception("Retrieval failed trace_id=%s chat_source=%s", tid, chat_source)
                retrieved_raw = []

            logger.info("STREAM RETRIEVED COUNT: %d", len(retrieved_raw))

            retrieved = self._normalize_retrieved(retrieved_raw, limit=_top_k, min_score=_min_score)

            # ── [GUARD 2] PII scan chunks ──────────────────────────────
            logger.info("Guard2 stream: user_role=%s user_id=%s",
            getattr(user, "role", None), getattr(user, "id", None))
            if GUARDS_ENABLED:
                retrieved = guard_service.scan_chunks(retrieved, user=user)

            # ── [POLICY] Policy-contract enforcement ──────────────────
            if POLICY_ENABLED and retrieved:
                query_intent = intent_classifier.classify(effective_query)
                retrieved, _ = self._apply_policy_contracts(
                    db, retrieved, user, effective_query, query_intent
                )
                logger.info("Policy stream: intent=%s approved=%d", query_intent, len(retrieved))

            history = self._load_history(db, conversation_id, effective_query)

            if not retrieved:
                full_text = "Xin lỗi, không tìm thấy thông tin liên quan. Vui lòng thử diễn đạt lại câu hỏi."
                for token in full_text:
                    yield {"type": "token", "text": token}

            elif llm_service.is_configured():
                try:
                    prompt = llm_service.build_prompt(
                        question=llm_extra_context + effective_query,
                        contexts=retrieved,
                        chat_history=history,
                    )
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

            sources = self._build_sources_from_retrieved(retrieved, full_text)

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