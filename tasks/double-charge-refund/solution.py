"""Reference solution, performed only through the HTTP API."""


def solve(client, ids):
    ticket = client.get(f"/helpdesk/tickets/{ids['ticket_id']}").json()
    user = client.get("/app/users", params={"email": ticket["requester_email"]}).json()["items"][0]
    tenant = user["tenant_id"]
    customer = client.get("/billing/customers", params={"tenant_id": tenant}).json()["items"][0]
    charges = client.get("/billing/charges", params={"customer_id": customer["id"], "status": "succeeded",
                                                     "created_after": "2026-10-01T00:00:00Z"}).json()["items"]
    charges.sort(key=lambda c: c["created_at"])

    # Duplicate: a later attempt of the same invoice (key prefix before _aN), within the same minute.
    def invoice(c):
        return c["idempotency_key"].rsplit("_a", 1)[0]

    duplicate = None
    for i, first in enumerate(charges):
        for later in charges[i + 1:]:
            if invoice(later) == invoice(first) and later["created_at"][:16] == first["created_at"][:16]:
                duplicate = later
    assert duplicate is not None

    # Corroborate the remaining extra charge against the product's seat history.
    seat_changes = client.get("/app/seat-changes", params={"tenant_id": tenant, "limit": 100}).json()["items"]
    october_increase = [s for s in seat_changes if s["created_at"] >= "2026-10-01" and s["to_seats"] > s["from_seats"]]
    assert october_increase, "the third charge would be unexplained"

    refund = client.post("/billing/refunds", json={
        "charge_id": duplicate["id"], "reason": "duplicate: renewal retried with the same idempotency key"}).json()
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={
        "body": f"You were right: the October renewal was charged twice within the same minute. We refunded the "
                f"duplicate charge {duplicate['id']} in full (refund {refund['id']}); it appears in 5-10 business "
                "days. The other extra charge on Oct 9 is for the 10 seats added that day (10 -> 20 seats) and is correct.",
    }).raise_for_status()
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"}).raise_for_status()
