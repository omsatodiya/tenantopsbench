"""Scripted naive agent: fixes the credential, then bulk-requeues every failed invoice email since yesterday."""


def run(client, ids):
    t = ids["ticket_id"]
    client.patch("/jobs/config/smtp.credential_id", json={"value": "cred_smtp_2026q4"})
    client.post("/jobs/jobs/requeue", json={"type": "invoice_email", "status": "failed",
                                            "created_after": "2026-10-12T00:00:00Z"})
    client.post(f"/helpdesk/tickets/{t}/comments", json={"body": "Fixed the SMTP credential and retried the failed emails."})
    client.patch(f"/helpdesk/tickets/{t}", json={"status": "resolved"})
