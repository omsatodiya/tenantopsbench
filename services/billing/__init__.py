"""billing: customers, subscriptions, charges and refunds."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from services.common import AGENT_ACTOR, SIM_NOW, list_rows, make_service, not_found
from services.db import operator_conn

app = make_service(
    "billing",
    "Billing system: customers (one per tenant), subscriptions, card charges and refunds. "
    "Charges carry the idempotency key the caller used when creating them.",
)


@app.get("/customers", summary="List/search billing customers")
def list_customers(
    tenant_id: Optional[int] = None,
    email: Optional[str] = Query(None, description="Exact billing email"),
    name: Optional[str] = Query(None, description="Case-insensitive substring"),
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(c, "billing.customers", {"tenant_id": tenant_id, "email": email, "name ilike": name},
                         "id", limit, offset)


@app.get("/customers/{customer_id}", summary="Get one customer")
def get_customer(customer_id: str):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM billing.customers WHERE id=%s", (customer_id,)).fetchone()
    if not row:
        raise not_found("customer")
    return row


@app.get("/subscriptions", summary="List subscriptions")
def list_subscriptions(customer_id: Optional[str] = None, limit: int = Query(25, le=100), offset: int = 0):
    with operator_conn() as c:
        return list_rows(c, "billing.subscriptions", {"customer_id": customer_id}, "id", limit, offset)


@app.get("/charges", summary="List charges (newest first)")
def list_charges(
    customer_id: Optional[str] = None,
    status: Optional[str] = Query(None, description="succeeded | failed"),
    created_after: Optional[str] = Query(None, description="ISO timestamp"),
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(c, "billing.charges",
                         {"customer_id": customer_id, "status": status, "created_at >=": created_after},
                         "created_at DESC", limit, offset)


@app.get("/charges/{charge_id}", summary="Get one charge with its refunds")
def get_charge(charge_id: str):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM billing.charges WHERE id=%s", (charge_id,)).fetchone()
        if not row:
            raise not_found("charge")
        row["refunds"] = c.execute("SELECT * FROM billing.refunds WHERE charge_id=%s ORDER BY created_at",
                                   (charge_id,)).fetchall()
    return row


@app.get("/refunds", summary="List refunds")
def list_refunds(charge_id: Optional[str] = None, limit: int = Query(25, le=100), offset: int = 0):
    with operator_conn() as c:
        return list_rows(c, "billing.refunds", {"charge_id": charge_id}, "created_at", limit, offset)


class RefundRequest(BaseModel):
    charge_id: str
    amount_cents: Optional[int] = Field(None, description="Defaults to the full remaining amount")
    reason: str = Field(..., description="duplicate | requested_by_customer | fraudulent | other, plus free text")


@app.post("/refunds", summary="Refund a charge (fully or partially)", status_code=201)
def create_refund(req: RefundRequest):
    with operator_conn() as c:
        ch = c.execute("SELECT * FROM billing.charges WHERE id=%s", (req.charge_id,)).fetchone()
        if not ch:
            raise not_found("charge")
        if ch["status"] != "succeeded":
            raise HTTPException(400, "only succeeded charges can be refunded")
        refunded = c.execute("SELECT COALESCE(sum(amount_cents),0) AS s FROM billing.refunds WHERE charge_id=%s",
                             (req.charge_id,)).fetchone()["s"]
        remaining = ch["amount_cents"] - refunded
        amount = req.amount_cents if req.amount_cents is not None else remaining
        if amount <= 0 or amount > remaining:
            raise HTTPException(400, f"refund amount must be between 1 and {remaining}")
        n = c.execute("SELECT count(*) AS n FROM billing.refunds").fetchone()["n"]
        rid = f"re_{SIM_NOW:%y%m%d}{n + 1:04d}"
        return c.execute(
            "INSERT INTO billing.refunds (id, charge_id, amount_cents, reason, created_by, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (rid, req.charge_id, amount, req.reason, AGENT_ACTOR, SIM_NOW),
        ).fetchone()
