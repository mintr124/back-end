from __future__ import annotations

from typing import Optional, Tuple, Any, Dict, Iterable
import logging

import httpx

from app.core.config import settings

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

logger = logging.getLogger(__name__)


DEFAULT_VI_SYSTEM_PROMPT = """
Bạn là trợ lý RAG SME.

Nguyên tắc bắt buộc:
- Chỉ trả lời dựa trên ngữ cảnh được cung cấp và yêu cầu của người dùng.
- Không suy đoán nếu ngữ cảnh không đủ.
- Nếu không tìm thấy thông tin phù hợp, hãy nói rõ là không tìm thấy trong tài liệu.
- Viết ngắn gọn, rõ ràng, chỉnh chu, dễ hiểu.
- Không lặp lại toàn bộ ngữ cảnh.
- Không trộn lẫn nhiều chủ đề vào một câu trả lời.
- Nếu câu hỏi liên quan đến một người, chỉ trả lời đúng thông tin được chứng minh trong ngữ cảnh.
- Nếu ngữ cảnh có dấu hiệu OCR bẩn, ưu tiên nói “tài liệu trích xuất chưa đủ rõ” thay vì bịa.
- Nếu có thể trả lời bằng một câu ngắn, hãy làm vậy.
- Nếu cần giải thích, dùng cấu trúc:
  1) Kết luận
  2) Giải thích ngắn
  3) Nếu cần, đề xuất bước tiếp theo
""".strip()


class LLMService:
    def is_configured(self) -> bool:
        if settings.llm_provider == "openai":
            return bool(settings.openai_api_key)
        if settings.llm_provider == "ollama":
            return bool(settings.olama_url)
        return False

    def _build_instructions(self, system: Optional[str]) -> str:
        if system and system.strip():
            return f"{DEFAULT_VI_SYSTEM_PROMPT}\n\n{system.strip()}"
        return DEFAULT_VI_SYSTEM_PROMPT

    def build_prompt(
        self,
        *,
        question: str,
        contexts: list[dict] | None = None,
        chat_history: list[dict] | None = None,
        extra_instructions: str | None = None,
    ) -> str:
        """
        Build prompt chặt chẽ cho RAG.
        contexts: list các chunk đã retrieve, mỗi item nên có:
          - document_text
          - score
          - metadata
          - chunk_id
        """

        contexts = contexts or []
        chat_history = chat_history or []

        context_blocks: list[str] = []
        for idx, ctx in enumerate(contexts, start=1):
            doc_text = (ctx.get("document_text") or "").strip()
            if not doc_text:
                continue

            score = ctx.get("score")
            chunk_id = ctx.get("chunk_id")
            metadata = ctx.get("metadata") or {}
            page = metadata.get("source_page") or metadata.get("page_start") or metadata.get("page")

            header_parts = [f"[Context {idx}]"]
            if chunk_id:
                header_parts.append(f"chunk_id={chunk_id}")
            if score is not None:
                header_parts.append(f"score={score}")
            if page is not None:
                header_parts.append(f"page={page}")

            header = " | ".join(header_parts)
            context_blocks.append(f"{header}\n{doc_text}")

        context_text = "\n\n---\n\n".join(context_blocks).strip()

        history_text = ""
        if chat_history:
            hist_lines = []
            for item in chat_history[-6:]:
                role = item.get("role", "")
                content = (item.get("content") or "").strip()
                if content:
                    hist_lines.append(f"{role}: {content}")
            if hist_lines:
                history_text = "\n".join(hist_lines)

        prompt = f"""
CÂU HỎI NGƯỜI DÙNG
{question.strip()}

NGỮ CẢNH TRUY XUẤT
{context_text if context_text else "[Không có ngữ cảnh truy xuất]"}

LỊCH SỬ HỘI THOẠI
{history_text if history_text else "[Không có lịch sử]"}

YÊU CẦU TRẢ LỜI
- Chỉ dùng thông tin có trong ngữ cảnh, không đoán.
- Nếu ngữ cảnh không đủ, trả lời: "Không tìm thấy thông tin đủ tin cậy trong tài liệu."
- Nếu có thông tin trực tiếp, trả lời ngắn gọn, đúng trọng tâm.
- Nếu câu hỏi hỏi về danh tính / năm sinh / ngày sinh / số điện thoại / mã định danh, chỉ trả lời đúng trường liên quan.
- Không trích nguyên đoạn dài từ ngữ cảnh.
- Không nhắc đến quá trình suy luận.
- Không bịa thêm chi tiết.
- Nếu ngữ cảnh OCR bẩn hoặc lẫn nhiều trường, hãy ưu tiên nói rằng tài liệu chưa đủ rõ.

ĐỊNH DẠNG TRẢ LỜI
- Trả lời trực tiếp trước.
- Nếu cần, thêm 1 câu giải thích ngắn.
- Không dùng bullet trừ khi thật sự cần.
""".strip()

        if extra_instructions and extra_instructions.strip():
            prompt += f"\n\nGHI CHÚ BỔ SUNG\n{extra_instructions.strip()}"

        return prompt

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
        fallback_to_ollama: bool = True,
    ) -> Tuple[str, Any, str]:
        """
        Returns: (text, raw_response, source)
        source: openai | ollama | fallback
        """
        provider = settings.llm_provider

        logger.info(
            "LLM generate start provider=%s max_tokens=%s temperature=%s",
            provider,
            max_tokens,
            temperature,
        )

        # 1) OpenAI
        if provider == "openai":
            try:
                if OpenAI is None:
                    raise RuntimeError("openai package not installed")

                if not settings.openai_api_key:
                    raise RuntimeError("OPENAI_API_KEY is not configured")

                client = OpenAI(
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_api_base or None,
                )

                model = settings.openai_model or "gpt-4.1-mini"
                instructions = self._build_instructions(system)

                resp = client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=prompt,
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                )

                text = getattr(resp, "output_text", "") or ""
                logger.info("LLM generate success source=openai model=%s", model)
                return text, resp, "openai"

            except Exception:
                logger.exception("LLM generate failed source=openai")

                if not fallback_to_ollama:
                    raise

                logger.warning("LLM fallback triggered from=openai to=ollama")

        # 2) Ollama
        if provider == "ollama" or fallback_to_ollama:
            if not settings.olama_url:
                raise RuntimeError("Ollama URL not configured")

            url = settings.olama_url.rstrip("/") + "/v1/generate"
            model = settings.olama_model

            final_prompt = prompt
            if system and system.strip():
                final_prompt = f"{DEFAULT_VI_SYSTEM_PROMPT}\n\n{system.strip()}\n\n{prompt}"

            payload: Dict[str, Any] = {
                "model": model,
                "prompt": final_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            try:
                with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                    r = client.post(url, json=payload)
                    r.raise_for_status()
                    data = r.json()

                    text = data.get("output") or data.get("text") or ""
                    source = "ollama" if provider == "ollama" else "fallback"

                    logger.info("LLM generate success source=%s model=%s", source, model)
                    return text, data, source

            except Exception:
                logger.exception("LLM generate failed source=ollama")
                raise

        raise RuntimeError("No LLM provider configured")


llm_service = LLMService()
