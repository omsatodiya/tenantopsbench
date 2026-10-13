"""Generic tools handed to the agent. Deliberately service-agnostic: the agent must discover endpoints
through GET /catalog and the per-service OpenAPI specs."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

BASE_URL = "http://tenantops.internal"
MODEL_BODY_LIMIT = 12_000   # chars of a response body the model sees
LOG_BODY_LIMIT = 4_096      # chars of a body written to the trace

_PATH = {"type": "string", "description": f"Path such as /catalog or full URL under {BASE_URL}"}
_BODY = {"type": "object", "description": "JSON request body", "additionalProperties": True}

TOOL_SPECS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "http_get", "description": "Send an HTTP GET request to the internal platform.",
        "parameters": {"type": "object", "properties": {
            "path": _PATH,
            "params": {"type": "object", "description": "Query-string parameters", "additionalProperties": True},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "http_post", "description": "Send an HTTP POST request with an optional JSON body.",
        "parameters": {"type": "object", "properties": {"path": _PATH, "body": _BODY}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "http_patch", "description": "Send an HTTP PATCH request with a JSON body.",
        "parameters": {"type": "object", "properties": {"path": _PATH, "body": _BODY}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "http_delete", "description": "Send an HTTP DELETE request.",
        "parameters": {"type": "object", "properties": {"path": _PATH}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "End the task. Call this once you are done, with a short summary of what you found and did.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]

WRITE_TOOLS = {"http_post", "http_patch", "http_delete"}


def _path(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        parts = urlsplit(raw)
        raw = parts.path + (f"?{parts.query}" if parts.query else "")
    return raw if raw.startswith("/") else "/" + raw


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars; narrow the query with filters or limit/offset]"


def execute(client, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call against the platform. Returns a record with request + response."""
    if name == "finish":
        return {"request": {"tool": "finish"}, "status": None, "body": "", "finished": True,
                "summary": str(args.get("summary", ""))}
    method = {"http_get": "GET", "http_post": "POST", "http_patch": "PATCH", "http_delete": "DELETE"}.get(name)
    if method is None:
        return {"request": {"tool": name}, "status": None, "body": f"error: unknown tool {name!r}", "finished": False}
    path = _path(args.get("path", ""))
    kwargs: dict[str, Any] = {}
    if method == "GET" and isinstance(args.get("params"), dict):
        kwargs["params"] = {k: v for k, v in args["params"].items() if v is not None}
    if method in ("POST", "PATCH"):
        body = args.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
        kwargs["json"] = body if body is not None else {}
    try:
        resp = client.request(method, path, **kwargs)
        status, text = resp.status_code, resp.text
        try:
            text = json.dumps(resp.json(), separators=(",", ":"), default=str)
        except ValueError:
            pass
    except Exception as e:  # malformed requests must not crash the trial
        status, text = 0, f"request error: {type(e).__name__}: {e}"
    return {"request": {"method": method, "path": path, **kwargs}, "status": status, "body": text, "finished": False}
