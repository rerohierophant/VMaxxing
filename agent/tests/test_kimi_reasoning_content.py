"""Regression tests for provider reasoning_content preservation.

Invariant: ``ai_message.additional_kwargs["reasoning_content"]`` is the
single source of truth. ``OpenAICompatClient`` populates it from both
the non-streaming response path and the streaming delta path.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.context import ContextBuilder
from src.agent.loop import _attach_tool_call_thought_signatures
from src.providers.capabilities import get_provider_capabilities
from src.providers.chat import ChatLLM, ToolCallRequest, _dedupe_finish_reason
from src.providers.llm import OpenAICompatClient


class TestParseResponseSingleSource:
    """_parse_response reads reasoning_content from exactly one place."""

    def test_reads_from_additional_kwargs(self) -> None:
        ai_message = SimpleNamespace(
            content="",
            tool_calls=[],
            additional_kwargs={"reasoning_content": "step-by-step reasoning"},
            response_metadata={"finish_reason": "stop"},
        )

        response = ChatLLM._parse_response(ai_message)

        assert response.reasoning_content == "step-by-step reasoning"

    def test_absent_reasoning_content_yields_none(self) -> None:
        """Non-thinking providers leave reasoning_content unset."""
        ai_message = SimpleNamespace(
            content="hello",
            tool_calls=[],
            additional_kwargs={},
            response_metadata={"finish_reason": "stop"},
        )

        response = ChatLLM._parse_response(ai_message)

        assert response.reasoning_content is None
        assert response.content == "hello"

    def test_tool_calls_are_preserved_alongside_reasoning(self) -> None:
        ai_message = SimpleNamespace(
            content="",
            tool_calls=[{"id": "tc_1", "name": "bash", "args": {"command": "pwd"}}],
            additional_kwargs={"reasoning_content": "think then call"},
            response_metadata={"finish_reason": "tool_calls"},
        )

        response = ChatLLM._parse_response(ai_message)

        assert response.reasoning_content == "think then call"
        assert response.finish_reason == "tool_calls"
        assert response.tool_calls[0].id == "tc_1"
        assert response.tool_calls[0].arguments == {"command": "pwd"}

    def test_tool_call_thought_signatures_are_preserved_by_id_and_index(self) -> None:
        ai_message = SimpleNamespace(
            content="",
            tool_calls=[
                {"id": "tc_1", "name": "bash", "args": {"command": "pwd"}},
                {"id": "tc_2", "name": "read_file", "args": {"path": "README.md"}},
            ],
            additional_kwargs={
                "tool_call_thought_signatures": [
                    {"id": "tc_1", "index": 0, "thought_signature": "sig-a"},
                    {"index": 1, "thought_signature": "sig-b"},
                ],
            },
            response_metadata={"finish_reason": "tool_calls"},
        )

        response = ChatLLM._parse_response(ai_message)

        assert response.tool_calls[0].thought_signature == "sig-a"
        assert response.tool_calls[1].thought_signature == "sig-b"

    def test_missing_tool_call_thought_signature_stays_none(self) -> None:
        ai_message = SimpleNamespace(
            content="",
            tool_calls=[
                {"id": "tc_1", "name": "bash", "args": {"command": "pwd"}},
                {"id": "tc_2", "name": "read_file", "args": {"path": "README.md"}},
            ],
            additional_kwargs={
                "tool_call_thought_signatures": [
                    {"id": "tc_1", "index": 0, "thought_signature": "sig-a"},
                ],
            },
            response_metadata={"finish_reason": "tool_calls"},
        )

        response = ChatLLM._parse_response(ai_message)

        assert response.tool_calls[0].thought_signature == "sig-a"
        assert response.tool_calls[1].thought_signature is None


class TestDedupeFinishReason:
    """OpenRouter-style relays emit finish_reason on every stream chunk;
    AIMessageChunk.__add__ concatenates them into 'stopstop', etc. ReAct
    loop uses finish_reason for exit decisions, so equality must survive."""

    def test_clean_values_unchanged(self) -> None:
        assert _dedupe_finish_reason("stop") == "stop"
        assert _dedupe_finish_reason("tool_calls") == "tool_calls"

    def test_duplicated_dedupes(self) -> None:
        assert _dedupe_finish_reason("stopstop") == "stop"
        assert _dedupe_finish_reason("stopstopstop") == "stop"
        assert _dedupe_finish_reason("tool_callstool_calls") == "tool_calls"

    def test_suffix_match_picks_longest_valid_marker(self) -> None:
        # endswith — "stoptool_calls" ends with "tool_calls"
        assert _dedupe_finish_reason("stoptool_calls") == "tool_calls"

    def test_empty_returns_empty(self) -> None:
        # No marker matches; raw is returned. Callers supply a default upstream.
        assert _dedupe_finish_reason("") == ""

    def test_unknown_marker_passed_through(self) -> None:
        assert _dedupe_finish_reason("custom_reason") == "custom_reason"


class TestContextBuilderToolCallReplay:
    """reasoning_content flows back into the next request's assistant message."""

    def test_format_assistant_tool_calls_preserves_reasoning_content(self) -> None:
        message = ContextBuilder.format_assistant_tool_calls(
            [ToolCallRequest(id="tc_1", name="bash", arguments={"command": "pwd"})],
            content="",
            reasoning_content="step-by-step reasoning",
        )

        assert message["role"] == "assistant"
        assert message["reasoning_content"] == "step-by-step reasoning"
        assert message["tool_calls"][0]["id"] == "tc_1"

    def test_format_assistant_tool_calls_omits_reasoning_when_absent(self) -> None:
        message = ContextBuilder.format_assistant_tool_calls(
            [ToolCallRequest(id="tc_1", name="bash", arguments={"command": "pwd"})],
            content="",
        )

        assert "reasoning_content" not in message


