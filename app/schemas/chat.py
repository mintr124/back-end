from __future__ import annotations
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = None


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: Optional[str]
    status: str
    last_message_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class MessageCreateRequest(BaseModel):
    content: str
    clientMessageId: Optional[str] = None


class AssistantMessage(BaseModel):
    id: str
    content: str
    createdAt: datetime


class SourceRead(BaseModel):
    documentId: Optional[str]
    documentTitle: Optional[str]
    versionId: Optional[str]
    sectionPath: Optional[str]
    relevance: Optional[float]
    excerpt: Optional[str]
    surroundingContext: Optional[str] = None


class MessagePostResponse(BaseModel):
    conversationId: str
    messageId: str
    assistantMessage: AssistantMessage
    traceId: str
    sources: list[SourceRead]


class UserMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: Optional[str]
    timestamp: Optional[datetime]
    messageId: Optional[str]
    # keep optional other fields so they can be null
    conversationId: Optional[str] = None
    assistantMessage: Optional[AssistantMessage] = None
    traceId: Optional[str] = None
    sources: Optional[list[SourceRead]] = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: Optional[str]
    created_at: datetime
    # other fields optional and may be null
    messageId: Optional[str] = None
    conversationId: Optional[str] = None
    traceId: Optional[str] = None
    sources: Optional[list[SourceRead]] = None
    assistantMessage: Optional[AssistantMessage] = None


class ConversationMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    conversationId: str
    messageId: str
    content: Optional[str]
    createdAt: Optional[datetime]
    assistantMessage: Optional[AssistantMessage] = None
    traceId: Optional[str] = None
    sources: Optional[list[SourceRead]] = None


class TraceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trace_id: str
    conversation_id: Optional[str]
    message_id: Optional[str]
    user_input: Optional[str]
    assistant_output_summary: Optional[str]
    retrieved_sources: Optional[dict]
    timings: Optional[dict]
    created_at: datetime
