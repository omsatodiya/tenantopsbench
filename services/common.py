"""Helpers shared by every mock service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException

# The simulated "current time" of the world. Seeds are written relative to it and every
# write made through the API is stamped with it, which keeps final DB state deterministic.
SIM_NOW = datetime(2026, 10, 13, 9, 30, tzinfo=timezone.utc)

AGENT_ACTOR = "ops-agent"


def make_service(title: str, description: str) -> FastAPI:
    return FastAPI(
        title=title,
        description=description,
        version="1.0",
        docs_url=None,
        redoc_url=None,
    )


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """Build a WHERE clause from {"col op": value}, skipping None values."""
    parts, args = [], []
    for key, val in filters.items():
        if val is None:
            continue
        col, _, op = key.partition(" ")
        op = op or "="
        if op == "ilike":
            parts.append(f"{col} ILIKE %s")
            args.append(f"%{val}%")
        else:
            parts.append(f"{col} {op} %s")
            args.append(val)
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def page(items: list[dict[str, Any]], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def list_rows(conn, table: str, filters: dict[str, Any], order: str, limit: int, offset: int) -> dict[str, Any]:
    clause, args = where(filters)
    total = conn.execute(f"SELECT count(*) AS n FROM {table}{clause}", args).fetchone()["n"]
    rows = conn.execute(
        f"SELECT * FROM {table}{clause} ORDER BY {order} LIMIT %s OFFSET %s", [*args, limit, offset]
    ).fetchall()
    return page(rows, total, limit, offset)
