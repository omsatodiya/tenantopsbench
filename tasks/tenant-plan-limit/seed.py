"""tenant-plan-limit: a customer upgraded in billing, but the product's cached plan is stale and blocks invites."""
from datetime import datetime, timedelta, timezone

from services.seedlib import World, base_world, sort_audit, ts

UTC = timezone.utc
UPGRADED_AT = ts(hours=-20)  # 2026-10-12 13:30 UTC


def build() -> World:
    w = base_world()
    rng = w.rng
    rng.seed(6006)
    tenant = 5  # Driftwood Analytics, was on Starter (5 seats)

    users = w.find("app_db.users", tenant_id=tenant)
    for i, u in enumerate(users):  # exactly 5 active users: at the Starter limit
        u["status"] = "active" if i < 5 else "deactivated"
        u["deactivated_at"] = None if i < 5 else ts(days=-40)

    cus = w.one("billing.customers", tenant_id=tenant)
    old = w.one("billing.subscriptions", customer_id=cus["id"])
    old.update(status="canceled", updated_at=UPGRADED_AT)
    w.add("billing.subscriptions", id=f"sub_{cus['id'][4:]}_team", customer_id=cus["id"], plan="team", status="active",
          current_period_start=UPGRADED_AT, current_period_end=datetime(2026, 11, 12, 13, 30, tzinfo=UTC),
          updated_at=UPGRADED_AT)
    w.add("billing.charges", id="ch_83110962", customer_id=cus["id"], amount_cents=39100, currency="usd",
          description="Upgrade Starter -> Team (prorated)", idempotency_key=f"sub_upgrade_{cus['id']}_2026-10-12",
          status="succeeded", created_at=UPGRADED_AT + timedelta(seconds=4))

    # The billing -> product sync failed, so plan_cache still says starter / 5 seats.
    pc = w.one("app_db.plan_cache", tenant_id=tenant)
    pc.update(plan="starter", seat_limit=5, source="billing-sync", synced_at=ts(days=-3))
    w.add("jobs.jobs", type="billing_plan_sync", tenant_id=tenant,
          payload={"customer_id": cus["id"], "event": "subscription.updated"}, status="dead", attempts=5,
          max_attempts=5, last_error="HTTP 503 from app_db plan-cache endpoint during maintenance window",
          created_at=UPGRADED_AT + timedelta(seconds=10), updated_at=UPGRADED_AT + timedelta(minutes=45), sent_at=None)
    for _ in range(6):  # healthy syncs for others
        t = rng.choice([2, 3, 4])
        created = ts(days=-rng.uniform(2, 9))
        w.add("jobs.jobs", type="billing_plan_sync", tenant_id=t, payload={"event": "invoice.paid"}, status="succeeded",
              attempts=1, max_attempts=5, last_error=None, created_at=created, updated_at=created + timedelta(seconds=3),
              sent_at=None)

    owner = users[0]
    for m in (25, 70, 90):
        w.add("audit.events", occurred_at=ts(minutes=-m), actor=owner["email"], tenant_id=tenant, action="invite.blocked",
              target_type="tenant", target_id=str(tenant),
              details={"reason": "seat_limit_reached", "seat_limit": 5, "active_users": 5,
                       "invitee": f"new.analyst{m}@driftwood.ai"})
    w.add("audit.events", occurred_at=UPGRADED_AT, actor=owner["email"], tenant_id=tenant, action="billing.plan_changed",
          target_type="subscription", target_id=f"sub_{cus['id'][4:]}_team", details={"from": "starter", "to": "team"})

    tk = w.add("helpdesk.tickets", subject="Can't invite new user after upgrading",
               body="We upgraded to the Team plan yesterday but we still can't invite a 6th person. "
                    "It says we've reached our seat limit.",
               requester_email=owner["email"], tenant_id=tenant, status="open", priority="high", assignee_agent_id=None,
               created_at=ts(minutes=-20), updated_at=ts(minutes=-20), resolved_at=None)
    sort_audit(w)
    w.ids.update(ticket_id=tk["id"], requester=owner["email"], tenant=tenant)
    return w
