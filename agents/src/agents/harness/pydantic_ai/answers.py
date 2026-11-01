"""Turning a turn's tool answers into Pydantic AI's deferred tool results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from pydantic_ai import DeferredToolResults, ToolCallPart, ToolDenied

from ..types import Approval, HarnessInput, ToolAnswer, ToolOutput, ToolSpec

__all__ = ("INTERRUPTED", "ToolAnswers")

INTERRUPTED = (
    "The agent restarted while this tool call was running, so it may or may "
    "not have taken effect. Check its effect before calling the tool again."
)


class ToolAnswers:
    """The client's answers to a turn's tool requests, by tool call id.

    Parameters
    ----------
    input
        The turn's input; a prompt carries no answers.
    """

    __slots__ = ("_answers",)

    def __init__(self, input: HarnessInput) -> None:
        self._answers: dict[str, ToolAnswer] = (
            {} if isinstance(input, str) else {answer.id: answer for answer in input}
        )

    def for_first_run(self) -> DeferredToolResults | None:
        """Return the answers as given, or ``None`` if there are none."""
        if not self._answers:
            return None

        return _to_results(self._answers.values())

    def for_resume(
        self, pending: Sequence[ToolCallPart], tools: Mapping[str, ToolSpec]
    ) -> DeferredToolResults | None:
        """Return a result for each call a crash interrupted.

        A call re-runs only if its tool is replay-safe and needs no approval
        or was approved. Every other call keeps the client's answer or is
        reported to the model as interrupted.
        """
        if not pending:
            return None

        return _to_results(
            self._resumed(call, tools.get(call.tool_name)) for call in pending
        )

    def _resumed(self, call: ToolCallPart, spec: ToolSpec | None) -> ToolAnswer:
        answer = self._answers.get(call.tool_call_id)
        if isinstance(answer, ToolOutput) or (
            isinstance(answer, Approval) and not answer.approved
        ):
            return answer

        approved = answer is not None or (
            spec is not None and not spec.requires_approval
        )
        if approved and spec is not None and spec.replay == "safe":
            return Approval(id=call.tool_call_id, approved=True)
        return ToolOutput(id=call.tool_call_id, value=INTERRUPTED)


def _to_results(answers: Iterable[ToolAnswer]) -> DeferredToolResults:
    results = DeferredToolResults()
    for answer in answers:
        match answer:
            case Approval(approved=True):
                results.approvals[answer.id] = True
            case Approval(message=message):
                results.approvals[answer.id] = (
                    ToolDenied(message) if message else ToolDenied()
                )
            case ToolOutput(value=value):
                results.calls[answer.id] = value
    return results
