# Tests for the opt-in PostgreSQL script mode (``pg_copy``), which keeps
# a ``COPY ... FROM STDIN`` statement together with its raw data section
# and terminating ``\.`` line as one execution unit.

import sqlparse
from sqlparse import sql
from sqlparse import tokens as T
from sqlparse.exceptions import SQLParseError

COPY_SQL = (
    'COPY observations (id, note) FROM STDIN WITH (FORMAT csv);\n'
    '1,"a;b"\n'
    '2,"c"\n'
    '\\.\n'
    'SELECT 42;\n'
)


# ---------------------------------------------------------------- split

def test_split_default_unchanged():
    # Without the opt-in flag the historical behavior is preserved.
    stmts = sqlparse.split(COPY_SQL)
    assert len(stmts) == 2
    assert stmts[0].startswith('COPY observations')
    assert 'SELECT 42;' in stmts[1]


def test_split_pg_copy_keeps_unit():
    stmts = sqlparse.split(COPY_SQL, pg_copy=True)
    assert len(stmts) == 2
    assert stmts[0] == COPY_SQL[:-len('SELECT 42;\n')].strip()
    assert stmts[1] == 'SELECT 42;'


def test_split_pg_copy_strip_semicolon_keeps_data():
    stmts = sqlparse.split(COPY_SQL, pg_copy=True, strip_semicolon=True)
    assert stmts[0].startswith(
        'COPY observations (id, note) FROM STDIN WITH (FORMAT csv)\n')
    assert stmts[0].endswith('\\.')
    assert '1,"a;b"' in stmts[0]
    assert stmts[1] == 'SELECT 42'


# ---------------------------------------------------------------- parse

def test_parse_pg_copy_statement_boundaries():
    stmts = sqlparse.parse(COPY_SQL, pg_copy=True)
    assert len(stmts) == 2
    assert isinstance(stmts[0], sql.Statement)
    assert isinstance(stmts[1], sql.Statement)
    assert stmts[1].get_type() == 'SELECT'


def test_parse_pg_copy_data_is_opaque_token():
    stmts = sqlparse.parse(COPY_SQL, pg_copy=True)
    [data] = [t for t in stmts[0].flatten() if t.ttype is T.CopyData]
    assert data.value == '\n1,"a;b"\n2,"c"\n\\.\n'
    # the trailing statement does not contain data
    assert not [t for t in stmts[1].flatten()
                if t.ttype is T.CopyData]


def test_parse_pg_copy_header_still_grouped():
    stmts = sqlparse.parse(COPY_SQL, pg_copy=True)
    # the header is processed by the regular grouping machinery
    assert any(isinstance(t, sql.Parenthesis) for t in stmts[0].tokens)


def test_parse_pg_copy_data_looking_like_sql():
    sql_ = (
        'COPY t (a) FROM STDIN WITH (FORMAT csv);\n'
        'SELECT 1); INSERT INTO x VALUES ($$);\n'
        '"\\. inside"\n'
        '\\.\n'
        'SELECT 1;\n'
    )
    stmts = sqlparse.parse(sql_, pg_copy=True)
    assert len(stmts) == 2
    [data] = [t for t in stmts[0].flatten() if t.ttype is T.CopyData]
    assert data.value == (
        '\nSELECT 1); INSERT INTO x VALUES ($$);\n'
        '"\\. inside"\n\\.\n')
    assert str(stmts[1]) == 'SELECT 1;'


def test_parse_pg_copy_only_copy_from_stdin_starts_data():
    # COPY FROM 'file' is plain SQL and must not start a data section
    stmts = sqlparse.parse(
        "COPY t FROM 'data.csv' WITH (FORMAT csv); SELECT 1;",
        pg_copy=True)
    assert len(stmts) == 2
    assert not [t for s in stmts for t in s.flatten()
                if t.ttype is T.CopyData]


def test_parse_pg_copy_columns_named_from_stdin():
    # Identifiers inside the column list coincidentally named from/stdin
    # must not be mistaken for the FROM STDIN clause.
    stmts = sqlparse.parse(
        "COPY froms (from, stdin) FROM 'file'; SELECT 1;",
        pg_copy=True)
    assert len(stmts) == 2
    assert not [t for s in stmts for t in s.flatten()
                if t.ttype is T.CopyData]


