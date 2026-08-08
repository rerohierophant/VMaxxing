"""OpenAI-compatible chat client — drop-in replacement for ``langchain-openai``.

VMaxxing previously used ``langchain-openai`` purely as the model-provider
layer (``ChatOpenAI``). This module removes that dependency and talks directly to
any OpenAI-compatible ``/v1/chat/completions`` endpoint (OpenAI, DeepSeek,
Ollama, Qwen, …) via the ``openai`` SDK.

It exposes the minimal surface that :class:`src.providers.chat.ChatLLM` relies on
(``bind_tools`` / ``invoke`` / ``stream``) and returns langchain-compatible
``AIMessage``-like objects, so the agent loop, swarm workers, and tools keep
working unchanged.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - import guard
    OpenAI = None  # type: ignore


logger = logging.getLogger(__name__)


def _reasoning_of(message: Any) -> Optional[str]:
    """Best-effort extraction of provider reasoning text (DeepSeek ``reasoning_content``).

    The ``openai`` SDK surfaces ``reasoning_content`` differently across versions:
    sometimes as a typed attribute, sometimes only in ``model_extra``. We try both
    plus the ``reasoning`` alias (OpenRouter) so reasoning is never silently lost.
    """
    if message is None:
        return None
    value = getattr(message, "reasoning_content", None)
    if value:
        return value
    extra = getattr(message, "model_extra", None) or {}
    if isinstance(extra, dict):
        return extra.get("reasoning_content") or extra.get("reasoning") or None
    return None


class AIMessage:
    """Minimal langchain-compatible assistant message.

    Mirrors the attributes :func:`src.providers.chat.ChatLLM._parse_response`
    reads: ``content``, ``tool_calls`` (list of ``{"id","name","args"}`` dicts),
    ``additional_kwargs`` (``reasoning_content``), ``response_metadata``
    (``finish_reason``), and ``usage_metadata``.
    """

    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[list[dict[str, Any]]] = None,
        additional_kwargs: Optional[dict[str, Any]] = None,
        response_metadata: Optional[dict[str, Any]] = None,
        usage_metadata: Optional[dict[str, int]] = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls if tool_calls is not None else []
        self.additional_kwargs = additional_kwargs if additional_kwargs is not None else {}
        # An explicit ``{}`` means "no finish_reason reported" and must be
        # preserved; only fill the default when the caller omits the argument.
        self.response_metadata = (
            response_metadata if response_metadata is not None else {"finish_reason": "stop"}
        )
        self.usage_metadata = usage_metadata


class AIMessageChunk(AIMessage):
    """Streaming variant supporting ``__add__`` accumulation."""

    def __add__(self, other: "AIMessageChunk") -> "AIMessageChunk":
        content = (self.content or "") + (other.content or "")
        reasoning = (self.additional_kwargs.get("reasoning_content", "")) + (
            other.additional_kwargs.get("reasoning_content", "")
        )
        tool_calls = [*self.tool_calls, *other.tool_calls]
        finish = (
            other.response_metadata.get("finish_reason")
            or self.response_metadata.get("finish_reason")
            or "stop"
        )
        usage = other.usage_metadata or self.usage_metadata
        return AIMessageChunk(
            content=content,
            tool_calls=tool_calls,
            additional_kwargs={"reasoning_content": reasoning} if reasoning else {},
            response_metadata={"finish_reason": finish},
            usage_metadata=usage,
        )


class OpenAICompatClient:
    """OpenAI-compatible chat model used in place of ``langchain ChatOpenAI``."""

    def __init__(
        self,
        *,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        timeout: int = 120,
        max_retries: int = 2,
        caps: Any = None,
        reasoning_effort: Optional[str] = None,
        default_headers: Optional[dict[str, str]] = None,
        vibe_provider: Optional[str] = None,
        extra_body: Optional[dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is required for the LLM provider. "
                "Install it with: pip install openai"
            )
        self._api_key = api_key or "sk-noauth"
        self._base_url = base_url or None
        self._model = model
        # Public attribute for parity with langchain's ``ChatOpenAI``; the agent
        # loop and swarm telemetry read it via ``getattr(llm, "model_name", ...)``.
        self.model_name = model
        self._temperature = temperature
        self._timeout = timeout
        self._max_retries = max_retries
        self._caps = caps
        self._reasoning_effort = reasoning_effort
        self._default_headers = default_headers or None
        self._vibe_provider = vibe_provider
        self._extra_body = extra_body
        self._max_tokens = max_tokens
        self._tools: Optional[list[dict[str, Any]]] = None
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=self._default_headers,
        )

    def bind_tools(self, tools: Optional[list[dict[str, Any]]]) -> "OpenAICompatClient":
        """Attach OpenAI-format tool schemas; returns a bound client."""
        bound = OpenAICompatClient.__new__(OpenAICompatClient)
        bound.__dict__.update(self.__dict__)
        bound._tools = tools
        return bound

    def _prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize outbound assistant history to valid OpenAI wire format.

        ``ContextBuilder.format_assistant_tool_calls`` emits a few fields that are
        internal carriers rather than wire format, and providers disagree on the
        rest. This mirrors the capability-driven rules the previous langchain
        ``_get_request_payload`` override applied:

        * ``additional_kwargs`` is a langchain-only carrier — always dropped.
        * ``content=None`` is normalized to ``""`` for strict providers
          (Moonshot kimi-k2.6 rejects null assistant content).
        * ``reasoning_content`` is sent only when the provider expects it back
          (Kimi); otherwise it is stripped (DeepSeek rejects unknown fields).
        * ``tool_calls[].extra_content`` (Gemini thought signatures) is kept only
          for providers that round-trip it, and stripped everywhere else.

        The input list is never mutated — callers keep their own history intact.
        """
        caps = self._caps
        send_reasoning = bool(getattr(caps, "send_reasoning_content", False))
        normalize_content = bool(getattr(caps, "normalize_assistant_content", False))
        keep_signatures = bool(getattr(caps, "gemini_thought_signatures", False))

        prepared: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                prepared.append(message)
                continue

            new_message = dict(message)
            new_message.pop("additional_kwargs", None)

            if normalize_content and new_message.get("content") is None:
                new_message["content"] = ""

            if send_reasoning:
                new_message["reasoning_content"] = new_message.get("reasoning_content") or ""
            else:
                new_message.pop("reasoning_content", None)

            tool_calls = new_message.get("tool_calls")
            if tool_calls and not keep_signatures:
                new_message["tool_calls"] = [
                    {k: v for k, v in tc.items() if k != "extra_content"}
                    if isinstance(tc, dict)
                    else tc
                    for tc in tool_calls
                ]

            prepared.append(new_message)
        return prepared

    def _base_kwargs(
        self, messages: list[dict[str, Any]], *, stream: bool, timeout: Optional[int]
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._prepare_messages(messages),
            "temperature": self._temperature,
            "stream": stream,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        # extra_body: prefer an explicitly configured value (e.g. reasoning
        # effort forwarded from build_llm); otherwise derive it for
        # OpenRouter-style relays that require opt-in thinking activation.
        extra_body = self._extra_body
        if (
            extra_body is None
            and getattr(self._caps, "openrouter_reasoning_body", False)
            and self._reasoning_effort
        ):
            extra_body = {"reasoning": {"effort": self._reasoning_effort}}
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        return kwargs

    def invoke(
        self, messages: list[dict[str, Any]], config: Optional[dict[str, Any]] = None
    ) -> AIMessage:
        """Synchronous completion."""
        timeout = (config or {}).get("timeout")
        kwargs = self._base_kwargs(messages, stream=False, timeout=timeout)
        if self._tools:
            kwargs["tools"] = self._tools
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        reasoning = _reasoning_of(msg)
        tool_calls: list[dict[str, Any]] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except (ValueError, TypeError):
                args = {}
            tool_calls.append(
                {
                    "id": tc.id or "",
                    "name": tc.function.name or "",
                    "args": args,
                }
            )
        additional_kwargs: dict[str, Any] = {}
        if reasoning:
            additional_kwargs["reasoning_content"] = reasoning
        usage = None
        if getattr(resp, "usage", None) is not None:
            u = resp.usage
            usage = {
                "input_tokens": getattr(u, "prompt_tokens", 0),
                "output_tokens": getattr(u, "completion_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }
        return AIMessage(
            content=content,
            tool_calls=tool_calls,
            additional_kwargs=additional_kwargs,
            response_metadata={"finish_reason": choice.finish_reason or "stop"},
            usage_metadata=usage,
        )

    def stream(
        self, messages: list[dict[str, Any]], config: Optional[dict[str, Any]] = None
    ):
        """Streaming completion; yields :class:`AIMessageChunk` deltas.

        Text and reasoning deltas are emitted as they arrive; tool calls,
        ``finish_reason``, and ``usage`` are assembled and emitted as a final
        terminating chunk so ``ChatLLM``'s accumulation reproduces them.
        """
        timeout = (config or {}).get("timeout")
        kwargs = self._base_kwargs(messages, stream=True, timeout=timeout)
        if self._tools:
            kwargs["tools"] = self._tools
        stream = self._client.chat.completions.create(**kwargs)

        tool_acc: dict[int, dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        usage: Optional[dict[str, int]] = None

        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if choices:
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    text = getattr(delta, "content", None) or ""
                    if text:
                        yield AIMessageChunk(content=text)
                    reasoning = _reasoning_of(delta)
                    if reasoning:
                        yield AIMessageChunk(
                            content="",
                            additional_kwargs={"reasoning_content": reasoning},
                        )
                    for tc in getattr(delta, "tool_calls", None) or []:
                        idx = tc.index if tc.index is not None else 0
                        slot = tool_acc.setdefault(
                            idx, {"id": "", "name": "", "args": []}
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["args"].append(fn.arguments)
                fr = getattr(choice, "finish_reason", None)
                if fr:
                    finish_reason = fr
            if getattr(chunk, "usage", None) is not None:
                u = chunk.usage
                usage = {
                    "input_tokens": getattr(u, "prompt_tokens", 0),
                    "output_tokens": getattr(u, "completion_tokens", 0),
                    "total_tokens": getattr(u, "total_tokens", 0),
                }

        tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            raw_args = "".join(slot["args"])
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except (ValueError, TypeError):
                args = {}
            tool_calls.append(
                {
                    "id": slot["id"] or f"call_{idx}",
                    "name": slot["name"],
                    "args": args,
                }
            )
        yield AIMessageChunk(
            content="",
            tool_calls=tool_calls,
            additional_kwargs={},
            response_metadata={"finish_reason": finish_reason or "stop"},
            usage_metadata=usage,
        )