class TestAgentLoopToolCallReplay:
    """Gemini tool-call thought_signature flows back into the next assistant message."""

    def test_attaches_thought_signatures_to_matching_tool_calls(self) -> None:
        tool_calls = [
            ToolCallRequest(
                id="tc_1",
                name="bash",
                arguments={"command": "pwd"},
                thought_signature="sig-a",
            ),
            ToolCallRequest(
                id="tc_2",
                name="read_file",
                arguments={"path": "README.md"},
                thought_signature="sig-b",
            ),
        ]
        message = ContextBuilder.format_assistant_tool_calls(tool_calls, content="")

        _attach_tool_call_thought_signatures(message, tool_calls)

        assert message["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig-a"
        assert message["tool_calls"][1]["extra_content"]["google"]["thought_signature"] == "sig-b"

    def test_unsigned_tool_calls_are_left_untouched(self) -> None:
        tool_calls = [
            ToolCallRequest(
                id="tc_1",
                name="bash",
                arguments={"command": "pwd"},
                thought_signature="sig-a",
            ),
            ToolCallRequest(id="tc_2", name="read_file", arguments={"path": "README.md"}),
        ]
        message = ContextBuilder.format_assistant_tool_calls(tool_calls, content="")

        _attach_tool_call_thought_signatures(message, tool_calls)

        assert message["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig-a"
        assert "extra_content" not in message["tool_calls"][1]


class _FakeCompletions:
    """Stand-in for ``client.chat.completions`` returning a canned payload."""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.last_kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return self._result


class _FakeClient:
    def __init__(self, result: Any) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(result))


def _message(
    content: str = "",
    reasoning_content: Any = None,
    tool_calls: Any = None,
    model_extra: Any = None,
) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    if reasoning_content is not None:
        msg.reasoning_content = reasoning_content
    if model_extra is not None:
        msg.model_extra = model_extra
    return msg


def _tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _response(message: Any, finish_reason: str = "stop", usage: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage,
    )


def _client_for(instance: Any, result: Any) -> Any:
    instance._client = _FakeClient(result)
    return instance


class TestOpenAICompatClientNonStreaming:
    """``invoke`` path: reasoning capture, tool-call parsing, usage/finish_reason."""

    def _instance(self, model: str = "kimi-k2-thinking") -> Any:
        os.environ.setdefault("OPENAI_API_KEY", "sk-test")
        caps = get_provider_capabilities("moonshot", model)
        try:
            return OpenAICompatClient(model=model, api_key="sk-test", caps=caps)
        except RuntimeError:  # pragma: no cover - openai SDK absent
            pytest.skip("the 'openai' package is not installed")

    def test_preserves_reasoning_from_typed_attribute(self) -> None:
        instance = self._instance()
        _client_for(instance, _response(_message(reasoning_content="thinking step")))

        result = instance.invoke([{"role": "user", "content": "hi"}])

        assert result.additional_kwargs["reasoning_content"] == "thinking step"

    def test_preserves_reasoning_from_model_extra_fallback(self) -> None:
        """Some ``openai`` SDK versions only surface it via ``model_extra``."""
        instance = self._instance()
        _client_for(
            instance,
            _response(_message(model_extra={"reasoning_content": "extra reasoning"})),
        )

        result = instance.invoke([{"role": "user", "content": "hi"}])

        assert result.additional_kwargs["reasoning_content"] == "extra reasoning"

    def test_no_reasoning_leaves_additional_kwargs_empty(self) -> None:
        instance = self._instance()
        _client_for(instance, _response(_message(content="plain answer")))

        result = instance.invoke([{"role": "user", "content": "hi"}])

        assert result.content == "plain answer"
        assert "reasoning_content" not in result.additional_kwargs

    def test_parses_tool_calls_with_json_arguments(self) -> None:
        instance = self._instance()
        _client_for(
            instance,
            _response(
                _message(tool_calls=[_tool_call("c1", "get_price", '{"symbol": "AAPL"}')]),
                finish_reason="tool_calls",
            ),
        )

        result = instance.invoke([{"role": "user", "content": "price?"}])

        assert result.tool_calls == [
            {"id": "c1", "name": "get_price", "args": {"symbol": "AAPL"}}
        ]
        assert result.response_metadata["finish_reason"] == "tool_calls"

    def test_malformed_tool_arguments_degrade_to_empty_dict(self) -> None:
        """A truncated stream must not crash the loop with a JSONDecodeError."""
        instance = self._instance()
        _client_for(
            instance,
            _response(_message(tool_calls=[_tool_call("c1", "get_price", '{"symbol":')])),
        )

        result = instance.invoke([{"role": "user", "content": "price?"}])

        assert result.tool_calls[0]["args"] == {}

    def test_propagates_usage_metadata(self) -> None:
        instance = self._instance()
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=5, total_tokens=16)
        _client_for(instance, _response(_message(content="ok"), usage=usage))

        result = instance.invoke([{"role": "user", "content": "hi"}])

        assert result.usage_metadata == {
            "input_tokens": 11,
            "output_tokens": 5,
            "total_tokens": 16,
        }


