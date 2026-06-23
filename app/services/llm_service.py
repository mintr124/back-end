from __future__ import annotations

import re
import json as jsonlib
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
Bạn là trợ lý RAG doanh nghiệp, trả lời dựa trên tài liệu nội bộ.

Nguyên tắc:
- Chỉ trả lời dựa trên ngữ cảnh được cung cấp, không suy đoán.
- Nếu không tìm thấy thông tin, nói rõ không có trong tài liệu.
- Không lặp lại nguyên văn toàn bộ ngữ cảnh.
- Không trộn lẫn nhiều chủ đề vào một câu trả lời.
- Nếu ngữ cảnh có dấu hiệu OCR bẩn, ưu tiên nói "tài liệu trích xuất chưa đủ rõ".
- Độ dài câu trả lời phù hợp với độ phức tạp của câu hỏi:
  + Câu hỏi tra cứu (tên, số, ngày) → trả lời 1-2 câu
  + Câu hỏi giải thích quy trình/quy định → trả lời đầy đủ, có thể dùng danh sách
  + Câu hỏi so sánh/tổng hợp → trả lời có cấu trúc rõ ràng
""".strip()


class LLMService:
    def is_configured(self) -> bool:
        if settings.llm_provider == "openai":
            return bool(settings.openai_api_key)
        if settings.llm_provider in ("ollama", "olama"):
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
CÂU HỎI HIỆN TẠI (ưu tiên tuyệt đối)
{question.strip()}

NGỮ CẢNH TRUY XUẤT
{context_text if context_text else "[Không có ngữ cảnh truy xuất]"}

LỊCH SỬ HỘI THOẠI (chỉ để hiểu ngữ cảnh, KHÔNG phải câu hỏi cần trả lời)
{history_text if history_text else "[Không có lịch sử]"}

YÊU CẦU TRẢ LỜI
- Trả lời DUY NHẤT cho "CÂU HỎI HIỆN TẠI" ở trên.
- Chỉ dùng thông tin có trong ngữ cảnh, không đoán.
- Không nhắc đến quá trình suy luận, không bịa thêm chi tiết.
- Lịch sử hội thoại chỉ dùng để hiểu đại từ/tham chiếu, không phải chủ đề để trả lời.
- Dùng danh sách hoặc bảng nếu nội dung có nhiều mục cần liệt kê.
- BẮT BUỘC: sau mỗi câu/đoạn lấy thông tin từ ngữ cảnh, chèn số thứ tự Context tương ứng dạng [N] (ví dụ [1], [2][3]). Chỉ cite Context thực sự dùng để trả lời, không cite tất cả.

ĐỊNH DẠNG TRẢ LỜI
- Trả lời trực tiếp trước, giải thích sau nếu cần.
- Không bắt đầu bằng "Dựa trên ngữ cảnh..." hay các cụm mở đầu thừa.
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
        logger.info("LLM PROMPT:\n%s", prompt)

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

                logger.info("LLM generate SYSTEM:\n%s", instructions)

                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                text = resp.choices[0].message.content or ""
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

            url = settings.olama_url.rstrip("/") + "/api/generate"
            model = settings.olama_model

            final_prompt = prompt
            if system and system.strip():
                final_prompt = f"{DEFAULT_VI_SYSTEM_PROMPT}\n\n{system.strip()}\n\n{prompt}"

            payload: Dict[str, Any] = {
                "model": model,
                "prompt": final_prompt,
                "stream": True,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                }
            }

            try:
                with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
                    full_text = ""
                    with client.stream("POST", url, json=payload) as resp:
                        resp.raise_for_status()
                        for line in resp.iter_lines():
                            if not line.strip():
                                continue
                            try:
                                chunk = jsonlib.loads(line)
                                full_text += chunk.get("response", "")
                                if chunk.get("done"):
                                    break
                            except Exception:
                                continue

                    full_text = re.sub(r"<think>.*?</think>", "", full_text, flags=re.DOTALL).strip()

                    source = "ollama" if provider == "ollama" else "fallback"
                    logger.info("LLM generate success source=%s model=%s", source, model)
                    return full_text, {"response": full_text}, source

            except Exception:
                logger.exception("LLM generate failed source=ollama")
                raise

        raise RuntimeError("No LLM provider configured")

    def generate_stream(self, prompt: str, max_tokens: int = 256, temperature: float = 0.0, system: Optional[str] = None,):
        provider = settings.llm_provider
        logger.info(
            "LLM generate_stream start provider=%s max_tokens=%s temperature=%s",
            provider, max_tokens, temperature,
        )
        logger.info("LLM STREAM PROMPT:\n%s", prompt)

        if provider == "openai":
            if OpenAI is None:
                raise RuntimeError("openai package not installed")
            client = OpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base or None,
            )
            model = settings.openai_model or "gpt-4o-mini"
            instructions = system if system else self._build_instructions(None)
            logger.info("LLM generate_stream SYSTEM:\n%s", instructions)
            with client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            ) as stream:
                for chunk in stream:
                    token = chunk.choices[0].delta.content or ""
                    if token:
                        yield token
            return

        url = settings.olama_url.rstrip("/") + "/api/generate"
        model = settings.olama_model
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = jsonlib.loads(line)
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
                    except Exception:
                        continue

    def generate_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 16000,
        temperature: float = 0.0,
        use_default_instructions: bool = True,
    ) -> Tuple[str, Any, str]:
        """Generate với response_format=json_object — đảm bảo output là valid JSON."""
        if settings.llm_provider != "openai":
            return self.generate(prompt, system, max_tokens, temperature, fallback_to_ollama=True)
        if OpenAI is None:
            raise RuntimeError("openai package not installed")
        client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_api_base or None,
        )
        model = settings.openai_model or "gpt-4.1-mini"

        if use_default_instructions:
            instructions = self._build_instructions(system)
        else:
            instructions = (system or "").strip() or "Bạn là hệ thống xử lý dữ liệu. Chỉ trả về JSON."

        # --- LOG ĐẦY ĐỦ REQUEST GỬI LÊN LLM (system + user prompt thật) ---
        logger.info(
            "LLM generate_json REQUEST model=%s max_tokens=%s temperature=%s use_default_instructions=%s",
            model, max_tokens, temperature, use_default_instructions,
        )
        logger.info("LLM generate_json SYSTEM:\n%s", instructions)
        logger.info("LLM generate_json USER PROMPT:\n%s", prompt)
        # -------------------------------------------------------------------

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or ""
        logger.info("LLM generate_json success model=%s len=%d", model, len(text))
        return text, resp, "openai"


llm_service = LLMService()