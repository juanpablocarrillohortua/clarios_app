"""The chat assistant without a network: its rules, its service, its model adapter and wiring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from openai import OpenAIError
from pydantic import Field

from energy.adapters.langchain_chat_model import (
    SYSTEM_PROMPT_FILE,
    LangChainChatModel,
    load_system_prompt,
)
from energy.core.settings import ChatSettings, Settings
from energy.domain.chat import (
    ChatContext,
    ChatMessage,
    ChatRole,
    apply_cutoff,
    build_context,
    time_note,
)
from energy.domain.exceptions import (
    ChatModelUnavailableError,
    ChatNotConfiguredError,
    EmptyQuestionError,
    NaiveDatetimeError,
)
from energy.pipelines.flows.chat import build_chat_service
from energy.ports.chat_model import ChatModel
from energy.services.chat_service import ChatService

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

# 03:30 UTC on Tuesday 2026-10-20 is 22:30 on Monday 2026-10-19 at the plant.
NOW = datetime(2026, 10, 20, 3, 30, tzinfo=UTC)
CHAT_VARIABLES = (
    "CHAT_API_KEY",
    "CHAT_MODEL",
    "CHAT_CONTEXT_WINDOW_LIMIT",
    "CHAT_TIMEOUT_SECONDS",
    "CHAT_SYSTEM_PROMPT_FILE",
)


def _conversation(turns: int) -> list[ChatMessage]:
    """Return ``turns`` questions and answers, oldest first."""
    messages: list[ChatMessage] = []
    for turn in range(1, turns + 1):
        messages.append(ChatMessage(ChatRole.USER, f"question {turn}"))
        messages.append(ChatMessage(ChatRole.ASSISTANT, f"answer {turn}"))
    return messages


@pytest.fixture
def no_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the assistant's variables, so the developer's own `.env` values are not read."""
    for name in CHAT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class FakeModel:
    """A model that records each context and answers with a fixed text."""

    def __init__(self, answer: str = "respuesta") -> None:
        self.answer = answer
        self.contexts: list[ChatContext] = []

    def reply(self, context: ChatContext) -> str:
        self.contexts.append(context)
        return self.answer