def _delta_chunk(
    content: str = "",
    reasoning_content: Any = None,
    tool_calls: Any = None,
    finish_reason: Any = None,
    usage: Any = None,
) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=usage,
    )


def _stream_tool_call(index: int, call_id: Any, name: Any, arguments: Any) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class TestOpenAICompatClientStreaming:
    """``stream`` path — the route swarm workers take via ``ChatLLM.stream_chat``."""

    def _instance(self, model: str = "kimi-k2-thinking") -> Any:
        os.environ.setdefault("OPENAI_API_KEY", "sk-test")
        caps = get_provider_capabilities("moonshot", model)
        try:
            return OpenAICompatClient(model=model, api_key="sk-test", caps=caps)
        except RuntimeError:  # pragma: no cover - openai SDK absent
            pytest.skip("the 'openai' package is not installed")

    def test_preserves_reasoning_on_streaming_delta(self) -> None:
        instance = self._instance()
        _client_for(instance, [_delta_chunk(reasoning_content="thinking step")])

        chunks = list(instance.stream([{"role": "user", "content": "hi"}]))

        assert any(
            c.additional_kwargs.get("reasoning_content") == "thinking step" for c in chunks
        )

    def test_streaming_chunks_accumulate_reasoning_across_chunks(self) -> None:
        """``AIMessageChunk.__add__`` concatenates reasoning from every delta."""
        instance = self._instance()
        _client_for(
            instance,
            [
                _delta_chunk(reasoning_content="first "),
                _delta_chunk(reasoning_content="second "),
                _delta_chunk(reasoning_content="third"),
            ],
        )

        accumulated = None
        for chunk in instance.stream([{"role": "user", "content": "hi"}]):
            accumulated = chunk if accumulated is None else accumulated + chunk

        assert accumulated is not None
        assert accumulated.additional_kwargs["reasoning_content"] == "first second third"

    def test_streaming_accumulates_visible_content(self) -> None:
        instance = self._instance()
        _client_for(
            instance,
            [_delta_chunk(content="Hel"), _delta_chunk(content="lo "), _delta_chunk(content="world")],
        )

        accumulated = None
        for chunk in instance.stream([{"role": "user", "content": "hi"}]):
            accumulated = chunk if accumulated is None else accumulated + chunk

        assert accumulated is not None
        assert accumulated.content == "Hello world"

    def test_streaming_assembles_fragmented_tool_call_arguments(self) -> None:
        """Providers split ``arguments`` across deltas; the final chunk must parse."""
        instance = self._instance()
        _client_for(
            instance,
            [
                _delta_chunk(tool_calls=[_stream_tool_call(0, "c1", "get_price", '{"sym')]),
                _delta_chunk(tool_calls=[_stream_tool_call(0, None, None, 'bol": "AAPL"}')]),
                _delta_chunk(finish_reason="tool_calls"),
            ],
        )

        chunks = list(instance.stream([{"role": "user", "content": "price?"}]))

        final = chunks[-1]
        assert final.tool_calls == [
            {"id": "c1", "name": "get_price", "args": {"symbol": "AAPL"}}
        ]
        assert final.response_metadata["finish_reason"] == "tool_calls"

    def test_streaming_final_chunk_carries_usage(self) -> None:
        instance = self._instance()
        usage = SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)
        _client_for(
            instance,
            [_delta_chunk(content="ok", finish_reason="stop"), _delta_chunk(usage=usage)],
        )

        chunks = list(instance.stream([{"role": "user", "content": "hi"}]))

        assert chunks[-1].usage_metadata == {
            "input_tokens": 7,
            "output_tokens": 3,
            "total_tokens": 10,
        }


