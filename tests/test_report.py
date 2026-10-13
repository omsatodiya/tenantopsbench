import json

from report.generate import generate
from runner import run as runmod
from tests.test_harness import _resp


def test_report_generates_index_and_trace_pages(tmp_path, monkeypatch):
    results = tmp_path / "results"
    monkeypatch.setattr(runmod, "RESULTS", results)
    script = [_resp([("http_get", {"path": "/catalog"})]), _resp([("http_get", {"path": "/billing/openapi.json"})]),
              _resp([("finish", {"summary": "<b>done</b>"})])]

    def factory():
        it = iter(script * 2)
        return lambda m, t: next(it)

    runmod.run_benchmark("groq:fake-model", task_ids="double-charge-refund", trials=2, chat_factory=factory, run_id="r1")
    index = generate(tmp_path / "site", results_dir=results)
    html = index.read_text(encoding="utf-8")
    assert "groq:fake-model" in html and "double-charge-refund" in html and "0/2" in html
    page = tmp_path / "site" / "traces" / "r1" / "double-charge-refund__t1.html"
    body = page.read_text(encoding="utf-8")
    assert "FAIL" in body and "/billing/openapi.json" in body and "acted_too_little" in body
    assert "<b>done</b>" not in body  # autoescaped
    assert json.loads((results / "r1" / "summary.json").read_text())["overall"]["valid_trials"] == 2


def test_report_with_no_results(tmp_path):
    html = generate(tmp_path / "site", results_dir=tmp_path / "none").read_text(encoding="utf-8")
    assert "No model results yet" in html
