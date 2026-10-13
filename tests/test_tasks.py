"""Validity checks required for EVERY task (see README "Validity checks")."""
import pytest
from fastapi.testclient import TestClient

from runner.taskkit import AXES, all_tasks, passed
from services.catalog import app

TASKS = all_tasks()
IDS = [t.id for t in TASKS]


@pytest.fixture
def client():
    with TestClient(app, base_url="http://tenantops.internal", raise_server_exceptions=True) as c:
        yield c


def failing(results):
    return [f"{r['axis']}: {r['name']} ({r['detail']})" for r in results if not r["passed"]]


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_task_metadata_and_axes(task):
    w = task.world()
    assert task.prompt(w)
    assert "{" not in task.prompt(w), "unrendered prompt placeholder"
    counts = {a: sum(c.axis == a for c in task.verifiers.V.checks) for a in AXES}
    for axis, n in counts.items():
        assert 3 <= n <= 8, f"{axis} has {n} assertions"
    for f in ("README.md", "seed.py", "verifiers.py", "solution.py", "naive.py", "task.yaml"):
        assert (task.dir / f).exists(), f


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_reference_solution_passes_all_verifiers(task, client):
    w = task.reset()
    task.solution.solve(client, w.ids)
    results = task.verify()
    assert passed(results), failing(results)


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_null_agent_fails(task):
    task.reset()
    assert not passed(task.verify())


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_naive_agent_fails(task, client):
    w = task.reset()
    for run in [getattr(task.naive, n) for n in dir(task.naive) if n.startswith("run")]:
        w = task.reset()
        run(client, w.ids)
        results = task.verify()
        assert not passed(results), f"naive agent {run.__name__} passed"


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_reseed_fully_resets(task, client):
    w = task.reset()
    task.solution.solve(client, w.ids)
    assert passed(task.verify())
    task.reset()
    after = task.verify()
    assert not passed(after)
    task.reset()
    assert after == task.verify(), "state after reseed differs from a fresh seed"


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_verifiers_and_seed_are_deterministic(task, client):
    assert task.world().ordered() == task.world().ordered()
    w = task.reset()
    task.solution.solve(client, w.ids)
    assert task.verify() == task.verify()
