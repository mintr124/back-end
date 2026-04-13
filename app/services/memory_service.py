"""
memory_service.py
==================
Hybrid Conversation Memory:

  history = [summary của messages cũ] + [3 recent Q&A] + [3 relevant Q&A]

Pipeline mỗi lần user gửi message:
  1. load_history(conv_id, query)
     → trả về list[dict] để nhét vào LLM prompt
  2. update_summary(conv_id, new_messages)  (gọi sau khi có assistant reply)
     → cập nhật rolling summary bằng LLM nếu conversation đủ dài

Design:
  - Recent:   3 cặp Q&A gần nhất (6 messages) — đảm bảo mạch hội thoại
  - Relevant: 3 cặp Q&A liên quan nhất với query hiện tại (cosine sim trên embedding)
              dedup với recent để không trùng
  - Summary:  1 đoạn tóm tắt ngắn (~150 token) của tất cả messages cũ hơn recent
              được update mỗi SUMMARY_UPDATE_EVERY lượt

Embedding dùng lại embedding_service (OpenAI/Ollama) — không cần infra thêm.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.conversation import Conversation
from app.repositories.message_repository import MessageRepository
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────
RECENT_PAIRS    = 3    # số cặp Q&A gần nhất giữ nguyên
RELEVANT_PAIRS  = 3    # số cặp Q&A liên quan nhất
SUMMARY_UPDATE_EVERY = 6   # update summary sau mỗi N cặp Q&A mới
MAX_SUMMARY_TOKENS   = 150 # LLM prompt token limit cho summary
DONE_STATUSES = {"success", "fallback", "no_answer", "llm_error"}
# ──────────────────────────────────────────────────────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class MemoryService:
    def __init__(self):
        self.msg_repo = MessageRepository()

    # ------------------------------------------------------------------
    # Public: load history for prompt
    # ------------------------------------------------------------------

    def load_history(
        self,
        db: Session,
        conversation_id: str,
        query: str,
    ) -> list[dict]:
        """
        Returns list[dict] with keys: role, content
        Order: summary (if any) → relevant pairs → recent pairs
        Deduplication: relevant pairs that overlap with recent are dropped.
        """
        # Load all messages (max 200 để tránh OOM)
        all_msgs = self.msg_repo.list_by_conversation(db, conversation_id, limit=200)

        # Build Q&A pairs: [(user_msg, assistant_msg), ...]
        pairs = self._build_pairs(all_msgs)

        if not pairs:
            return []

        # Split: recent vs older
        recent_pairs  = pairs[-RECENT_PAIRS:]          # cuối cùng
        older_pairs   = pairs[:-RECENT_PAIRS] if len(pairs) > RECENT_PAIRS else []

        # Get summary from conversation
        conv = db.get(Conversation, conversation_id)
        summary = (conv.summary or "").strip() if conv else ""

        # Find relevant pairs from older_pairs
        relevant_pairs = self._find_relevant(query, older_pairs, top_k=RELEVANT_PAIRS)

        # Dedup: remove relevant pairs already in recent
        recent_ids = {id(p) for p in recent_pairs}
        relevant_pairs = [p for p in relevant_pairs if id(p) not in recent_ids]

        # Build history list
        history: list[dict] = []

        if summary:
            history.append({
                "role":    "system",
                "content": f"[Tóm tắt cuộc trò chuyện trước]\n{summary}",
            })

        # relevant first (older context), then recent (immediate context)
        for u_msg, a_msg in relevant_pairs:
            history.append({"role": "user",      "content": u_msg.content or ""})
            history.append({"role": "assistant",  "content": a_msg.content or ""})

        for u_msg, a_msg in recent_pairs:
            history.append({"role": "user",      "content": u_msg.content or ""})
            history.append({"role": "assistant",  "content": a_msg.content or ""})

        return history

    # ------------------------------------------------------------------
    # Public: update rolling summary after assistant replies
    # ------------------------------------------------------------------

    def update_summary(
        self,
        db: Session,
        conversation_id: str,
    ) -> None:
        """
        Gọi sau khi assistant đã reply.
        Chỉ update summary nếu số cặp Q&A vượt ngưỡng SUMMARY_UPDATE_EVERY.
        Summary chỉ tóm tắt các messages CŨ HƠN RECENT_PAIRS cặp cuối.
        """
        all_msgs = self.msg_repo.list_by_conversation(db, conversation_id, limit=200)
        pairs    = self._build_pairs(all_msgs)

        # Chỉ update khi đủ dài
        if len(pairs) < RECENT_PAIRS + SUMMARY_UPDATE_EVERY:
            return

        older_pairs = pairs[:-RECENT_PAIRS]

        # Gọi LLM để tạo summary
        try:
            from app.services.llm_service import llm_service
            if not llm_service.is_configured():
                return

            history_text = "\n".join(
                f"User: {u.content or ''}\nAssistant: {a.content or ''}"
                for u, a in older_pairs
            )

            prompt = (
                f"Hãy tóm tắt ngắn gọn (tối đa {MAX_SUMMARY_TOKENS} từ) "
                f"nội dung cuộc trò chuyện sau, giữ lại các thông tin quan trọng:\n\n"
                f"{history_text}\n\n"
                f"Tóm tắt:"
            )

            summary_text, _, _ = llm_service.generate(
                prompt=prompt,
                max_tokens=MAX_SUMMARY_TOKENS,
                temperature=0.0,
            )

            if summary_text and summary_text.strip():
                conv = db.get(Conversation, conversation_id)
                if conv:
                    conv.summary = summary_text.strip()
                    conv.summary_updated_at = datetime.now(timezone.utc)
                    db.flush()
                    logger.info("Summary updated conv_id=%s len=%d", conversation_id, len(summary_text))

        except Exception:
            logger.exception("Failed to update summary conv_id=%s", conversation_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_pairs(self, messages):
        pairs = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if (
                m.role == "user"
                and i + 1 < len(messages)
                and messages[i + 1].role == "assistant"
            ):
                assistant = messages[i + 1]
                is_done = assistant.status in DONE_STATUSES
                is_streaming_with_content = (
                    assistant.status == "streaming"
                    and assistant.content
                    and len(assistant.content.strip()) > 10  # có nội dung thật
                )
                if is_done or is_streaming_with_content:
                    pairs.append((m, assistant))
                i += 2
            else:
                i += 1
        return pairs

    def _find_relevant(
        self,
        query: str,
        pairs: list[tuple[Message, Message]],
        top_k: int,
    ) -> list[tuple[Message, Message]]:
        """
        Tìm top_k cặp Q&A có nội dung liên quan nhất đến query.
        Dùng cosine similarity trên embedding của user message.
        Fallback về keyword overlap nếu embedding không khả dụng.
        """
        if not pairs or not query.strip():
            return []

        # Try embedding-based
        if embedding_service.is_configured():
            try:
                return self._relevant_by_embedding(query, pairs, top_k)
            except Exception:
                logger.warning("Embedding relevance failed, falling back to keyword")

        # Fallback: keyword overlap
        return self._relevant_by_keyword(query, pairs, top_k)

    def _relevant_by_embedding(
        self,
        query: str,
        pairs: list[tuple[Message, Message]],
        top_k: int,
    ) -> list[tuple[Message, Message]]:
        # Embed query + all user messages in one batch
        texts = [query] + [u.content or "" for u, _ in pairs]
        vectors = embedding_service.embed_many(texts)

        query_vec = vectors[0]
        pair_vecs  = vectors[1:]

        scored = []
        for i, (pair, vec) in enumerate(zip(pairs, pair_vecs)):
            sim = _cosine(query_vec, vec)
            scored.append((sim, i, pair))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [pair for _, _, pair in scored[:top_k]]

    def _relevant_by_keyword(
        self,
        query: str,
        pairs: list[tuple[Message, Message]],
        top_k: int,
    ) -> list[tuple[Message, Message]]:
        q_tokens = set(query.lower().split())
        scored = []
        for pair in pairs:
            u_msg = pair[0]
            d_tokens = set((u_msg.content or "").lower().split())
            overlap = len(q_tokens & d_tokens)
            if overlap > 0:
                scored.append((overlap, pair))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [pair for _, pair in scored[:top_k]]


memory_service = MemoryService()