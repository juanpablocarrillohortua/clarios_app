"""The chat assistant's model, through LangChain's agent and OpenAI.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.chat_model.ChatModel` with LangChain's
    ``create_agent``. The agent holds the assistant's instructions (the system
    prompt) and its tools: plain functions that take text and return text,
    each turned into a LangChain tool named after the function and described
    by its docstring (phase 2 of ``docs/chatbot-plan.md`` gives it the data
    tools of :class:`~energy.services.chat_data_service.ChatDataService`,
    passed in by the composition root). This module turns a domain
    :class:`~energy.domain.chat.ChatContext` into LangChain messages (each note
    a system message, the history and the question as user and assistant
    messages) and returns the text of the agent's last message.

    The instructions live in a text file, ``prompts/system_prompt.txt`` beside
    this module, packaged with it. :func:`load_system_prompt` refuses a missing
    or empty file: a model without instructions would answer about anything.

    :func:`openai_chat_model` builds the model the platform uses, an OpenAI
    chat model with a key, a model name and a timeout. Any failure of the
    vendor becomes :class:`~energy.domain.exceptions.ChatModelUnavailableError`,
    and the key is never logged.

Depends on:
    ``energy.domain``, ``energy.ports``, ``langchain``, ``langchain-openai``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from openai import OpenAIError

from energy.domain.chat import ChatRole
from energy.domain.exceptions import ChatModelUnavailableError, ChatNotConfiguredError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from langchain_core.language_models import BaseChatModel
    from pydantic import SecretStr

    from energy.domain.chat import ChatContext, ChatMessage

#: The assistant's instructions, packaged with this module.
SYSTEM_PROMPT_FILE: Final = Path(__file__).with_name("prompts") / "system_prompt.txt"

# How often the OpenAI client retries a failed request before giving up.
_MAX_RETRIES: Final = 2


def load_system_prompt(path: Path | None = None) -> str:
    """Read the assistant's instructions.

    Args:
        path: the file; the packaged :data:`SYSTEM_PROMPT_FILE` when ``None``.

    Returns:
        The instructions, without surrounding blank space.

    Raises:
        ChatNotConfiguredError: if the file is missing, unreadable or empty.
    """
    source = path or SYSTEM_PROMPT_FILE
    try:
        text = source.read_text(encoding="utf-8").strip()
    except OSError as exc:
        msg = "the assistant's instructions cannot be read"
        raise ChatNotConfiguredError(msg, file=source.name) from exc
    if not text:
        msg = "the assistant's instructions are empty"
        raise ChatNotConfiguredError(msg, file=source.name)
    return text


def _as_langchain(message: ChatMessage) -> BaseMessage:
    """Return one message of the conversation as LangChain's message of its role."""
    if message.role is ChatRole.USER:
        return HumanMessage(content=message.content)
    return AIMessage(content=message.content)


class LangChainChatModel:
    """A chat model run as a LangChain agent with fixed instructions."""

    def __init__(
        self,
        llm: BaseChatModel,
        system_prompt: str,
        tools: Sequence[Callable[..., str]] = (),
    ) -> None:
        """Build the agent.

        Args:
            llm: the language model.
            system_prompt: the assistant's instructions.
            tools: what the agent may call: functions named for the model, whose
                docstrings tell it when and how.
        """
        wrapped = [StructuredTool.from_function(func=tool) for tool in tools]
        self._agent: Any = create_agent(llm, tools=wrapped, system_prompt=system_prompt)

    def reply(self, context: ChatContext) -> str:
        """Return the agent's answer to the context's question.

        Raises:
            ChatModelUnavailableError: if the vendor refused, timed out or
                could not be reached.
        """
        messages: list[BaseMessage] = [SystemMessage(content=note) for note in context.notes]
        messages.extend(_as_langchain(message) for message in context.messages)
        try:
            state = self._agent.invoke({"messages": messages})
        except OpenAIError as exc:
            msg = "the language model did not answer"
            raise ChatModelUnavailableError(msg, reason=type(exc).__name__) from exc
        final = state["messages"][-1]
        if not isinstance(final, AIMessage):
            msg = "the language model returned no answer"
            raise ChatModelUnavailableError(msg)
        return final.text


def openai_chat_model(
    *,
    api_key: SecretStr,
    model: str,
    timeout_seconds: float,
    system_prompt: str,
    tools: Sequence[Callable[..., str]] = (),
) -> LangChainChatModel:
    """Build the assistant's model on OpenAI.

    Args:
        api_key: the OpenAI key.
        model: the OpenAI model's name.
        timeout_seconds: how long one request may take.
        system_prompt: the assistant's instructions.
        tools: what the model may call.

    Returns:
        The model, ready to answer.
    """
    llm = ChatOpenAI(
        model_name=model,
        openai_api_key=api_key,
        request_timeout=timeout_seconds,
        max_retries=_MAX_RETRIES,
    )
    return LangChainChatModel(llm, system_prompt, tools)


__all__ = [
    "SYSTEM_PROMPT_FILE",
    "LangChainChatModel",
    "load_system_prompt",
    "openai_chat_model",
]
