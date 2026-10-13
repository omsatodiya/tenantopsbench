"""app_db: the multi-tenant product database (tenants, users, memberships, projects, API keys)."""
from __future__ import annotations

from typing import Literal, Optional

import psycopg
from fastapi import Body, HTTPException, Query
from pydantic import BaseModel, Field

from services.common import SIM_NOW, list_rows, make_service, not_found
from services.db import operator_conn, tenant_conn

app = make_service(
    "app_db",
    "Operator API over the multi-tenant product database. Operator endpoints see every tenant; "
    "endpoints under /tenants/{tenant_id}/... execute exactly as the product does for that tenant "
    "(role tenant_app with row-level security). Includes RLS administration under /admin/rls.",
)

TENANT_TABLES = {"users", "memberships", "projects", "project_members", "api_keys", "seat_changes", "plan_cache"}


# ---------------------------------------------------------------- tenants
@app.get("/tenants", summary="List tenants (customer companies)")
def list_tenants():
    with operator_conn() as c:
        return c.execute("SELECT * FROM app_db.tenants ORDER BY id").fetchall()


@app.get("/tenants/{tenant_id}", summary="Get one tenant")
def get_tenant(tenant_id: int):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM app_db.tenants WHERE id=%s", (tenant_id,)).fetchone()
    if not row:
        raise not_found("tenant")
    return row


# ---------------------------------------------------------------- users
@app.get("/users", summary="Search users across tenants")
def list_users(
    tenant_id: Optional[int] = None,
    email: Optional[str] = Query(None, description="Exact email"),
    name: Optional[str] = Query(None, description="Case-insensitive substring match on name"),
    status: Optional[str] = Query(None, description="active | deactivated"),
    kind: Optional[str] = Query(None, description="human | service_account"),
    limit: int = Query(25, le=100),
    offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(
            c, "app_db.users",
            {"tenant_id": tenant_id, "email": email, "name ilike": name, "status": status, "kind": kind},
            "id", limit, offset,
        )


@app.get("/users/{user_id}", summary="Get one user")
def get_user(user_id: int):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM app_db.users WHERE id=%s", (user_id,)).fetchone()
    if not row:
        raise not_found("user")
    return row


class UserPatch(BaseModel):
    status: Optional[Literal["active", "deactivated"]] = None
    title: Optional[str] = None
    manager_email: Optional[str] = None
    role: Optional[Literal["owner", "admin", "member"]] = None


@app.patch("/users/{user_id}", summary="Update a user (e.g. deactivate)")
def patch_user(user_id: int, patch: UserPatch):
    fields = patch.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "nothing to update")
    if fields.get("status") == "deactivated":
        fields["deactivated_at"] = SIM_NOW
    elif fields.get("status") == "active":
        fields["deactivated_at"] = None
    sets = ", ".join(f"{k}=%s" for k in fields)
    with operator_conn() as c:
        row = c.execute(
            f"UPDATE app_db.users SET {sets} WHERE id=%s RETURNING *", [*fields.values(), user_id]
        ).fetchone()
    if not row:
        raise not_found("user")
    return row


@app.delete("/users/{user_id}", summary="Permanently delete a user and their memberships")
def delete_user(user_id: int):
    with operator_conn() as c:
        c.execute("DELETE FROM app_db.memberships WHERE user_id=%s", (user_id,))
        c.execute("DELETE FROM app_db.project_members WHERE user_id=%s", (user_id,))
        try:
            row = c.execute("DELETE FROM app_db.users WHERE id=%s RETURNING id", (user_id,)).fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(409, "user still referenced (e.g. owns API keys)")
    if not row:
        raise not_found("user")
    return {"deleted": user_id}


