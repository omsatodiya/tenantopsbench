"""helpdesk: support tickets, comments and support agents."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel

from services.common import AGENT_ACTOR, SIM_NOW, list_rows, make_service, not_found
from services.db import operator_conn

app = make_service(
    "helpdesk",
    "Support helpdesk: tickets (internal and customer), comments and the support agents tickets are assigned to.",
)


@app.get("/tickets", summary="List/search tickets")
def list_tickets(
    status: Optional[str] = Query(None, description="open | pending | resolved | closed"),
    assignee_agent_id: Optional[int] = None,
    requester_email: Optional[str] = None,
    tenant_id: Optional[int] = None,
    q: Optional[str] = Query(None, description="Case-insensitive substring of the subject"),
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(
            c, "helpdesk.tickets",
            {"status": status, "assignee_agent_id": assignee_agent_id, "requester_email": requester_email,
             "tenant_id": tenant_id, "subject ilike": q},
            "id", limit, offset,
        )


@app.get("/tickets/{ticket_id}", summary="Get a ticket with its comments")
def get_ticket(ticket_id: int):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM helpdesk.tickets WHERE id=%s", (ticket_id,)).fetchone()
        if not row:
            raise not_found("ticket")
        row["comments"] = c.execute(
            "SELECT * FROM helpdesk.comments WHERE ticket_id=%s ORDER BY id", (ticket_id,)
        ).fetchall()
    return row


class TicketPatch(BaseModel):
    status: Optional[Literal["open", "pending", "resolved", "closed"]] = None
    assignee_agent_id: Optional[int] = None
    priority: Optional[Literal["low", "normal", "high", "urgent"]] = None


@app.patch("/tickets/{ticket_id}", summary="Update ticket status, assignee or priority")
def patch_ticket(ticket_id: int, patch: TicketPatch):
    fields = patch.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "nothing to update")
    with operator_conn() as c:
        if "assignee_agent_id" in fields:
            agent = c.execute("SELECT active FROM helpdesk.agents WHERE id=%s", (fields["assignee_agent_id"],)).fetchone()
            if not agent:
                raise HTTPException(400, "unknown agent")
            if not agent["active"]:
                raise HTTPException(400, "cannot assign to an inactive agent")
        if fields.get("status") in ("resolved", "closed"):
            fields["resolved_at"] = SIM_NOW
        fields["updated_at"] = SIM_NOW
        sets = ", ".join(f"{k}=%s" for k in fields)
        row = c.execute(f"UPDATE helpdesk.tickets SET {sets} WHERE id=%s RETURNING *",
                        [*fields.values(), ticket_id]).fetchone()
    if not row:
        raise not_found("ticket")
    return row


class NewComment(BaseModel):
    body: str
    internal: bool = False


@app.post("/tickets/{ticket_id}/comments", summary="Add a comment (internal=false is visible to the requester)",
          status_code=201)
def add_comment(ticket_id: int, comment: NewComment):
    with operator_conn() as c:
        if not c.execute("SELECT 1 FROM helpdesk.tickets WHERE id=%s", (ticket_id,)).fetchone():
            raise not_found("ticket")
        return c.execute(
            "INSERT INTO helpdesk.comments (ticket_id, author, body, internal, created_at) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (ticket_id, AGENT_ACTOR, comment.body, comment.internal, SIM_NOW),
        ).fetchone()


@app.get("/agents", summary="List support agents")
def list_agents(
    email: Optional[str] = None,
    name: Optional[str] = Query(None, description="Case-insensitive substring"),
    active: Optional[bool] = None,
    limit: int = Query(50, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(c, "helpdesk.agents", {"email": email, "name ilike": name, "active": active},
                         "id", limit, offset)


class AgentPatch(BaseModel):
    active: Optional[bool] = None
    team: Optional[str] = None


@app.patch("/agents/{agent_id}", summary="Update an agent (e.g. deactivate)")
def patch_agent(agent_id: int, patch: AgentPatch):
    fields = patch.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "nothing to update")
    sets = ", ".join(f"{k}=%s" for k in fields)
    with operator_conn() as c:
        row = c.execute(f"UPDATE helpdesk.agents SET {sets} WHERE id=%s RETURNING *",
                        [*fields.values(), agent_id]).fetchone()
    if not row:
        raise not_found("agent")
    return row
