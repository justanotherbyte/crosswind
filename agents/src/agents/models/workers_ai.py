"""`WorkersAIModel`: chat completions from a Workers AI binding.

Workers AI chat models answer in one of two shapes: ``response`` with
``tool_calls`` (flat or nested under ``function``), or an OpenAI-style
``choices[0].message``. `Completion` parses both.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Self, TypedDict

from .. import _ffi
from ..core.types import JSONValue

__all__ = (
    "ChatMessage",
    "Completion",
    "FunctionCall",
    "FunctionSchema",
    "ToolCall",
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
    """The model's answer: text, tool calls, or both."""

    text: str
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
            tool_calls=tuple(
                ToolCall.from_output(call) for call in message.get("tool_calls") or ()
            ),
            usage=Usage.from_output(data.get("usage")),
        )


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

    Examples
    --------
    >>> model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
    >>> completion = await model.complete([{"role": "user", "content": "Hi"}])
    """

    def __init__(self, name: str, *, binding: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.name = name
        """The model id."""
        self.binding = binding
        """The ``ai`` binding."""

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
        if not messages:
            raise ValueError("complete() needs at least one message")

        payload: dict[str, Any] = {**(options or {}), "messages": list(messages)}
        if tools:
            payload["tools"] = list(tools)

        output = await self.binding.run(self.name, _ffi.py_to_js(payload))
        return Completion.from_output(_ffi.js_to_py(output))