class RecordingChatModel(GenericFakeChatModel):
    """LangChain's fake chat model, keeping the messages of every call."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


class FailingChatModel(GenericFakeChatModel):
    """A chat model whose vendor refuses every request."""

    def _generate(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002 (refuses whatever comes)
        msg = "the vendor refused"
        raise OpenAIError(msg)


# -----------------------------------------------------------------------------
# The rules (energy.domain.chat)
# -----------------------------------------------------------------------------
class TestCutoff:
    def test_keeps_the_last_messages(self) -> None:
        history = _conversation(3)

        assert apply_cutoff(history, 4) == tuple(history[-4:])

    def test_keeps_a_short_history_whole(self) -> None:
        history = _conversation(1)

        assert apply_cutoff(history, 20) == tuple(history)

    def test_zero_keeps_nothing(self) -> None:
        assert apply_cutoff(_conversation(2), 0) == ()

    def test_refuses_a_negative_limit(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            apply_cutoff(_conversation(1), -1)


class TestTimeNote:
    def test_states_the_day_and_time_at_the_plant(self) -> None:
        note = time_note(NOW)

        assert "Date: 2026-10-19" in note
        assert "Time: 22:30:00" in note
        assert "Weekday: Monday" in note
        assert "America/Bogota" in note
        assert "2026-10-19T22:30:00-05:00" in note

    def test_any_time_zone_gives_the_same_note(self) -> None:
        elsewhere = NOW.astimezone(timezone(timedelta(hours=9)))

        assert time_note(elsewhere) == time_note(NOW)

    def test_refuses_a_naive_time(self) -> None:
        with pytest.raises(NaiveDatetimeError):
            time_note(datetime(2026, 10, 19, 22, 30))  # noqa: DTZ001 (the point of the test)


class TestBuildContext:
    def test_puts_other_notes_after_the_time(self) -> None:
        context = build_context([], "hola", now=NOW, limit=20, notes=("Sources: none.",))

        assert context.notes == (time_note(NOW), "Sources: none.")

    def test_puts_the_note_the_history_and_the_question_in_order(self) -> None:
        history = _conversation(2)

        context = build_context(history, "¿y en septiembre?", now=NOW, limit=20)

        assert context.notes == (time_note(NOW),)
        assert context.messages == (
            *history,
            ChatMessage(ChatRole.USER, "¿y en septiembre?"),
        )
        assert context.question == ChatMessage(ChatRole.USER, "¿y en septiembre?")

    def test_cuts_the_history_but_never_the_question(self) -> None:
        history = _conversation(5)

        context = build_context(history, "última", now=NOW, limit=3)

        assert context.messages == (*history[-3:], ChatMessage(ChatRole.USER, "última"))

    @pytest.mark.parametrize("question", ["", "   ", "\t"])
    def test_refuses_an_empty_question(self, question: str) -> None:
        with pytest.raises(EmptyQuestionError):
            build_context([], question, now=NOW, limit=20)


# -----------------------------------------------------------------------------
# The service (energy.services.chat_service)
# -----------------------------------------------------------------------------
class TestChatService:
    def test_answers_with_the_model_and_the_plant_time(self) -> None:
        model = FakeModel("hola")
        service = ChatService(model, history_limit=2, clock=lambda: NOW)

        answer = service.answer(_conversation(3), "¿qué es una EQ?")

        assert answer == "hola"
        (context,) = model.contexts
        assert context.notes == (time_note(NOW),)
        assert context.messages[:-1] == tuple(_conversation(3)[-2:])
        assert context.question.content == "¿qué es una EQ?"

    def test_sends_its_notes_with_every_question(self) -> None:
        model = FakeModel()
        service = ChatService(model, clock=lambda: NOW, notes=("Sources: none.",))

        service.answer([], "uno")
        service.answer([], "dos")

        assert [context.notes[1:] for context in model.contexts] == [("Sources: none.",)] * 2

    def test_the_fake_is_a_chat_model(self) -> None:
        assert isinstance(FakeModel(), ChatModel)

    def test_an_empty_question_reaches_no_model(self) -> None:
        model = FakeModel()

        with pytest.raises(EmptyQuestionError):
            ChatService(model, clock=lambda: NOW).answer([], " ")

        assert model.contexts == []

    def test_logs_no_text_of_the_conversation(self, capsys: pytest.CaptureFixture[str]) -> None:
        ChatService(FakeModel("secreto"), clock=lambda: NOW).answer([], "pregunta privada")

        logged = capsys.readouterr()
        assert "pregunta privada" not in logged.out + logged.err
        assert "secreto" not in logged.out + logged.err


# -----------------------------------------------------------------------------
# The adapter (energy.adapters.langchain_chat_model)
# -----------------------------------------------------------------------------
class TestSystemPrompt:
    def test_the_packaged_prompt_is_not_empty(self) -> None:
        assert SYSTEM_PROMPT_FILE.is_file()
        assert load_system_prompt().startswith("You are the assistant of Clarios Energy")

    def test_the_packaged_prompt_asks_for_spanish(self) -> None:
        assert "answer in Spanish" in load_system_prompt()

    def test_reads_another_file(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("  Be brief.  \n", encoding="utf-8")

        assert load_system_prompt(prompt) == "Be brief."

    def test_refuses_an_empty_file(self, tmp_path: Path) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(" \n", encoding="utf-8")

        with pytest.raises(ChatNotConfiguredError, match="empty"):
            load_system_prompt(prompt)

    def test_refuses_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ChatNotConfiguredError, match="cannot be read"):
            load_system_prompt(tmp_path / "missing.txt")


class TestLangChainChatModel:
    def test_sends_the_prompt_the_notes_and_the_conversation(self) -> None:
        llm = RecordingChatModel(messages=iter([AIMessage(content="Una EQ es...")]))
        model = LangChainChatModel(llm, "Be brief.")
        context = build_context(_conversation(1), "¿qué es una EQ?", now=NOW, limit=20)

        answer = model.reply(context)

        assert answer == "Una EQ es..."
        (sent,) = llm.seen
        assert [type(message) for message in sent] == [
            SystemMessage,
            SystemMessage,
            HumanMessage,
            AIMessage,
            HumanMessage,
        ]
        assert sent[0].content == "Be brief."
        assert sent[1].content == time_note(NOW)
        assert [message.content for message in sent[2:]] == [
            "question 1",
            "answer 1",
            "¿qué es una EQ?",
        ]

    def test_a_vendor_failure_is_unavailable(self) -> None:
        model = LangChainChatModel(FailingChatModel(messages=iter([])), "Be brief.")

        with pytest.raises(ChatModelUnavailableError) as raised:
            model.reply(build_context([], "hola", now=NOW, limit=20))

        assert raised.value.details == {"reason": "OpenAIError"}


# -----------------------------------------------------------------------------
# Settings and wiring
# -----------------------------------------------------------------------------
@pytest.mark.usefixtures("no_chat_env")
class TestChatSettings:
    def test_not_configured_by_default(self) -> None:
        settings = ChatSettings(_env_file=None)

        assert settings.api_key is None
        assert not settings.is_configured
        assert settings.context_window_limit == 20
        assert settings.system_prompt_file is None

    def test_blank_values_are_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAT_API_KEY", "  ")
        monkeypatch.setenv("CHAT_SYSTEM_PROMPT_FILE", "")

        settings = ChatSettings(_env_file=None)

        assert settings.api_key is None
        assert settings.system_prompt_file is None

    def test_the_key_is_kept_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAT_API_KEY", "sk-made-up")

        settings = ChatSettings(_env_file=None)

        assert settings.is_configured
        assert "sk-made-up" not in repr(settings)


@pytest.mark.usefixtures("no_chat_env")
class TestBuildChatService:
    def test_refuses_without_a_key(self) -> None:
        with pytest.raises(ChatNotConfiguredError, match="CHAT_API_KEY"):
            build_chat_service(Settings(chat=ChatSettings(_env_file=None)))

    def test_refuses_an_empty_prompt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("", encoding="utf-8")
        monkeypatch.setenv("CHAT_API_KEY", "sk-made-up")
        monkeypatch.setenv("CHAT_SYSTEM_PROMPT_FILE", str(prompt))

        with pytest.raises(ChatNotConfiguredError, match="empty"):
            build_chat_service(Settings(chat=ChatSettings(_env_file=None)))

    def test_wires_the_service_without_calling_the_vendor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAT_API_KEY", "sk-made-up")

        assert isinstance(
            build_chat_service(Settings(chat=ChatSettings(_env_file=None))), ChatService
        )
