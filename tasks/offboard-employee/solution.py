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
    # The requester is internal (people-ops@tenantops.io), so the employee is in the internal tenant.
    internal = next(t for t in client.get("/app/tenants").json() if t["slug"] == "tenantops")
    matches = [u for u in client.get("/app/users", params={"name": "Priya Nair", "tenant_id": internal["id"]}).json()["items"]
               if u["name"] == "Priya Nair"]
    assert len(matches) == 1
    priya = matches[0]
    manager = client.get("/app/users", params={"email": priya["manager_email"]}).json()["items"][0]
    p_agent = client.get("/helpdesk/agents", params={"email": priya["email"]}).json()["items"][0]
    m_agent = client.get("/helpdesk/agents", params={"email": manager["email"]}).json()["items"][0]

    moved = []
    for status in ("open", "pending"):
        for t in _all(client, "/helpdesk/tickets", status=status, assignee_agent_id=p_agent["id"]):
            client.patch(f"/helpdesk/tickets/{t['id']}", json={"assignee_agent_id": m_agent["id"]}).raise_for_status()
            moved.append(t["id"])

    # Keys she owns, plus keys she created under other identities (only the audit log knows).
    keys = {k["id"] for k in _all(client, "/app/api-keys", owner_user_id=priya["id"], status="active")}
    keys |= {e["target_id"] for e in _all(client, "/audit/events", action="api_key.created", actor=priya["email"])}
    for kid in sorted(keys):
        if client.get(f"/app/api-keys/{kid}").json()["status"] == "active":
            client.post(f"/app/api-keys/{kid}/revoke").raise_for_status()

    client.patch(f"/helpdesk/agents/{p_agent['id']}", json={"active": False}).raise_for_status()
    client.patch(f"/app/users/{priya['id']}", json={"status": "deactivated"}).raise_for_status()

    client.post(f"/helpdesk/tickets/{ticket['id']}/comments", json={
        "body": f"Offboarded Priya Nair ({priya['email']}): app user deactivated, helpdesk agent deactivated, "
                f"API keys revoked ({', '.join(sorted(keys))}, including one she created under a service account), "
                f"and {len(moved)} open tickets reassigned to her manager {manager['name']}.",
        "internal": True,
    }).raise_for_status()
    client.patch(f"/helpdesk/tickets/{ticket['id']}", json={"status": "resolved"}).raise_for_status()
