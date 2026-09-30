# Tests for the opt-in PostgreSQL script mode handling inline
# ``COPY ... FROM STDIN`` data blocks.

import sqlparse
from sqlparse import tokens as T


SCRIPT = (
    'COPY observations (id, note) FROM STDIN WITH (FORMAT csv);\n'
    '1,"a;b"\n'
    '2,"c"\n'
    '\\.\n'
    'SELECT 42;\n'
)


# --- split -------------------------------------------------------------

def test_split_copy_default_unchanged():
    # Without the opt-in flag nothing changes: the data is parsed as SQL.
    statements = sqlparse.split(SCRIPT)
    assert statements == [
        'COPY observations (id, note) FROM STDIN WITH (FORMAT csv);',
        '1,"a;b"\n2,"c"\n\\.\nSELECT 42;',
    ]


def test_split_copy_postgres_copy():
    statements = sqlparse.split(SCRIPT, postgres_copy=True)
    assert statements == [
        'COPY observations (id, note) FROM STDIN WITH (FORMAT csv);\n'
        '1,"a;b"\n2,"c"\n\\.',
        'SELECT 42;',
    ]


def test_split_copy_keeps_header_data_and_terminator_together():
    statements = sqlparse.split(SCRIPT, postgres_copy=True)
    block = statements[0]
    assert block.startswith(
        'COPY observations (id, note) FROM STDIN WITH (FORMAT csv);')
    assert '1,"a;b"' in block
    assert '2,"c"' in block
    assert block.endswith('\\.')


def test_split_copy_data_with_semicolons():
    sql = (
        'COPY t FROM STDIN;\n'
        'SELECT 1; DROP TABLE x; (a;b)\n'
        '\\.\n'
        'SELECT 2;\n'
    )
    statements = sqlparse.split(sql, postgres_copy=True)
    assert len(statements) == 2
    assert 'SELECT 1; DROP TABLE x; (a;b)' in statements[0]
    assert statements[1] == 'SELECT 2;'


# --- parse -------------------------------------------------------------

def test_parse_copy_statements():
    statements = sqlparse.parse(SCRIPT, postgres_copy=True)
    assert len(statements) == 2
    assert str(statements[1]).strip() == 'SELECT 42;'
    assert statements[1].get_type() == 'SELECT'


def test_parse_copy_token_is_opaque():
    statement = sqlparse.parse(SCRIPT, postgres_copy=True)[0]
    token = statement.token_next_by(t=T.CopyData)[1]
    assert token is not None
    assert token.ttype is T.CopyData
    assert token.value == '\n1,"a;b"\n2,"c"\n\\.\n'


def test_parse_get_copy_data():
    statement = sqlparse.parse(SCRIPT, postgres_copy=True)[0]
    assert statement.get_copy_data() == '1,"a;b"\n2,"c"\n'


def test_parse_get_copy_data_none_without_block():
    statement = sqlparse.parse('SELECT 1;', postgres_copy=True)[0]
    assert statement.get_copy_data() is None


def test_parse_get_copy_data_none_without_flag():
    # Without postgres_copy the data is tokenized as SQL, so there is no
    # CopyData token.
    statement = sqlparse.parse(SCRIPT)[0]
    assert statement.get_copy_data() is None


def test_parse_copy_terminator_at_eof_without_newline():
    sql = 'COPY t FROM STDIN;\nx\n\\.'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 1
    assert statements[0].get_copy_data() == 'x\n'


def test_parse_copy_terminator_only_at_eof():
    sql = 'COPY t FROM STDIN;\n\\.'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 1
    assert statements[0].get_copy_data() == ''


def test_parse_copy_empty_data_block():
    sql = 'COPY t FROM STDIN;\n\\.\nSELECT 1;'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() == ''
    assert statements[1].get_type() == 'SELECT'


def test_parse_copy_crlf():
    sql = 'COPY t FROM STDIN;\r\n1,"a;b"\r\n\\.\r\nSELECT 1;\r\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() == '1,"a;b"\r\n'


