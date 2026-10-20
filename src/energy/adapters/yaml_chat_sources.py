"""Read the chat assistant's sources catalogue from ``config/chat/sources.yaml``.

Layer: adapters

Responsibility:
    Turns the YAML file into a :class:`~energy.domain.chat_sources.SourceCatalogue`,
    which checks that it holds together. A file that cannot be read, or whose
    shape is wrong, is a :class:`~energy.domain.exceptions.ConfigurationError`
    naming the file and what is wrong.

Depends on:
    ``energy.domain``, ``pyyaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import yaml

from energy.domain.chat_sources import DataSource, ReportingView, SourceCatalogue, SourceState
from energy.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

#: Where the catalogue lives, relative to the configuration directory.
SOURCES_FILE: Final = "chat/sources.yaml"


def _text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = "the assistant's sources catalogue is not valid"
        raise ConfigurationError(msg, problems=[f"{where}: '{key}' must be a non-empty text"])
    return value.strip()


def _source(entry: Any, position: int) -> DataSource:
    where = f"sources[{position}]"
    if not isinstance(entry, dict):
        msg = "the assistant's sources catalogue is not valid"
        raise ConfigurationError(msg, problems=[f"{where}: must be a mapping"])
    files = entry.get("files") or []
    views = entry.get("views") or []
    if not isinstance(files, list) or not isinstance(views, list):
        msg = "the assistant's sources catalogue is not valid"
        raise ConfigurationError(msg, problems=[f"{where}: 'files' and 'views' must be lists"])
    try:
        state = SourceState(_text(entry, "state", where))
    except ValueError as exc:
        msg = "the assistant's sources catalogue is not valid"
        raise ConfigurationError(
            msg, problems=[f"{where}: 'state' must be implemented or pending"]
        ) from exc
    return DataSource(
        key=_text(entry, "key", where),
        files=tuple(str(name) for name in files),
        description=_text(entry, "description", where),
        state=state,
        views=tuple(
            ReportingView(
                name=_text(view, "name", f"{where}.views[{index}]"),
                description=_text(view, "description", f"{where}.views[{index}]"),
            )
            for index, view in enumerate(views)
        ),
    )


def load_chat_sources(config_dir: Path) -> SourceCatalogue:
    """Read and check the assistant's sources catalogue.

    Args:
        config_dir: the configuration directory (``CONFIG_DIR``).

    Returns:
        The catalogue.

    Raises:
        ConfigurationError: if the file is missing, is not YAML, or does not
            describe a valid catalogue.
    """
    path = config_dir / SOURCES_FILE
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = "the assistant's sources catalogue cannot be read"
        raise ConfigurationError(msg, file=SOURCES_FILE) from exc
    entries = document.get("sources") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        msg = "the assistant's sources catalogue is not valid"
        raise ConfigurationError(msg, problems=[f"{SOURCES_FILE}: 'sources' must be a list"])
    return SourceCatalogue(tuple(_source(entry, index) for index, entry in enumerate(entries)))


__all__ = ["SOURCES_FILE", "load_chat_sources"]
