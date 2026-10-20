"""What a query of the chat assistant must look like before it reaches a database.

Layer: domain

Responsibility:
    The assistant writes its own SQL. The database is what keeps that SQL
    harmless: the reporting login is read-only and sees one schema, and every
    query runs in a transaction that is rolled back. This module is the first,
    cheaper filter, and the one that gives the model a reason it can act on:
    :func:`check_query` accepts one statement that starts with ``SELECT`` or
    ``WITH`` and refuses anything else.

    To find the statement's words, string literals, quoted identifiers,
    dollar-quoted bodies and comments are blanked out first, so a ``;`` or a
    keyword inside them counts for nothing, and a second statement hidden
    behind a comment is still seen.

Depends on:
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

import re
from typing import Final

from energy.domain.exceptions import QueryRefusedError

#: The longest query accepted, in characters.
MAX_QUERY_CHARS: Final = 10_000

_READ_KEYWORDS: Final = frozenset({"select", "with"})
_FIRST_WORD: Final = re.compile(r"\s*([A-Za-z_]+)")
_DOLLAR_TAG: Final = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_NEWLINE: Final = chr(10)
_COMMENT: Final = " "
_LITERAL: Final = "x"


def _refuse(reason: str) -> QueryRefusedError:
    return QueryRefusedError("the query was not run", reason=reason)


def _quoted_end(sql: str, start: int) -> int:
    """Return where a quoted text or name that opens at ``start`` ends (exclusive)."""
    quote, j = sql[start], start + 1
    while True:
        j = sql.find(quote, j)
        if j == -1:
            raise _refuse("a quoted text or name is not closed")
        if j + 1 < len(sql) and sql[j + 1] == quote:  # a doubled quote stays inside
            j += 2
            continue
        return j + 1


def _block_comment_end(sql: str, start: int) -> int:
    """Return where a block comment that opens at ``start`` ends; such comments nest."""
    depth, j = 1, start + 2
    while depth:
        if j >= len(sql):
            raise _refuse("a comment is not closed")
        if sql.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif sql.startswith("*/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    return j


def _skipped_end(sql: str, start: int) -> tuple[int, str] | None:
    """Return where a literal, quoted name or comment starting at ``start`` ends, if one does.

    With it, the character that stands in for it: a comment is blank space, a
    literal or a quoted name is a word, so it still counts as part of the
    statement.
    """
    char = sql[start]
    if char in ("'", '"'):
        return _quoted_end(sql, start), _LITERAL
    if sql.startswith("--", start):
        end = sql.find(_NEWLINE, start)
        return (len(sql) if end == -1 else end), _COMMENT
    if sql.startswith("/*", start):
        return _block_comment_end(sql, start), _COMMENT
    if char == "$" and (tag := _DOLLAR_TAG.match(sql, start)):
        close = sql.find(tag.group(0), tag.end())
        if close == -1:
            raise _refuse("a dollar-quoted text is not closed")
        return close + len(tag.group(0)), _LITERAL
    return None


def _blank_out(sql: str) -> str:
    """Return the query with comments blanked out and literals and quoted names masked.

    The result has the length of the query, so a position in one is a position
    in the other.

    Raises:
        QueryRefusedError: if a literal, quoted name or comment never ends.
    """
    out = list(sql)
    i = 0
    while i < len(sql):
        skipped = _skipped_end(sql, i)
        if skipped is None:
            i += 1
            continue
        end, fill = skipped
        out[i:end] = fill * (end - i)
        i = end
    return "".join(out)


def check_query(sql: str, *, max_chars: int = MAX_QUERY_CHARS) -> str:
    """Accept one read-only statement, or say why not.

    Args:
        sql: the query the model wrote.
        max_chars: the longest query accepted.

    Returns:
        The statement, without surrounding space or trailing semicolons.

    Raises:
        QueryRefusedError: if the query is empty or too long, holds more than
            one statement, or does not start with ``SELECT`` or ``WITH``.
    """
    text = sql.strip()
    if not text:
        raise _refuse("the query is empty")
    if len(text) > max_chars:
        raise _refuse(f"the query is longer than {max_chars} characters")
    skeleton = _blank_out(text)
    cut = len(skeleton.rstrip())
    while cut and skeleton[cut - 1] == ";":
        cut = len(skeleton[: cut - 1].rstrip())
    if ";" in skeleton[:cut]:
        raise _refuse("only one statement may be run at a time")
    first = _FIRST_WORD.match(skeleton[:cut])
    if first is None or first.group(1).lower() not in _READ_KEYWORDS:
        raise _refuse("only a SELECT (or WITH ... SELECT) may be run")
    return text[:cut].rstrip()


__all__ = ["MAX_QUERY_CHARS", "check_query"]
