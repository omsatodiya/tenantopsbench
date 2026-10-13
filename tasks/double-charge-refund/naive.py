"""Scripted naive agents."""


def _october(client, ids):
    customer = client.get("/billing/customers", params={"tenant_id": 4}).json()["items"][0]
    return client.get("/billing/charges", params={"customer_id": customer["id"], "status": "succeeded",
                                                  "created_after": "2026-10-01T00:00:00Z"}).json()["items"]


def _close(client, ids):
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={"body": "Refunded the duplicate charge."})
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"})


def run_refund_latest(client, ids):
    """Takes the ticket at face value: refunds the most recent identical charge."""
    latest = _october(client, ids)[0]  # newest first
    client.post("/billing/refunds", json={"charge_id": latest["id"], "reason": "duplicate charge"})
    _close(client, ids)


def run_refund_all_extras(client, ids):
    """Keeps only the first October charge and refunds everything else that looks identical."""
    charges = sorted(_october(client, ids), key=lambda c: c["created_at"])
    for c in charges[1:]:
        client.post("/billing/refunds", json={"charge_id": c["id"], "reason": "duplicate charge"})
    _close(client, ids)
