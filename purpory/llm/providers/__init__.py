from __future__ import annotations
from typing import Any
from purpory.llm.providers.base import BaseLLMProvider
from purpory.llm.providers.openai import OpenAICompatProvider
from purpory.llm.providers.anthropic import AnthropicProvider
from purpory.llm.providers.claude_cli import ClaudeCLIProvider
from purpory.llm.providers.bedrock import BedrockProvider
from purpory.llm.providers.azure import AzureProvider

_PROVIDERS: dict[str, type[BaseLLMProvider]] = {
    "claude": AnthropicProvider,
    "claude-cli": ClaudeCLIProvider,
    "bedrock": BedrockProvider,
    "azure": AzureProvider,
    # OpenAI Compat serves everything else by default
}

def get_provider(backend: str) -> BaseLLMProvider:
    """Fetch the LLM provider adapter for the given backend name."""
    provider_cls = _PROVIDERS.get(backend, OpenAICompatProvider)
    return provider_cls()
