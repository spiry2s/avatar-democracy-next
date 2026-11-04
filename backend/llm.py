"""
The single place the backend talks to Claude.

Every call returns schema-validated JSON via structured outputs
(output_config.format), so callers never parse markdown fences or coerce
free-form shapes. Calls stream so large max_tokens values don't hit HTTP
timeouts, and stop reasons are checked: a truncated or refused response is an
error, never silently half-parsed.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from anthropic import Anthropic


class LLMError(ValueError):
    """The model's response could not be used (bad JSON, truncated, refused)."""


class TruncatedOutput(LLMError):
    pass


class Refused(LLMError):
    pass


_client: Anthropic | None = None
_client_lock = threading.Lock()


def get_client() -> Anthropic:
    global _client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Get a key at https://console.anthropic.com"
        )
    with _client_lock:
        if _client is None:
            _client = Anthropic()
        return _client


# Older models need thinking switched on explicitly, and Haiku 4.5 accepts
# neither adaptive thinking nor effort. Current models think adaptively by default.
_EXPLICIT_ADAPTIVE = ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-4-6")
_NO_THINKING_OR_EFFORT = ("claude-haiku-4-5",)


def _model_kwargs(model: str, effort: str | None) -> dict[str, Any]:
    if model.startswith(_NO_THINKING_OR_EFFORT):
        return {}
    kw: dict[str, Any] = {}
    if model.startswith(_EXPLICIT_ADAPTIVE):
        kw["thinking"] = {"type": "adaptive"}
    if effort:
        kw["effort"] = effort
    return kw


def call_json(
    *,
    name: str,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    system: str | None = None,
    max_tokens: int = 32000,
    effort: str | None = None,
    client: Anthropic | None = None,
) -> Any:
    """Run one structured-output request and return the parsed JSON.

    `name` identifies the call site (used in error messages and by test fakes).
    """
    client = client or get_client()
    extra = _model_kwargs(model, effort)
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if "effort" in extra:
        output_config["effort"] = extra.pop("effort")

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": output_config,
        **extra,
    }
    if system:
        kwargs["system"] = system

    with client.messages.stream(**kwargs) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise Refused(f"{name}: the model declined this request")
    if message.stop_reason == "max_tokens":
        raise TruncatedOutput(f"{name}: response hit max_tokens={max_tokens} and was cut off")

    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"{name}: response was not valid JSON ({e}): {text[:300]}") from e


# ── schema helpers ────────────────────────────────────────────────────────────


def obj(properties: dict[str, Any]) -> dict[str, Any]:
    """A closed JSON object where every property is required."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


STR = {"type": "string"}
NUM = {"type": "number"}
INT = {"type": "integer"}


def enum(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


POSITION = enum(["yes", "no", "abstain"])
LEVEL = enum(["high", "medium", "low"])


def section_id_field(section_ids: list[str]) -> dict[str, Any]:
    """Constrain a section_id to the bill's actual ids, so downstream joins
    can never miss because a model reformatted 'Sec. 101' as '101'."""
    return enum(section_ids) if section_ids else STR
