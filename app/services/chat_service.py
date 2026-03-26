from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

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
from app.utils.status_answer import is_no_answer

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self):
        self.convs = ConversationRepository()
        self.msgs = MessageRepository()
        self.traces = TraceRepository()
        self.msg_sources = MessageSourceRepository()

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

    def _normalize_retrieved(self, retrieved: list[dict], limit: int = 5) -> list[dict]:
        cleaned: list[dict] = []
        for r in retrieved or []:
            doc_text = (r.get("document_text") or "").strip()
            if not doc_text:
                continue

            md = r.get("metadata") or {}
            cleaned.append(
                {
                    "chunk_id": r.get("chunk_id"),
                    "document_text": doc_text,
                    "metadata": md,
                    "score": self._safe_score(r),
                    "semantic_score": r.get("semantic_score"),
                    "keyword_score": r.get("keyword_score"),
                    "distance": r.get("distance"),
                }
            )

        cleaned.sort(
            key=lambda x: (
                x["score"] is not None,
                x["score"] if x["score"] is not None else -1.0,
            ),
            reverse=True,
        )
        return cleaned[:limit]

    def _build_sources_from_retrieved(self, retrieved: list[dict]) -> list[dict]:
        sources: list[dict] = []
        for r in retrieved or []:
            md = r.get("metadata", {}) or {}

            sources.append(
                {
                    "documentId": md.get("document_id"),
                    "documentTitle": md.get("document_title") or md.get("document_id"),
                    "versionId": md.get("document_version_id"),
                    "sectionPath": md.get("section_path"), #TODO: check all sectionPath, generative when embedding
                    "relevance": r.get("score") if r.get("score") is not None else r.get("relevance"),
                    "excerpt": r.get("document_text") or md.get("excerpt"),
                }
            )

        return sources

    def _load_recent_history(self, db: Session, conversation_id: str, limit: int = 6) -> list[dict]:
        """
        Hãy chỉ lấy lượt chat gần nhất để tăng tính mạch lạc của câu trả lời.
        Chỉ lấy nội dung của user/assistant, không nhồi toàn bộ lịch sử.
        """
        msgs = self.msgs.list_by_conversation(db, conversation_id, limit=limit)
        history: list[dict] = []

        for m in msgs[-limit:]:
            if m.role not in {"user", "assistant"}:
                continue
            content = (m.content or "").strip()
            if not content:
                continue
            history.append(
                {
                    "role": m.role,
                    "content": content,
                }
            )

        return history

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

        # idempotency
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
            user_input=content,
            status="running",
        )
        self.traces.create(db, tr)
        db.flush()

        # retrieval
        retrieved_raw = retrieval_service.retrieve(query=content, user=user, top_k=3)
        retrieved = self._normalize_retrieved(retrieved_raw, limit=5)

        history = self._load_recent_history(db, conversation_id, limit=6)

        answer_text: str | None = None
        llm_raw: Any = None
        prompt: str | None = None
        sources: list[dict] = []

        if llm_service.is_configured():
            try:
                prompt = llm_service.build_prompt(
                    question=content,
                    contexts=retrieved,
                    chat_history=history,
                    extra_instructions=(
                        "Nếu câu hỏi là dạng hỏi trực tiếp về một người, "
                        "hãy trả lời đúng thông tin đó, ngắn gọn, không kèm dữ liệu thừa."
                    ),
                )

                llm_text, llm_raw, _source = llm_service.generate(
                    prompt=prompt,
                    max_tokens=512,
                    temperature=0.0,
                )

                if llm_text and llm_text.strip():
                    answer_text = llm_text.strip()
                    
                assistant_status = "no_answer" if is_no_answer(answer_text) else "success"

                if assistant_status == "success":
                    sources = self._build_sources_from_retrieved(retrieved)
                else:
                    sources = []
                    answer_text = answer_text  

            except Exception:
                logger.exception(
                    "LLM generation failed trace_id=%s conversation_id=%s",
                    tid,
                    conversation_id,
                )
                answer_text = None
                llm_raw = None

        # fallback to minimal generator if LLM not configured or failed
        if not answer_text:
            answer_text, sources = answer_service.generate(
                user_input=content,
                retrieved=retrieved,
            )
            if not sources:
                sources = self._build_sources_from_retrieved(retrieved)

        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer_text,
            status=assistant_status,
            trace_id=tid,
        )
        self.msgs.create(db, assistant_msg)
        db.flush()
        db.refresh(assistant_msg)

        # persist sources
        self._persist_sources(db, assistant_msg.id, sources)

        # update trace
        tr.assistant_output_summary = (answer_text[:2000] if answer_text else None)
        tr.retrieved_sources = retrieved
        # tr.llm_prompt = prompt
        tr.llm_response = {
            "text": llm_text,
            "response_id": getattr(llm_raw, "id", None),
            "model": getattr(llm_raw, "model", None),
        }

        tr.status = "completed"

        audit_service.log_action(
            db,
            trace_id=tid,
            user_id=user.id,
            action="chat.message",
            resource_type="conversation",
            resource_id=conversation_id,
            decision="allow",
            input_json={"message": content},
            output_json={"assistant_message": answer_text, "sources": sources},
        )

        db.commit()
        db.refresh(assistant_msg)
        return user_msg, assistant_msg, sources

    def list_messages_flat(self, db: Session, conversation_id: str, limit: int = 1000) -> list[dict]:
        """
        Return conversation items shaped for API consumption.

        Each item represents a user message and its assistant reply (if present):
        {
          conversationId, messageId, content, createdAt, assistantMessage?, traceId?, sources?
        }
        """
        msgs = self.msgs.list_by_conversation(db, conversation_id, limit=limit)
        out: list[dict] = []
        i = 0

        while i < len(msgs):
            m = msgs[i]
            if m.role == "user":
                user_msg = m
                assistant_msg = None

                if i + 1 < len(msgs) and msgs[i + 1].role == "assistant":
                    assistant_msg = msgs[i + 1]
                    i += 2
                else:
                    i += 1

                item = {
                    "conversationId": conversation_id,
                    "messageId": user_msg.id,
                    "content": user_msg.content,
                    "createdAt": user_msg.created_at,
                    "assistantMessage": None,
                    "traceId": None,
                    "sources": [],
                }

                if assistant_msg:
                    srcs = self.msg_sources.list_by_message(db, assistant_msg.id)
                    sources_out = []
                    for s in (srcs or []):
                        sources_out.append(
                            {
                                "documentId": s.document_id,
                                "documentTitle": s.document_title,
                                "versionId": s.version_id,
                                "sectionPath": s.section_path,
                                "relevance": s.relevance,
                                "excerpt": s.excerpt,
                                "surroundingContext": s.surrounding_context,
                            }
                        )

                    item["assistantMessage"] = {
                        "id": assistant_msg.id,
                        "content": assistant_msg.content,
                        "status": assistant_msg.status,
                        "createdAt": assistant_msg.created_at,
                    }
                    item["traceId"] = assistant_msg.trace_id or user_msg.trace_id
                    item["sources"] = sources_out

                out.append(item)
            else:
                i += 1

        return out


chat_service = ChatService()
