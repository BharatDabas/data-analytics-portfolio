"""Thin DuckDB access layer: connections, SQL-file execution and idempotent loads."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SQL_DIR = Path(__file__).resolve().parent


def connect(db_path: str | Path = ":memory:", read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    con.execute("SET TimeZone = 'UTC'")
    return con


def run_sql_file(con: duckdb.DuckDBPyConnection, name: str) -> None:
    """Execute every statement in database/<name> (e.g. schema.sql, views.sql)."""
    con.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def replace_table(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Full refresh of a table, keeping the DDL-defined column order and types."""
    con.execute(f"DELETE FROM {table}")
    return append(con, table, df)


def append(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    missing = [c for c in cols if c not in df.columns]
    frame = df.copy()
    for c in missing:
        frame[c] = None
    con.register("_load_df", frame[cols])
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM _load_df")
    finally:
        con.unregister("_load_df")
    return len(frame)


def upsert(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame, keys: list[str]) -> int:
    """Idempotent load: delete rows whose natural key is in df, then insert df."""
    if df.empty:
        return 0
    con.register("_keys_df", df[keys].drop_duplicates())
    try:
        cond = " AND ".join(f"t.{k} = k.{k}" for k in keys)
        con.execute(f"DELETE FROM {table} t WHERE EXISTS (SELECT 1 FROM _keys_df k WHERE {cond})")
    finally:
        con.unregister("_keys_df")
    return append(con, table, df)


def query(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pd.DataFrame:
    return con.execute(sql, params or []).df()
