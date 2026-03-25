from typing import Optional, Tuple, Any, Dict
import logging

from app.core.config import settings
import httpx

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

logger = logging.getLogger(__name__)


class LLMService:
    def is_configured(self) -> bool:
        if settings.llm_provider == "openai":
            return bool(settings.openai_api_key)
        if settings.llm_provider == "ollama":
            return bool(settings.olama_url)
        return False

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
            provider, max_tokens, temperature
        )

        # 1) OpenAI
        if provider == "openai":
            try:
                if OpenAI is None:
                    raise RuntimeError("openai package not installed")

                client = OpenAI(
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_api_base or None,
                )

                model = settings.openai_model or "gpt-4o-mini"

                messages = []
                if system:
                    messages.append({"role": "developer", "content": system})
                messages.append({"role": "user", "content": prompt})

                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                )

                text = resp.choices[0].message.content or ""
                logger.info(
                    "LLM generate success source=openai model=%s",
                    model
                )
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

            payload: Dict[str, Any] = {
                "model": model,
                "prompt": prompt if not system else f"{system}\n\n{prompt}",
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

                    logger.info(
                        "LLM generate success source=%s model=%s",
                        source, model
                    )
                    return text, data, source

            except Exception:
                logger.exception("LLM generate failed source=ollama")
                raise

        raise RuntimeError("No LLM provider configured")


llm_service = LLMService()
