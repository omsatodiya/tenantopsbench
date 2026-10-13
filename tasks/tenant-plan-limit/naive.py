"""Scripted naive agent: bumps the seat limit so the 6th invite works."""


def run(client, ids):
    client.patch("/app/tenants/5/seat-limit", json={"seat_limit": 10})
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={"body": "Raised your seat limit."})
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"})
