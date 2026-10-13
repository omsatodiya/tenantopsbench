"""Benchmark runner: tasks x trials for one model -> results/<run_id>/."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from harness.agent import openai_chat, run_agent
from harness.tools import BASE_URL
from runner.config import PROVIDERS, api_key, cost, parse_model
from runner.tags import tag_trial
from runner.taskkit import AXES, ROOT, all_tasks, load_task, passed
from services.catalog import app as platform_app

RESULTS = ROOT / "results"
TEMPERATURE = 0.7


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", s).strip("-")


def aggregate(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """pass@1 = mean over trial repeats k of (pass rate across tasks in repeat k); SE across repeats."""
    valid = [t for t in trials if not t["infra_error"]]
    by_k: dict[int, list[bool]] = {}
    for t in valid:
        by_k.setdefault(t["trial"], []).append(t["passed"])
    rates = [sum(v) / len(v) for v in by_k.values() if v]
    mean = sum(rates) / len(rates) if rates else 0.0
    se = (math.sqrt(sum((r - mean) ** 2 for r in rates) / (len(rates) - 1)) / math.sqrt(len(rates))
          if len(rates) > 1 else 0.0)
    n = len(valid)
    return {
        "pass_at_1": mean, "std_err": se, "valid_trials": n, "passed_trials": sum(t["passed"] for t in valid),
        "infra_errors": len(trials) - n,
        "avg_cost_usd": sum(t["cost_usd"] for t in valid) / n if n else 0.0,
        "avg_list_cost_usd": (sum(t["list_cost_usd"] or 0 for t in valid) / n) if n else 0.0,
        "avg_output_tokens": sum(t["tokens_out"] for t in valid) / n if n else 0.0,
        "avg_input_tokens": sum(t["tokens_in"] for t in valid) / n if n else 0.0,
        "avg_steps": sum(t["steps"] for t in valid) / n if n else 0.0,
        "axis_pass_rate": {a: (sum(t["axes"][a] for t in valid) / n if n else 0.0) for a in AXES},
    }


def run_benchmark(model: str, task_ids: str = "all", trials: int = 3, max_steps: int = 40,
                  run_id: str | None = None, chat_factory=None) -> Path:
    provider, model_name = parse_model(model)
    if chat_factory is None:
        key = api_key(provider)
        if not key:
            print(f"SKIP: {PROVIDERS[provider]['key_env']} is not set; provider {provider} skipped.", file=sys.stderr)
            raise SystemExit(2)
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=PROVIDERS[provider]["base_url"], timeout=120, max_retries=0)
        chat = openai_chat(client, model_name, temperature=TEMPERATURE,
                           max_tokens=PROVIDERS[provider].get("max_tokens"))
    else:
        chat = chat_factory()

    tasks = all_tasks() if task_ids == "all" else [load_task(t.strip()) for t in task_ids.split(",")]
    started = datetime.now(timezone.utc)
    run_id = run_id or f"{started:%Y%m%d-%H%M%S}_{provider}_{_slug(model_name)}"
    out = RESULTS / run_id
    (out / "traces").mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for k in range(1, trials + 1):          # repeat-major order spreads provider rate limits across tasks
        for task in tasks:
            world = task.reset()
            ticket = task.prompt(world)
            trace_name = f"{task.id}__t{k}.jsonl"
            print(f"[{run_id}] {task.id} trial {k}/{trials} ...", flush=True)
            with open(out / "traces" / trace_name, "w", encoding="utf-8") as fh, \
                    TestClient(platform_app, base_url=BASE_URL) as platform:
                def log(entry, fh=fh):
                    fh.write(json.dumps(entry, default=str) + "\n")
                    fh.flush()

                log({"type": "meta", "task": task.id, "trial": k, "model": model, "ticket": ticket,
                     "max_steps": max_steps, "temperature": TEMPERATURE,
                     "context_char_budget": PROVIDERS[provider].get("context_char_budget")})
                res = run_agent(chat, platform, ticket, max_steps=max_steps, log=log,
                                context_char_budget=PROVIDERS[provider].get("context_char_budget"))
                verifiers = task.verify()
                ok = passed(verifiers) and res.infra_error is None
                tags = tag_trial(res.trace, verifiers, task.meta, infra_error=res.infra_error is not None)
                billed, listed = cost(provider, model_name, res.tokens_in, res.tokens_out)
                rec = {
                    "task": task.id, "trial": k, "passed": ok, "infra_error": res.infra_error,
                    "axes": {a: all(v["passed"] for v in verifiers if v["axis"] == a) for a in AXES},
                    "verifiers": verifiers, "steps": res.steps, "finished": res.finished,
                    "stop_reason": res.stop_reason, "summary": res.summary,
                    "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
                    "cost_usd": billed, "list_cost_usd": listed, "failure_tags": tags,
                    "trace_file": f"traces/{trace_name}",
                }
                log({"type": "result", **{k2: v for k2, v in rec.items() if k2 != "trace_file"}})
            records.append(rec)
            print(f"    -> {'PASS' if ok else 'FAIL'} steps={res.steps} tags={tags} "
                  f"tokens={res.tokens_in}/{res.tokens_out}{' INFRA: ' + res.infra_error if res.infra_error else ''}",
                  flush=True)
            _write_summary(out, run_id, provider, model, model_name, trials, max_steps, started, tasks, records)
    return out


def _write_summary(out, run_id, provider, model, model_name, trials, max_steps, started, tasks, records):
    summary = {
        "run_id": run_id, "model": model, "provider": provider, "model_name": model_name,
        "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "trials_per_task": trials, "max_steps": max_steps, "temperature": TEMPERATURE,
        "context_char_budget": PROVIDERS[provider].get("context_char_budget"),
        "free_tier": PROVIDERS[provider]["free_tier"],
        "overall": aggregate(records),
        "per_task": {t.id: aggregate([r for r in records if r["task"] == t.id]) for t in tasks},
        "trials": records,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