class TestOpenAICompatClientOutboundPayload:
    """``_prepare_messages`` path: capability-driven normalization of outbound
    assistant history (issue #39 round-trip).

    ``ContextBuilder`` emits assistant dicts that carry internal-only fields
    (``additional_kwargs``) plus provider-specific ones (``reasoning_content``,
    ``tool_calls[].extra_content``). The client must send exactly what the
    target provider accepts — no more, no less.
    """

    def _instance(
        self,
        model: str = "kimi-k2-0905-preview",
        provider: str = "moonshot",
    ) -> Any:
        os.environ.setdefault("OPENAI_API_KEY", "sk-test")
        caps = get_provider_capabilities(provider, model)
        try:
            return OpenAICompatClient(model=model, api_key="sk-test", caps=caps)
        except RuntimeError:  # pragma: no cover - openai SDK absent
            pytest.skip("the 'openai' package is not installed")

    @staticmethod
    def _assistant(payload_messages: list) -> dict:
        return next(m for m in payload_messages if m["role"] == "assistant")

    def test_preserves_reasoning_content_for_kimi(self) -> None:
        """Kimi requires the reasoning trace to be replayed on continuations."""
        instance = self._instance()
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "I should call a tool",
                "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "t", "arguments": "{}"}},
                ],
            },
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert assistant_msg["reasoning_content"] == "I should call a tool"

    def test_normalizes_none_content_on_assistant_messages(self) -> None:
        """Moonshot kimi-k2.6 rejects assistant turns with null content."""
        instance = self._instance()
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "t", "arguments": "{}"}},
                ],
            },
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert assistant_msg["content"] == ""
        assert "extra_content" not in assistant_msg["tool_calls"][0]

    def test_injects_empty_reasoning_content_when_absent(self) -> None:
        """kimi-k2.6 requires reasoning_content on every assistant turn."""
        instance = self._instance()
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "plain assistant reply"},
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert assistant_msg["reasoning_content"] == ""

    def test_openai_does_not_inject_empty_reasoning_content(self) -> None:
        """Strict Kimi continuation fields must not leak into OpenAI payloads."""
        instance = self._instance(model="gpt-4", provider="openai")
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "plain assistant reply"},
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert "reasoning_content" not in assistant_msg

    def test_deepseek_does_not_replay_reasoning_content_outbound(self) -> None:
        """DeepSeek reasoning traces are inbound progress, not next-turn payload."""
        instance = self._instance(model="deepseek-v4-pro", provider="deepseek")
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "internal reasoning",
            },
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert "reasoning_content" not in assistant_msg

    def test_user_and_system_messages_untouched(self) -> None:
        """Only assistant messages get the reasoning_content injection."""
        instance = self._instance()
        history = [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]

        prepared = instance._prepare_messages(history)
        for m in prepared:
            assert "reasoning_content" not in m
        assert prepared == history

    def test_additional_kwargs_never_sent_over_the_wire(self) -> None:
        """``additional_kwargs`` is an internal carrier, not OpenAI wire format."""
        instance = self._instance()
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "t", "arguments": "{}"}},
                ],
                "additional_kwargs": {"tool_calls": [{"id": "c1"}]},
            },
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert "additional_kwargs" not in assistant_msg

    def test_prepare_messages_does_not_mutate_caller_history(self) -> None:
        """The agent loop keeps reusing its history list across turns."""
        instance = self._instance(model="gpt-4", provider="openai")
        assistant = {
            "role": "assistant",
            "content": "",
            "reasoning_content": "keep me in history",
            "additional_kwargs": {"tool_calls": []},
        }
        history = [{"role": "user", "content": "hi"}, assistant]

        instance._prepare_messages(history)

        assert assistant["reasoning_content"] == "keep me in history"
        assert "additional_kwargs" in assistant

    def test_gemini_keeps_tool_call_thought_signature(self) -> None:
        """Gemini 400s on the next turn when the signature is not echoed back."""
        from src.agent.context import ContextBuilder
        from src.agent.loop import _attach_tool_call_thought_signatures
        from src.providers.chat import ToolCallRequest

        instance = self._instance(model="gemini-3-pro-preview", provider="gemini")
        tool_calls = [
            ToolCallRequest(id="c1", name="load_skill",
                            arguments={"name": "momentum"}, thought_signature="sig-a"),
        ]
        assistant = ContextBuilder.format_assistant_tool_calls(tool_calls)
        _attach_tool_call_thought_signatures(assistant, tool_calls)
        history = [
            {"role": "user", "content": "load momentum"},
            assistant,
            {"role": "tool", "tool_call_id": "c1", "content": "loaded"},
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        signature = assistant_msg["tool_calls"][0]["extra_content"]["google"]["thought_signature"]
        assert signature == "sig-a"

    def test_gemini_parallel_calls_keep_first_signature_only(self) -> None:
        """Gemini signs only the first of N parallel calls; never fabricate the rest."""
        from src.agent.context import ContextBuilder
        from src.agent.loop import _attach_tool_call_thought_signatures
        from src.providers.chat import ToolCallRequest

        instance = self._instance(model="gemini-3-pro-preview", provider="gemini")
        tool_calls = [
            ToolCallRequest(id="c1", name="load_skill", arguments={"name": "a"},
                            thought_signature="sig-a"),
            ToolCallRequest(id="c2", name="load_skill", arguments={"name": "b"},
                            thought_signature=None),
            ToolCallRequest(id="c3", name="load_skill", arguments={"name": "c"},
                            thought_signature=None),
        ]
        assistant = ContextBuilder.format_assistant_tool_calls(tool_calls)
        _attach_tool_call_thought_signatures(assistant, tool_calls)
        history = [
            {"role": "user", "content": "load a, b, c"},
            assistant,
        ]

        tcs = self._assistant(instance._prepare_messages(history))["tool_calls"]
        assert tcs[0]["extra_content"]["google"]["thought_signature"] == "sig-a"
        assert "extra_content" not in tcs[1]
        assert "extra_content" not in tcs[2]

    def test_non_gemini_strips_tool_call_extra_content(self) -> None:
        """Gemini thought signatures must be Gemini-only payload mutations."""
        instance = self._instance(model="gpt-4", provider="openai")
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "extra_content": {"google": {"thought_signature": "sig-a"}},
                        "function": {"name": "t", "arguments": "{}"},
                    },
                ],
            },
        ]

        assistant_msg = self._assistant(instance._prepare_messages(history))
        assert "extra_content" not in assistant_msg["tool_calls"][0]
