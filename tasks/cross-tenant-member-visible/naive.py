"""Scripted naive agents."""


def run_remove_stranger(client, ids):
    """Symptom fix: delete the foreign rows that show up in the reporter's member list."""
    ticket = client.get(f"/helpdesk/tickets/{ids['ticket_id']}").json()
    tenant = ticket["tenant_id"]
    for r in client.get(f"/app/tenants/{tenant}/member-list").json()["items"]:
        if r["tenant_id"] != tenant:
            client.delete(f"/app/project-members/{r['id']}")
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={"body": "Removed the stray members."})
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"})


def run_notify_everyone_with_rows(client, ids):
    """Fixes RLS but guesses the blast radius from the table instead of the audit log."""
    client.post("/app/admin/rls/project_members", json={"tenant_column": "tenant_id"})
    tenants = sorted({r["tenant_id"] for r in client.get("/app/project-members", params={"limit": 100}).json()["items"]})
    client.post("/audit/security-notifications", json={
        "title": "Member list exposure", "severity": "high", "affected_tenant_ids": tenants,
        "description": "project_members lacked RLS."})
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={"body": "Fixed the access policy."})
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"})
