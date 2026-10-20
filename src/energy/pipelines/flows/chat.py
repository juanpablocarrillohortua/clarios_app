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

    The adapters both roots need (the model, where the queries run) are built
    by :mod:`energy.adapters.chat_wiring`; the API joins them to the same
    services in its own composition root, ``api/deps.py``.

Depends on:
    ``energy.adapters``, ``energy.services``, ``energy.core``, ``energy.domain``.
"""

from __future__ import annotations

from energy.adapters.chat_wiring import (
    build_chat_model,
    build_reporting_query,
    reporting_login_url,
    require_chat_key,
)
from energy.adapters.yaml_chat_sources import load_chat_sources
from energy.core.settings import Settings, get_settings
from energy.services.chat_data_service import ChatDataService
from energy.services.chat_service import ChatService


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
    require_chat_key(resolved)
    data = build_chat_data_service(resolved)
    model = build_chat_model(
        resolved, tools=(data.list_sources, data.describe_reporting, data.run_sql)
    )
    return ChatService(
        model,
        history_limit=resolved.chat.context_window_limit,
        notes=(data.sources_note(),),
    )


__all__ = [
    "build_chat_data_service",
    "build_chat_service",
    "build_reporting_query",
    "reporting_login_url",
]
