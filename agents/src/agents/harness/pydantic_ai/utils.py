"""Helpers on Pydantic AI message histories, and the transcript they map to."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    ImageUrl,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    NativeToolCallPart,
    NativeToolReturnPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
    VideoUrl,
)
from pydantic_ai import FilePart as ModelFilePart
from pydantic_ai import TextPart as ModelTextPart
from pydantic_ai.messages import BaseToolCallPart, BaseToolReturnPart
from pydantic_core import to_jsonable_python

from ..store import is_answers
from ..transcript import (
    DataPart,
    FilePart,
    JSONObject,
    MessagePart,
    ReasoningPart,
    TextPart,
    ToolApproval,
    ToolPart,
)
from ..types import FileInput, HarnessInput, TextInput

__all__ = (
    "INVALID_INPUT",
    "MAX_FILE_URL",
    "dump_messages",
    "file_part",
    "invalid_args",
    "load_messages",
    "pending_calls",
    "reply_metadata",
    "to_reply",
    "user_prompt",
)

MAX_FILE_URL = 768 * 1024
"""The longest ``data:`` URL a file part carries. Streams caps a chunk at
1 MiB, and base64 inflates a file by about a third; a larger file is dropped
for a ``dropped-file`` data part."""

INVALID_INPUT = "The tool input isn't valid JSON."


def load_messages(data: bytes | None) -> list[ModelMessage]:
    """Parse a stored history; ``None`` is an empty one."""
    if data is None:
        return []

    return ModelMessagesTypeAdapter.validate_json(data)


def dump_messages(messages: Sequence[ModelMessage]) -> bytes:
    """Serialize a history for storage."""
    return ModelMessagesTypeAdapter.dump_json(list(messages))


def pending_calls(messages: Sequence[ModelMessage]) -> list[ToolCallPart]:
    """Return the last response's tool calls that have no result yet."""
    answered: set[str] = set()
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            return [
                call for call in message.tool_calls if call.tool_call_id not in answered
            ]

        answered.update(
            part.tool_call_id
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
    return []


def user_prompt(input: HarnessInput) -> str | list[UserContent] | None:
    """Return the prompt for a turn's input; tool answers have none."""
    if isinstance(input, str):
        return input
    if is_answers(input):
        return None

    return [
        part.text if isinstance(part, TextInput) else _user_file(part)
        for part in input
        if isinstance(part, TextInput | FileInput)
    ]


def _user_file(part: FileInput) -> UserContent:
    if part.url.startswith("data:"):
        return BinaryContent.from_data_uri(part.url)

    kind = part.media_type.partition("/")[0]
    cls = {"image": ImageUrl, "audio": AudioUrl, "video": VideoUrl}.get(
        kind, DocumentUrl
    )
    return cls(url=part.url, media_type=part.media_type)


def to_reply(messages: Sequence[ModelMessage]) -> list[MessagePart]:
    """Return the assistant message's parts for the turn ending ``messages``.

    The turn is everything after the last request with a user prompt, so a
    continuation after tool answers maps the whole turn again. Tool results
    update their call's part; a retried call is an error, and the model's
    retry is a new part. Citations, output-validation feedback
    (``RetryFeedbackPart``), and Pydantic AI's internal parts (compaction,
    instructions, capability and settings deltas) are dropped.
    """
    parts: list[MessagePart] = []
    calls: dict[str, ToolPart] = {}
    for message in _turn(messages):
        if isinstance(message, ModelResponse):
            for part in message.parts:
                _add_response_part(parts, calls, part)
            continue

        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                _set_result(calls.get(part.tool_call_id), part)
    return parts


def reply_metadata(messages: Sequence[ModelMessage]) -> JSONObject | None:
    """Return the turn's last response's model name, usage, and finish reason."""
    response = next(
        (m for m in reversed(_turn(messages)) if isinstance(m, ModelResponse)), None
    )
    if response is None:
        return None

    usage = {
        key: value for key, value in to_jsonable_python(response.usage).items() if value
    }
    metadata: JSONObject = {
        "modelName": response.model_name,
        "usage": usage or None,
        "finishReason": response.finish_reason,
    }
    return {key: value for key, value in metadata.items() if value is not None}


def _turn(messages: Sequence[ModelMessage]) -> Sequence[ModelMessage]:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        ):
            return messages[index + 1 :]
    return messages


def _add_response_part(
    parts: list[MessagePart], calls: dict[str, ToolPart], part: ModelResponsePart
) -> None:
    match part:
        case ModelTextPart(content=text) if text:
            parts.append(TextPart(text=text))
        case ThinkingPart():
            parts.append(
                ReasoningPart(text=part.content, provider_metadata=_thinking(part))
            )
        case BaseToolCallPart():
            call = _tool_call(part)
            calls[call.tool_call_id] = call
            parts.append(call)
        case NativeToolReturnPart():
            _set_result(calls.get(part.tool_call_id), part)
        case ModelFilePart(content=content):
            parts.append(file_part(content))


def _thinking(part: ThinkingPart) -> JSONObject | None:
    fields = {"signature": part.signature, "providerName": part.provider_name}
    kept: JSONObject = {key: value for key, value in fields.items() if value}
    return {"pydanticAi": kept} if kept else None


def _tool_call(part: BaseToolCallPart) -> ToolPart:
    provider_executed = True if isinstance(part, NativeToolCallPart) else None
    args = invalid_args(part)
    if args is not None:
        return ToolPart(
            tool_call_id=part.tool_call_id,
            tool_name=part.tool_name,
            state="output-error",
            input=args,
            error_text=INVALID_INPUT,
            provider_executed=provider_executed,
        )

    return ToolPart(
        tool_call_id=part.tool_call_id,
        tool_name=part.tool_name,
        state="input-available",
        input=part.args_as_dict(),
        provider_executed=provider_executed,
    )


def invalid_args(part: BaseToolCallPart) -> str | None:
    """Return a tool call's raw arguments if they aren't a JSON object."""
    try:
        part.args_as_dict(raise_if_invalid=True)
    except (ValueError, AssertionError):
        return str(part.args)
    return None


def _set_result(call: ToolPart | None, part: BaseToolReturnPart) -> None:
    if call is None:
        return

    match part.outcome:
        case "success":
            call.state = "output-available"
            call.output = to_jsonable_python(part.content)
        case "denied":
            call.state = "output-denied"
            call.approval = ToolApproval(
                id=call.approval.id if call.approval else call.tool_call_id,
                approved=False,
                reason=part.model_response_str(wrap_if_error=False),
            )
        case _:
            call.state = "output-error"
            call.error_text = part.model_response_str(wrap_if_error=False)


def file_part(content: BinaryContent) -> FilePart | DataPart:
    """Return a model-made file as a part, or a note if it's too large to keep."""
    url = content.data_uri
    if len(url) > MAX_FILE_URL:
        return DataPart(
            name="dropped-file",
            data={"mediaType": content.media_type, "bytes": len(content.data)},
        )
    return FilePart(media_type=content.media_type, url=url)
