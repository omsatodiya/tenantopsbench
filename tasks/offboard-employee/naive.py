"""Scripted naive agent: name search, deactivate every match, revoke only keys they own."""


def run(client, ids):
    users = [u for u in client.get("/app/users", params={"name": "Priya Nair"}).json()["items"] if u["name"] == "Priya Nair"]
    for u in users:
        client.patch(f"/app/users/{u['id']}", json={"status": "deactivated"})
        for k in client.get("/app/api-keys", params={"owner_user_id": u["id"]}).json()["items"]:
            client.post(f"/app/api-keys/{k['id']}/revoke")
        for a in client.get("/helpdesk/agents", params={"email": u["email"]}).json()["items"]:
            client.patch(f"/helpdesk/agents/{a['id']}", json={"active": False})
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={"body": "Done."})
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"})
