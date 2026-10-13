"""offboard-employee: deactivate Priya Nair everywhere, hand over her tickets, revoke a key hidden under a service account."""
from datetime import timedelta

from services.seedlib import World, base_world, sort_audit, ts


def build() -> World:
    w = base_world()
    rng = w.rng
    rng.seed(4004)

    def user(tenant_id, name, email, title=None, manager=None, created=-300):
        return w.add("app_db.users", tenant_id=tenant_id, name=name, email=email, kind="human", role="member",
                     title=title, manager_email=manager, status="active", created_at=ts(days=created),
                     deactivated_at=None)

    marcus = user(1, "Marcus Oyelaran", "marcus.oyelaran@tenantops.io", "Support Lead", None, -700)
    priya = user(1, "Priya Nair", "priya.nair@tenantops.io", "Support Engineer II", marcus["email"], -420)
    natarajan = user(1, "Priya Natarajan", "priya.natarajan@tenantops.io", "Support Engineer", marcus["email"], -200)
    other_priya = user(3, "Priya Nair", "priya.nair@brightwave.health", None, None, -150)
    # an internal decoy manager with a similar role
    for u in (marcus, priya, natarajan):
        w.add("app_db.memberships", tenant_id=1, user_id=u["id"], team="support", role="lead" if u is marcus else "member",
              created_at=u["created_at"])
    w.add("app_db.memberships", tenant_id=3, user_id=other_priya["id"], team="operations", role="member",
          created_at=other_priya["created_at"])

    agents = {}
    for u, team in ((marcus, "tier2"), (priya, "tier2"), (natarajan, "tier1")):
        agents[u["email"]] = w.add("helpdesk.agents", name=u["name"], email=u["email"], team=team, active=True)

    # Tickets assigned to Priya: open/pending must move to her manager, resolved/closed stay as they are.
    customer_users = [u for u in w.rows("app_db.users") if u["tenant_id"] in (2, 3, 4, 5) and u["kind"] == "human"]
    subjects = ["SAML certificate expiring", "Bulk import stuck at 80%", "Webhook retries flooding endpoint",
                "Invoice PDF missing VAT line", "Need audit log export", "2FA reset request",
                "Project archive failed", "Seat count looks wrong"]
    open_ids, closed_ids = [], []
    for i, subj in enumerate(subjects):
        req = rng.choice(customer_users)
        status = ["open", "pending", "open", "pending", "open", "resolved", "closed", "resolved"][i]
        created = ts(days=-rng.uniform(1, 12))
        t = w.add("helpdesk.tickets", subject=subj, body=f"{subj}. Please help.", requester_email=req["email"],
                  tenant_id=req["tenant_id"], status=status, priority=rng.choice(["normal", "high"]),
                  assignee_agent_id=agents[priya["email"]]["id"], created_at=created,
                  updated_at=created + timedelta(hours=3),
                  resolved_at=created + timedelta(days=1) if status in ("resolved", "closed") else None)
        (open_ids if status in ("open", "pending") else closed_ids).append(t["id"])
    for subj in ("Login page slow", "CSV export encoding"):
        req = rng.choice(customer_users)
        w.add("helpdesk.tickets", subject=subj, body=subj, requester_email=req["email"], tenant_id=req["tenant_id"],
              status="open", priority="normal", assignee_agent_id=agents[natarajan["email"]]["id"],
              created_at=ts(days=-2), updated_at=ts(days=-2), resolved_at=None)

    # API keys
    def key(kid, tenant_id, name, owner, created_days, actor):
        k = w.add("app_db.api_keys", id=kid, tenant_id=tenant_id, name=name, owner_user_id=owner["id"], prefix=kid[3:9],
                  scopes="read,write", status="active", created_at=ts(days=created_days),
                  last_used_at=ts(hours=-rng.randint(1, 30)), revoked_at=None)
        w.add("audit.events", occurred_at=k["created_at"], actor=actor["email"], tenant_id=tenant_id,
              action="api_key.created", target_type="api_key", target_id=kid,
              details={"name": name, "owner_user_id": owner["id"]})
        return k

    svc = w.one("app_db.users", name="svc-reporting-export")
    personal = key("ak_pn7h2kq9xa", 1, "priya-laptop-cli", priya, -90, priya)
    hidden = key("ak_rx4m8tz2vb", 1, "reporting-export-prod-2", svc, -34, priya)      # created BY Priya, owned by svc
    svc_other = key("ak_rq9w3np6cd", 1, "reporting-export-prod", svc, -160, marcus)   # same svc, someone else
    nat_key = key("ak_nt5s8yb4ke", 1, "natarajan-cli", natarajan, -40, natarajan)
    bw_key = key("ak_bw2p6nr8xf", 3, "brightwave-etl", other_priya, -60, other_priya)
    # Priya also rotated a shared key months ago that someone else owns: she did not create it.
    w.add("audit.events", occurred_at=ts(days=-20), actor=priya["email"], tenant_id=1, action="api_key.viewed",
          target_type="api_key", target_id=svc_other["id"], details={})

    hr = "people-ops@tenantops.io"
    tk = w.add("helpdesk.tickets", subject="Offboarding: Priya Nair",
               body="Please offboard Priya Nair, last day today.", requester_email=hr, tenant_id=1, status="open",
               priority="normal", assignee_agent_id=None, created_at=ts(hours=-1), updated_at=ts(hours=-1),
               resolved_at=None)
    sort_audit(w)
    w.ids.update(ticket_id=tk["id"], requester=hr, priya_user=priya["id"], priya_agent=agents[priya["email"]]["id"],
                 marcus_agent=agents[marcus["email"]]["id"], natarajan_user=natarajan["id"],
                 natarajan_agent=agents[natarajan["email"]]["id"], other_priya_user=other_priya["id"],
                 personal_key=personal["id"], hidden_key=hidden["id"], open_tickets=open_ids, closed_tickets=closed_ids,
                 untouchable_keys=[svc_other["id"], nat_key["id"], bw_key["id"]])
    return w
