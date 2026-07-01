from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse
import json as jsonlib
import logging
logger = logging.getLogger(__name__)

from app.core.deps import get_current_user, get_db
from app.schemas.chat import (
    ConversationCreateRequest,
    ConversationRead,
    MessageCreateRequest,
    MessagePostResponse,
    SourceRead,
    TraceRead,
    UserMessageRead,
    MessageRead,
    ConversationMessageRead,
)
from app.services.chat_service import chat_service
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.trace_repository import TraceRepository
from app.repositories.message_source_repository import MessageSourceRepository
from app.models.user import User
from typing import Any

router = APIRouter()


@router.post("/conversations", response_model=ConversationRead)
def create_conversation(payload: ConversationCreateRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    conv = chat_service.create_conversation(db, current_user, payload.title)
    return ConversationRead.model_validate(conv)


@router.post("/conversations/{conversation_id}/messages", response_model=MessagePostResponse)
def post_message(conversation_id: str, payload: MessageCreateRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # basic permission: ensure conversation exists and belongs to user (or allow)
    repo = ConversationRepository()
    conv = repo.get(db, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.status != "open":
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_msg, assistant_msg, sources = chat_service.post_message(
        db, current_user, conversation_id, payload.content, payload.clientMessageId, request.state.trace_id
    )

    src_objs = [SourceRead(**s) for s in (sources or [])]

    return MessagePostResponse(
        conversationId=conversation_id,
        messageId=user_msg.id,
        assistantMessage={
            "id": assistant_msg.id,
            "content": assistant_msg.content,
            "status": assistant_msg.status,
            "createdAt": assistant_msg.created_at,
        },
        traceId=request.state.trace_id,
        sources=src_objs,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessageRead])
def get_messages(conversation_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Delegate formatting/aggregation to the chat service; return unified messages
    # ensure conversation is open
    repo = ConversationRepository()
    conv = repo.get(db, conversation_id)
    if not conv or conv.status != "open":
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = chat_service.list_messages_flat(db, conversation_id, limit=1000)
    return msgs


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def rename_conversation(conversation_id: str, payload: ConversationCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> Any:
    repo = ConversationRepository()
    conv = repo.get(db, conversation_id)
    if not conv or conv.status != "open":
        raise HTTPException(status_code=404, detail="Conversation not found")

    updated = repo.update_title(db, conversation_id, payload.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.commit()
    db.refresh(updated)
    return ConversationRead.model_validate(updated)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    repo = ConversationRepository()
    conv = repo.get(db, conversation_id)
    if not conv or conv.status != "open":
        raise HTTPException(status_code=404, detail="Conversation not found")

    updated = repo.set_status(db, conversation_id, "close")
    if not updated:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.commit()
    return {"ok": True}


@router.get("/traces/{trace_id}", response_model=TraceRead)
def get_trace(trace_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo = TraceRepository()
    tr = repo.get_by_trace_id(db, trace_id)
    if not tr:
        raise HTTPException(status_code=404, detail="Trace not found")
    return TraceRead.model_validate(tr)


@router.get("/users/{user_id}/conversations", response_model=list[ConversationRead])
def list_conversations_by_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo = ConversationRepository()
    convs = repo.list_by_user(db, user_id)
    return [ConversationRead.model_validate(c) for c in (convs or [])]


@router.post("/conversations/{conversation_id}/messages/stream")
def post_message_stream(
    conversation_id: str,
    payload: MessageCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    from app.db.session import SessionLocal
    db_check = SessionLocal()
    try:
        repo = ConversationRepository()
        conv = repo.get(db_check, conversation_id)
        if not conv or conv.status != "open":
            raise HTTPException(status_code=404, detail="Conversation not found")
    finally:
        db_check.close()

    def generate():
        stream_db = SessionLocal()
        try:
            for event in chat_service.post_message_stream(
                stream_db, current_user, conversation_id,
                payload.content, payload.clientMessageId,
                request.state.trace_id,
                oui_ids=payload.oui_ids,
                mode=payload.mode,
                chat_source=payload.chat_source,
                file_content=payload.file_content,
                file_name=payload.file_name,
            ):
                yield f"data: {jsonlib.dumps(event)}\n\n"
        finally:
            stream_db.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/search")
def search_documents(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.retrieval_service import retrieval_service
    query = payload.get("query", "").strip()
    mode = payload.get("mode", "hybrid")
    top_k = min(int(payload.get("top_k", 10)), 20)
    if not query:
        return []
    results = retrieval_service.retrieve(
        query=query, user=current_user, top_k=top_k, mode=mode, db=db
    )
    return results


@router.post("/conversations/{conversation_id}/generate-title")
def generate_title(
    conversation_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.llm_service import llm_service

    first_message = (payload.get("first_message") or "").strip()
    if not first_message:
        return {"title": "Cuộc trò chuyện"}

    try:
        title, _, _ = llm_service.generate(
            prompt=f'Tạo tiêu đề ngắn gọn (tối đa 6 từ, không dấu ngoặc kép) cho cuộc trò chuyện bắt đầu bằng câu hỏi sau:\n"{first_message}"\n\nChỉ trả về tiêu đề, không giải thích thêm.',
            system="Bạn là trợ lý tạo tiêu đề ngắn gọn. Chỉ trả về tiêu đề, không có gì khác.",
            max_tokens=30,
            temperature=0.3,
            fallback_to_ollama=False,
        )
        title = (title or "").strip().strip('"').strip("'")
        if not title:
            title = first_message[:40]
    except Exception:
        logger.exception("generate_title failed conv_id=%s", conversation_id)
        title = first_message[:40]

    repo = ConversationRepository()
    repo.update_title(db, conversation_id, title)
    db.commit()

    return {"title": title}