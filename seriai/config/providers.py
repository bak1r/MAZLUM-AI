"""
Provider abstraction layer.
Keeps model/API details out of business logic.
"""
import os
import logging
from typing import Optional
from dataclasses import dataclass

log = logging.getLogger("seriai.providers")


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list = None
    stop_reason: str = ""

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []


class AnthropicProvider:
    """Anthropic API wrapper (Sonnet 4 ana beyin, Haiku 4.5 hafif işler)."""

    def __init__(self):
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            self._client = anthropic.Anthropic(api_key=api_key)

    def chat(
        self,
        messages: list,
        model: str,
        system: str = "",
        max_tokens: int = 4096,
        tools: list = None,
        temperature: float = 0.3,
    ) -> LLMResponse:
        self._ensure_client()
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:
            error_msg = str(e)
            log.error(f"Anthropic API error (model={model}): {error_msg}")
            # Sessiz fallback YASAK — hatayı açıkça fırlat
            if "not_found" in error_msg.lower() or "404" in error_msg:
                raise ValueError(f"Model '{model}' erişilemedi. Model ID doğru mu? API key erişimi var mı?") from e
            if "rate_limit" in error_msg.lower() or "429" in error_msg:
                raise RuntimeError(f"API rate limit aşıldı. Biraz bekleyip tekrar deneyin.") from e
            if "overloaded" in error_msg.lower() or "529" in error_msg:
                raise RuntimeError(f"API şu an meşgul. Biraz bekleyip tekrar deneyin.") from e
            raise

        text_parts = []
        tool_calls = []
        # Keep raw content blocks for proper Anthropic message format
        raw_content = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                raw_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                raw_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        result = LLMResponse(
            text="\n".join(text_parts),
            model=model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
        )
        result.raw_content = raw_content  # for proper message reconstruction
        return result


class GeminiProvider:
    """Google Gemini API wrapper.
    NOT: chat() metodu şu an production'da kullanılmıyor.
    Voice engine Gemini Live API'yi doğrudan kullanıyor (voice.py).
    Bu sınıf gelecekte non-voice Gemini ihtiyaçları için tutuluyor.
    """

    def __init__(self):
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import google.generativeai as genai
            api_key = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY not set")
            genai.configure(api_key=api_key)
            self._client = genai

    def chat(
        self,
        messages: list,
        model: str,
        system: str = "",
        max_tokens: int = 2048,
        tools: list = None,
        temperature: float = 0.3,
    ) -> LLMResponse:
        self._ensure_client()
        gen_model = self._client.GenerativeModel(
            model_name=model,
            system_instruction=system if system else None,
        )
        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [msg["content"]]})

        try:
            resp = gen_model.generate_content(
                contents,
                generation_config={"max_output_tokens": max_tokens, "temperature": temperature},
            )
        except Exception as e:
            error_msg = str(e)
            log.error(f"Gemini API error (model={model}): {error_msg}")
            if "not found" in error_msg.lower() or "404" in error_msg:
                raise ValueError(f"Model '{model}' erişilemedi.") from e
            if "quota" in error_msg.lower() or "429" in error_msg:
                raise RuntimeError(f"Gemini API kotası aşıldı.") from e
            raise

        try:
            text = resp.text if resp.text else ""
        except (ValueError, AttributeError):
            # Some responses have no text (blocked, empty, etc.)
            text = ""
            log.warning(f"Gemini response has no text. finish_reason={getattr(resp, 'prompt_feedback', 'unknown')}")

        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
        )


# ── Provider registry ───────────────────────────────────────────────

_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "google": GeminiProvider,
}

_instances: dict = {}


def get_provider(name: str):
    """Get or create a provider instance."""
    if name not in _instances:
        cls = _PROVIDERS.get(name)
        if cls is None:
            raise ValueError(f"Unknown provider: {name}. Available: {list(_PROVIDERS.keys())}")
        _instances[name] = cls()
    return _instances[name]
