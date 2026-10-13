"""Print the README results tables (markdown) straight from results/*/summary.json, so numbers never drift."""
from __future__ import annotations

from collections import defaultdict

from report.generate import load_runs
from runner.run import aggregate
from runner.taskkit import AXES, ROOT, all_tasks


def main() -> None:
    runs = load_runs(ROOT / "results")
    by_model = defaultdict(list)
    for r in runs:
        by_model[r["model"]].extend(r["trials"])
    rows = sorted(((m, aggregate(t)) for m, t in by_model.items()), key=lambda x: -x[1]["pass_at_1"])

    print("| Model | pass@1 | ± std err | passed / valid trials | infra errors | avg cost (billed) | avg cost (list price) | avg output tokens | avg steps |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m, a in rows:
        listed = f"${a['avg_list_cost_usd']:.4f}" if a["avg_list_cost_usd"] else "n/a"
        print(f"| {m} | {100 * a['pass_at_1']:.1f}% | {100 * a['std_err']:.1f} pp | {a['passed_trials']} / {a['valid_trials']} "
              f"| {a['infra_errors']} | ${a['avg_cost_usd']:.4f} | {listed} | {a['avg_output_tokens']:.0f} | {a['avg_steps']:.1f} |")
    print()
    tasks = [t.id for t in all_tasks()]
    print("| Task | " + " | ".join(m for m, _ in rows) + " |")
    print("|---|" + "---:|" * len(rows))
    for tid in tasks:
        cells = []
        for m, _ in rows:
            ts = [t for t in by_model[m] if t["task"] == tid and not t["infra_error"]]
            cells.append(f"{sum(t['passed'] for t in ts)}/{len(ts)}")
        print(f"| {tid} | " + " | ".join(cells) + " |")
    print()
    print("| Model | " + " | ".join(f"{a} failed" for a in AXES) + " |")
    print("|---|" + "---:|" * len(AXES))
    for m, _ in rows:
        valid = [t for t in by_model[m] if not t["infra_error"]]
        n = len(valid) or 1
        print(f"| {m} | " + " | ".join(f"{100 * sum(not t['axes'][a] for t in valid) / n:.0f}%" for a in AXES) + " |")


if __name__ == "__main__":
    main()
