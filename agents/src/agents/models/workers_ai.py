"""`WorkersAIModel`: chat completions from a Workers AI binding.

Workers AI chat models answer in one of two shapes: ``response`` with
``tool_calls`` (flat or nested under ``function``), or an OpenAI-style
``choices[0].message``. `Completion` parses both. Streamed answers are
server-sent events of OpenAI-style ``choices[0].delta`` chunks, ending in a
chunk with the request's ``usage`` and ``[DONE]``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, Literal, NotRequired, Self, TypedDict

from .. import _ffi
from ..core.sse import iter_events
from ..core.types import JSONValue

__all__ = (
    "ChatMessage",
    "Completion",
    "CompletionEvent",
    "FunctionCall",
    "FunctionSchema",
    "ReasoningDelta",
    "TextDelta",
    "ToolCall",
    "ToolCallDelta",
    "ToolCallPayload",
    "ToolSchema",
    "Usage",
    "WorkersAIModel",
)


class FunctionCall(TypedDict):
    """The function half of an assistant message's tool call."""

    name: str
    arguments: str


class ToolCallPayload(TypedDict):
    """A tool call as an assistant message carries it back to the model."""

    id: str
    type: Literal["function"]
    function: FunctionCall


class ChatMessage(TypedDict):
    """One message of the conversation sent to the model."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: NotRequired[str]
    tool_call_id: NotRequired[str]
    tool_calls: NotRequired[list[ToolCallPayload]]


class FunctionSchema(TypedDict):
    """A tool's name, description, and JSON schema for its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolSchema(TypedDict):
    """A tool the model may call."""

    type: Literal["function"]
    function: FunctionSchema


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolCall:
    """A tool call the model made; ``id`` is generated if the model sent none."""

    id: str
    name: str
    arguments: dict[str, JSONValue]

    @classmethod
    def from_output(cls, data: Mapping[str, Any]) -> Self:
        """Parse a flat ``{name, arguments}`` or nested ``{id, function}`` call."""
        function = data.get("function", data)
        arguments = function.get("arguments") or {}

        return cls(
            id=data.get("id") or f"call_{uuid.uuid4().hex}",
            name=function["name"],
            arguments=json.loads(arguments)
            if isinstance(arguments, str)
            else arguments,
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class Usage:
    """Token counts for one request."""

    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_output(cls, data: Mapping[str, Any] | None) -> Self:
        """Parse a ``usage`` object; ``None`` counts nothing."""
        if data is None:
            return cls()

        return cls(
            input_tokens=int(data.get("prompt_tokens", 0)),
            output_tokens=int(data.get("completion_tokens", 0)),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class Completion:
    """The model's answer: text, tool calls, or both, and any reasoning."""

    text: str
    reasoning: str = ""
    tool_calls: Sequence[ToolCall] = ()
    usage: Usage = Usage()

    @classmethod
    def from_output(cls, data: Mapping[str, Any]) -> Self:
        """Parse either output shape a Workers AI chat model returns."""
        if "choices" in data:
            message = data["choices"][0]["message"]
            text = message.get("content")
        else:
            message = data
            text = data.get("response")

        return cls(
            text=_as_text(text),
            reasoning=message.get("reasoning_content")
            or message.get("reasoning")
            or "",
            tool_calls=tuple(
                ToolCall.from_output(call) for call in message.get("tool_calls") or ()
            ),
            usage=Usage.from_output(data.get("usage")),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class TextDelta:
    """The next piece of the answer's text."""

    text: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ReasoningDelta:
    """The next piece of the model's reasoning."""

    text: str


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolCallDelta:
    """The next piece of the tool call at ``index``.

    The first piece of a call carries its ``id`` and ``name``; later pieces
    carry only more of the JSON ``arguments``.
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""

    @classmethod
    def from_output(cls, data: Mapping[str, Any]) -> Self:
        """Parse one entry of a chunk's ``delta.tool_calls``."""
        function = data.get("function") or {}

        return cls(
            index=data.get("index", 0),
            id=data.get("id"),
            name=function.get("name"),
            arguments=_as_text(function.get("arguments")),
        )


type CompletionEvent = TextDelta | ReasoningDelta | ToolCallDelta | Usage
"""What `WorkersAIModel.stream` yields; `Usage` counts the whole request."""


def _chunk_events(data: Mapping[str, Any]) -> Iterator[CompletionEvent]:
    if "choices" not in data:
        if text := _as_text(data.get("response")):
            yield TextDelta(text=text)
        if (usage := data.get("usage")) is not None:
            yield Usage.from_output(usage)
        return

    for choice in data["choices"][:1]:
        delta = choice.get("delta") or {}
        if reasoning := delta.get("reasoning_content") or delta.get("reasoning"):
            yield ReasoningDelta(text=reasoning)
        if text := delta.get("content"):
            yield TextDelta(text=text)
        for call in delta.get("tool_calls") or ():
            yield ToolCallDelta.from_output(call)


def _as_text(value: object) -> str:
    if value is None:
        return ""

    return value if isinstance(value, str) else json.dumps(value)


class WorkersAIModel:
    """A Workers AI chat model, called through the ``ai`` binding.

    Parameters
    ----------
    name
        The model id, like ``"@cf/meta/llama-4-scout-17b-16e-instruct"``.
    binding
        The ``ai`` binding (``env.AI``).
    clean_text
        Rewrites the text of each answer `complete` returns, like
        ``str.strip`` for a model that starts its answers with blank lines.
        ``None`` keeps the text as the model sent it. `stream` doesn't call
        it, since it needs the whole text.

    Examples
    --------
    >>> model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
    >>> completion = await model.complete([{"role": "user", "content": "Hi"}])
    """

    def __init__(
        self,
        name: str,
        *,
        binding: Any,
        clean_text: Callable[[str], str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.name = name
        """The model id."""
        self.binding = binding
        """The ``ai`` binding."""
        self.clean_text = clean_text
        """Rewrites the text of each answer; ``None`` keeps it as sent."""

    def __repr__(self) -> str:  # noqa: D105
        return f"<{type(self).__name__} name={self.name!r}>"

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        options: Mapping[str, object] | None = None,
    ) -> Completion:
        """Run the model on a conversation and return its answer.

        Parameters
        ----------
        messages
            The conversation, oldest first.
        tools
            The tools the model may call.
        options
            Extra inputs such as ``max_tokens`` or ``temperature``.

        Raises
        ------
        ValueError
            If ``messages`` is empty.
        """
        payload = _payload(messages, tools, options)
        output = await self.binding.run(self.name, _ffi.py_to_js(payload))
        completion = Completion.from_output(_ffi.js_to_py(output))
        if self.clean_text is None:
            return completion

        return replace(completion, text=self.clean_text(completion.text))

    @asynccontextmanager
    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        options: Mapping[str, object] | None = None,
    ) -> AsyncGenerator[AsyncGenerator[CompletionEvent]]:
        """Run the model on a conversation and read its answer as it arrives.

        Takes the same arguments as `complete`, and the block iterates over
        `TextDelta`, `ReasoningDelta` and `ToolCallDelta` pieces, then the
        request's `Usage`.
        Leaving the block before the answer ends cancels it.

        Raises
        ------
        ValueError
            If ``messages`` is empty.

        Examples
        --------
        >>> async with model.stream(messages) as events:
        ...     async for event in events:
        ...         if isinstance(event, TextDelta):
        ...             print(event.text, end="")
        """
        payload = {**_payload(messages, tools, options), "stream": True}
        body = await self.binding.run(self.name, _ffi.py_to_js(payload))

        async with _ffi.read_stream(body) as chunks:
            yield _completion_events(chunks)


async def _completion_events(
    chunks: AsyncIterator[bytes],
) -> AsyncGenerator[CompletionEvent]:
    async for event in iter_events(chunks):
        if event.data == "[DONE]":
            return
        for item in _chunk_events(json.loads(event.data)):
            yield item


def _payload(
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolSchema],
    options: Mapping[str, object] | None,
) -> dict[str, Any]:
    if not messages:
        raise ValueError("a model call needs at least one message")

    payload: dict[str, Any] = {**(options or {}), "messages": list(messages)}
    if tools:
        payload["tools"] = list(tools)
    return payload
