#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""Parse SQL statements."""

# Setup namespace
from collections.abc import Generator
from typing import IO, Any

from sqlparse import cli, engine, filters, formatter, sql, tokens

__version__ = "0.6.0"
__all__ = ["cli", "engine", "filters", "formatter", "sql", "tokens"]


def parse(
    sql: str, encoding: str | None = None, pg_copy: bool = False
) -> tuple[sql.Statement, ...]:
    """Parse sql and return a list of statements.

    :param sql: A string containing one or more SQL statements.
    :param encoding: The encoding of the statement (optional).
    :param pg_copy: If True, parse as a PostgreSQL script: a
        ``COPY ... FROM STDIN`` statement together with its raw data
        section and the terminating ``\\.`` line forms a single
        statement. The data is kept verbatim and is not parsed as SQL
        (optional).
    :returns: A tuple of :class:`~sqlparse.sql.Statement` instances.
    """
    return tuple(parsestream(sql, encoding, pg_copy=pg_copy))


def parsestream(
    stream: str | IO[str],
    encoding: str | None = None,
    pg_copy: bool = False,
) -> Generator[sql.Statement, None, None]:
    """Parses sql statements from file-like object.

    :param stream: A file-like object.
    :param encoding: The encoding of the stream contents (optional).
    :param pg_copy: See :func:`parse` (optional).
    :returns: A generator of :class:`~sqlparse.sql.Statement` instances.
    """
    stack = engine.FilterStack(pg_copy=pg_copy)
    stack.enable_grouping()
    return stack.run(stream, encoding)


def format(sql: str, encoding: str | None = None, **options: Any) -> str:
    """Format *sql* according to *options*.

    Available options are documented in :ref:`formatting`.

    In addition to the formatting options this function accepts the
    keyword "encoding" which determines the encoding of the statement.

    Pass ``pg_copy=True`` to treat the input as a PostgreSQL script:
    ``COPY ... FROM STDIN`` data sections are kept verbatim instead of
    being reformatted.

    :returns: The formatted SQL statement as string.
    """
    pg_copy = options.pop('pg_copy', False)
    stack = engine.FilterStack(pg_copy=pg_copy)
    options = formatter.validate_options(options)
    stack = formatter.build_filter_stack(stack, options)
    stack.postprocess.append(filters.SerializerUnicode())
    return "".join(stack.run(sql, encoding))


def split(
    sql: str,
    encoding: str | None = None,
    strip_semicolon: bool = False,
    pg_copy: bool = False,
) -> list[str]:
    """Split *sql* into single statements.

    :param sql: A string containing one or more SQL statements.
    :param encoding: The encoding of the statement (optional).
    :param strip_semicolon: If True, remove trailing semicolons
        (default: False).
    :param pg_copy: If True, treat ``COPY ... FROM STDIN`` together with
        its raw data section and the ``\\.`` terminator as one statement
        (default: False).
    :returns: A list of strings.
    """
    stack = engine.FilterStack(strip_semicolon=strip_semicolon,
                               pg_copy=pg_copy)
    return [str(stmt).strip() for stmt in stack.run(sql, encoding)]
