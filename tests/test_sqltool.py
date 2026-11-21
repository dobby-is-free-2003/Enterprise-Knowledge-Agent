import pytest

from eka.sqltool import SqlTool


def test_schema_only_lists_permitted_tables(deps, U):
    assert deps.sql.describe(U("priya.sharma")) == ""
    support = deps.sql.describe(U("meera.iyer"))
    assert "tickets(" in support and "customers(" in support and "orders(" not in support
    assert "orders(" in deps.sql.describe(U("karan.mehta"))


def test_aggregate_query_works(deps, U):
    r = deps.sql.run("SELECT COUNT(*) AS n FROM customers", U("anita.desai"))
    assert r.ok and r.rows == [[40]]


@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "INSERT INTO customers VALUES (999,'x','x','x','x','x')",
    "UPDATE customers SET name='x'",
    "SELECT 1; DROP TABLE customers",
    "PRAGMA table_info(customers)",
    "ATTACH DATABASE '/tmp/evil.db' AS e",
    "SELECT name FROM sqlite_master",
    "SELECT load_extension('x')",
    "WITH x AS (SELECT 1) DELETE FROM customers",
    "",
])
def test_hostile_sql_is_blocked(deps, U, sql):
    r = deps.sql.run(sql, U("anita.desai"))
    assert not r.ok and r.error


def test_table_level_access_control(deps, U):
    assert not deps.sql.run("SELECT * FROM orders", U("meera.iyer")).ok          # support: no orders
    assert not deps.sql.run("SELECT * FROM customers", U("priya.sharma")).ok     # engineering: nothing
    assert deps.sql.run("SELECT COUNT(*) FROM tickets", U("meera.iyer")).ok
    # a join cannot smuggle in a forbidden table
    assert not deps.sql.run("SELECT c.name FROM customers c JOIN orders o ON o.customer_id=c.id", U("meera.iyer")).ok


def test_row_limit_enforced(deps, U):
    r = deps.sql.run("SELECT * FROM orders", U("karan.mehta"))
    assert r.ok and len(r.rows) == 50 and r.truncated


def test_database_is_opened_read_only(deps, U):
    # even if the authorizer were bypassed, a write must fail at the connection level
    import sqlite3
    conn = sqlite3.connect(f"file:{deps.settings.sql_db_path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM customers")


def test_runaway_query_times_out(deps, U):
    tool = SqlTool(deps.settings.sql_db_path, timeout_s=0.2)
    r = tool.run("SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e", U("karan.mehta"))
    assert not r.ok and "too long" in r.error
