"""Wire the chat assistant outside the API.

Layer: pipelines

Responsibility:
    The composition root of the assistant for the command line and for its
    development page (``scripts/chat_streamlit.py``, ``make chat-ui``). It
    binds the OpenAI model of :mod:`energy.adapters.langchain_chat_model`,
    with the instructions of its prompt file, to
    :class:`~energy.services.chat_service.ChatService`, and gives the model
    the data tools of :class:`~energy.services.chat_data_service.ChatDataService`.

    ``CHAT_DATA_SOURCE`` decides where those tools read:

    * ``simulated`` (the default): synthetic rows shaped like the reporting
      views, in an in-memory DuckDB database. Nothing of the plant's data
      reaches the model's vendor.
    * ``database``: the real ``reporting`` views, through the read-only
      reporting login (``REPORTING_DB_USER`` and ``REPORTING_DB_PASSWORD``),
      at the application database's address or at ``CHAT_DB_HOST`` and
      ``CHAT_DB_PORT``. Only once the plant agrees.

    The API wires the same pieces in its own composition root from phase 3 of
    ``docs/chatbot-plan.md``.

Depends on:
    ``energy.adapters``, ``energy.services``, ``energy.core``, ``energy.domain``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.engine import make_url

from energy.adapters.duckdb_reporting_query import DuckDBReportingQuery
from energy.adapters.langchain_chat_model import load_system_prompt, openai_chat_model
from energy.adapters.postgres_reporting_query import PostgresReportingQuery
from energy.adapters.yaml_chat_sources import load_chat_sources
from energy.core.settings import Settings, get_settings
from energy.domain.exceptions import ChatNotConfiguredError
from energy.services.chat_data_service import ChatDataService
from energy.services.chat_service import ChatService

if TYPE_CHECKING:
    from sqlalchemy.engine import URL

    from energy.ports.reporting_query import ReportingQuery


def reporting_login_url(settings: Settings) -> URL:
    """Return the database address with the reporting login in place of the application's.

    Args:
        settings: the process's settings.

    Returns:
        The URL the assistant's queries use in database mode.

    Raises:
        ChatNotConfiguredError: if ``REPORTING_DB_PASSWORD`` is not set.
    """
    password = settings.reporting.db_password
    if password is None or not password.get_secret_value():
        msg = "the assistant's database mode needs the reporting login (REPORTING_DB_PASSWORD)"
        raise ChatNotConfiguredError(msg)
    url = make_url(settings.database.url).set(
        username=settings.reporting.db_user, password=password.get_secret_value()
    )
    if settings.chat.db_host is not None:
        url = url.set(host=settings.chat.db_host)
    if settings.chat.db_port is not None:
        url = url.set(port=settings.chat.db_port)
    return url


def build_reporting_query(settings: Settings | None = None) -> ReportingQuery:
    """Return where the assistant's queries run, as ``CHAT_DATA_SOURCE`` says.

    Raises:
        ChatNotConfiguredError: in database mode, without the reporting login.
    """
    resolved = settings or get_settings()
    if resolved.chat.data_source == "database":
        return PostgresReportingQuery(reporting_login_url(resolved))
    return DuckDBReportingQuery()


def build_chat_data_service(settings: Settings | None = None) -> ChatDataService:
    """Wire the assistant's data tools.

    Raises:
        ChatNotConfiguredError: in database mode, without the reporting login.
        ConfigurationError: if the sources catalogue cannot be read.
    """
    resolved = settings or get_settings()
    return ChatDataService(
        build_reporting_query(resolved),
        load_chat_sources(resolved.data.config_dir),
        row_limit=resolved.chat.sql_row_limit,
        timeout_seconds=resolved.chat.sql_timeout_seconds,
    )


def build_chat_service(settings: Settings | None = None) -> ChatService:
    """Wire the assistant to its OpenAI model and its data tools.

    Args:
        settings: the process's settings; the process-wide ones when ``None``.

    Returns:
        The service.

    Raises:
        ChatNotConfiguredError: if the model key is missing, the instructions
            are missing or empty, or database mode has no reporting login.
    """
    resolved = settings or get_settings()
    chat = resolved.chat
    if chat.api_key is None:
        msg = "the assistant has no model key (CHAT_API_KEY)"
        raise ChatNotConfiguredError(msg)
    data = build_chat_data_service(resolved)
    model = openai_chat_model(
        api_key=chat.api_key,
        model=chat.model,
        timeout_seconds=chat.timeout_seconds,
        system_prompt=load_system_prompt(chat.system_prompt_file),
        tools=(data.list_sources, data.describe_reporting, data.run_sql),
    )
    return ChatService(model, history_limit=chat.context_window_limit, notes=(data.sources_note(),))


__all__ = [
    "build_chat_data_service",
    "build_chat_service",
    "build_reporting_query",
    "reporting_login_url",
]
