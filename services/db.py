"""Database access for all mock services, plus the reset/seed mechanism."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

OWNER_URL = os.environ.get(
    "TOB_DATABASE_URL", "postgresql://tob_owner:tob_owner_pw@localhost:5433/tenantops"
)
TENANT_URL = os.environ.get(
    "TOB_TENANT_DATABASE_URL", "postgresql://tenant_app:tenant_app_pw@localhost:5433/tenantops"
)
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
SCHEMAS = ("app_db", "jobs", "billing", "helpdesk", "audit")


def connect(url: str = OWNER_URL, **kw: Any) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row, **kw)


@contextmanager
def operator_conn() -> Iterator[psycopg.Connection]:
    """Connection with operator (owner) privileges: what internal ops tooling uses."""
    with connect() as conn:
        yield conn


@contextmanager
def tenant_conn(tenant_id: int) -> Iterator[psycopg.Connection]:
    """Connection scoped to one tenant exactly like the product does it: role tenant_app + app.tenant_id."""
    with connect() as conn:
        conn.execute("SET ROLE tenant_app")
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        yield conn


def bootstrap_roles() -> None:
    """Create the tenant-scoped login role (idempotent)."""
    with connect(autocommit=True) as conn:
        conn.execute(
            """
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tenant_app') THEN
                CREATE ROLE tenant_app LOGIN PASSWORD 'tenant_app_pw' NOSUPERUSER NOBYPASSRLS;
              END IF;
            END $$;
            """
        )


def _adapt(v: Any) -> Any:
    if isinstance(v, (dict, list)) and not (isinstance(v, list) and all(isinstance(x, int) for x in v)):
        return Jsonb(v)
    return v


def reset(tables: dict[str, list[dict[str, Any]]], post_sql: str = "") -> None:
    """Drop and rebuild every service schema, then load the given rows.

    `tables` maps "schema.table" -> list of row dicts (insertion order is preserved).
    `post_sql` runs after loading (used by tasks that need to alter DDL, e.g. drop a policy).
    """
    bootstrap_roles()
    with connect() as conn:
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        for table, rows in tables.items():
            if not rows:
                continue
            cols = list(rows[0].keys())
            sql = "INSERT INTO {} ({}) VALUES ({})".format(
                table, ", ".join(cols), ", ".join(["%s"] * len(cols))
            )
            with conn.cursor() as cur:
                cur.executemany(sql, [[_adapt(r.get(c)) for c in cols] for r in rows])
        _sync_identity_sequences(conn)
        if post_sql:
            conn.execute(post_sql)
        conn.commit()


def _sync_identity_sequences(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE is_identity = 'YES' AND table_schema = ANY(%s)
        """,
        (list(SCHEMAS),),
    ).fetchall()
    for r in rows:
        fq = f'{r["table_schema"]}.{r["table_name"]}'
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, %s), GREATEST(COALESCE((SELECT max({r['column_name']}) FROM {fq}), 0), 1000))",
            (fq, r["column_name"]),
        )


def jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
