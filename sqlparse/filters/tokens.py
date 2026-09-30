#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

import re

from sqlparse import tokens as T


class _CaseFilter:
    ttype = None

    def __init__(self, case=None):
        case = case or 'upper'
        self.convert = getattr(str, case)

    def process(self, stream):
        for ttype, value in stream:
            if ttype in self.ttype:
                value = self.convert(value)
            yield ttype, value


class KeywordCaseFilter(_CaseFilter):
    ttype = T.Keyword


class IdentifierCaseFilter(_CaseFilter):
    ttype = T.Name, T.String.Symbol

    def process(self, stream):
        for ttype, value in stream:
            if ttype in self.ttype and value.strip()[0] != '"':
                value = self.convert(value)
            yield ttype, value


class TruncateStringFilter:
    def __init__(self, width, char):
        self.width = width
        self.char = char

    def process(self, stream):
        for ttype, value in stream:
            if ttype != T.Literal.String.Single:
                yield ttype, value
                continue

            if value[:2] == "''":
                inner = value[2:-2]
                quote = "''"
            else:
                inner = value[1:-1]
                quote = "'"

            if len(inner) > self.width:
                value = ''.join((quote, inner[:self.width], self.char, quote))
            yield ttype, value


COPY_TERMINATOR = '\\.'
_COPY_LINE_END = re.compile(r'\r\n|\r|\n')


class CopyDataFilter:
    """Preprocess filter for PostgreSQL script mode.

    The raw data following a ``COPY ... FROM STDIN`` header is not SQL.
    This filter passes the stream through but, while a data block is open,
    re-types its tokens as the opaque :data:`~sqlparse.tokens.CopyData`
    type so subsequent preprocess filters (keyword/identifier casing,
    string truncation) leave the data untouched. The statement splitter
    later groups those tokens into one block using the same header and
    terminator rules.
    """

    def process(self, stream):
        in_data = False
        line = ''
        # Header recognition.
        await_first = True
        is_copy = False
        seen_from = False
        seen_stdin = False
        depth = 0

        for ttype, value in stream:
            if in_data:
                # Split this token at physical line boundaries and check
                # each completed line for the terminator.
                pos = 0
                terminated_at = None
                for match in _COPY_LINE_END.finditer(value):
                    content = line + value[pos:match.start()]
                    end = match.end()
                    if content == COPY_TERMINATOR:
                        terminated_at = end
                        break
                    line = ''
                    pos = end
                else:
                    # No terminator: remember the trailing partial line and
                    # forward the whole token as opaque data.
                    line += value[pos:]
                    yield T.CopyData, value
                    continue

                # Prefix up to and including the terminator line break is
                # opaque data; the splitter recognizes and groups it.
                yield T.CopyData, value[:terminated_at]
                in_data = False
                line = ''
                await_first = True
                is_copy = False
                seen_from = False
                seen_stdin = False
                depth = 0
                # The standard lexer emits line breaks as standalone
                # tokens, so nothing follows the break in this token. Any
                # suffix would belong to the next SQL token; emit it as a
                # neutral token in that (theoretical) case.
                value = value[terminated_at:]
                ttype = T.Other
                if not value:
                    continue

            # Track parenthesis depth and the COPY header.
            if ttype is T.Punctuation and value == '(':
                depth += 1
            elif ttype is T.Punctuation and value == ')':
                depth = max(0, depth - 1)
            elif ttype not in T.Whitespace and ttype not in T.Comment:
                if await_first:
                    await_first = False
                    is_copy = (ttype in T.Keyword
                               and value.upper() == 'COPY')
                elif is_copy and depth == 0 and ttype in T.Keyword:
                    unified = value.upper()
                    if unified == 'FROM':
                        seen_from = True
                    elif unified == 'STDIN':
                        seen_stdin = True

            yield ttype, value

            if (ttype is T.Punctuation and value == ';'
                    and is_copy and seen_from and seen_stdin
                    and depth == 0):
                in_data = True
                line = ''
                is_copy = False
                seen_from = False
                seen_stdin = False
                depth = 0

        # A pending "\." at EOF has already been emitted token-by-token as
        # CopyData; the statement splitter recognizes it as the terminator.
