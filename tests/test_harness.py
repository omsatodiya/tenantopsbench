"""Harness, runner and tagging tests with a scripted fake LLM (no network)."""
import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from harness.agent import InfraError, ModelFormatError, openai_chat, run_agent
from runner import run as runmod
from runner.tags import tag_trial
from runner.taskkit import load_task


def _resp(calls=(), content="", tin=100, tout=20):
    tool_calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(calls)]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls or None))],
              usage=NS(prompt_tokens=tin, completion_tokens=tout))


def scripted(*turns):
    it = iter(turns)

    def chat(messages, tools):
        assert messages[0]["role"] == "system" and "catalog" in messages[0]["content"]
        assert {t["function"]["name"] for t in tools} == {"http_get", "http_post", "http_patch", "http_delete", "finish"}
        return next(it)
    return chat


def test_agent_loop_logs_steps_and_finishes(client):
    load_task("invoice-emails-stopped").reset()
    logged = []
    chat = scripted(
        _resp([("http_get", {"path": "/catalog"})]),
        _resp([("http_get", {"path": "http://tenantops.internal/jobs/config"})]),
        _resp(content="thinking"),
        _resp([("finish", {"summary": "done"})]),
    )
    res = run_agent(chat, client, "ticket", max_steps=10, log=logged.append)
    assert res.finished and res.summary == "done" and res.steps == 4
    assert res.tokens_in == 400 and res.tokens_out == 80
    assert logged[0]["tool_calls"][0]["status"] == 200
    assert logged[1]["tool_calls"][0]["request"]["path"] == "/jobs/config"
    assert logged[2]["note"] == "no tool call"
    assert all("latency_s" in e for e in logged)


def test_agent_stops_at_max_steps(client):
    load_task("invoice-emails-stopped").reset()
    chat = lambda m, t: _resp([("http_get", {"path": "/catalog"})])
    res = run_agent(chat, client, "ticket", max_steps=3)
    assert not res.finished and res.steps == 3 and res.stop_reason == "max_steps"


def test_long_responses_truncated_in_trace(client):
    load_task("invoice-emails-stopped").reset()
    logged = []
    chat = scripted(_resp([("http_get", {"path": "/app/openapi.json"})]), _resp([("finish", {"summary": "x"})]))
    run_agent(chat, client, "t", log=logged.append)
    assert len(logged[0]["tool_calls"][0]["response"]) < 4300


def test_infra_error_ends_trial(client):
    def chat(m, t):
        raise InfraError("401")
    res = run_agent(chat, client, "t")
    assert res.infra_error == "401" and res.stop_reason == "infra_error"


def test_repeated_model_format_errors_end_trial_without_infra_flag(client):
    def chat(m, t):
        raise ModelFormatError("tool_use_failed")
    res = run_agent(chat, client, "t")
    assert res.infra_error is None and res.stop_reason == "repeated_model_format_errors"


def _api_error(cls, status, msg="boom", headers=None):
    req = httpx.Request("POST", "https://x/chat/completions")
    resp = httpx.Response(status, request=req, headers=headers or {}, json={"error": {"message": msg}})
    return cls(msg, response=resp, body=None)


def test_rate_limit_backoff_then_success():
    calls, sleeps = [], []

    class Completions:
        def create(self, **kw):
            calls.append(kw)
            if len(calls) < 3:
                raise _api_error(openai.RateLimitError, 429, headers={"retry-after": "1.5"})
            return "ok"

    client = NS(chat=NS(completions=Completions()))
    chat = openai_chat(client, "m", sleep=sleeps.append)
    assert chat([], []) == "ok" and len(calls) == 3 and len(sleeps) == 2 and sleeps[0] >= 1.5


def test_auth_error_is_infra_and_tool_failure_is_model_error():
    class Bad:
        def __init__(self, exc):
            self.exc = exc

        def create(self, **kw):
            raise self.exc

    chat = openai_chat(NS(chat=NS(completions=Bad(_api_error(openai.AuthenticationError, 401)))), "m", sleep=lambda s: None)
    with pytest.raises(InfraError):
        chat([], [])
    chat = openai_chat(NS(chat=NS(completions=Bad(_api_error(openai.BadRequestError, 400, "tool_use_failed")))), "m",
                       sleep=lambda s: None)
    with pytest.raises(ModelFormatError):
        chat([], [])


