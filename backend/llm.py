"""
Vendor-neutral LLM calls.

Call sites send a system prompt (optional) and a user prompt, and get text or
JSON back. They do not import a vendor SDK. `get_provider` picks the
implementation and the model for a role:

- primary: bill analysis, the operator Avatar vote, chamber axis tags
- checker: vote cross-check, delegate drift, cross-bill conflict

Today the only implementation is Anthropic. An OpenAI-compatible provider
(OpenAI itself, or an open-weight model behind a `/chat/completions` endpoint)
is a new class plus a `PROVIDERS` entry: implement `complete`, and inherit
`complete_json`. No call site should need to change.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Callable

from backend import config


class LLMConfigError(RuntimeError):
    """The selected provider is missing a credential or is not implemented.

    Subclasses RuntimeError so existing HTTP handlers keep returning 500 with
    this message, instead of an unhandled failure.
    """


class LLMProvider(ABC):
    """Send a system prompt and a user prompt; get text or JSON back."""

    provider_id: str
    model: str

    @abstractmethod
    def complete(
        self,
        *,
        user: str,
        system: str | None = None,
        max_tokens: int = 8000,
    ) -> str:
        """Return the assistant text for this prompt pair."""

    def complete_json(
        self,
        *,
        user: str,
        system: str | None = None,
        max_tokens: int = 8000,
        error_prefix: str = "Could not parse response as JSON",
    ) -> Any:
        """Return parsed JSON. Strips one leading markdown fence.

        Raises:
            ValueError: the model text is not JSON. The message includes a
                short snippet of the text, matching the historical parsers.
        """
        text = strip_markdown_fences(
            self.complete(user=user, system=system, max_tokens=max_tokens)
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{error_prefix}: {e}\n\nResponse:\n{text[:500]}"
            ) from e


def strip_markdown_fences(text: str) -> str:
    """Drop one leading ``` fence, same rules the call sites used to inline."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])
    return text


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API. This is the default provider for both roles."""

    provider_id = "anthropic"

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        if api_key is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        self._api_key = api_key or ""
        if not self._api_key.strip():
            raise LLMConfigError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Get a key at https://console.anthropic.com"
            )
        # Created on the first call so a missing key never constructs a client.
        self._client: Any = None

    def _anthropic(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def complete(
        self,
        *,
        user: str,
        system: str | None = None,
        max_tokens: int = 8000,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        # Omit `system` entirely when the caller has none. That matches the
        # historical messages.create calls for axis tags, drift, and conflicts.
        if system is not None:
            kwargs["system"] = system
        message = self._anthropic().messages.create(**kwargs)
        return "".join(
            block.text for block in message.content if hasattr(block, "text")
        ).strip()


def _anthropic(model: str) -> LLMProvider:
    return AnthropicProvider(model=model)


# One factory per vendor id. A follow-up OpenAI-compatible provider registers
# here (for example "openai") and reads its own base URL and credential.
PROVIDERS: dict[str, Callable[[str], LLMProvider]] = {
    "anthropic": _anthropic,
}


def credentials_configured(provider_id: str | None = None) -> bool:
    """Whether `provider_id` (default: the primary role) has a credential set.

    For the default Anthropic provider this is `ANTHROPIC_API_KEY`, so health
    checks keep their current meaning.
    """
    provider_id = provider_id if provider_id is not None else config.LLM_PROVIDER
    provider_id = (provider_id or "").strip().lower()
    if provider_id == "anthropic":
        return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())
    return False


def get_provider(role: str = "primary") -> LLMProvider:
    """Build the provider configured for `primary` or `checker`."""
    normalized = (role or "").strip().lower()
    if normalized == "primary":
        provider_id = config.LLM_PROVIDER
        model = config.MODEL
    elif normalized == "checker":
        provider_id = config.CHECKER_PROVIDER
        model = config.CHECKER_MODEL
    else:
        raise LLMConfigError(
            f"Unknown LLM role {role!r}. Use 'primary' or 'checker'."
        )

    provider_id = (provider_id or "").strip().lower() or "anthropic"
    factory = PROVIDERS.get(provider_id)
    if factory is None:
        known = ", ".join(sorted(PROVIDERS)) or "(none)"
        raise LLMConfigError(
            f"Unknown LLM provider {provider_id!r} for the {normalized} role. "
            f"Available providers: {known}."
        )
    return factory(model)


def resolve(provider: LLMProvider | None, role: str) -> LLMProvider:
    """Return `provider` when a caller injected one, otherwise the role default."""
    if provider is not None:
        return provider
    return get_provider(role)
