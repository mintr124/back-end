from sqlalchemy.orm import Session
import uuid
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

    def post_message(self, db: Session, user, conversation_id: str, content: str, client_message_id: str | None, trace_id: str):
        # idempotency
        if client_message_id:
            existing = self.msgs.find_by_client_id(db, conversation_id, client_message_id)
            if existing:
                return existing, None, None

        user_msg = Message(conversation_id=conversation_id, role="user", content=content, client_message_id=client_message_id, trace_id=trace_id)
        self.msgs.create(db, user_msg)

        # create trace
        tid = trace_id or uuid.uuid4().hex
        tr = Trace(trace_id=tid, conversation_id=conversation_id, message_id=user_msg.id, user_id=user.id, user_input=content, status="running")
        self.traces.create(db, tr)

        db.commit()

        # retrieval
        retrieved = retrieval_service.retrieve(query=content, user=user, top_k=5)

        # Try LLM generation first (if configured). Build a prompt that includes retrieved sources.
        answer_text = None
        sources = []

        # prepare source list for prompt and for eventual persistence
        #TODO: check the candidate chunk with scopes access again
        prompt_sources = []
        for r in retrieved[:5]:
            md = r.get("metadata", {})
            excerpt = r.get("document_text") or md.get("excerpt") or ""
            title = md.get("document_title") or md.get("document_id") or ""
            prompt_sources.append(f"Source: {title}\nExcerpt: {excerpt}\nRelevance: {r.get('relevance')}")

        prompt_body = "\n\n".join([p for p in prompt_sources if p])
        prompt = f"Use the following sources to answer the question. Cite sources when applicable. If the sources don't contain the answer, be honest.\n\n{prompt_body}\n\nQuestion: {content}"

        if llm_service.is_configured():
            try:
                llm_text, llm_raw = llm_service.generate(prompt=prompt)
                if llm_text:
                    answer_text = llm_text
                    # build simple sources metadata from retrieved results
                    sources = [
                        {
                            "documentId": r.get("metadata", {}).get("document_id"),
                            "documentTitle": r.get("metadata", {}).get("document_title") or r.get("metadata", {}).get("document_id"),
                            "versionId": r.get("metadata", {}).get("document_version_id"),
                            "sectionPath": r.get("metadata", {}).get("section_path"),
                            "relevance": r.get("relevance"),
                            "excerpt": r.get("document_text") or r.get("metadata", {}).get("excerpt"),
                        }
                        for r in retrieved[:5]
                    ]
                else:
                    answer_text = None
            except Exception:
                answer_text = None

        # fallback to minimal generator if LLM not configured or failed
        if not answer_text:
            answer_text, sources = answer_service.generate(user_input=content, retrieved=retrieved)

        assistant_msg = Message(conversation_id=conversation_id, role="assistant", content=answer_text, trace_id=tid)
        self.msgs.create(db, assistant_msg)

        # persist sources
        for s in sources:
            src = MessageSource(message_id=assistant_msg.id, document_id=s.get("documentId"), version_id=s.get("versionId"), section_path=s.get("sectionPath"), relevance=s.get("relevance"), excerpt=s.get("excerpt"))
            self.msg_sources.create(db, src)

        # update trace
        tr.assistant_output_summary = (answer_text[:2000] if answer_text else None)
        tr.retrieved_sources = retrieved
        tr.llm_prompt = prompt if llm_service.is_configured() else None
        tr.llm_response = llm_raw if 'llm_raw' in locals() else {"text": answer_text}
        tr.status = "completed"
        self.traces.create(db, tr) if False else None

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
        """Return conversation items shaped for API consumption.

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
                    # collect sources for assistant message
                    srcs = self.msg_sources.list_by_message(db, assistant_msg.id)
                    sources_out = []
                    for s in (srcs or []):
                        sources_out.append({
                            "documentId": s.document_id,
                            "documentTitle": s.document_id,
                            "versionId": s.version_id,
                            "sectionPath": s.section_path,
                            "relevance": s.relevance,
                            "excerpt": s.excerpt,
                            "surroundingContext": s.surrounding_context,
                        })

                    item["assistantMessage"] = {
                        "id": assistant_msg.id,
                        "content": assistant_msg.content,
                        "createdAt": assistant_msg.created_at,
                    }
                    item["traceId"] = assistant_msg.trace_id or user_msg.trace_id
                    item["sources"] = sources_out

                out.append(item)
            else:
                # skip assistant-only messages
                i += 1

        return out


chat_service = ChatService()
