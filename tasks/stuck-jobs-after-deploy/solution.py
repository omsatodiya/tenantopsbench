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
    deploys = _all(client, "/audit/events", action="deploy.completed")
    latest = deploys[-1]
    assert "date_range" in " ".join(latest["details"]["changes"])

    fixed = []
    for j in _all(client, "/jobs/jobs", type="report_export", status="dead"):
        if "PayloadValidationError" not in (j["last_error"] or ""):
            continue  # dead for an unrelated reason
        p = j["payload"]
        v2 = {"report_id": p["report_id"], "format": p["format"], "date_range": {"from": p["from"], "to": p["to"]}}
        client.patch(f"/jobs/jobs/{j['id']}", json={"payload": v2}).raise_for_status()
        client.post(f"/jobs/jobs/{j['id']}/requeue").raise_for_status()
        fixed.append(j["id"])

    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={
        "body": f"This morning's release changed the report job format, and reports scheduled just before it could not "
                f"run. We converted and re-ran the {len(fixed)} affected reports; they will arrive shortly.",
    }).raise_for_status()
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"}).raise_for_status()