def test_parse_copy_terminator_inside_quoted_field():
    sql = 'COPY t FROM STDIN;\n"\\."\nreal,row\n\\.\nSELECT 1;\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() == '"\\."\nreal,row\n'


def test_parse_copy_unterminated_block():
    # No terminator: the rest of the stream stays raw data and must not be
    # parsed as SQL.
    sql = 'COPY t FROM STDIN;\nx\nSELECT 1;\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 1
    assert statements[0].get_copy_data() == 'x\nSELECT 1;\n'


def test_parse_copy_multiple_blocks():
    sql = ('COPY a FROM STDIN;\n1\n\\.\n'
           'COPY b FROM STDIN;\n2\n\\.\n'
           'SELECT 3;\n')
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 3
    assert statements[0].get_copy_data() == '1\n'
    assert statements[1].get_copy_data() == '2\n'
    assert statements[2].get_type() == 'SELECT'


def test_parse_copy_after_other_statement():
    sql = 'SELECT 0;\nCOPY t FROM STDIN;\nx\n\\.\nSELECT 1;\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 3
    assert statements[0].get_type() == 'SELECT'
    assert statements[1].get_copy_data() == 'x\n'
    assert statements[2].get_type() == 'SELECT'


def test_parse_copy_file_variant_splits_normally():
    # COPY ... FROM 'file' has no STDIN data and behaves as ordinary SQL.
    sql = "COPY t FROM 'data.csv';\nSELECT 1;\n"
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() is None


def test_parse_copy_column_named_copy_unaffected():
    sql = 'SELECT copy FROM t;\nSELECT 1;\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() is None


def test_parse_copy_header_case_insensitive():
    sql = 'copy t from stdin;\nx\n\\.\nselect 1;\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() == 'x\n'


def test_parse_copy_from_inside_column_list_ignored():
    # A FROM-like token inside the (column list) must not be counted.
    sql = 'COPY t ("from", stdin) FROM STDIN;\nx\n\\.\nSELECT 1;\n'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert statements[0].get_copy_data() == 'x\n'


def test_parse_copy_grouping_still_applies_to_header():
    statement = sqlparse.parse(SCRIPT, postgres_copy=True)[0]
    # The column list of the header is still grouped as a Parenthesis.
    classes = {type(t).__name__ for t in statement.tokens}
    assert 'Parenthesis' in classes


def test_parse_plain_sql_unaffected_in_copy_mode():
    statements = sqlparse.parse(
        'SELECT a, b FROM t WHERE x = 1;', postgres_copy=True)
    assert len(statements) == 1
    assert statements[0].get_type() == 'SELECT'


def test_parse_copy_terminator_must_start_at_line_beginning():
    # Whitespace before "\." means it is ordinary data (psql rule).
    sql = 'COPY t FROM STDIN;\n  \\.\nreal\n\\.\nSELECT 1;'
    statements = sqlparse.parse(sql, postgres_copy=True)
    assert len(statements) == 2
    assert '  \\.' in statements[0].get_copy_data()
    assert statements[0].get_copy_data().endswith('real\n')


def test_split_copy_with_strip_semicolon():
    statements = sqlparse.split(
        'COPY t FROM STDIN;\nx\n\\.\nSELECT 1;',
        postgres_copy=True, strip_semicolon=True)
    assert statements[0].endswith('\\.')
    assert statements[1] == 'SELECT 1'


def test_parse_copy_header_is_fully_analyzable():
    statement = sqlparse.parse(SCRIPT, postgres_copy=True)[0]
    flattened = [(t.ttype, t.value) for t in statement.flatten()]
    # Header tokens precede the opaque block and keep their real types.
    assert T.Keyword in [tt for tt, _ in flattened]
    assert any(v == 'COPY' for _, v in flattened)
    assert any(v == 'STDIN' for _, v in flattened)


# --- format ------------------------------------------------------------

