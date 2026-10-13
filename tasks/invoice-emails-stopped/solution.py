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
    ticket_id = ids["ticket_id"]
    config = {c["key"]: c["value"] for c in client.get("/jobs/config").json()}
    creds = client.get("/jobs/credentials", params={"kind": "smtp"}).json()["items"]
    current = next(c for c in creds if c["id"] == config["smtp.credential_id"])
    assert current["status"] == "expired"
    # Corroborate with the audit log: which credential replaced the expired one?
    events = client.get("/audit/events", params={"action": "credential.created", "limit": 100}).json()["items"]
    replacement = next(e["target_id"] for e in events if e["details"].get("replaces") == current["id"])
    assert next(c for c in creds if c["id"] == replacement)["status"] == "active"
    client.patch("/jobs/config/smtp.credential_id", json={"value": replacement}).raise_for_status()

    # Requeue only auth failures that were never delivered (sent_at is the delivery proof).
    requeued = []
    for j in _all(client, "/jobs/jobs", type="invoice_email", status="failed"):
        if j["sent_at"] is None and "535" in (j["last_error"] or ""):
            client.post(f"/jobs/jobs/{j['id']}/requeue").raise_for_status()
            requeued.append(j["id"])

    client.post(f"/helpdesk/tickets/{ticket_id}/comments", json={
        "body": f"The mail worker was still using SMTP credential {current['id']}, which expired at "
                f"{current['expires_at']}. We switched it to {replacement} and re-sent the {len(requeued)} invoice "
                "emails that were never delivered. Emails that had already been delivered were not re-sent.",
    }).raise_for_status()
    client.patch(f"/helpdesk/tickets/{ticket_id}", json={"status": "resolved"}).raise_for_status()
