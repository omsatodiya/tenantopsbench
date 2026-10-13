"""jobs: background job queue, worker configuration and integration credentials."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from services.common import AGENT_ACTOR, SIM_NOW, list_rows, make_service, not_found, where
from services.db import operator_conn

app = make_service(
    "jobs",
    "Background job queue used by the platform (emails, webhooks, exports), the worker configuration "
    "key/value store, and the registry of integration credentials the workers use. "
    "Note: `sent_at` is set by the worker at the moment the side effect (email, webhook) was delivered.",
)


@app.get("/jobs", summary="List/filter jobs")
def list_jobs(
    type: Optional[str] = Query(None, description="e.g. invoice_email, webhook_delivery, report_export"),
    status: Optional[str] = Query(None, description="queued | running | succeeded | failed | dead"),
    tenant_id: Optional[int] = None,
    created_after: Optional[str] = Query(None, description="ISO timestamp"),
    created_before: Optional[str] = Query(None, description="ISO timestamp"),
    limit: int = Query(25, le=100),
    offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(
            c, "jobs.jobs",
            {"type": type, "status": status, "tenant_id": tenant_id,
             "created_at >=": created_after, "created_at <": created_before},
            "id", limit, offset,
        )


@app.get("/jobs/stats", summary="Job counts grouped by type and status")
def job_stats():
    with operator_conn() as c:
        return c.execute(
            "SELECT type, status, count(*) AS n FROM jobs.jobs GROUP BY type, status ORDER BY type, status"
        ).fetchall()


@app.get("/jobs/{job_id}", summary="Get one job")
def get_job(job_id: int):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM jobs.jobs WHERE id=%s", (job_id,)).fetchone()
    if not row:
        raise not_found("job")
    return row


_REQUEUE_SET = "status='queued', attempts=0, updated_at=%s"


@app.post("/jobs/{job_id}/requeue", summary="Requeue one job (worker will run it again)")
def requeue_job(job_id: int):
    with operator_conn() as c:
        row = c.execute(f"UPDATE jobs.jobs SET {_REQUEUE_SET} WHERE id=%s RETURNING *", (SIM_NOW, job_id)).fetchone()
    if not row:
        raise not_found("job")
    return row


class BulkRequeue(BaseModel):
    type: Optional[str] = None
    status: str = Field("failed", description="Only jobs currently in this status are requeued")
    created_after: Optional[str] = None
    created_before: Optional[str] = None


@app.post("/jobs/requeue", summary="Bulk-requeue every job matching a filter")
def bulk_requeue(req: BulkRequeue):
    clause, args = where({"type": req.type, "status": req.status,
                          "created_at >=": req.created_after, "created_at <": req.created_before})
    with operator_conn() as c:
        rows = c.execute(f"UPDATE jobs.jobs SET {_REQUEUE_SET}{clause} RETURNING id", [SIM_NOW, *args]).fetchall()
    return {"requeued": len(rows), "job_ids": [r["id"] for r in rows]}


@app.post("/jobs/{job_id}/cancel", summary="Cancel a queued or failed job (marks it dead)")
def cancel_job(job_id: int):
    with operator_conn() as c:
        row = c.execute("UPDATE jobs.jobs SET status='dead', updated_at=%s WHERE id=%s RETURNING *",
                        (SIM_NOW, job_id)).fetchone()
    if not row:
        raise not_found("job")
    return row


class JobPatch(BaseModel):
    payload: Optional[dict] = None
    max_attempts: Optional[int] = None


@app.patch("/jobs/{job_id}", summary="Edit a job's payload or max_attempts")
def patch_job(job_id: int, patch: JobPatch):
    from psycopg.types.json import Jsonb

    fields = patch.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "nothing to update")
    if "payload" in fields:
        fields["payload"] = Jsonb(fields["payload"])
    sets = ", ".join(f"{k}=%s" for k in fields)
    with operator_conn() as c:
        row = c.execute(f"UPDATE jobs.jobs SET {sets}, updated_at=%s WHERE id=%s RETURNING *",
                        [*fields.values(), SIM_NOW, job_id]).fetchone()
    if not row:
        raise not_found("job")
    return row


# ---------------------------------------------------------------- worker config
@app.get("/config", summary="Worker configuration key/value pairs")
def list_config():
    with operator_conn() as c:
        return c.execute("SELECT * FROM jobs.config ORDER BY key").fetchall()


class ConfigValue(BaseModel):
    value: str


@app.patch("/config/{key}", summary="Set a worker configuration value")
def set_config(key: str, body: ConfigValue):
    with operator_conn() as c:
        row = c.execute(
            "UPDATE jobs.config SET value=%s, updated_at=%s, updated_by=%s WHERE key=%s RETURNING *",
            (body.value, SIM_NOW, AGENT_ACTOR, key),
        ).fetchone()
    if not row:
        raise not_found("config key")
    return row


# ---------------------------------------------------------------- credentials
@app.get("/credentials", summary="Integration credentials registry (secrets are never returned)")
def list_credentials(kind: Optional[str] = None, status: Optional[str] = None):
    with operator_conn() as c:
        return list_rows(c, "jobs.credentials", {"kind": kind, "status": status}, "created_at", 100, 0)


@app.get("/credentials/{cred_id}", summary="Get one credential's metadata")
def get_credential(cred_id: str):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM jobs.credentials WHERE id=%s", (cred_id,)).fetchone()
    if not row:
        raise not_found("credential")
    return row
