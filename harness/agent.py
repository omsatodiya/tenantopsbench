"""Minimal agent loop: system prompt + ticket, generic HTTP tools, max N steps, finish(summary)."""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from harness.tools import BASE_URL, LOG_BODY_LIMIT, MODEL_BODY_LIMIT, TOOL_SPECS, execute, truncate

SYSTEM_PROMPT = f"You are an IT/ops responder at a SaaS company. Use the HTTP tools. Start at GET {BASE_URL}/catalog."
MAX_RETRIES = 12
MAX_CONSECUTIVE_MODEL_ERRORS = 3


class InfraError(Exception):
    """The provider failed in a way that says nothing about the model's ability (auth, quota, outage)."""


class RequestTooLarge(InfraError):
    """The provider's per-request token limit was exceeded (HTTP 413)."""


class ModelFormatError(Exception):
    """The provider rejected the model's own output (e.g. malformed tool call). Counts against the model."""


@dataclass
class TrialResult:
    finished: bool = False
    summary: str = ""
    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    infra_error: str | None = None
    stop_reason: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)


ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Any]


def openai_chat(client, model: str, temperature: float = 0.7, sleep=time.sleep, max_tokens: int | None = None,
                max_rate_limit_wait: float = 900) -> ChatFn:
    """Wrap an `openai.OpenAI` client with retry/backoff and error classification."""
    import openai

    def chat(messages, tools):
        delay = 2.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                extra = {"max_tokens": max_tokens} if max_tokens else {}
                return client.chat.completions.create(model=model, messages=messages, tools=tools,
                                                      tool_choice="auto", temperature=temperature, **extra)
            except openai.BadRequestError as e:
                msg = str(e)
                if any(s in msg for s in ("tool_use_failed", "Failed to call a function", "failed_generation",
                                          "function call", "MALFORMED_FUNCTION_CALL")):
                    raise ModelFormatError(msg[:500]) from e
                if "context" in msg.lower() and "length" in msg.lower():
                    raise ModelFormatError("context length exceeded: " + msg[:300]) from e
                raise InfraError(f"400: {msg[:300]}") from e
            except (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError) as e:
                raise InfraError(f"{type(e).__name__}: {str(e)[:300]}") from e
            except openai.APIStatusError as e:
                if e.status_code == 413:
                    raise RequestTooLarge(f"413 request too large for provider limits: {str(e)[:300]}") from e
                if e.status_code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                    raise InfraError(f"{e.status_code}: {str(e)[:300]}") from e
                retry_after = e.response.headers.get("retry-after") if e.response is not None else None
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else delay
                if e.status_code == 429:
                    wait = max(wait, delay)
                    if wait > max_rate_limit_wait:
                        raise InfraError(f"429 rate limit, retry-after {wait:.0f}s exceeds cap: {str(e)[:200]}") from e
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                if attempt == MAX_RETRIES:
                    raise InfraError(f"{type(e).__name__}: {str(e)[:300]}") from e
                wait = delay
            sleep(min(wait, max_rate_limit_wait) + random.uniform(0, 1))
            delay = min(delay * 2, 90)
        raise InfraError("retries exhausted")

    return chat


ELIDED_KEEP = 300
MIN_CONTEXT_BUDGET = 4000
BUDGET_SHRINK = 0.7


