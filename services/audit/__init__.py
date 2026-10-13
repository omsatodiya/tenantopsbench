"""audit: append-only audit event log and security notifications."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from services.common import AGENT_ACTOR, SIM_NOW, list_rows, make_service
from services.db import operator_conn

app = make_service(
    "audit",
    "Append-only audit log of platform events (deploys, credential changes, data access, admin actions) "
    "and the security-notification register used to inform tenants of incidents.",
)


@app.get("/events", summary="Query audit events (oldest first)")
def list_events(
    action: Optional[str] = Query(None, description="Exact action, e.g. api_key.created"),
    action_prefix: Optional[str] = Query(None, description="e.g. 'credential.' or 'member_list.'"),
    actor: Optional[str] = None,
    tenant_id: Optional[int] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    since: Optional[str] = Query(None, description="ISO timestamp (inclusive)"),
    until: Optional[str] = Query(None, description="ISO timestamp (exclusive)"),
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(
            c, "audit.events",
            {"action": action, "action like": f"{action_prefix}%" if action_prefix else None,
             "actor": actor, "tenant_id": tenant_id, "target_type": target_type, "target_id": target_id,
             "occurred_at >=": since, "occurred_at <": until},
            "occurred_at, id", limit, offset,
        )


@app.get("/events/actions", summary="Distinct action names with counts")
def list_actions():
    with operator_conn() as c:
        return c.execute("SELECT action, count(*) AS n FROM audit.events GROUP BY action ORDER BY action").fetchall()


@app.get("/security-notifications", summary="List security notifications")
def list_notifications():
    with operator_conn() as c:
        return c.execute("SELECT * FROM audit.security_notifications ORDER BY id").fetchall()


class NewNotification(BaseModel):
    title: str
    severity: Literal["low", "medium", "high", "critical"]
    affected_tenant_ids: list[int] = Field(
        ..., description="Tenants whose data was exposed or impacted by the incident (not tenants that merely observed it)"
    )
    description: str


@app.post("/security-notifications", summary="File a security notification for affected tenants", status_code=201)
def create_notification(n: NewNotification):
    if not n.affected_tenant_ids:
        raise HTTPException(400, "affected_tenant_ids must not be empty")
    with operator_conn() as c:
        known = {r["id"] for r in c.execute("SELECT id FROM app_db.tenants").fetchall()}
        unknown = sorted(set(n.affected_tenant_ids) - known)
        if unknown:
            raise HTTPException(400, f"unknown tenant ids: {unknown}")
        return c.execute(
            "INSERT INTO audit.security_notifications (title, severity, affected_tenant_ids, description, created_by, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (n.title, n.severity, sorted(set(n.affected_tenant_ids)), n.description, AGENT_ACTOR, SIM_NOW),
        ).fetchone()
