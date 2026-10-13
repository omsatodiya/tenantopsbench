"""cross-tenant-member-visible: the new project_members table shipped without an RLS policy."""
from datetime import timedelta

from services.seedlib import World, base_world, sort_audit, ts

RELEASE_AT = ts(hours=-18.5)          # 2026-10-12 15:00 UTC: member list v2 ships (beta: Acme, Brightwave)
COPPERLEAF_BETA_AT = ts(hours=-2)     # 2026-10-13 07:30 UTC: Copperleaf added to the beta, rows backfilled


def build() -> World:
    w = base_world()
    rng = w.rng
    rng.seed(2002)

    # The bug: the new table has no policy and RLS is off. Older tables keep their policies.
    w.post_sql = ("DROP POLICY tenant_isolation ON app_db.project_members;"
                  "ALTER TABLE app_db.project_members DISABLE ROW LEVEL SECURITY;")

    for pm in w.find("app_db.project_members", tenant_id=4):
        pm["added_at"] = COPPERLEAF_BETA_AT + timedelta(seconds=pm["id"])

    w.add("audit.events", occurred_at=RELEASE_AT, actor="deploy-bot", tenant_id=None, action="deploy.completed",
          target_type="release", target_id="2026.10.12-1",
          details={"changes": ["member list v2: new table app_db.project_members (migration 0142)",
                               "backfill project_members for beta tenants"]})
    for tid, at in ((2, RELEASE_AT + timedelta(minutes=5)), (3, RELEASE_AT + timedelta(minutes=5)),
                    (4, COPPERLEAF_BETA_AT)):
        w.add("audit.events", occurred_at=at, actor="growth-team@tenantops.io", tenant_id=tid,
              action="feature_flag.enabled", target_type="feature_flag", target_id="member_list_v2",
              details={"tenant_id": tid})

    def rows_visible_at(t):
        return sorted(pm["id"] for pm in w.rows("app_db.project_members") if pm["added_at"] <= t)

    # Member-list views. Without RLS each view returned every row that existed at that moment.
    views = []
    t = RELEASE_AT + timedelta(minutes=20)
    while t < ts(hours=-2.2):                        # Acme & Brightwave browse until 07:18 today
        views.append((rng.choice([2, 2, 3]), t))
        t += timedelta(minutes=rng.randint(45, 150))
    views.append((2, ts(hours=-2.25)))               # 07:15: the Acme user who files the ticket
    for m in (10, 40, 65):                           # Copperleaf browses after joining the beta
        views.append((4, COPPERLEAF_BETA_AT + timedelta(minutes=m)))
    for tid, at in views:
        viewer = rng.choice([u for u in w.find("app_db.users", tenant_id=tid) if u["status"] == "active"])
        w.add("audit.events", occurred_at=at, actor=viewer["email"], tenant_id=tid, action="member_list.viewed",
              target_type="endpoint", target_id="GET /tenants/{tenant_id}/member-list",
              details={"viewer_user_id": viewer["id"], "row_ids": rows_visible_at(at)})
    # v1 user-list views (RLS protected, own rows only): noise
    for _ in range(8):
        tid = rng.choice([2, 3, 4, 5])
        u = rng.choice(w.find("app_db.users", tenant_id=tid))
        w.add("audit.events", occurred_at=ts(hours=-rng.uniform(1, 40)), actor=u["email"], tenant_id=tid,
              action="user_list.viewed", target_type="endpoint", target_id="GET /tenants/{tenant_id}/users",
              details={"viewer_user_id": u["id"]})

    exposed = set()
    for tid, at in views:
        exposed |= {pm["tenant_id"] for pm in w.rows("app_db.project_members")
                    if pm["added_at"] <= at and pm["tenant_id"] != tid}

    foreign = next(pm for pm in w.rows("app_db.project_members") if pm["tenant_id"] == 3)
    requester = w.find("app_db.users", tenant_id=2)[2]
    body = (f"I can see someone from another company in our member list: {foreign['display_name']} "
            f"({foreign['email']}). They are not one of ours. What is going on?")
    tk = w.add("helpdesk.tickets", subject="Stranger in our member list", body=body,
               requester_email=requester["email"], tenant_id=2, status="open", priority="high",
               assignee_agent_id=None, created_at=ts(hours=-2.2), updated_at=ts(hours=-2.2), resolved_at=None)
    sort_audit(w)
    w.ids.update(ticket_id=tk["id"], requester=requester["email"], body=body, exposed_tenants=sorted(exposed))
    return w
