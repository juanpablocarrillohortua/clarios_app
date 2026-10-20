"""Questions to the chat assistant.

Layer: api

- ``POST /chat`` answers one question, given the conversation so far.

The API stores nothing of a conversation: the web app keeps it in the browser
and sends it with each question, and nothing of it is logged. The assistant
answers from the ``reporting`` views, or from simulated rows shaped like them
while ``CHAT_DATA_SOURCE`` is ``simulated`` (``docs/chatbot-plan.md``).

Needs ``chat:use``, which ``api/main.py`` attaches where it mounts this router.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from energy.api.deps import ChatServiceDep
from energy.api.schemas.requests import ChatRequest
from energy.api.schemas.responses import ChatAnswerResponse, ErrorResponse
from energy.domain.chat import ChatMessage, ChatRole

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    summary="Ask the assistant",
    status_code=status.HTTP_200_OK,
    responses={
        422: {"model": ErrorResponse, "description": "The question is blank."},
        502: {"model": ErrorResponse, "description": "The model did not answer."},
        503: {"model": ErrorResponse, "description": "The assistant is not configured."},
    },
)
def ask(request: ChatRequest, chat: ChatServiceDep) -> ChatAnswerResponse:
    """Answer one question, given the conversation so far.

    A plain function, not a coroutine: the model takes seconds, and FastAPI
    runs a plain handler in its thread pool, off the event loop.

    Args:
        request: the earlier messages, oldest first, and the question.
        chat: the assistant.

    Returns:
        The answer, as plain text.

    Raises:
        EmptyQuestionError: if the question is blank (HTTP 422).
        ChatModelUnavailableError: if the model did not answer (HTTP 502).
        ChatNotConfiguredError: if the assistant has no key, no instructions
            or, in database mode, no reporting login (HTTP 503).
    """
    history = [
        ChatMessage(role=ChatRole(turn.role), content=turn.content) for turn in request.history
    ]
    return ChatAnswerResponse(answer=chat.answer(history, request.question))


__all__ = ["router"]
