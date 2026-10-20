"""Build the chat assistant's adapters from the settings.

Layer: adapters

Responsibility:
    The part of the assistant's wiring that needs only adapters, settings and
    the domain, shared by its two composition roots: ``pipelines/flows/chat.py``
    (the command line and the development page) and ``api/deps.py`` (the web
    app's ``POST /api/chat``). The API may not import ``pipelines``, so what
    both need lives here; each root then joins these adapters to the services.

    ``CHAT_DATA_SOURCE`` decides where the data tools read:

    * ``simulated`` (the default): synthetic rows shaped like the reporting
      views, in an in-memory DuckDB database. Nothing of the plant's data
      reaches the model's vendor.
    * ``database``: the real ``reporting`` views, through the read-only
      reporting login (``REPORTING_DB_USER`` and ``REPORTING_DB_PASSWORD``),
      at the application database's address or at ``CHAT_DB_HOST`` and
      ``CHAT_DB_PORT``. Only once the plant agrees.

Depends on:
    ``energy.adapters``, ``energy.core``, ``energy.domain``, ``energy.ports``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.engine import make_url

from energy.adapters.duckdb_reporting_query import DuckDBReportingQuery
from energy.adapters.langchain_chat_model import load_system_prompt, openai_chat_model
from energy.adapters.postgres_reporting_query import PostgresReportingQuery
from energy.domain.exceptions import ChatNotConfiguredError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import SecretStr
    from sqlalchemy.engine import URL

    from energy.core.settings import Settings
    from energy.ports.chat_model import ChatModel
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


def build_reporting_query(settings: Settings) -> ReportingQuery:
    """Return where the assistant's queries run, as ``CHAT_DATA_SOURCE`` says.

    Raises:
        ChatNotConfiguredError: in database mode, without the reporting login.
    """
    if settings.chat.data_source == "database":
        return PostgresReportingQuery(reporting_login_url(settings))
    return DuckDBReportingQuery()


def require_chat_key(settings: Settings) -> SecretStr:
    """Return the model key, refusing early when there is none.

    A composition root calls this before building anything else, so an
    assistant without a key costs nothing to refuse.

    Raises:
        ChatNotConfiguredError: if ``CHAT_API_KEY`` is not set.
    """
    if settings.chat.api_key is None:
        msg = "the assistant has no model key (CHAT_API_KEY)"
        raise ChatNotConfiguredError(msg)
    return settings.chat.api_key


def build_chat_model(settings: Settings, tools: Sequence[Callable[..., str]]) -> ChatModel:
    """Return the OpenAI model with the assistant's instructions and the given tools.

    Args:
        settings: the process's settings.
        tools: the functions the model may call.

    Raises:
        ChatNotConfiguredError: if the model key is missing, or the
            instructions are missing or empty.
    """
    chat = settings.chat
    return openai_chat_model(
        api_key=require_chat_key(settings),
        model=chat.model,
        timeout_seconds=chat.timeout_seconds,
        system_prompt=load_system_prompt(chat.system_prompt_file),
        tools=tools,
    )


__all__ = [
    "build_chat_model",
    "build_reporting_query",
    "reporting_login_url",
    "require_chat_key",
]
