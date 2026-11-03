"""`PydanticAIHarness`: a harness whose turns run a Pydantic AI agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    Agent,
    AgentRun,
    AgentRunResult,
    CallToolsNode,
    DeferredToolRequests,
    FunctionToolset,
    ModelMessage,
    ModelRequest,
    ModelRequestNode,
    SystemPromptPart,
    Tool,
)

from ..harness import Harness
from ..store import is_answers
from ..transcript import MessagePart, ToolApproval, ToolPart
from ..types import Approval, ChunkEvent, ToolOutput, ToolRequest, Turn, TurnResult
from .answers import ToolAnswers
from .events import PartIds, to_chunks
from .utils import (
    dump_messages,
    load_messages,
    pending_calls,
    reply_metadata,
    to_reply,
    user_prompt,
)

if TYPE_CHECKING:
    from ...core.types import Duration

__all__ = ("PydanticAIHarness",)

type _Output = str | DeferredToolRequests


class PydanticAIHarness(Harness):
    """Runs a Pydantic AI agent, one turn per operation.

    The agent keeps its model, instructions, and own tools. Each turn adds
    the target's ``@tool`` methods, passes the target as ``deps``, and asks
    for text. A turn that stops on tool calls needing approval or the
    client settles ``needs_input``. The turn streams `ChunkEvent`s and saves
    the assistant message's parts after every step.

    Parameters
    ----------
    id
        The capability id.
    agent
        The Pydantic AI agent to run.
    target
        The object whose ``@tool`` methods are the tools, and the run's deps.
    max_restarts
        How many times a crash-interrupted turn is resumed.
    heartbeat
        How often a session with open work is checked.

    Examples
    --------
    >>> class Notes(DurableObject):
    ...     def __init__(self, ctx, env):
    ...         super().__init__(ctx, env)
    ...         agent = pydantic_ai.Agent("openai:gpt-5", instructions="Keep notes.")
    ...         self.harness = PydanticAIHarness("notes", agent, target=self)
    ...         Lifecycle(self).use(self.harness)
    """

    def __init__(
        self,
        id: str,
        agent: Agent[Any, Any],
        *,
        target: object | None = None,
        max_restarts: int = 5,
        heartbeat: Duration = 30,
    ) -> None:
        super().__init__(
            id, target=target, max_restarts=max_restarts, heartbeat=heartbeat
        )
        self.agent = agent
        """The Pydantic AI agent each turn runs."""

        self._specs = {spec.name: spec for spec in self.tools}
        self._toolset = FunctionToolset(
            [
                Tool(
                    spec.fn,
                    takes_ctx=False,
                    name=spec.name,
                    requires_approval=spec.requires_approval,
                )
                for spec in self.tools
            ]
        )

    async def run_turn(self, turn: Turn) -> TurnResult:
        """Run the agent from the turn's checkpoint, or its history and input."""
        messages = load_messages(turn.checkpoint or turn.history)
        answers = ToolAnswers(turn.input)
        if turn.checkpoint is None:
            prompt = user_prompt(turn.input)
            results = answers.for_first_run()
        else:
            prompt = None
            results = answers.for_resume(pending_calls(messages), self._specs)

        ids = PartIds()
        async with self.agent.iter(
            prompt,
            message_history=messages,
            deferred_tool_results=results,
            deps=self.target,
            toolsets=[self._toolset],
            output_type=[str, DeferredToolRequests],
        ) as run:
            async for node in run:
                if isinstance(node, ModelRequestNode | CallToolsNode):
                    await self._step(turn, run, node, ids)

        assert run.result is not None
        return _to_turn_result(turn, run.result)

    def reset_history(self, handoff: str | None) -> bytes | None:
        """Start the new history with the handoff note as a system prompt.

        Pydantic AI adds an agent's ``system_prompt`` only to an empty
        history, so after a reset with a handoff only its ``instructions``
        (which apply to every request) still reach the model.
        """
        if not handoff:
            return None

        return dump_messages([ModelRequest(parts=[SystemPromptPart(content=handoff)])])

    async def _step(
        self,
        turn: Turn,
        run: AgentRun[Any, _Output],
        node: ModelRequestNode[Any, _Output] | CallToolsNode[Any, _Output],
        ids: PartIds,
    ) -> None:
        checkpoint = run.all_messages()
        if isinstance(node, ModelRequestNode):
            checkpoint = [*checkpoint, node.request]
            ids.next_node()
        turn.save(
            dump_messages(checkpoint),
            reply=_reply(turn, checkpoint),
            metadata=reply_metadata(checkpoint),
        )

        async with node.stream(run.ctx) as events:
            async for event in events:
                for chunk in to_chunks(event, ids):
                    turn.emit(ChunkEvent(operation_id=turn.operation_id, chunk=chunk))


def _to_turn_result(turn: Turn, result: AgentRunResult[_Output]) -> TurnResult:
    messages = result.all_messages()
    history = dump_messages(messages)
    metadata = reply_metadata(messages)
    if not isinstance(result.output, DeferredToolRequests):
        return TurnResult(
            history=history,
            reply=_reply(turn, messages),
            metadata=metadata,
            text=result.output,
        )

    requests = [
        ToolRequest(
            id=call.tool_call_id,
            name=call.tool_name,
            args=call.args_as_dict(),
            kind=kind,
        )
        for kind, calls in (
            ("approval", result.output.approvals),
            ("call", result.output.calls),
        )
        for call in calls
    ]
    return TurnResult(
        history=history,
        reply=_reply(turn, messages, requests),
        metadata=metadata,
        status="needs_input",
        requests=requests,
    )


def _reply(
    turn: Turn,
    messages: Sequence[ModelMessage],
    requests: Sequence[ToolRequest] = (),
) -> list[MessagePart]:
    """Map the turn's messages, then add what the history doesn't record.

    That is the pending requests (an approval request, or the owner of a
    client tool call: the participant who started the turn), and the
    answers this turn continues from.
    """
    parts = to_reply(messages)
    pending = {request.id: request.kind for request in requests}
    answers = (
        {answer.id: answer for answer in turn.input} if is_answers(turn.input) else {}
    )

    for part in parts:
        if not isinstance(part, ToolPart):
            continue

        kind = pending.get(part.tool_call_id)
        answer = answers.get(part.tool_call_id)
        if kind == "approval" and part.state == "input-available":
            part.state = "approval-requested"
            part.approval = ToolApproval(id=part.tool_call_id)
        elif kind == "call" or isinstance(answer, ToolOutput):
            part.owner = turn.participant
        elif isinstance(answer, Approval) and part.approval is None:
            part.approval = ToolApproval(
                id=part.tool_call_id, approved=answer.approved, reason=answer.message
            )
    return parts
