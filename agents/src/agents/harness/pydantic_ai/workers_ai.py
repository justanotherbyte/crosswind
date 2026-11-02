"""`WorkersAIModel` as a Pydantic AI model."""

from __future__ import annotations

from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Sequence,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic_ai import (
    BinaryContent,
    CachePoint,
    ImageUrl,
    InstructionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelSettings,
    RequestUsage,
    RetryFeedbackPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolDefinition,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from workers import fetch

from ...models import workers_ai
from ...models.workers_ai import (
    ChatMessage,
    Completion,
    CompletionEvent,
    ContentPart,
    ImageContentPart,
    ReasoningDelta,
    TextDelta,
    ToolCallDelta,
    ToolSchema,
    Usage,
)

if TYPE_CHECKING:
    from pydantic_ai import ModelResponseStreamEvent, RunContext
    from pydantic_ai.profiles import ModelProfileSpec

__all__ = ("WorkersAIModel",)

_PROVIDER = "cloudflare"

_OPTIONS = (
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "presence_penalty",
    "frequency_penalty",
)


class WorkersAIModel(workers_ai.WorkersAIModel, Model):
    """A Workers AI chat model that a Pydantic AI agent can run.

    Prompts may hold images: `BinaryImage` or image `BinaryContent`, and
    `ImageUrl`, which is downloaded because Workers AI takes only ``data:``
    URLs. Other files in a prompt raise `TypeError`. A tool's images reach
    the model in a user message after the tool results, and its other
    files as a note naming them. Streamed requests stream text, reasoning
    and tool calls token by token, and skip whitespace-only text ahead of
    the answer.

    Parameters
    ----------
    name
        The model id, like ``"@cf/qwen/qwen3-30b-a3b-fp8"``.
    binding
        The ``ai`` binding (``env.AI``).
    settings
        Default settings; ``max_tokens``, ``temperature``, ``top_p``,
        ``top_k``, ``seed``, ``presence_penalty`` and ``frequency_penalty``
        reach the model.
    profile
        The Pydantic AI model profile.
    clean_text
        Rewrites the text of each answer before Pydantic AI sees it.
        ``None`` keeps the text as the model sent it. Streamed requests
        don't call it, since it needs the whole text.

    Examples
    --------
    >>> model = WorkersAIModel(
    ...     "@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI, clean_text=str.strip
    ... )
    >>> agent = pydantic_ai.Agent(model, instructions="Be brief.")
    """

    _unsupported_file_inputs: ClassVar[
        frozenset[Literal["image", "document", "audio", "video"]]
    ] = frozenset({"document", "audio", "video"})

    def __init__(
        self,
        name: str,
        *,
        binding: Any,
        settings: ModelSettings | None = None,
        profile: ModelProfileSpec | None = None,
        clean_text: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(
            name,
            binding=binding,
            clean_text=clean_text,
            settings=settings,
            profile=profile,
        )

    @property
    def model_name(self) -> str:
        """The model id."""
        return self.name

    @property
    def system(self) -> str:
        """The provider, for telemetry."""
        return _PROVIDER

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Send the conversation to Workers AI and return its answer."""
        settings, parameters = self.prepare_request(
            model_settings, model_request_parameters
        )

        chat, tools, options = await _to_inputs(messages, settings, parameters)
        completion = await self.complete(chat, tools=tools, options=options)
        return _to_model_response(completion, self.name)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Send the conversation to Workers AI and stream its answer."""
        settings, parameters = self.prepare_request(
            model_settings, model_request_parameters
        )

        chat, tools, options = await _to_inputs(messages, settings, parameters)
        async with self.stream(chat, tools=tools, options=options) as events:
            yield _StreamedResponse(
                model_request_parameters=parameters,
                events=events,
                name=self.name,
            )


@dataclass
class _StreamedResponse(StreamedResponse):
    events: AsyncGenerator[CompletionEvent]
    name: str
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    _usage_received: bool | None = field(default=False, init=False)

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        async for event in self.events:
            match event:
                case TextDelta(text=text):
                    for part_event in self._parts_manager.handle_text_delta(
                        vendor_part_id="content",
                        content=text,
                        ignore_leading_whitespace=True,
                    ):
                        yield part_event
                case ReasoningDelta(text=text):
                    for part_event in self._parts_manager.handle_thinking_delta(
                        vendor_part_id="reasoning",
                        content=text,
                        provider_name=_PROVIDER,
                    ):
                        yield part_event
                case ToolCallDelta():
                    part_event = self._parts_manager.handle_tool_call_delta(
                        vendor_part_id=event.index,
                        tool_name=event.name,
                        args=event.arguments or None,
                        tool_call_id=event.id,
                    )
                    if part_event is not None:
                        yield part_event
                case Usage():
                    self._add_usage(
                        _to_request_usage(event), reported=True, cumulative=True
                    )

    async def close_stream(self) -> None:
        """Stop reading; leaving `request_stream` then cancels the answer."""
        await self.events.aclose()

    @property
    def model_name(self) -> str:
        """The model id."""
        return self.name

    @property
    def provider_name(self) -> str:
        """The provider."""
        return _PROVIDER

    @property
    def provider_url(self) -> None:
        """Workers AI is reached through a binding, not a URL."""
        return None

    @property
    def timestamp(self) -> datetime:
        """When the request started."""
        return self.started


async def _to_inputs(
    messages: Sequence[ModelMessage],
    settings: ModelSettings | None,
    parameters: ModelRequestParameters,
) -> tuple[list[ChatMessage], list[ToolSchema], dict[str, object]]:
    instructions = InstructionPart.join(parameters.instruction_parts or [])

    return (
        await _to_chat_messages(messages, instructions),
        [_to_tool_schema(tool) for tool in parameters.tool_defs.values()],
        _to_options(settings),
    )


async def _to_chat_messages(
    messages: Sequence[ModelMessage], instructions: str | None
) -> list[ChatMessage]:
    chat = [item for message in messages for item in await _chat_messages(message)]
    if instructions:
        opening = next(
            (i for i, item in enumerate(chat) if item["role"] != "system"), len(chat)
        )
        chat.insert(opening, {"role": "system", "content": instructions})
    return chat


async def _chat_messages(message: ModelMessage) -> list[ChatMessage]:
    if isinstance(message, ModelRequest):
        return await _request_messages(message)
    return [_response_message(message)]


async def _request_messages(request: ModelRequest) -> list[ChatMessage]:
    chat: list[ChatMessage] = []
    files: list[UserContent] = []
    for part in request.parts:
        match part:
            case SystemPromptPart(content=content):
                chat.append({"role": "system", "content": content})
            case UserPromptPart(content=content):
                chat.append({"role": "user", "content": await _user_content(content)})
            case ToolReturnPart():
                text, returned = part.model_response_str_and_user_content()
                files.extend(returned)
                chat.append(
                    {
                        "role": "tool",
                        "name": part.tool_name,
                        "tool_call_id": part.tool_call_id,
                        "content": text,
                    }
                )
            case RetryFeedbackPart():
                chat.append({"role": "user", "content": part.model_response()})

    if files:
        chat.append({"role": "user", "content": await _user_content(files)})
    return chat


async def _user_content(
    content: str | Sequence[UserContent],
) -> str | list[ContentPart]:
    if isinstance(content, str):
        return content

    parts = [part for item in content if (part := await _content_part(item))]
    if any(part["type"] == "image_url" for part in parts):
        return parts
    return "\n".join(part["text"] for part in parts if part["type"] == "text")


async def _content_part(item: UserContent) -> ContentPart | None:
    match item:
        case str():
            return {"type": "text", "text": item}
        case TextContent(content=text):
            return {"type": "text", "text": text}
        case CachePoint():
            return None
        case BinaryContent() if item.is_image:
            return _image_part(item.data_uri)
        case ImageUrl():
            return _image_part(await _download(item))
        case _:
            raise TypeError(
                "WorkersAIModel sends text and images only, not"
                f" {type(item).__name__} ({item.media_type})"
            )


async def _download(image: ImageUrl) -> str:
    response = await fetch(image.url)
    if not response.ok:
        raise ValueError(f"Downloading {image.url} failed: HTTP {response.status}")

    header = (response.headers.get("content-type") or "").split(";")[0].strip()
    media_type = header if header.startswith("image/") else image.media_type
    return BinaryContent(await response.bytes(), media_type=media_type).data_uri


def _image_part(url: str) -> ImageContentPart:
    return {"type": "image_url", "image_url": {"url": url}}


def _response_message(response: ModelResponse) -> ChatMessage:
    message: ChatMessage = {
        "role": "assistant",
        "content": "".join(
            part.content for part in response.parts if isinstance(part, TextPart)
        ),
    }
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.tool_call_id,
                "type": "function",
                "function": {
                    "name": call.tool_name,
                    "arguments": call.args_as_json_str(),
                },
            }
            for call in response.tool_calls
        ]
    return message


def _to_tool_schema(tool: ToolDefinition) -> ToolSchema:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.parameters_json_schema,
        },
    }


def _to_options(settings: ModelSettings | None) -> dict[str, object]:
    if settings is None:
        return {}

    return {key: value for key, value in settings.items() if key in _OPTIONS}


def _to_model_response(completion: Completion, name: str) -> ModelResponse:
    thinking = (
        [ThinkingPart(completion.reasoning, provider_name=_PROVIDER)]
        if completion.reasoning
        else []
    )
    text = [TextPart(completion.text)] if completion.text else []
    calls = [
        ToolCallPart(call.name, call.arguments, tool_call_id=call.id)
        for call in completion.tool_calls
    ]

    return ModelResponse(
        parts=[*thinking, *text, *calls],
        usage=_to_request_usage(completion.usage),
        model_name=name,
        provider_name=_PROVIDER,
    )


def _to_request_usage(usage: Usage) -> RequestUsage:
    return RequestUsage(
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
    )