def test_parse_pg_copy_case_insensitive_and_comment():
    stmts = sqlparse.parse(
        '-- leading comment\ncopy t from stdin;\nx\n\\.\nselect 1;',
        pg_copy=True)
    assert len(stmts) == 2
    assert [t for t in stmts[0].flatten() if t.ttype is T.CopyData]
    assert str(stmts[1]) == 'select 1;'


def test_parse_pg_copy_newline_styles():
    for nl in ('\n', '\r\n', '\r'):
        sql_ = f'COPY t FROM STDIN;{nl}1{nl}\\.{nl}SELECT 1;'
        stmts = sqlparse.parse(sql_, pg_copy=True)
        assert len(stmts) == 2, nl
        [data] = [t for t in stmts[0].flatten()
                  if t.ttype is T.CopyData]
        assert data.value == f'{nl}1{nl}\\.{nl}', repr(data.value)
        assert str(stmts[1]) == 'SELECT 1;'


def test_parse_pg_copy_terminator_at_eof():
    sql_ = 'COPY t FROM STDIN;\nx\n\\.'
    stmts = sqlparse.parse(sql_, pg_copy=True)
    assert len(stmts) == 1
    assert str(stmts[0]) == sql_


def test_parse_pg_copy_without_terminator_consumes_remainder():
    sql_ = 'COPY t FROM STDIN;\nfoo\nbar\n'
    stmts = sqlparse.parse(sql_, pg_copy=True)
    assert len(stmts) == 1
    assert str(stmts[0]) == sql_


def test_parse_pg_copy_among_other_statements():
    sql_ = 'SELECT 1;\nCOPY t FROM STDIN;\nd\n\\.\nSELECT 2;'
    stmts = sqlparse.parse(sql_, pg_copy=True)
    assert len(stmts) == 3
    assert str(stmts[0]) == 'SELECT 1;'
    assert str(stmts[2]) == 'SELECT 2;'


# ---------------------------------------------------------------- format

def test_format_pg_copy_keeps_data_and_reformats_sql():
    out = sqlparse.format(COPY_SQL, pg_copy=True, reindent=True)
    # header got reformatted onto multiple lines
    assert out.startswith('COPY observations (id, note)\nFROM STDIN')
    # data is byte-identical
    assert '1,"a;b"\n2,"c"\n\\.\n' in out
    # trailing statement still passes the formatting pipeline
    assert out.endswith('SELECT 42;')


def test_format_pg_copy_data_roundtrips():
    out = sqlparse.format(COPY_SQL, pg_copy=True, reindent=True,
                          keyword_case='upper')
    # extract and re-run: data section must survive another pass
    stmts = sqlparse.parse(out, pg_copy=True)
    assert len(stmts) == 2
    [data] = [t for t in stmts[0].flatten() if t.ttype is T.CopyData]
    assert data.value == '\n1,"a;b"\n2,"c"\n\\.\n'


def test_format_pg_copy_keyword_case_leaves_data_alone():
    sql_ = 'copy t from stdin;\nselect (1);\n\\.\nSELECT 1;'
    out = sqlparse.format(sql_, pg_copy=True, keyword_case='upper')
    assert out.startswith('COPY t FROM STDIN;')
    assert '\nselect (1);\n\\.\n' in out


def test_format_pg_copy_python_output_roundtrips():
    out = sqlparse.format(COPY_SQL, pg_copy=True, output_format='python')
    namespace = {}
    exec(out, namespace)
    assert namespace['sql'] == (
        'COPY observations (id, note) FROM STDIN WITH (FORMAT csv);\n'
        '1,"a;b"\n2,"c"\n\\.\n')
    assert namespace['sql2'] == 'SELECT 42;'


def test_format_pg_copy_python_output_escapes_data():
    sql_ = "COPY t FROM STDIN;\na\\b'x;y\n\\.\nSELECT 1;"
    out = sqlparse.format(sql_, pg_copy=True, output_format='python')
    namespace = {}
    exec(out, namespace)
    assert namespace['sql'] == "COPY t FROM STDIN;\na\\b'x;y\n\\.\n"


def test_format_pg_copy_php_output_escapes_quotes():
    out = sqlparse.format('COPY t FROM STDIN;\nx"q\n\\.\nSELECT 1;',
                          pg_copy=True, output_format='php')
    assert 'x\\"q' in out


def test_format_pg_copy_invalid_option():
    import pytest
    with pytest.raises(SQLParseError):
        sqlparse.format('SELECT 1;', pg_copy='yes')
