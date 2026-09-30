#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

import re

from sqlparse import sql
from sqlparse import tokens as T


# A PostgreSQL ``COPY ... FROM STDIN`` block is terminated by a line
# containing only ``\.`` (psql's ``copyend`` marker).  The marker is only
# recognized at a physical line boundary, so a ``\.`` appearing inside a
# CSV field (e.g. ``"\."``) never terminates the block.
_COPY_TERMINATOR = '\\.'
_COPY_LINE_END = re.compile(r'\r\n|\r|\n')


class StatementSplitter:
    """Filter that split stream at individual statements

    When ``postgres_copy`` is enabled, inline data following a PostgreSQL
    ``COPY ... FROM STDIN`` header (up to and including the ``\\.``
    terminator line) is captured verbatim as opaque
    :data:`~sqlparse.tokens.CopyData` tokens.  The data is not SQL, so
    keywords, parentheses, semicolons and quotes inside it must not affect
    statement splitting; it is therefore kept out of the regular token
    handling entirely.
    """

    def __init__(self, postgres_copy=False):
        self._postgres_copy = postgres_copy
        self._reset()

    def _reset(self):
        """Set the filter attributes to its default values"""
        self._block_stack = []
        self._parenthesis_level = 0
        self._unconfirmed_start = None
        self._is_create = False
        self._seen_begin = False

        # State of an inline ``COPY ... FROM STDIN`` data block.
        self._in_copy_data = False
        self._copy_lines = []
        self._copy_line = ''
        # Header recognition: ``COPY`` has to be the first significant
        # keyword of the statement, and ``FROM`` / ``STDIN`` have to appear
        # outside of the column list parentheses.
        self._copy_await_first = True
        self._copy_is_copy = False
        self._copy_seen_from = False
        self._copy_seen_stdin = False

        self.consume_ws = False
        self.tokens = []
        self.level = 0

    def _handle_nested_block(self, unified):
        """Check for nested loop or control structures inside a block"""
        if unified == 'FOR':
            self._unconfirmed_start = 'FOR'
            return 0
        if unified == 'WHILE':
            self._unconfirmed_start = 'WHILE'
            return 0
        if unified in ('LOOP', 'DO'):
            if self._unconfirmed_start in ('FOR', 'WHILE'):
                self._block_stack.append(self._unconfirmed_start)
                self._unconfirmed_start = None
                return 1
            if unified == 'LOOP':
                self._block_stack.append('LOOP')
                return 1
        if unified in ('IF', 'CASE'):
            self._block_stack.append(unified)
            return 1
        return None

    def _handle_closing_keyword(self, unified):
        """Handle closing keywords for blocks"""
        if unified == 'END IF':
            if self._block_stack and self._block_stack[-1] == 'IF':
                self._block_stack.pop()
                return -1
        elif unified == 'END FOR':
            if self._block_stack and self._block_stack[-1] == 'FOR':
                self._block_stack.pop()
                return -1
        elif unified == 'END WHILE':
            if self._block_stack and self._block_stack[-1] == 'WHILE':
                self._block_stack.pop()
                return -1
        elif unified == 'END LOOP':
            if (self._block_stack and
                    self._block_stack[-1] in ('LOOP', 'FOR', 'WHILE')):
                self._block_stack.pop()
                return -1
        elif unified == 'END CASE':
            if self._block_stack and self._block_stack[-1] == 'CASE':
                self._block_stack.pop()
                return -1
        elif unified == 'END':
            if self._block_stack:
                self._block_stack.pop()
            return -1
        return 0

    def _change_splitlevel(self, ttype, value):
        """Get the new split level (increase, decrease or remain equal)"""

        # Semicolon resets unconfirmed loop starters
        # and handles standalone BEGIN;
        if ttype is T.Punctuation and value == ';':
            self._unconfirmed_start = None
            if self._seen_begin:
                self._seen_begin = False
                if self._block_stack and self._block_stack[-1] == 'BEGIN':
                    self._block_stack.pop()
                    return -1
            return 0

        # parenthesis increase/decrease a level
        if ttype is T.Punctuation and value == '(':
            self._parenthesis_level += 1
            return 1
        elif ttype is T.Punctuation and value == ')':
            self._parenthesis_level = max(0, self._parenthesis_level - 1)
            return -1
        elif ttype not in T.Keyword:  # if normal token return
            return 0

        # Everything after here is ttype = T.Keyword
        unified = value.upper()

        # DDL Create though can contain more words such as "or replace"
        if ttype is T.Keyword.DDL and unified.startswith('CREATE'):
            self._is_create = True
            return 0

        # Handle DECLARE block start (only for CREATE statements)
        if unified == 'DECLARE' and self._is_create and not self._block_stack:
            self._block_stack.append('DECLARE')
            return 1

        # Handle BEGIN block start
        if unified == 'BEGIN':
            self._seen_begin = True
            # Transition DECLARE to BEGIN if present
            if self._block_stack and self._block_stack[-1] == 'DECLARE':
                self._block_stack.pop()
                self._block_stack.append('BEGIN')
                return 0
            else:
                self._block_stack.append('BEGIN')
                return 1

        # Issue826: If we see a transaction keyword after BEGIN,
        # it's a transaction statement, not a block.
        if self._seen_begin and \
                (ttype is T.Keyword or ttype is T.Name) and \
                unified in ('TRANSACTION', 'WORK', 'TRAN',
                            'DISTRIBUTED', 'DEFERRED',
                            'IMMEDIATE', 'EXCLUSIVE'):
            self._seen_begin = False
            if self._block_stack and self._block_stack[-1] == 'BEGIN':
                self._block_stack.pop()
                return -1
            return 0

        # Inside a block, check for nested loop or control structures
        if 'BEGIN' in self._block_stack:
            res = self._handle_nested_block(unified)
            if res is not None:
                return res

        # Handle closing keywords
        return self._handle_closing_keyword(unified)

    def _track_copy_header(self, ttype, value):
        """Track a possible ``COPY ... FROM STDIN`` header.

        Only significant for the first statement keyword ``COPY`` and for
        ``FROM`` / ``STDIN`` occurring at parenthesis level 0, i.e. outside
        of the optional ``(column, ...)`` list.
        """
        if ttype in T.Whitespace or ttype in T.Comment:
            return
        if self._copy_await_first:
            self._copy_await_first = False
            self._copy_is_copy = ttype in T.Keyword and value.upper() == 'COPY'
            return
        if not self._copy_is_copy or self._parenthesis_level != 0:
            return
        if ttype in T.Keyword:
            unified = value.upper()
            if unified == 'FROM':
                self._copy_seen_from = True
            elif unified == 'STDIN':
                self._copy_seen_stdin = True

    def _is_copy_header_semicolon(self, ttype, value):
        """True if *ttype* / *value* terminate a ``COPY ... FROM STDIN``
        header, i.e. the following tokens are raw data rather than SQL."""
        return (
            self._postgres_copy
            and ttype is T.Punctuation and value == ';'
            and self._copy_is_copy
            and self._copy_seen_from
            and self._copy_seen_stdin
            and self._parenthesis_level == 0)

    def _start_copy_data(self):
        self._in_copy_data = True
        # Complete lines already finalized (each including its line
        # break) and the text accumulated on the current, unfinished line.
        self._copy_lines = []
        self._copy_line = ''
        # The header semicolon does not end the statement: the data block
        # is part of it, so the semicolon must not trigger the end-of-
        # statement whitespace consumption. consume_ws gets re-enabled in
        # _finish_copy_data() once the terminator line is seen.
        self.consume_ws = False

    def _finish_copy_data(self):
        """Close the open data block, appending its raw text (completed
        lines plus the terminator line) as a single opaque token."""
        data = ''.join(self._copy_lines)
        self.tokens.append(sql.Token(T.CopyData, data))
        self._in_copy_data = False
        self._copy_lines = []
        self._copy_line = ''
        # The next token belongs to a new statement, just as after a
        # regular semicolon.
        self.consume_ws = True

    def _feed_copy_data(self, ttype, value):
        """Consume one lexed token while inside a COPY data block.

        The raw text is accumulated physical line by physical line; a
        line containing only ``\\.`` closes the block. Returns
        ``(ttype, value)`` pairs left over past the terminator that have
        to be processed as SQL again (empty in practice: the lexer
        emits line breaks as standalone newline tokens, so nothing ever
        shares a token with the terminating break)."""
        rest = []
        pos = 0
        for match in _COPY_LINE_END.finditer(value):
            content = self._copy_line + value[pos:match.start()]
            end = match.end()
            line_text = content + value[match.start():end]
            self._copy_line = ''
            pos = end
            if content == _COPY_TERMINATOR:
                # The terminator line closes the data block.
                self._copy_lines.append(line_text)
                self._finish_copy_data()
                if end < len(value):
                    rest.append((ttype, value[end:]))
                return rest
            self._copy_lines.append(line_text)
        self._copy_line += value[pos:]
        return rest

    def _flush_copy_data(self):
        """Flush a data block that reaches the end of the stream.

        An unterminated block (no ``\\.`` line) is kept as data too: its
        contents must never be parsed as SQL. A ``\\.`` on the final line
        without a trailing line break is still the terminator."""
        if not self._in_copy_data:
            return
        # The pending line is appended verbatim -- even if it is a
        # ``\.`` terminator without a trailing line break, the opaque
        # token must preserve the original text. get_copy_data() strips
        # the terminator from the returned payload.
        self._copy_lines.append(self._copy_line)
        data = ''.join(self._copy_lines)
        self.tokens.append(sql.Token(T.CopyData, data))
        self._in_copy_data = False
        self._copy_lines = []
        self._copy_line = ''

    def _sql_token(self, ttype, value):
        """Process one token of the regular SQL token stream.

        Returns the list of statements completed by this token (usually
        empty)."""
        EOS_TTYPE = T.Whitespace, T.Comment.Single
        emitted = []

        # Yield token if we finished a statement and there's no whitespaces
        # It will count newline token as a non whitespace. In this context
        # whitespace ignores newlines.
        # why don't multi line comments also count?
        if self.consume_ws and ttype not in EOS_TTYPE:
            emitted.append(sql.Statement(self.tokens))

            # Reset filter and prepare to process next statement
            self._reset()

        # Change current split level (increase, decrease or remain equal)
        self.level += self._change_splitlevel(ttype, value)

        # Append the token to the current statement
        self.tokens.append(sql.Token(ttype, value))

        if self._postgres_copy:
            self._track_copy_header(ttype, value)

        # Check if we get the end of a statement
        # Issue762: Allow GO (or "GO 2") as statement splitter.
        # When implementing a language toggle, it's not only to add
        # keywords it's also to change some rules, like this splitting
        # rule.
        # Issue809: Ignore semicolons inside BEGIN...END blocks, but handle
        # standalone BEGIN; as a transaction statement
        if ttype is T.Punctuation and value == ';':
            self._seen_begin = False
            if self._is_copy_header_semicolon(ttype, value):
                # Everything up to the ``\.`` line is raw data belonging
                # to this statement, so the header semicolon does not
                # split.
                self._start_copy_data()
            # Split on semicolon if not inside a BEGIN...END block
            elif self.level <= 0 and 'BEGIN' not in self._block_stack:
                self.consume_ws = True
        elif ttype is T.Keyword and value.split()[0] == 'GO':
            self.consume_ws = True
        elif (ttype not in (T.Whitespace, T.Newline, T.Comment.Single,
                            T.Comment.Multiline)
              and not (ttype is T.Keyword and value.upper() == 'BEGIN')):
            # Reset _seen_begin if we see a non-whitespace, non-comment
            # token but not for BEGIN itself (which just set the flag)
            self._seen_begin = False

        return emitted

    def process(self, stream):
        """Process the stream"""
        # Run over all stream tokens
        for ttype, value in stream:
            if self._in_copy_data:
                for ttype_, value_ in self._feed_copy_data(ttype, value):
                    yield from self._sql_token(ttype_, value_)
                continue
            yield from self._sql_token(ttype, value)

        # An open COPY data block is part of the pending statement.
        self._flush_copy_data()

        # Yield pending statement (if any)
        if self.tokens and not all(t.is_whitespace for t in self.tokens):
            yield sql.Statement(self.tokens)
