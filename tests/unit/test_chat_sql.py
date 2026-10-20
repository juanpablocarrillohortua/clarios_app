"""What a query of the chat assistant must look like (``energy.domain.chat_sql``)."""

from __future__ import annotations

import pytest

from energy.domain.chat_sql import MAX_QUERY_CHARS, check_query
from energy.domain.exceptions import QueryRefusedError

pytestmark = pytest.mark.unit

NL = chr(10)


@pytest.mark.parametrize(
    ("query", "statement"),
    [
        ("SELECT 1", "SELECT 1"),
        ("  select 1  ", "select 1"),
        ("SELECT 1;", "SELECT 1"),
        ("SELECT 1 ;; ", "SELECT 1"),
        ("WITH t AS (SELECT 1 AS a) SELECT a FROM t", "WITH t AS (SELECT 1 AS a) SELECT a FROM t"),
        ("/* why */ SELECT 1", "/* why */ SELECT 1"),
        ("-- why" + NL + "SELECT 1", "-- why" + NL + "SELECT 1"),
        ("SELECT * FROM t WHERE a = 'x;y'", "SELECT * FROM t WHERE a = 'x;y'"),
        ("SELECT 'it''s; fine'", "SELECT 'it''s; fine'"),
        ('SELECT "mes;" FROM t;', 'SELECT "mes;" FROM t'),
        ("SELECT 'a -- not a comment'", "SELECT 'a -- not a comment'"),
        ("SELECT $$;$$", "SELECT $$;$$"),
        ("SELECT $tag$ ; $tag$", "SELECT $tag$ ; $tag$"),
        ("SELECT 1 /* a /* nested ; */ still */", "SELECT 1"),
        ("SELECT 1 -- trailing ;", "SELECT 1"),
    ],
)
def test_accepts_one_read(query: str, statement: str) -> None:
    assert check_query(query) == statement


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("SELECT 1; DELETE FROM t", "one statement"),
        ("SELECT 1 /* ; */; SELECT 2", "one statement"),
        ("SELECT 1 -- hidden" + NL + "; DROP TABLE t", "one statement"),
        ("UPDATE t SET a = 1", "only a SELECT"),
        ("DELETE FROM t", "only a SELECT"),
        ("COPY t TO 'x.csv'", "only a SELECT"),
        ("SET search_path = bronze", "only a SELECT"),
        ("CREATE TABLE x AS SELECT 1", "only a SELECT"),
        ("(SELECT 1)", "only a SELECT"),
        ("SELECT 'open", "not closed"),
        ('SELECT "open', "not closed"),
        ("SELECT 1 /* open", "not closed"),
        ("SELECT $$ open", "not closed"),
    ],
)
def test_refuses_anything_else(query: str, reason: str) -> None:
    with pytest.raises(QueryRefusedError) as refused:
        check_query(query)

    assert reason in refused.value.details["reason"]


def test_refuses_a_query_over_the_limit() -> None:
    query = "SELECT " + "1 + " * MAX_QUERY_CHARS + "1"

    with pytest.raises(QueryRefusedError) as refused:
        check_query(query)

    assert "longer than" in refused.value.details["reason"]


def test_the_limit_can_be_lower() -> None:
    with pytest.raises(QueryRefusedError):
        check_query("SELECT 1234567890", max_chars=10)
