"""Reference solution, performed only through the HTTP API."""


def _all(client, path, **params):
    items, offset = [], 0
    while True:
        page = client.get(path, params={**params, "limit": 100, "offset": offset}).json()
        items += page["items"]
        offset += 100
        if offset >= page["total"]:
            return items


def solve(client, ids):
    ticket = client.get(f"/helpdesk/tickets/{ids['ticket_id']}").json()
    tenant = ticket["tenant_id"]
    # Reproduce: the tenant-scoped member list returns other tenants' rows.
    rows = client.get(f"/app/tenants/{tenant}/member-list").json()["items"]
    assert any(r["tenant_id"] != tenant for r in rows)

    rls = {t["table"]: t for t in client.get("/app/admin/rls").json()}
    assert not rls["project_members"]["rls_enabled"]

    # Who saw what: every member_list.viewed event records the row ids it returned.
    owner = {pm["id"]: pm["tenant_id"] for pm in _all(client, "/app/project-members")}
    exposed = set()
    for ev in _all(client, "/audit/events", action="member_list.viewed"):
        exposed |= {owner[r] for r in ev["details"]["row_ids"] if owner[r] != ev["tenant_id"]}

    client.post("/app/admin/rls/project_members", json={"tenant_column": "tenant_id"}).raise_for_status()
    rows = client.get(f"/app/tenants/{tenant}/member-list").json()["items"]
    assert rows and all(r["tenant_id"] == tenant for r in rows)

    client.post("/audit/security-notifications", json={
        "title": "Cross-tenant exposure of project member lists",
        "severity": "high",
        "affected_tenant_ids": sorted(exposed),
        "description": "Release 2026.10.12-1 created app_db.project_members without a row-level security policy. "
                       "Member-list views from other tenants returned these tenants' project member names and emails. "
                       "RLS with a tenant_isolation policy has been enabled.",
    }).raise_for_status()
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={
        "body": "Thanks for reporting this. A missing access policy on a newly released table let member lists show "
                "users from other organisations. It is fixed and the affected organisations are being notified. "
                "No accounts were changed.",
    }).raise_for_status()
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"}).raise_for_status()
