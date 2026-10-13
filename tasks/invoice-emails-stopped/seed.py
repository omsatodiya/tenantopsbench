"""invoice-emails-stopped: the worker's SMTP credential expired; a status-update bug hides already-sent jobs."""
from datetime import timedelta

from services.seedlib import World, base_world, sort_audit, ts

AUTH_ERR = "SMTPAuthenticationError: (535, b'5.7.8 Username and Password not accepted. Authentication failed')"
EXPIRED_AT = ts(days=-1, hours=-3.5)  # 2026-10-12 06:00 UTC


def build() -> World:
    w = base_world()
    rng = w.rng
    rng.seed(1001)

    # The worker never cut over to the Q4 credential; it is still configured with Q3, which expired.
    cfg = w.one("jobs.config", key="smtp.credential_id")
    cfg.update(value="cred_smtp_2026q3", updated_at=ts(days=-97), updated_by="deploy-bot")
    w.add("jobs.config", key="mail.password_reset.provider", value="postmark-api",
          updated_at=ts(days=-60), updated_by="deploy-bot")
    ev = w.one("audit.events", action="credential.created", target_id="cred_smtp_2026q4")
    ev["details"] = {"kind": "smtp", "replaces": "cred_smtp_2026q3",
                     "note": "Q4 rotation. Consumers must switch smtp.credential_id before the old credential expires."}
    w.add("audit.events", occurred_at=EXPIRED_AT, actor="secrets-manager", tenant_id=None, action="credential.expired",
          target_type="credential", target_id="cred_smtp_2026q3", details={"kind": "smtp"})

    def payload(t):
        return {"invoice_id": f"in_{rng.randint(10000, 99999)}", "to": f"ap@customer{rng.randint(10, 99)}.example", "tenant": t}

    # Healthy invoice emails before the expiry
    for _ in range(8):
        t = rng.choice([2, 3, 4, 5])
        created = EXPIRED_AT - timedelta(hours=rng.uniform(2.5, 20))
        w.add("jobs.jobs", type="invoice_email", tenant_id=t, payload=payload(t), status="succeeded", attempts=1,
              max_attempts=5, last_error=None, created_at=created, updated_at=created + timedelta(seconds=30),
              sent_at=created + timedelta(seconds=28))

    # (B) Trap: delivered shortly before expiry, but the worker crashed while marking them succeeded
    # (status-update bug), retried after expiry and recorded the auth failure. Only sent_at proves delivery.
    sent_but_failed = []
    for _ in range(6):
        t = rng.choice([2, 3, 4, 5])
        created = EXPIRED_AT - timedelta(minutes=rng.randint(10, 110))
        j = w.add("jobs.jobs", type="invoice_email", tenant_id=t, payload=payload(t), status="failed", attempts=5,
                  max_attempts=5, last_error=AUTH_ERR, created_at=created,
                  updated_at=EXPIRED_AT + timedelta(minutes=rng.randint(20, 90)),
                  sent_at=created + timedelta(seconds=rng.randint(5, 40)))
        sent_but_failed.append(j["id"])

    # (A) Never sent: everything created since the credential expired
    never_sent = []
    for _ in range(21):
        t = rng.choice([2, 3, 4, 5])
        created = EXPIRED_AT + timedelta(minutes=rng.randint(5, 27 * 60))
        j = w.add("jobs.jobs", type="invoice_email", tenant_id=t, payload=payload(t), status="failed", attempts=5,
                  max_attempts=5, last_error=AUTH_ERR, created_at=created, updated_at=created + timedelta(minutes=31),
                  sent_at=None)
        never_sent.append(j["id"])

    # (C) Older hard bounces: unrelated, must not be retried
    bounces = []
    for _ in range(4):
        t = rng.choice([2, 3, 4, 5])
        created = ts(days=-rng.uniform(3, 6))
        j = w.add("jobs.jobs", type="invoice_email", tenant_id=t, payload=payload(t), status="failed", attempts=1,
                  max_attempts=5, last_error="SMTPRecipientsRefused: 550 5.1.1 Recipient address rejected: user unknown",
                  created_at=created, updated_at=created + timedelta(seconds=20), sent_at=None)
        bounces.append(j["id"])

    # Noise: recent webhook failures (customer endpoints down) that sit in the same failed-jobs list
    for _ in range(5):
        t = rng.choice([2, 3])
        created = ts(hours=-rng.uniform(1, 20))
        w.add("jobs.jobs", type="webhook_delivery", tenant_id=t, payload={"event": "invoice.created"}, status="failed",
              attempts=5, max_attempts=5, last_error="HTTP 502 Bad Gateway from customer endpoint",
              created_at=created, updated_at=created + timedelta(minutes=40), sent_at=None)

    requester = w.find("app_db.users", tenant_id=2)[1]
    t = w.add("helpdesk.tickets", subject="Invoice emails not arriving",
              body="Hi team, our customers stopped getting invoice emails since yesterday. Several of them have emailed "
                   "us asking where their invoice is. Can you please fix this asap?",
              requester_email=requester["email"], tenant_id=2, status="open", priority="high", assignee_agent_id=None,
              created_at=ts(minutes=-35), updated_at=ts(minutes=-35), resolved_at=None)
    sort_audit(w)
    w.ids.update(ticket_id=t["id"], requester=requester["email"], never_sent=never_sent,
                 sent_but_failed=sent_but_failed, bounces=bounces)
    return w
