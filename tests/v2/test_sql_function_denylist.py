"""``check_query`` refuses SELECTs that call functions reading files, reaching other servers or changing state."""

from __future__ import annotations

import pytest

from testzeus_hercules.act.sql import QueryRefused, check_query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT LOAD_FILE('/etc/passwd')",
        "select load_file ('/etc/passwd') as x",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT \"pg_read_file\"('/etc/passwd')",
        "SELECT pg_read_binary_file('x')",
        "SELECT * FROM pg_ls_dir('.')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT * FROM dblink('host=evil', 'select 1') AS t(a int)",
        "SELECT dblink_exec('host=evil', 'drop table x')",
        "SELECT pg_sleep(100)",
        "SELECT SLEEP(100)",
        "SELECT BENCHMARK(100000000, MD5('x'))",
        "SELECT * FROM users INTO OUTFILE '/tmp/users.csv'",
        "SELECT * FROM users INTO DUMPFILE '/tmp/x'",
        "SELECT nextval('orders_id_seq')",
        "SELECT set_config('role', 'admin', false)",
        "WITH x AS (SELECT pg_read_file('/etc/passwd') AS f) SELECT f FROM x",
        "SELECT /* hidden */ load_file('/x')",
    ],
)
def test_dangerous_functions_are_refused(query: str) -> None:
    with pytest.raises(QueryRefused):
        check_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT name, sleep_hours FROM pets WHERE note = 'pg_sleep(1) is a function'",
        "SELECT load_filename FROM uploads",
        "SELECT benchmark_score FROM runs -- benchmark(1)",
        "SELECT count(*) FROM pets",
    ],
)
def test_plain_selects_mentioning_those_names_are_allowed(query: str) -> None:
    assert check_query(query) == query
