"""double-charge-refund: one true duplicate charge and one legitimate seat add-on that looks like a duplicate."""
from datetime import datetime, timezone

from services.seedlib import World, base_world, ts

UTC = timezone.utc


def build() -> World:
    w = base_world()
    rng = w.rng
    rng.seed(3003)
    tenant = 4  # Copperleaf Studio, Team plan, $49/seat/month, 10 seats
    cus = w.one("billing.customers", tenant_id=tenant)
    cid = cus["id"]

    # Normalise October for this customer, then lay out the scenario.
    w.tables["billing.charges"] = [c for c in w.rows("billing.charges")
                                   if not (c["customer_id"] == cid and c["created_at"].month == 10)]
    desc = "Team plan - Oct 2026"
    renew_key = f"sub_renew_{cid}_2026-10"
    original = w.add("billing.charges", id="ch_81726354", customer_id=cid, amount_cents=49000, currency="usd",
                     description=desc, idempotency_key=renew_key, status="succeeded",
                     created_at=datetime(2026, 10, 1, 9, 0, 12, tzinfo=UTC))
    # The renewal worker timed out waiting for the processor and retried with a suffixed key: true duplicate.
    duplicate = w.add("billing.charges", id="ch_81726401", customer_id=cid, amount_cents=49000, currency="usd",
                      description=desc, idempotency_key=f"{renew_key}:attempt-2", status="succeeded",
                      created_at=datetime(2026, 10, 1, 9, 0, 53, tzinfo=UTC))
    # Legitimate: 10 extra seats added on Oct 9 (billed for the full month, per pricing policy).
    addon = w.add("billing.charges", id="ch_82990417", customer_id=cid, amount_cents=49000, currency="usd",
                  description=desc, idempotency_key=f"sub_update_{cid}_2026-10-09", status="succeeded",
                  created_at=datetime(2026, 10, 9, 14, 22, 3, tzinfo=UTC))
    # A different customer with a declined card + retry this month: looks like two charges, is not.
    acme = w.one("billing.customers", tenant_id=2)
    w.tables["billing.charges"] = [c for c in w.rows("billing.charges")
                                   if not (c["customer_id"] == acme["id"] and c["created_at"].month == 10)]
    w.add("billing.charges", id="ch_81726120", customer_id=acme["id"], amount_cents=189000, currency="usd",
          description="Business plan - Oct 2026", idempotency_key=f"sub_renew_{acme['id']}_2026-10", status="failed",
          created_at=datetime(2026, 10, 1, 9, 0, 4, tzinfo=UTC))
    w.add("billing.charges", id="ch_81990033", customer_id=acme["id"], amount_cents=189000, currency="usd",
          description="Business plan - Oct 2026", idempotency_key=f"sub_renew_{acme['id']}_2026-10#retry-card",
          status="succeeded", created_at=datetime(2026, 10, 3, 9, 0, 4, tzinfo=UTC))
    w.tables["billing.charges"].sort(key=lambda c: c["created_at"])

    # Seat history lives only in the product database.
    owner = w.find("app_db.users", tenant_id=tenant)[0]
    seat_rows = w.find("app_db.seat_changes", tenant_id=tenant)
    seat_rows[-1]["to_seats"] = 10
    w.add("app_db.seat_changes", tenant_id=tenant, changed_by_user_id=owner["id"], from_seats=10, to_seats=20,
          reason="design team + autumn interns", created_at=datetime(2026, 10, 9, 14, 21, 40, tzinfo=UTC))
    w.add("audit.events", occurred_at=datetime(2026, 10, 9, 14, 21, 40, tzinfo=UTC), actor=owner["email"],
          tenant_id=tenant, action="settings.changed", target_type="tenant", target_id=str(tenant),
          details={"setting": "billing"})

    requester = owner
    tk = w.add("helpdesk.tickets", subject="Charged twice this month",
               body="I was charged twice this month, please refund.", requester_email=requester["email"],
               tenant_id=tenant, status="open", priority="normal", assignee_agent_id=None,
               created_at=ts(hours=-3), updated_at=ts(hours=-3), resolved_at=None)
    from services.seedlib import sort_audit
    sort_audit(w)
    w.ids.update(ticket_id=tk["id"], requester=requester["email"], original=original["id"],
                 duplicate=duplicate["id"], addon=addon["id"], customer=cid)
    return w
