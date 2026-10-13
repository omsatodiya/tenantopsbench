"""Static HTML report + trace viewer, generated from results/*/summary.json. No build step, no JS deps."""
from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from runner.tags import DEFINITIONS, PRIORITY
from runner.taskkit import AXES, ROOT, all_tasks

TEMPLATES = Path(__file__).with_name("templates")


def load_runs(results_dir: Path) -> list[dict[str, Any]]:
    runs = []
    for p in sorted(results_dir.glob("*/summary.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        s["_dir"] = p.parent
        runs.append(s)
    return runs


def _read_trace(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta, steps = {}, []
    if not path.exists():
        return meta, steps
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("type") == "meta":
            meta = e
        elif e.get("type") != "result":
            steps.append(e)
    return meta, steps


def _pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2)
    except (ValueError, TypeError):
        return text


def generate(out_dir: Path, results_dir: Path | None = None, copy_to_docs: bool = False) -> Path:
    results_dir = results_dir or ROOT / "results"
    runs = load_runs(results_dir)
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["pretty"] = _pretty
    env.filters["pct"] = lambda x: f"{100 * x:.0f}%"
    tasks = {t.id: t.meta for t in all_tasks()}

    if out_dir.exists():
        shutil.rmtree(out_dir / "traces", ignore_errors=True)
    (out_dir / "traces").mkdir(parents=True, exist_ok=True)

    # Several runs of the same model are merged into one leaderboard row.
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        for t in r["trials"]:
            by_model[r["model"]].append({**t, "run_id": r["run_id"]})

    from runner.run import aggregate

    leaderboard, matrix, axis_rows, tag_rows = [], {}, [], []
    for model, trials in by_model.items():
        agg = aggregate(trials)
        leaderboard.append({"model": model, **agg, "runs": sorted({t["run_id"] for t in trials})})
        for tid in tasks:
            ts = [t for t in trials if t["task"] == tid]
            valid = [t for t in ts if not t["infra_error"]]
            matrix[(model, tid)] = {
                "passed": sum(t["passed"] for t in valid), "valid": len(valid), "infra": len(ts) - len(valid),
                "trials": [{"k": t["trial"], "passed": t["passed"], "infra": bool(t["infra_error"]),
                            "href": f"traces/{t['run_id']}/{t['task']}__t{t['trial']}.html",
                            "tag": (t["failure_tags"] or [""])[0]} for t in sorted(ts, key=lambda x: x["trial"])],
            }
        valid = [t for t in trials if not t["infra_error"]]
        n = len(valid) or 1
        axis_rows.append({"model": model, "n": len(valid),
                          "fail": {a: sum(not t["axes"][a] for t in valid) / n for a in AXES}})
        c = Counter(tag for t in valid for tag in t["failure_tags"][:1])
        tag_rows.append({"model": model, "counts": c, "n": len(valid)})
    leaderboard.sort(key=lambda r: (-r["pass_at_1"], r["avg_cost_usd"]))
    models = [r["model"] for r in leaderboard]

    # Trace pages
    for r in runs:
        (out_dir / "traces" / r["run_id"]).mkdir(parents=True, exist_ok=True)
        for t in r["trials"]:
            meta, steps = _read_trace(r["_dir"] / t["trace_file"])
            html = env.get_template("trace.html").render(run=r, trial=t, meta=meta, steps=steps,
                                                         task=tasks.get(t["task"], {}), definitions=DEFINITIONS)
            (out_dir / "traces" / r["run_id"] / f"{t['task']}__t{t['trial']}.html").write_text(html, encoding="utf-8")

    index = env.get_template("index.html").render(
        leaderboard=leaderboard, matrix=matrix, models=models, tasks=tasks, axis_rows=axis_rows, axes=AXES,
        tag_rows=tag_rows, tag_order=PRIORITY, definitions=DEFINITIONS, runs=runs)
    (out_dir / "index.html").write_text(index, encoding="utf-8")

    if copy_to_docs:
        docs = ROOT / "docs"
        shutil.rmtree(docs, ignore_errors=True)
        shutil.copytree(out_dir, docs, ignore=shutil.ignore_patterns("*.py", "templates", "__pycache__"))
        (docs / ".nojekyll").write_text("", encoding="utf-8")
    return out_dir / "index.html"
