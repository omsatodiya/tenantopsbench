"""`tob` command line: validate, run, report."""
from __future__ import annotations

import subprocess
import sys
from typing import Optional

import typer

from runner.taskkit import ROOT, all_tasks

app = typer.Typer(add_completion=False, help="TenantOpsBench: LLM agents vs. multi-tenant SaaS ops tickets.")


@app.command()
def validate(verbose: bool = typer.Option(False, "-v", help="Show every test")):
    """Run every validity check (reference solution passes, null/naive agents fail, reseed resets,
    verifiers deterministic) plus the service tests. Must be green before any model run."""
    args = [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-v" if verbose else "-q"]
    code = subprocess.call(args, cwd=ROOT)
    if code == 0:
        typer.secho("tob validate: all checks passed", fg=typer.colors.GREEN)
    else:
        typer.secho("tob validate: FAILED", fg=typer.colors.RED)
    raise typer.Exit(code)


@app.command("list")
def list_tasks():
    """List tasks."""
    for t in all_tasks():
        typer.echo(f"{t.id:32} {t.meta['difficulty']:8} {t.meta['title']}")


@app.command()
def run(
    model: str = typer.Option(..., help="provider:model, e.g. groq:llama-3.3-70b-versatile"),
    tasks: str = typer.Option("all", help="Comma-separated task ids or 'all'"),
    trials: int = typer.Option(3, help="Trials per task"),
    max_steps: int = typer.Option(40, help="Max agent steps per trial"),
    run_id: Optional[str] = typer.Option(None, help="Override the run id"),
):
    """Run a model on tasks and write results/<run_id>/ (traces + summary.json)."""
    from runner.run import run_benchmark

    run_benchmark(model=model, task_ids=tasks, trials=trials, max_steps=max_steps, run_id=run_id)


@app.command()
def status():
    """Show progress of every run in results/ (finished trials, passes, infra errors)."""
    import json

    for p in sorted((ROOT / "results").glob("*/summary.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        trials = s["trials"]
        expected = s["trials_per_task"] * len(s["per_task"])
        passed = sum(t["passed"] for t in trials)
        infra = sum(bool(t["infra_error"]) for t in trials)
        typer.echo(f"{s['run_id']:40} {len(trials):>2}/{expected} trials  passed={passed}  infra_errors={infra}  "
                   f"last update {s['finished_at'][:19]}Z")


@app.command()
def report(out: str = typer.Option("report", help="Output directory"),
           pages: bool = typer.Option(True, help="Also copy the site to docs/ for GitHub Pages")):
    """Generate the static HTML report + trace viewer from every run in results/."""
    from report.generate import generate

    path = generate(ROOT / out, copy_to_docs=pages)
    typer.echo(f"report written to {path}")


if __name__ == "__main__":
    app()
