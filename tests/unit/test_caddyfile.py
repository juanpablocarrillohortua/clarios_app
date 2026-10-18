"""The Caddyfile's limits on request bodies agree with the API's.

Caddy refuses an upload over 300 MiB before the API sees it, and every other
call to the API over 10 MiB. The number lives in two places, the Caddyfile and
``energy.domain.drafts.MAX_FILE_BYTES``; this test keeps them equal.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from energy.domain.drafts import MAX_FILE_BYTES, MIB

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def caddyfile(repo_root: Path) -> str:
    return (repo_root / "Caddyfile").read_text(encoding="utf-8")


def _block(text: str, opening: str) -> str:
    """Return the text of the braces block that ``opening`` begins."""
    start = text.index(opening)
    depth = 0
    for index in range(text.index("{", start), len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start : index + 1]
    msg = f"unbalanced block: {opening}"
    raise AssertionError(msg)


def test_an_upload_may_be_exactly_as_large_as_the_api_accepts(caddyfile: str) -> None:
    api = _block(caddyfile, "handle_path /api/* {")
    upload = _block(api, "request_body @upload {")

    size = re.search(r"max_size (\d+)MiB", upload)

    assert size is not None
    assert int(size.group(1)) * MIB == MAX_FILE_BYTES


def test_the_upload_route_is_the_drafts_post(caddyfile: str) -> None:
    api = _block(caddyfile, "handle_path /api/* {")

    assert re.search(r"@upload \{\s*method POST\s*path /drafts\s*\}", api)
    assert re.search(r"@notUpload not \{\s*method POST\s*path /drafts\s*\}", api)
    assert "max_size 10MiB" in _block(api, "request_body @notUpload {")


def test_a_slow_upload_has_half_an_hour_to_arrive(caddyfile: str) -> None:
    assert re.search(r"timeouts \{\s*read_body 30m\s*\}", caddyfile)
