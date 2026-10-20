"""The rules of a conversation with the chat assistant.

Layer: domain

Responsibility:
    Holds what a conversation is, independent of any language model or
    library: a message and its role, how much of the history goes with a new
    question, and the note that tells the model the plant's current date and
    time. :func:`build_context` puts them together in the order every model
    receives them:

    1. the notes (today, the date and time at the plant);
    2. the last messages of the history, at most the cutoff;
    3. the question.

    The assistant's own instructions (its system prompt) are not part of the
    context: the model adapter holds them, because they belong to the model's
    configuration and do not change between questions.

    The history never holds the question being asked: the caller keeps the
    conversation and sends it with each question, and the question goes last.

Depends on:
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from energy.domain.exceptions import EmptyQuestionError, NaiveDatetimeError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

# The plant's time zone: the assistant's "today" is a day in Colombia (UTC-5, no DST).
PLANT_ZONE: Final = ZoneInfo("America/Bogota")

# How many earlier messages go with a question when nothing else is configured.
DEFAULT_HISTORY_LIMIT: Final = 20

# Weekday names in a fixed language: strftime("%A") follows the process locale.
_WEEKDAYS: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class ChatRole(StrEnum):
    """Who wrote a message of the conversation."""

    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message of a conversation.

    Attributes:
        role: who wrote it.
        content: its text.
    """

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ChatContext:
    """Everything a model receives for one question, besides its own instructions.

    Attributes:
        notes: facts the model needs for this question only, such as the
            current date and time, in order.
        messages: the history kept after the cutoff, oldest first, followed
            by the question as the last user message.
    """

    notes: tuple[str, ...]
    messages: tuple[ChatMessage, ...]

    @property
    def question(self) -> ChatMessage:
        """Return the question: the last message."""
        return self.messages[-1]


def apply_cutoff(history: Sequence[ChatMessage], limit: int) -> tuple[ChatMessage, ...]:
    """Keep the last ``limit`` messages of a history.

    Args:
        history: the conversation so far, oldest first.
        limit: how many messages to keep; 0 keeps none.

    Returns:
        The last ``limit`` messages, oldest first.

    Raises:
        ValueError: if ``limit`` is negative.
    """
    if limit < 0:
        msg = f"the history limit cannot be negative, got {limit}"
        raise ValueError(msg)
    if limit == 0:
        return ()
    return tuple(history[-limit:])


def time_note(now: datetime) -> str:
    """Return the note that tells the model the current date and time at the plant.

    Args:
        now: the current instant; it must carry its time zone.

    Returns:
        The date, time, weekday and time zone at the plant, in plain English.

    Raises:
        NaiveDatetimeError: if ``now`` has no time zone.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        msg = "the current time must carry its time zone"
        raise NaiveDatetimeError(msg)
    local = now.astimezone(PLANT_ZONE)
    return (
        "Current date and time at the plant (use them for any question about dates):\n"
        f"Date: {local:%Y-%m-%d}\n"
        f"Time: {local:%H:%M:%S}\n"
        f"Weekday: {_WEEKDAYS[local.weekday()]}\n"
        "Time zone: America/Bogota (Colombia)\n"
        f"Timestamp: {local.isoformat()}"
    )


def build_context(
    history: Sequence[ChatMessage], question: str, *, now: datetime, limit: int
) -> ChatContext:
    """Put together what a model receives for one question.

    Args:
        history: the conversation so far, oldest first, without the question.
        question: what the user asks now.
        now: the current instant, with its time zone.
        limit: how many earlier messages go with the question.

    Returns:
        The context: the time note, the history after the cutoff, then the
        question.

    Raises:
        EmptyQuestionError: if the question is empty or blank.
        NaiveDatetimeError: if ``now`` has no time zone.
    """
    if not question.strip():
        msg = "the question is empty"
        raise EmptyQuestionError(msg)
    return ChatContext(
        notes=(time_note(now),),
        messages=(*apply_cutoff(history, limit), ChatMessage(ChatRole.USER, question)),
    )


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "PLANT_ZONE",
    "ChatContext",
    "ChatMessage",
    "ChatRole",
    "apply_cutoff",
    "build_context",
    "time_note",
]
