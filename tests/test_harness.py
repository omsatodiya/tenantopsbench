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
