#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

"""filter"""

from sqlparse import lexer
from sqlparse.engine import grouping
from sqlparse.engine.statement_splitter import StatementSplitter
from sqlparse.exceptions import SQLParseError
from sqlparse.filters import StripTrailingSemicolonFilter
from sqlparse.filters.tokens import CopyDataFilter


class FilterStack:
    def __init__(self, strip_semicolon=False, postgres_copy=False):
        self.preprocess = []
        self.stmtprocess = []
        self.postprocess = []
        self._grouping = False
        self._postgres_copy = postgres_copy
        # Must run before any other preprocess filter so that the raw data
        # of COPY blocks is already opaque for keyword/identifier casing
        # and string truncation filters.
        if postgres_copy:
            self.preprocess.append(CopyDataFilter())
        if strip_semicolon:
            self.stmtprocess.append(StripTrailingSemicolonFilter())

    def enable_grouping(self):
        self._grouping = True

    def run(self, sql, encoding=None):
        try:
            stream = lexer.tokenize(sql, encoding)
            # Process token stream
            for filter_ in self.preprocess:
                stream = filter_.process(stream)

            stream = StatementSplitter(
                postgres_copy=self._postgres_copy).process(stream)

            # Output: Stream processed Statements
            for stmt in stream:
                if self._grouping:
                    stmt = grouping.group(stmt)

                for filter_ in self.stmtprocess:
                    filter_.process(stmt)

                for filter_ in self.postprocess:
                    stmt = filter_.process(stmt)

                yield stmt
        except RecursionError as err:
            raise SQLParseError('Maximum recursion depth exceeded') from err
