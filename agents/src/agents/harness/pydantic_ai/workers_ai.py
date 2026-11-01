"""`WorkersAIModel` as a Pydantic AI model."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
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
    ContentPart,
    ImageContentPart,
    ToolSchema,
)

if TYPE_CHECKING:
    from pydantic_ai import ModelResponsePart, ModelResponseStreamEvent, RunContext
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
    files as a note naming them. Streamed requests return the whole answer
    as one chunk.

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

    Examples
    --------
    >>> model = WorkersAIModel("@cf/qwen/qwen3-30b-a3b-fp8", binding=env.AI)
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
    ) -> None:
        super().__init__(name, binding=binding, settings=settings, profile=profile)

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

        return await self._respond(messages, settings, parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Run `request` and replay its answer as a stream of one chunk."""
        settings, parameters = self.prepare_request(
            model_settings, model_request_parameters
        )

        response = await self._respond(messages, settings, parameters)
        yield _ReplayedResponse(model_request_parameters=parameters, response=response)

    async def _respond(
        self,
        messages: Sequence[ModelMessage],
        settings: ModelSettings | None,
        parameters: ModelRequestParameters,
    ) -> ModelResponse:
        instructions = InstructionPart.join(parameters.instruction_parts or [])
        completion = await self.complete(
            await _to_chat_messages(messages, instructions),
            tools=[_to_tool_schema(tool) for tool in parameters.tool_defs.values()],
            options=_to_options(settings),
        )

        return _to_model_response(completion, self.name)


@dataclass
class _ReplayedResponse(StreamedResponse):
    response: ModelResponse

    def __post_init__(self) -> None:
        self._usage = self.response.usage

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        for index, part in enumerate(self.response.parts):
            for event in self._events(index, part):
                yield event

    def _events(
        self, index: int, part: ModelResponsePart
    ) -> Iterator[ModelResponseStreamEvent]:
        if isinstance(part, TextPart):
            yield from self._parts_manager.handle_text_delta(
                vendor_part_id=index, content=part.content
            )
        else:
            yield self._parts_manager.handle_part(vendor_part_id=index, part=part)

    @property
    def model_name(self) -> str:
        """The model id."""
        return self.response.model_name or ""

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
        """When the answer arrived."""
        return self.response.timestamp


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
    text = [TextPart(completion.text)] if completion.text else []
    calls = [
        ToolCallPart(call.name, call.arguments, tool_call_id=call.id)
        for call in completion.tool_calls
    ]

    return ModelResponse(
        parts=[*text, *calls],
        usage=RequestUsage(
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
        ),
        model_name=name,
        provider_name=_PROVIDER,
    )
