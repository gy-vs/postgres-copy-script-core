#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

from sqlparse import sql
from sqlparse import tokens as T


class OutputFilter:
    varname_prefix = ''
    _quote_char = ''

    def __init__(self, varname='sql'):
        self.varname = self.varname_prefix + varname
        self.count = 0

    def _process(self, stream, varname, has_nl):
        raise NotImplementedError

    def process(self, stmt):
        self.count += 1
        if self.count > 1:
            varname = f'{self.varname}{self.count}'
        else:
            varname = self.varname

        has_nl = len(str(stmt).strip().splitlines()) > 1
        # A raw COPY data section is a single token that may itself span
        # several lines and contain quote/backslash characters. Pre-escape
        # it into one token the serializer can emit verbatim, so the
        # generated string literal is valid while decoding to the exact
        # original data.
        quote = self._quote_char
        stream = [self._prepare_copy_data(token, quote)
                  if token.ttype is T.CopyData else token
                  for token in stmt.tokens]
        stmt.tokens = self._process(stream, varname, has_nl)
        return stmt

    @staticmethod
    def _prepare_copy_data(token, quote):
        value = token.value.replace('\\', '\\\\').replace(quote, '\\' + quote)
        value = value.replace('\r', '\\r').replace('\n', '\\n')
        return sql.Token(T.CopyData, value)


class OutputPythonFilter(OutputFilter):
    _quote_char = "'"

    def _process(self, stream, varname, has_nl):
        # SQL query assignation to varname
        if self.count > 1:
            yield sql.Token(T.Whitespace, '\n')
        yield sql.Token(T.Name, varname)
        yield sql.Token(T.Whitespace, ' ')
        yield sql.Token(T.Operator, '=')
        yield sql.Token(T.Whitespace, ' ')
        if has_nl:
            yield sql.Token(T.Operator, '(')
        yield sql.Token(T.Text, "'")

        # Print the tokens on the quote
        for token in stream:
            # Token is a new line separator
            if token.is_whitespace and '\n' in token.value:
                # Close quote and add a new line
                yield sql.Token(T.Text, " '")
                yield sql.Token(T.Whitespace, '\n')

                # Quote header on secondary lines
                yield sql.Token(T.Whitespace, ' ' * (len(varname) + 4))
                yield sql.Token(T.Text, "'")

                # Indentation
                after_lb = token.value.split('\n', 1)[1]
                if after_lb:
                    yield sql.Token(T.Whitespace, after_lb)
                continue

            # Escape backslashes before quotes so a backslash preceding a
            # quote cannot break out of the generated string literal
            # (GHSA-3496-9g83-7v6x). A COPY data section was pre-escaped
            # in its entirety (including its embedded line breaks).
            elif token.ttype is T.CopyData:
                pass
            else:
                token.value = token.value.replace('\\', '\\\\').replace("'", "\\'")

            # Put the token
            yield sql.Token(T.Text, token.value)

        # Close quote
        yield sql.Token(T.Text, "'")
        if has_nl:
            yield sql.Token(T.Operator, ')')


class OutputPHPFilter(OutputFilter):
    varname_prefix = '$'
    _quote_char = '"'

    def _process(self, stream, varname, has_nl):
        # SQL query assignation to varname (quote header)
        if self.count > 1:
            yield sql.Token(T.Whitespace, '\n')
        yield sql.Token(T.Name, varname)
        yield sql.Token(T.Whitespace, ' ')
        if has_nl:
            yield sql.Token(T.Whitespace, ' ')
        yield sql.Token(T.Operator, '=')
        yield sql.Token(T.Whitespace, ' ')
        yield sql.Token(T.Text, '"')

        # Print the tokens on the quote
        for token in stream:
            # Token is a new line separator
            if token.is_whitespace and '\n' in token.value:
                # Close quote and add a new line
                yield sql.Token(T.Text, ' ";')
                yield sql.Token(T.Whitespace, '\n')

                # Quote header on secondary lines
                yield sql.Token(T.Name, varname)
                yield sql.Token(T.Whitespace, ' ')
                yield sql.Token(T.Operator, '.=')
                yield sql.Token(T.Whitespace, ' ')
                yield sql.Token(T.Text, '"')

                # Indentation
                after_lb = token.value.split('\n', 1)[1]
                if after_lb:
                    yield sql.Token(T.Whitespace, after_lb)
                continue

            # Escape backslashes before quotes so a backslash preceding a
            # quote cannot break out of the generated string literal
            # (GHSA-3496-9g83-7v6x). A COPY data section was pre-escaped
            # in its entirety (including its embedded line breaks).
            elif token.ttype is T.CopyData:
                pass
            else:
                token.value = token.value.replace('\\', '\\\\').replace('"', '\\"')

            # Put the token
            yield sql.Token(T.Text, token.value)

        # Close quote
        yield sql.Token(T.Text, '"')
        yield sql.Token(T.Punctuation, ';')