# ---------------------------------------------------------------- memberships / projects
@app.get("/memberships", summary="List team memberships")
def list_memberships(
    tenant_id: Optional[int] = None, user_id: Optional[int] = None, team: Optional[str] = None,
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(c, "app_db.memberships", {"tenant_id": tenant_id, "user_id": user_id, "team": team},
                         "id", limit, offset)


@app.delete("/memberships/{membership_id}", summary="Delete a membership")
def delete_membership(membership_id: int):
    with operator_conn() as c:
        row = c.execute("DELETE FROM app_db.memberships WHERE id=%s RETURNING id", (membership_id,)).fetchone()
    if not row:
        raise not_found("membership")
    return {"deleted": membership_id}


@app.get("/projects", summary="List projects")
def list_projects(tenant_id: Optional[int] = None, limit: int = Query(25, le=100), offset: int = 0):
    with operator_conn() as c:
        return list_rows(c, "app_db.projects", {"tenant_id": tenant_id}, "id", limit, offset)


@app.get("/project-members", summary="List project members across all tenants (operator view)")
def list_project_members(
    tenant_id: Optional[int] = None, project_id: Optional[int] = None, user_id: Optional[int] = None,
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(c, "app_db.project_members",
                         {"tenant_id": tenant_id, "project_id": project_id, "user_id": user_id},
                         "id", limit, offset)


@app.delete("/project-members/{row_id}", summary="Remove a project member row")
def delete_project_member(row_id: int):
    with operator_conn() as c:
        row = c.execute("DELETE FROM app_db.project_members WHERE id=%s RETURNING id", (row_id,)).fetchone()
    if not row:
        raise not_found("project member")
    return {"deleted": row_id}


# ---------------------------------------------------------------- tenant-scoped (what the product shows)
@app.get(
    "/tenants/{tenant_id}/member-list",
    summary="Member list exactly as the product renders it for a tenant (member list v2)",
    description="Runs `SELECT ... FROM app_db.project_members` as role tenant_app with app.tenant_id set. "
    "The query itself has no tenant filter; isolation relies on row-level security.",
)
def tenant_member_list(tenant_id: int, limit: int = Query(50, le=200), offset: int = 0):
    with tenant_conn(tenant_id) as c:
        rows = c.execute(
            "SELECT id, tenant_id, project_id, display_name, email, role, added_at "
            "FROM app_db.project_members ORDER BY id LIMIT %s OFFSET %s", (limit, offset)
        ).fetchall()
    return {"tenant_id": tenant_id, "items": rows}


@app.get("/tenants/{tenant_id}/users", summary="Users list as the product renders it for a tenant (RLS)")
def tenant_users(tenant_id: int, limit: int = Query(50, le=200), offset: int = 0):
    with tenant_conn(tenant_id) as c:
        rows = c.execute(
            "SELECT id, tenant_id, name, email, role, status FROM app_db.users ORDER BY id LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()
    return {"tenant_id": tenant_id, "items": rows}


# ---------------------------------------------------------------- API keys
@app.get("/api-keys", summary="List API keys")
def list_api_keys(
    tenant_id: Optional[int] = None, owner_user_id: Optional[int] = None,
    status: Optional[str] = Query(None, description="active | revoked"),
    limit: int = Query(25, le=100), offset: int = 0,
):
    with operator_conn() as c:
        return list_rows(c, "app_db.api_keys",
                         {"tenant_id": tenant_id, "owner_user_id": owner_user_id, "status": status},
                         "created_at", limit, offset)


@app.get("/api-keys/{key_id}", summary="Get one API key (secret is never returned)")
def get_api_key(key_id: str):
    with operator_conn() as c:
        row = c.execute("SELECT * FROM app_db.api_keys WHERE id=%s", (key_id,)).fetchone()
    if not row:
        raise not_found("api key")
    return row


@app.post("/api-keys/{key_id}/revoke", summary="Revoke an API key")
def revoke_api_key(key_id: str):
    with operator_conn() as c:
        row = c.execute(
            "UPDATE app_db.api_keys SET status='revoked', revoked_at=COALESCE(revoked_at, %s) WHERE id=%s RETURNING *",
            (SIM_NOW, key_id),
        ).fetchone()
    if not row:
        raise not_found("api key")
    return row


# ---------------------------------------------------------------- seats / plans
@app.get("/seat-changes", summary="Seat-count change history")
def list_seat_changes(tenant_id: Optional[int] = None, limit: int = Query(25, le=100), offset: int = 0):
    with operator_conn() as c:
        return list_rows(c, "app_db.seat_changes", {"tenant_id": tenant_id}, "created_at", limit, offset)


@app.get("/plan-cache", summary="Product-side cached plan entitlements per tenant")
def list_plan_cache(tenant_id: Optional[int] = None):
    with operator_conn() as c:
        return list_rows(c, "app_db.plan_cache", {"tenant_id": tenant_id}, "tenant_id", 100, 0)


@app.post("/plan-cache/{tenant_id}/resync", summary="Re-sync a tenant's cached plan from billing (source of truth)")
def resync_plan_cache(tenant_id: int):
    with operator_conn() as c:
        sub = c.execute(
            "SELECT s.plan FROM billing.subscriptions s JOIN billing.customers cu ON cu.id=s.customer_id "
            "WHERE cu.tenant_id=%s AND s.status='active' ORDER BY s.updated_at DESC LIMIT 1", (tenant_id,)
        ).fetchone()
        if not sub:
            raise HTTPException(404, "no active billing subscription for tenant")
        limit = PLAN_SEAT_LIMITS.get(sub["plan"], 10)
        row = c.execute(
            "UPDATE app_db.plan_cache SET plan=%s, seat_limit=%s, synced_at=%s WHERE tenant_id=%s RETURNING *",
            (sub["plan"], limit, SIM_NOW, tenant_id),
        ).fetchone()
        c.execute("UPDATE app_db.tenants SET plan=%s WHERE id=%s", (sub["plan"], tenant_id))
    if not row:
        raise not_found("plan cache entry")
    return row


@app.patch("/tenants/{tenant_id}/seat-limit", summary="Manually override a tenant's seat limit")
def override_seat_limit(tenant_id: int, seat_limit: int = Body(..., embed=True)):
    with operator_conn() as c:
        row = c.execute("UPDATE app_db.plan_cache SET seat_limit=%s, source='manual-override' WHERE tenant_id=%s RETURNING *",
                        (seat_limit, tenant_id)).fetchone()
    if not row:
        raise not_found("plan cache entry")
    return row


PLAN_SEAT_LIMITS = {"starter": 5, "team": 25, "business": 100, "enterprise": 1000}


# ---------------------------------------------------------------- RLS administration
@app.get("/admin/rls", summary="Row-level security status and policies for every app_db table")
def rls_status():
    with operator_conn() as c:
        tables = c.execute(
            "SELECT c.relname AS table, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='app_db' AND c.relkind='r' ORDER BY c.relname"
        ).fetchall()
        policies = c.execute(
            "SELECT tablename AS table, policyname AS name, cmd, qual AS using_expression "
            "FROM pg_policies WHERE schemaname='app_db' ORDER BY tablename, policyname"
        ).fetchall()
    for t in tables:
        t["policies"] = [p for p in policies if p["table"] == t["table"]]
    return tables


class RlsRequest(BaseModel):
    tenant_column: str = Field(..., description="Column holding the owning tenant id; rows are visible only "
                               "when this column equals current_setting('app.tenant_id')")


@app.post("/admin/rls/{table}", summary="Enable RLS on an app_db table with a tenant isolation policy")
def enable_rls(table: str, req: RlsRequest):
    with operator_conn() as c:
        exists = c.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='app_db' AND table_name=%s AND column_name=%s",
            (table, req.tenant_column),
        ).fetchone()
        if not exists:
            raise HTTPException(400, f"app_db.{table}.{req.tenant_column} does not exist")
        c.execute(f'DROP POLICY IF EXISTS tenant_isolation ON app_db."{table}"')
        c.execute(
            f'CREATE POLICY tenant_isolation ON app_db."{table}" '
            f"USING (\"{req.tenant_column}\" = NULLIF(current_setting('app.tenant_id', true), '')::int)"
        )
        c.execute(f'ALTER TABLE app_db."{table}" ENABLE ROW LEVEL SECURITY')
    return {"table": table, "rls_enabled": True, "policy": "tenant_isolation", "tenant_column": req.tenant_column}


@app.delete("/admin/rls/{table}", summary="Disable RLS on an app_db table")
def disable_rls(table: str):
    if table not in TENANT_TABLES:
        raise not_found("table")
    with operator_conn() as c:
        c.execute(f'ALTER TABLE app_db."{table}" DISABLE ROW LEVEL SECURITY')
    return {"table": table, "rls_enabled": False}
