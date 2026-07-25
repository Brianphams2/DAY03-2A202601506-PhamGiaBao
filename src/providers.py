"""LLM provider adapter for OpenAI-compatible APIs and offline tests."""

from __future__ import annotations

import os
from typing import Protocol

from dotenv import load_dotenv


load_dotenv()


class LLMProvider(Protocol):
    """Small interface used by the agent and by scripted test doubles."""

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        """Generate one text response."""


class ProviderError(RuntimeError):
    """A safe, key-free error surfaced when the upstream LLM call fails."""


class OpenAICompatibleProvider:
    """Call an OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("VILAO_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "gpt-4o-mini"
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key.startswith("your_"):
            raise ProviderError("Chưa cấu hình OPENAI_API_KEY trong file .env.")

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
            )
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
            )
            content = response.choices[0].message.content
            if not content:
                raise ProviderError("Provider trả về nội dung rỗng.")
            return content.strip()
        except ProviderError:
            raise
        except Exception as exc:
            # Do not include request headers or credentials in the error.
            raise ProviderError(
                f"Không gọi được LLM ({type(exc).__name__}): {exc}"
            ) from exc


class MockProvider:
    """Minimal offline provider for smoke tests and setup verification."""

    model_name = "offline-mock"
    base_url = "offline"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return (
            "Thought: Tôi đang chạy ở chế độ mock nên không có suy luận LLM thật.\n"
            "Final Answer: Chế độ mock đã hoạt động. Hãy dùng LLM_PROVIDER=vilao "
            "để chạy agent với model đã cấu hình."
        )


def get_llm_provider(provider_name: str | None = None) -> LLMProvider:
    """Create the provider selected by ``LLM_PROVIDER``."""
    name = (provider_name or os.getenv("LLM_PROVIDER") or "mock").lower().strip()
    if name in {"openai", "openai-compatible", "openai_compatible", "vilao"}:
        return OpenAICompatibleProvider()
    if name == "mock":
        return MockProvider()
    raise ValueError(
        f"LLM_PROVIDER='{name}' không được hỗ trợ. "
        "Giá trị hợp lệ: vilao, openai, openai-compatible, mock."
    )


if __name__ == "__main__":
    provider = get_llm_provider()
    model = getattr(provider, "model_name", "unknown")
    endpoint = getattr(provider, "base_url", "unknown")
    print(f"Provider: {provider.__class__.__name__}")
    print(f"Model: {model}")
    print(f"Endpoint: {endpoint}")
    print(provider.generate("Chỉ trả lời: Kết nối thành công."))
