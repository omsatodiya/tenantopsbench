"""Helpers shared by every mock service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi

# The simulated "current time" of the world. Seeds are written relative to it and every
# write made through the API is stamped with it, which keeps final DB state deterministic.
SIM_NOW = datetime(2026, 10, 13, 9, 30, tzinfo=timezone.utc)

AGENT_ACTOR = "ops-agent"


def make_service(title: str, description: str) -> FastAPI:
    app = FastAPI(
        title=title,
        description=description,
        version="1.0",
        docs_url=None,
        redoc_url=None,
    )

    def compact_openapi():
        if app.openapi_schema is None:
            app.openapi_schema = _compact(get_openapi(title=title, version="1.0", description=description,
                                                      routes=app.routes))
        return app.openapi_schema

    app.openapi = compact_openapi
    return app


def _compact(spec: dict[str, Any]) -> dict[str, Any]:
    """Strip FastAPI boilerplate (422 bodies, operationIds, auto titles) so specs stay small for agents."""
    for ops in spec.get("paths", {}).values():
        for op in ops.values():
            op.pop("operationId", None)
            responses = op.get("responses", {})
            responses.pop("422", None)
            for r in responses.values():
                if r.get("content", {}).get("application/json", {}).get("schema") == {}:
                    r.pop("content")
            for prm in op.get("parameters", []):
                prm.get("schema", {}).pop("title", None)
    schemas = spec.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    def simplify(node):
        if isinstance(node, dict):
            if isinstance(node.get("title"), str) and ("type" in node or "anyOf" in node):
                node.pop("title")
            any_of = node.get("anyOf")
            if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
                node.pop("anyOf")
                node.update(next(a for a in any_of if a != {"type": "null"}))
            for v in node.values():
                simplify(v)
        elif isinstance(node, list):
            for v in node:
                simplify(v)

    simplify(spec)
    return spec


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
