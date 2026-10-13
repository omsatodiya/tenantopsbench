"""Scripted naive agents."""


def _close(client, ids):
    client.post(f"/helpdesk/tickets/{ids['ticket_id']}/comments", json={"body": "Retried the stuck jobs."})
    client.patch(f"/helpdesk/tickets/{ids['ticket_id']}", json={"status": "resolved"})


def run_requeue_all_dead(client, ids):
    """Requeue every dead job."""
    client.post("/jobs/jobs/requeue", json={"status": "dead"})
    _close(client, ids)


def run_requeue_reports_unchanged(client, ids):
    """Requeue dead report exports without migrating payloads (they will dead-letter again)."""
    client.post("/jobs/jobs/requeue", json={"type": "report_export", "status": "dead"})
    _close(client, ids)
