"""Wire the chat assistant outside the API.

Layer: pipelines

Responsibility:
    The composition root of the assistant for the command line and for its
    development page (``scripts/chat_streamlit.py``, ``make chat-ui``). It
    binds the OpenAI model of :mod:`energy.adapters.langchain_chat_model`,
    with the instructions of its prompt file, to
    :class:`~energy.services.chat_service.ChatService`. The API wires the same
    pieces in its own composition root from phase 3 of
    ``docs/chatbot-plan.md``.

Depends on:
    ``energy.adapters``, ``energy.services``, ``energy.core``, ``energy.domain``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.adapters.langchain_chat_model import load_system_prompt, openai_chat_model
from energy.core.settings import get_settings
from energy.domain.exceptions import ChatNotConfiguredError
from energy.services.chat_service import ChatService

if TYPE_CHECKING:
    from energy.core.settings import ChatSettings


def build_chat_service(settings: ChatSettings | None = None) -> ChatService:
    """Wire the assistant to its OpenAI model.

    Args:
        settings: the assistant's settings; the process-wide ones when ``None``.

    Returns:
        The service.

    Raises:
        ChatNotConfiguredError: if the model key is missing, or the
            instructions are missing or empty.
    """
    chat = settings or get_settings().chat
    if chat.api_key is None:
        msg = "the assistant has no model key (CHAT_API_KEY)"
        raise ChatNotConfiguredError(msg)
    model = openai_chat_model(
        api_key=chat.api_key,
        model=chat.model,
        timeout_seconds=chat.timeout_seconds,
        system_prompt=load_system_prompt(chat.system_prompt_file),
    )
    return ChatService(model, history_limit=chat.context_window_limit)


__all__ = ["build_chat_service"]
