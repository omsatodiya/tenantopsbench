"""Tests of the mock services themselves (independent of any task)."""
import psycopg
import pytest

from services.catalog import SERVICES
from services.db import TENANT_URL, connect
from services.seedlib import base_world


@pytest.fixture(autouse=True)
def fresh_world():
    w = base_world()
    w.load()
    return w


def test_catalog_lists_every_service_with_spec_link(client):
    body = client.get("/catalog").json()
    names = {s["name"] for s in body["services"]}
    assert names == {name for name, *_ in SERVICES}
    for s in body["services"]:
        spec = client.get(s["openapi"])
        assert spec.status_code == 200
        doc = spec.json()
        assert doc["openapi"].startswith("3.")
        assert doc["paths"], s["name"]
        assert doc["servers"][0]["url"] == s["base_path"]


def test_rls_blocks_cross_tenant_reads_for_tenant_role():
    """Connect as the real tenant-scoped login role (no superuser, no BYPASSRLS)."""
    with psycopg.connect(TENANT_URL) as conn:
        for tid in (2, 3, 4):
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tid),))
            for table in ("users", "memberships", "project_members", "api_keys"):
                tenants = {r[0] for r in conn.execute(f"SELECT DISTINCT tenant_id FROM app_db.{table}")}
                assert tenants <= {tid}, (table, tid, tenants)
            assert conn.execute("SELECT count(*) FROM app_db.users").fetchone()[0] > 0
        conn.execute("SELECT set_config('app.tenant_id', '', false)")
        conn.execute("RESET app.tenant_id")
        assert conn.execute("SELECT count(*) FROM app_db.users").fetchone()[0] == 0


def test_tenant_role_cannot_disable_rls():
    with psycopg.connect(TENANT_URL) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("ALTER TABLE app_db.users DISABLE ROW LEVEL SECURITY")


def test_tenant_endpoint_is_isolated(client):
    rows = client.get("/app/tenants/3/member-list").json()["items"]
    assert rows and {r["tenant_id"] for r in rows} == {3}


def test_audit_log_is_append_only():
    with connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM audit.events WHERE id = 1")


def test_reset_restores_state(client, fresh_world):
    client.post("/jobs/requeue", json={"status": "failed"})
    client.patch("/jobs/config/smtp.port", json={"value": "2525"})
    fresh_world.load()
    with connect() as conn:
        assert conn.execute("SELECT value FROM jobs.config WHERE key='smtp.port'").fetchone()["value"] == "587"
        assert conn.execute("SELECT count(*) AS n FROM jobs.jobs WHERE status='queued'").fetchone()["n"] == 0


def test_agent_created_rows_get_ids_above_seed_range(client):
    r = client.post("/helpdesk/tickets/1/comments", json={"body": "hi"})
    assert r.status_code == 201 and r.json()["id"] > 1000


def test_refund_cannot_exceed_charge(client):
    with connect() as conn:
        ch = conn.execute("SELECT id, amount_cents FROM billing.charges WHERE status='succeeded' "
                          "AND id NOT IN (SELECT charge_id FROM billing.refunds) LIMIT 1").fetchone()
    r = client.post("/billing/refunds", json={"charge_id": ch["id"], "amount_cents": ch["amount_cents"] + 1, "reason": "x"})
    assert r.status_code == 400
    r = client.post("/billing/refunds", json={"charge_id": ch["id"], "reason": "duplicate"})
    assert r.status_code == 201 and r.json()["amount_cents"] == ch["amount_cents"]