def test_failure_tags():
    meta = {"relevant_services": ["billing"], "corroboration_paths": ["/app/seat-changes"]}
    V = lambda a, p: {"axis": a, "name": "x", "passed": p}
    write = {"step": 1, "tool_calls": [{"tool": "http_post", "request": {"path": "/billing/refunds"}, "status": 201}]}
    assert tag_trial([], [V("JUDGMENT", False)], meta) [0] == "acted_too_little"
    assert "took_ticket_at_face_value" in tag_trial([write], [V("JUDGMENT", False)], meta)
    assert "acted_too_much" in tag_trial([write], [V("JUDGMENT", True), V("SAFETY", False)], meta)
    assert "unfinished_job" in tag_trial([write], [V("JUDGMENT", True), V("FOOTPRINT", False)], meta)
    assert "tool_discovery_failure" in tag_trial([write], [V("JUDGMENT", True), V("FOOTPRINT", False)], meta)
    assert tag_trial([write], [V("JUDGMENT", True)], meta) == []
    assert tag_trial([], [], meta, infra_error=True) == ["infra_error"]


def test_run_benchmark_end_to_end_with_fake_model(tmp_path, monkeypatch):
    monkeypatch.setattr(runmod, "RESULTS", tmp_path)

    def factory():
        return lambda m, t: _resp([("finish", {"summary": "nothing to do"})])

    out = runmod.run_benchmark("groq:fake-model", task_ids="double-charge-refund", trials=2, chat_factory=factory,
                               run_id="fake")
    summary = json.loads((out / "summary.json").read_text())
    assert summary["overall"]["valid_trials"] == 2 and summary["overall"]["pass_at_1"] == 0.0
    assert summary["trials"][0]["failure_tags"][0] == "acted_too_little"
    lines = (out / summary["trials"][0]["trace_file"]).read_text().splitlines()
    assert json.loads(lines[0])["type"] == "meta" and json.loads(lines[-1])["type"] == "result"


def test_aggregate_excludes_infra_errors():
    t = lambda k, p, infra=None: {"trial": k, "passed": p, "infra_error": infra, "cost_usd": 0, "list_cost_usd": 0.01,
                                  "tokens_out": 10, "tokens_in": 100, "steps": 5,
                                  "axes": {"JUDGMENT": p, "FOOTPRINT": p, "SAFETY": True}}
    agg = runmod.aggregate([t(1, True), t(1, False), t(2, True), t(2, True), t(3, False, "429")])
    assert agg["valid_trials"] == 4 and agg["infra_errors"] == 1
    assert agg["pass_at_1"] == pytest.approx(0.75) and agg["std_err"] == pytest.approx(0.25)