def _context_size(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls", ""))) for m in messages)


def fit_context(messages: list[dict[str, Any]], budget_chars: int | None) -> list[dict[str, Any]]:
    """Return a copy of `messages` within `budget_chars`, eliding the oldest tool responses first.
    System prompt, ticket and the agent's own messages are always kept. Used for providers with small
    per-request token limits; the full, un-elided transcript is still what gets logged."""
    if budget_chars is None:
        return messages
    out = [dict(m) for m in messages]
    size = lambda: _context_size(out)
    tool_idx = [i for i, m in enumerate(out) if m["role"] == "tool"]
    for i in tool_idx[:-1]:  # never elide the most recent tool result
        if size() <= budget_chars:
            break
        c = out[i]["content"]
        if len(c) > ELIDED_KEEP + 80:
            out[i]["content"] = c[:ELIDED_KEEP] + f"...[older response elided to fit context: {len(c)} chars; re-request if needed]"
    over = size() - budget_chars
    if over > 0 and tool_idx:  # the latest response alone is too big: cut its tail
        i = tool_idx[-1]
        c = out[i]["content"]
        keep = max(len(c) - over - 200, 1000)
        if keep < len(c):
            out[i]["content"] = c[:keep] + (f"\n...[response truncated to fit the provider's request limit: {len(c)} chars "
                                            "total; use filters or limit/offset to see the rest]")
    return out


def _assistant_message(msg) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [{"id": tc.id, "type": "function",
                              "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                             for tc in msg.tool_calls]
    return out


def run_agent(chat: ChatFn, platform_client, ticket: str, max_steps: int = 40,
              log: Callable[[dict[str, Any]], None] | None = None,
              context_char_budget: int | None = None) -> TrialResult:
    res = TrialResult()
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT},
                                      {"role": "user", "content": ticket}]
    consecutive_model_errors = 0

    def record(entry):
        res.trace.append(entry)
        if log:
            log(entry)

    for step in range(1, max_steps + 1):
        res.steps = step
        t0 = time.perf_counter()
        try:
            while True:
                try:
                    resp = chat(fit_context(messages, context_char_budget), TOOL_SPECS)
                    break
                except RequestTooLarge as e:
                    # Provider rejected the request size: shrink the context budget and retry the same step.
                    actual = _context_size(fit_context(messages, context_char_budget))
                    current = min(context_char_budget or actual, actual)
                    new_budget = int(current * BUDGET_SHRINK)
                    if new_budget < MIN_CONTEXT_BUDGET or _context_size(fit_context(messages, new_budget)) >=                             _context_size(fit_context(messages, current)):
                        raise
                    context_char_budget = new_budget
                    record({"step": step, "note": "context_budget_reduced", "context_char_budget": new_budget,
                            "detail": str(e)[:200]})
        except InfraError as e:
            res.infra_error, res.stop_reason = str(e), "infra_error"
            record({"step": step, "error": "infra_error", "detail": str(e)})
            return res
        except ModelFormatError as e:
            consecutive_model_errors += 1
            record({"step": step, "error": "model_format_error", "detail": str(e),
                    "latency_s": round(time.perf_counter() - t0, 3)})
            if consecutive_model_errors >= MAX_CONSECUTIVE_MODEL_ERRORS:
                res.stop_reason = "repeated_model_format_errors"
                return res
            messages.append({"role": "user", "content": "Your previous tool call was malformed and was rejected. "
                                                        "Call one of the tools with valid JSON arguments."})
            continue
        latency = time.perf_counter() - t0
        consecutive_model_errors = 0
        usage = getattr(resp, "usage", None)
        t_in = getattr(usage, "prompt_tokens", 0) or 0
        t_out = getattr(usage, "completion_tokens", 0) or 0
        res.tokens_in += t_in
        res.tokens_out += t_out
        msg = resp.choices[0].message
        messages.append(_assistant_message(msg))
        entry: dict[str, Any] = {"step": step, "model_message": msg.content or "", "tool_calls": [],
                                 "tokens_in": t_in, "tokens_out": t_out, "latency_s": round(latency, 3)}

        if not msg.tool_calls:
            entry["note"] = "no tool call"
            record(entry)
            messages.append({"role": "user", "content": "Continue using the tools. Call finish(summary) when you are done."})
            continue

        finished = False
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError) as e:
                out = {"request": {"tool": name}, "status": None, "body": f"error: invalid arguments: {e}", "finished": False}
                args = {"_raw": tc.function.arguments}
            else:
                out = execute(platform_client, name, args)
            entry["tool_calls"].append({"tool": name, "args": args, "request": out["request"], "status": out["status"],
                                        "response": truncate(out["body"], LOG_BODY_LIMIT)})
            content = out.get("summary", "") if out["finished"] else (
                f"HTTP {out['status']}\n{truncate(out['body'], MODEL_BODY_LIMIT)}" if out["status"] is not None else out["body"])
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": content or "ok"})
            if out["finished"]:
                finished = True
                res.summary = out.get("summary", "")
        record(entry)
        if finished:
            res.finished, res.stop_reason = True, "finish"
            return res
    res.stop_reason = "max_steps"
    return res
