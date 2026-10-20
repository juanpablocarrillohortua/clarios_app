"""Port for the language model behind the chat assistant.

Layer: ports

Responsibility:
    Declares the one thing the assistant needs from a model: an answer to a
    context (:class:`~energy.domain.chat.ChatContext`). The model's own
    instructions, the vendor, the library and any tools it may call are the
    adapter's business. A model that cannot answer raises
    :class:`~energy.domain.exceptions.ChatModelUnavailableError`.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from energy.domain.chat import ChatContext


@runtime_checkable
class ChatModel(Protocol):
    """A language model that answers the last message of a context."""

    def reply(self, context: ChatContext) -> str:
        """Return the answer to the context's question.

        Args:
            context: the notes, the history and the question.

        Returns:
            The answer's text.

        Raises:
            ChatModelUnavailableError: if the model could not answer.
        """
        ...


__all__ = ["ChatModel"]
