"""Development page of the chat assistant (phase 1 of docs/chatbot-plan.md).

Run it on the host with ``make chat-ui`` and open http://localhost:8501. It
needs ``CHAT_API_KEY`` in ``.env``. The conversation lives in this browser tab
only (Streamlit's session state): nothing is stored, and each question carries
the conversation so far, as the web app will in phase 3.

This page is for developers trying the assistant; people of the plant will use
it from the web app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from energy.domain.chat import ChatMessage, ChatRole
from energy.domain.exceptions import (
    ChatModelUnavailableError,
    ChatNotConfiguredError,
    EmptyQuestionError,
)
from energy.pipelines.flows.chat import build_chat_service

if TYPE_CHECKING:
    from energy.services.chat_service import ChatService


@st.cache_resource
def _service() -> ChatService:
    """Build the assistant once per Streamlit process."""
    return build_chat_service()


st.title("Clarios Energy assistant")
st.caption("Development page. The conversation lives in this browser tab only.")

try:
    service = _service()
except ChatNotConfiguredError as exc:
    st.error(f"The assistant is not configured: {exc.message}.")
    st.stop()

if "messages" not in st.session_state or st.button("New conversation"):
    st.session_state.messages = []
history: list[ChatMessage] = st.session_state.messages

for message in history:
    st.chat_message(message.role.value).markdown(message.content)

question = st.chat_input("Ask a question")
if question:
    st.chat_message(ChatRole.USER.value).markdown(question)
    try:
        answer = service.answer(history, question)
    except (ChatModelUnavailableError, EmptyQuestionError) as exc:
        st.error(f"No answer: {exc.message}.")
        st.stop()
    st.chat_message(ChatRole.ASSISTANT.value).markdown(answer)
    history.extend((ChatMessage(ChatRole.USER, question), ChatMessage(ChatRole.ASSISTANT, answer)))