def test_fit_context_elides_oldest_tool_results_first():
    from harness.agent import fit_context
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "ticket"}]
    for i in range(4):
        msgs += [{"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "content": str(i) * 5000}]
    out = fit_context(msgs, 9000)
    tools = [m["content"] for m in out if m["role"] == "tool"]
    assert "elided" in tools[0] and "elided" in tools[1] and tools[3] == "3" * 5000
    assert msgs[3]["content"] == "0" * 5000  # original untouched
    assert fit_context(msgs, None) is msgs


def test_413_shrinks_context_budget_and_retries_same_step(client):
    from harness.agent import RequestTooLarge, _context_size
    load_task("invoice-emails-stopped").reset()
    script = iter([_resp([("http_get", {"path": "/app/openapi.json"})]),
                   _resp([("http_get", {"path": "/jobs/openapi.json"})]),
                   _resp([("http_get", {"path": "/catalog"})]),
                   _resp([("finish", {"summary": "ok"})])])
    seen = []

    def chat(messages, tools):
        size = _context_size(messages)
        if size > 9000:
            raise RequestTooLarge("413")
        seen.append(size)
        return next(script)

    logged = []
    res = run_agent(chat, client, "t", log=logged.append, context_char_budget=30000)
    assert res.finished and res.infra_error is None and res.steps == 4
    assert any(e.get("note") == "context_budget_reduced" for e in logged)
    assert max(seen) <= 9000


def test_old_spec_responses_are_condensed_to_an_endpoint_index_not_dropped(client):
    from harness.agent import condense_spec, fit_context
    spec = "HTTP 200\n" + json.dumps(client.get("/app/openapi.json").json(), separators=(",", ":"))
    index = condense_spec(spec)
    assert index.startswith("[condensed OpenAPI index for /app")
    assert "PATCH /app/users/{user_id} body{status,title,manager_email,role}" in index
    assert "GET /app/tenants/{tenant_id}/member-list" in index
    assert condense_spec("HTTP 200\n{\"items\": []}") is None
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"}, {"role": "tool", "content": spec}]
    msgs += [{"role": "tool", "content": "x" * 2500} for _ in range(3)]
    out = fit_context(msgs, 11000)
    assert out[2]["content"] == index


def test_run_resumes_and_skips_finished_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(runmod, "RESULTS", tmp_path)
    calls = []

    def factory():
        def chat(m, t):
            calls.append(1)
            return _resp([("finish", {"summary": "x"})])
        return chat

    runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit", trials=1, chat_factory=factory, run_id="r")
    assert len(calls) == 1
    out = runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit", trials=2, chat_factory=factory, run_id="r")
    assert len(calls) == 2  # only trial 2 ran
    summary = json.loads((out / "summary.json").read_text())
    assert sorted(t["trial"] for t in summary["trials"]) == [1, 2]


def test_resume_with_fewer_trials_drops_extra_trials(tmp_path, monkeypatch):
    monkeypatch.setattr(runmod, "RESULTS", tmp_path)
    factory = lambda: (lambda m, t: _resp([("finish", {"summary": "x"})]))
    runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit", trials=2, chat_factory=factory, run_id="c")
    out = runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit", trials=1, chat_factory=factory,
                               run_id="c")
    summary = json.loads((out / "summary.json").read_text())
    assert summary["trials_per_task"] == 1 and [t["trial"] for t in summary["trials"]] == [1]
    assert sorted(p.name for p in (out / "traces").iterdir()) == ["tenant-plan-limit__t1.jsonl"]


def test_run_stops_when_daily_quota_is_exhausted(tmp_path, monkeypatch):
    from harness.agent import InfraError
    monkeypatch.setattr(runmod, "RESULTS", tmp_path)
    calls = []

    def factory():
        def chat(m, t):
            calls.append(1)
            raise InfraError("429 rate limit ... on tokens per day (TPD): Limit 200000")
        return chat

    out = runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit,double-charge-refund", trials=2,
                               chat_factory=factory, run_id="q")
    assert len(calls) == 1  # stopped after the first quota error instead of burning every trial
    assert json.loads((out / "summary.json").read_text())["overall"]["infra_errors"] == 1


def test_second_copy_of_a_running_run_does_not_start(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    monkeypatch.setattr(runmod, "RESULTS", tmp_path)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "L" / "traces").mkdir(parents=True)
        (tmp_path / "L" / "run.lock").write_text(str(holder.pid))
        calls = []
        runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit", trials=1,
                             chat_factory=lambda: (lambda m, t: calls.append(1)), run_id="L")
        assert calls == [] and (tmp_path / "L" / "run.lock").read_text() == str(holder.pid)
    finally:
        holder.kill()
    # stale lock (dead pid) is ignored and cleaned up
    runmod.run_benchmark("groq:fake-model", task_ids="tenant-plan-limit", trials=1,
                         chat_factory=lambda: (lambda m, t: _resp([("finish", {"summary": "x"})])), run_id="L")
    assert not (tmp_path / "L" / "run.lock").exists() and os.path.exists(tmp_path / "L" / "summary.json")
