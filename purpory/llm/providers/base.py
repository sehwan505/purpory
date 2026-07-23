from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
from pathlib import Path

class BaseLLMProvider(ABC):
    """Abstract Base Class for LLM Backend Adapters."""

    @abstractmethod
    def call_direct(
        self,
        api_key: str | None,
        model: str,
        user_message: str,
        max_tokens: int,
        temperature: float | None,
        deep_mode: bool = False,
        images: list[Any] | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform semantic extraction directly, returning parsed JSON dict."""
        pass

    @abstractmethod
    def call_raw(
        self,
        api_key: str | None,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float | None = None,
        cfg: dict[str, Any] | None = None,
        usage_out: dict[str, int] | None = None,
    ) -> str:
        """Perform raw completion directly, returning the raw text string."""
        pass