def test_format_copy_preserves_data_by_default():
    # Without reindent the text round-trips apart from line rstrip.
    out = sqlparse.format(SCRIPT, postgres_copy=True)
    assert '1,"a;b"\n2,"c"\n\\.' in out


def test_format_copy_reindent_keeps_data_verbatim():
    out = sqlparse.format(SCRIPT, reindent=True, postgres_copy=True)
    # Data rows survive with original characters and line boundaries.
    assert '\n1,"a;b"\n2,"c"\n\\.\n' in out
    # The header SQL is reformatted.
    assert 'FROM STDIN' in out
    # The trailing SQL is reformatted independently.
    assert 'SELECT 42' in out


def test_format_copy_reindent_does_not_insert_spaces_in_data():
    out = sqlparse.format(SCRIPT, reindent=True, postgres_copy=True)
    data = sqlparse.parse(SCRIPT, postgres_copy=True)[0].get_copy_data()
    assert data in out


def test_format_copy_keyword_case_only_touches_sql():
    sql = 'copy t from stdin;\nx\n\\.\nselect 1;\n'
    out = sqlparse.format(sql, keyword_case='upper', postgres_copy=True)
    assert 'COPY t FROM STDIN;' in out
    assert '\nx\n\\.\n' in out
    assert 'SELECT 1;' in out


def test_format_copy_data_keeps_trailing_whitespace():
    sql = 'COPY t FROM STDIN;\nrow with trailing   \n\\.\n'
    out = sqlparse.format(sql, postgres_copy=True)
    assert 'row with trailing   \n' in out


def test_format_copy_default_mode_still_reformats_data_as_before():
    # Without the flag the data is treated as ordinary SQL tokens, so the
    # "a;b" row does not survive on its own line the way it does with
    # postgres_copy enabled.
    default = sqlparse.format(SCRIPT, reindent=True)
    enabled = sqlparse.format(SCRIPT, reindent=True, postgres_copy=True)
    assert '\n1,"a;b"\n' not in default
    assert '\n1,"a;b"\n' in enabled


def test_format_copy_truncate_strings_keeps_data():
    sql = "COPY t FROM STDIN;\n'abcdefghij'\n\\.\nSELECT 'abcdefghij';\n"
    out = sqlparse.format(sql, postgres_copy=True, truncate_strings=5)
    assert "'abcdefghij'\n\\." in out
    assert out.count("'abcde[...]'") == 1


def test_format_copy_identifier_case_keeps_data():
    sql = 'COPY t FROM STDIN;\nSELECT foo\n\\.\nSELECT BAR;\n'
    out = sqlparse.format(sql, postgres_copy=True, identifier_case='upper')
    assert '\nSELECT foo\n' in out
    assert 'SELECT BAR' in out


def test_format_copy_unicode_data_preserved():
    sql = 'COPY t FROM STDIN;\n1,äöü 日本語\n\\.\n'
    assert '1,äöü 日本語' in sqlparse.format(sql, postgres_copy=True)


def test_format_copy_eof_terminator_roundtrips():
    sql = 'COPY t FROM STDIN;\nx\n\\.'
    assert sqlparse.format(sql, postgres_copy=True) == sql


# --- CLI ---------------------------------------------------------------

def test_cli_postgres_copy_stdin(monkeypatch, capsys):
    import sys
    import io
    from sqlparse import cli

    monkeypatch.setattr(sys, 'stdin',
                        io.TextIOWrapper(io.BytesIO(SCRIPT.encode())))
    assert cli.main(['-', '--postgres-copy', '-r']) == 0
    out = capsys.readouterr().out
    assert '\n1,"a;b"\n2,"c"\n\\.\n' in out
    assert 'SELECT 42' in out


def test_cli_postgres_copy_flag_absent_by_default(monkeypatch, capsys):
    import sys
    import io
    from sqlparse import cli

    monkeypatch.setattr(sys, 'stdin',
                        io.TextIOWrapper(io.BytesIO(b'SELECT 1;\n')))
    assert cli.main(['-']) == 0
    assert capsys.readouterr().out == 'SELECT 1;'
