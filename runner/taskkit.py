"""Task loading and the deterministic verifier framework."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable

import yaml

from services.db import connect

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
AXES = ("JUDGMENT", "FOOTPRINT", "SAFETY")


# --------------------------------------------------------------------------- verifier framework
@dataclass
class Check:
    axis: str
    name: str
    fn: Callable[["DB", Any], Any]


class Verifier:
    """Registry of assertions over final DB state. A task passes only if every assertion passes."""

    def __init__(self) -> None:
        self.checks: list[Check] = []

    def _reg(self, axis: str, name: str):
        def deco(fn):
            self.checks.append(Check(axis, name, fn))
            return fn
        return deco

    def judgment(self, name: str):
        return self._reg("JUDGMENT", name)

    def footprint(self, name: str):
        return self._reg("FOOTPRINT", name)

    def safety(self, name: str):
        return self._reg("SAFETY", name)

    def run(self, world: Any) -> list[dict[str, Any]]:
        results = []
        with connect() as conn:
            db = DB(conn)
            for c in self.checks:
                try:
                    out = c.fn(db, world)
                    ok, detail = (out if isinstance(out, tuple) else (bool(out), ""))
                except Exception as e:  # a crashing check is a failing check
                    ok, detail = False, f"{type(e).__name__}: {e}"
                    conn.rollback()
                results.append({"axis": c.axis, "name": c.name, "passed": bool(ok), "detail": str(detail)[:500]})
        return results


class DB:
    def __init__(self, conn):
        self.conn = conn

    def all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        return self.conn.execute(sql, args).fetchone()

    def scalar(self, sql: str, *args: Any) -> Any:
        row = self.conn.execute(sql, args).fetchone()
        return None if row is None else next(iter(row.values()))


def unchanged(db: DB, world: Any, table: str, cols: Iterable[str], exclude_ids: Iterable[Any] = (),
              key: str = "id", only_ids: Iterable[Any] | None = None) -> tuple[bool, str]:
    """True iff every seeded row of `table` (minus exclusions) still exists with identical `cols`."""
    cols = list(cols)
    excl = set(exclude_ids)
    seeded = {r[key]: r for r in world.rows(table) if r[key] not in excl}
    if only_ids is not None:
        keep = set(only_ids)
        seeded = {k: v for k, v in seeded.items() if k in keep}
    current = {r[key]: r for r in db.all(f"SELECT {key}, {', '.join(cols)} FROM {table}")}
    diffs = []
    for k, row in seeded.items():
        cur = current.get(k)
        if cur is None:
            diffs.append(f"{k}: deleted")
            continue
        for c in cols:
            if cur[c] != row.get(c):
                diffs.append(f"{k}.{c}: {row.get(c)!r} -> {cur[c]!r}")
    return (not diffs, "; ".join(diffs[:8]))


def new_comments(db: DB, ticket_id: int, public_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM helpdesk.comments WHERE ticket_id=%s AND id > 1000"
    if public_only:
        sql += " AND NOT internal"
    return db.all(sql, ticket_id)


# --------------------------------------------------------------------------- task loading
def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Task:
    id: str
    dir: Path
    meta: dict[str, Any] = field(default_factory=dict)

    def _mod(self, name: str) -> ModuleType:
        return _load_module(self.dir / f"{name}.py", f"tasks_{self.id.replace('-', '_')}_{name}")

    @cached_property
    def seed(self) -> ModuleType:
        return self._mod("seed")

    @cached_property
    def verifiers(self) -> ModuleType:
        return self._mod("verifiers")

    @cached_property
    def solution(self) -> ModuleType:
        return self._mod("solution")

    @cached_property
    def naive(self) -> ModuleType:
        return self._mod("naive")

    def world(self):
        return self.seed.build()

    def reset(self):
        w = self.world()
        w.load()
        return w

    def verify(self) -> list[dict[str, Any]]:
        return self.verifiers.V.run(self.world())

    def prompt(self, world) -> str:
        return self.meta["prompt"].format(**world.ids).strip()


def passed(results: list[dict[str, Any]]) -> bool:
    return all(r["passed"] for r in results)


def load_task(task_id: str) -> Task:
    d = TASKS_DIR / task_id
    meta = yaml.safe_load((d / "task.yaml").read_text(encoding="utf-8"))
    return Task(id=meta["id"], dir=d, meta=meta)


def all_tasks() -> list[Task]:
    return [load_task(p.name) for p in sorted(TASKS_DIR.iterdir()) if (p / "task.yaml").exists()]
