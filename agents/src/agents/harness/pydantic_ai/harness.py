"""`PydanticAIHarness`: a harness whose turns run a Pydantic AI agent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    Agent,
    AgentRun,
    AgentRunResult,
    CallToolsNode,
    DeferredToolRequests,
    FunctionToolset,
    ModelRequestNode,
    Tool,
)

from ..harness import Harness
from ..types import ToolRequest, Turn, TurnResult
from .answers import ToolAnswers
from .events import to_harness_event
from .utils import dump_messages, load_messages, pending_calls

if TYPE_CHECKING:
    from ...core.types import Duration

__all__ = ("PydanticAIHarness",)

type _Output = str | DeferredToolRequests


class PydanticAIHarness(Harness):
    """Runs a Pydantic AI agent, one turn per operation.

    The agent keeps its model, instructions, and own tools. Each turn adds
    the target's ``@tool`` methods, passes the target as ``deps``, and asks
    for text. A turn that stops on tool calls needing approval or the
    client settles ``needs_input``.

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
            prompt = turn.input if isinstance(turn.input, str) else None
            results = answers.for_first_run()
        else:
            prompt = None
            results = answers.for_resume(pending_calls(messages), self._specs)

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
                    await self._step(turn, run, node)

        assert run.result is not None
        return _to_turn_result(run.result)

    async def _step(
        self,
        turn: Turn,
        run: AgentRun[Any, _Output],
        node: ModelRequestNode[Any, _Output] | CallToolsNode[Any, _Output],
    ) -> None:
        checkpoint = run.all_messages()
        if isinstance(node, ModelRequestNode):
            checkpoint = [*checkpoint, node.request]
        turn.save(dump_messages(checkpoint))

        async with node.stream(run.ctx) as events:
            async for event in events:
                if (
                    harness_event := to_harness_event(turn.operation_id, event)
                ) is not None:
                    turn.emit(harness_event)


def _to_turn_result(result: AgentRunResult[_Output]) -> TurnResult:
    history = dump_messages(result.all_messages())
    if not isinstance(result.output, DeferredToolRequests):
        return TurnResult(history=history, text=result.output)

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
    return TurnResult(history=history, status="needs_input", requests=requests)
