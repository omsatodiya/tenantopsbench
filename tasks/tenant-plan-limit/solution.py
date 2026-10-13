"""Reference solution, performed only through the HTTP API."""


def solve(client, ids):
    ticket = client.get(f"/helpdesk/tickets/{ids['ticket_id']}").json()
    tenant = ticket["tenant_id"]
    cache = client.get("/app/plan-cache", params={"tenant_id": tenant}).json()["items"][0]
    customer = client.get("/billing/customers", params={"tenant_id": tenant}).json()["items"][0]
    subs = client.get("/billing/subscriptions", params={"customer_id": customer["id"]}).json()["items"]
    active = [s for s in subs if s["status"] == "active"]
    assert len(active) == 1 and active[0]["plan"] != cache["plan"], "cache is stale vs billing"

    client.post(f"/app/plan-cache/{tenant}/resync").raise_for_status()
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={
        "body": "Your upgrade went through in billing, but the sync to your workspace failed, so it still enforced "
                "the Starter limit of 5 seats. We re-synced it: you are now on Team with 25 seats and can invite new users.",
    }).raise_for_status()
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"}).raise_for_status()
