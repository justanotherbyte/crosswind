"""Channels: an agent's conversations on every interface.

Port of upstream ``agents/experimental/channels``. Imported by path
(``from agents.channels import ...``): nothing here is in the root ``agents``
export, because its vocabulary would crowd ``agents.chat``'s.
"""

from .errors import ChannelsError, ResponseGrammarError
from .protocol import (
    ApprovalResponse,
    Cancel,
    ClientTool,
    ConversationCreate,
    ConversationFork,
    ConversationInfo,
    ConversationOperation,
    ConversationReset,
    ConversationSnapshot,
    InboundEvent,
    InboundMessage,
    Participant,
    QueuedTurn,
    RunningTurn,
    SettledTurn,
    ToolResult,
    ToolResultError,
    ToolResultOk,
    TurnOutcome,
    TurnStatus,
    WireChunk,
    WireMessage,
    parse_inbound_event,
)
from .response import ResponseWriter

__all__ = (
    "ApprovalResponse",
    "Cancel",
    "ChannelsError",
    "ClientTool",
    "ConversationCreate",
    "ConversationFork",
    "ConversationInfo",
    "ConversationOperation",
    "ConversationReset",
    "ConversationSnapshot",
    "InboundEvent",
    "InboundMessage",
    "Participant",
    "QueuedTurn",
    "ResponseGrammarError",
    "ResponseWriter",
    "RunningTurn",
    "SettledTurn",
    "ToolResult",
    "ToolResultError",
    "ToolResultOk",
    "TurnOutcome",
    "TurnStatus",
    "WireChunk",
    "WireMessage",
    "parse_inbound_event",
)
