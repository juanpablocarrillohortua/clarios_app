"""Application service for the chat assistant: answer one question of a conversation.

Layer: services

Responsibility:
    Owns the assistant's use case. It is stateless: the caller keeps the
    conversation and sends it with each question, and nothing is stored here.
    For each question it builds the context
    (:func:`~energy.domain.chat.build_context`: the plant's date and time, the
    last messages of the history, the question) and asks the model.

    It logs that a question was answered, how much history went with it and
    how long it took, but never the text of a question or of an answer.

Depends on:
    ``energy.domain``, ``energy.ports``, ``energy.core``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from energy.core.logging import get_logger
from energy.domain.chat import DEFAULT_HISTORY_LIMIT, build_context

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from energy.domain.chat import ChatMessage
    from energy.ports.chat_model import ChatModel

logger = get_logger(__name__)


def _utc_now() -> datetime:
    """Return the current instant, in UTC."""
    return datetime.now(UTC)


class ChatService:
    """Answers the questions of a conversation with the assistant."""

    def __init__(
        self,
        model: ChatModel,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        clock: Callable[[], datetime] = _utc_now,
        notes: Sequence[str] = (),
    ) -> None:
        """Wire the service.

        Args:
            model: the language model.
            history_limit: how many earlier messages go with each question.
            clock: the current instant, with its time zone.
            notes: facts that go with every question, after the time note.
        """
        self._model = model
        self._history_limit = history_limit
        self._clock = clock
        self._notes = tuple(notes)

    def answer(self, history: Sequence[ChatMessage], question: str) -> str:
        """Answer a question, given the conversation so far.

        Args:
            history: the conversation so far, oldest first, without the question.
            question: what the user asks now.

        Returns:
            The assistant's answer.

        Raises:
            EmptyQuestionError: if the question is empty or blank.
            ChatModelUnavailableError: if the model could not answer.
        """
        context = build_context(
            history, question, now=self._clock(), limit=self._history_limit, notes=self._notes
        )
        started = time.perf_counter()
        answer = self._model.reply(context)
        logger.info(
            "chat question answered",
            history_sent=len(context.messages) - 1,
            history_received=len(history),
            seconds=round(time.perf_counter() - started, 3),
        )
        return answer


__all__ = ["ChatService"]
