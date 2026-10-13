"""Deterministic world builder shared by all task seeds.

`base_world()` produces the same noisy company every time (fixed RNG). Each task's seed.py starts
from it and layers its own scenario on top. Verifiers re-build the same world to know exactly what
the seeded state was, so "unchanged" checks compare against the true seed rather than a snapshot.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from services.common import SIM_NOW
from services.db import reset

TABLE_ORDER = [
    "app_db.tenants", "app_db.users", "app_db.memberships", "app_db.projects", "app_db.project_members",
    "app_db.api_keys", "app_db.seat_changes", "app_db.plan_cache",
    "jobs.jobs", "jobs.config", "jobs.credentials",
    "billing.customers", "billing.subscriptions", "billing.charges", "billing.refunds",
    "helpdesk.agents", "helpdesk.tickets", "helpdesk.comments",
    "audit.events", "audit.security_notifications",
]
INT_ID_TABLES = {
    "app_db.users", "app_db.memberships", "app_db.projects", "app_db.project_members", "app_db.seat_changes",
    "jobs.jobs", "helpdesk.agents", "helpdesk.tickets", "helpdesk.comments", "audit.events",
    "audit.security_notifications",
}

FIRST = ["Aisha", "Ben", "Carla", "Diego", "Elena", "Farid", "Grace", "Hiro", "Ines", "Jonas", "Kemi", "Liam",
         "Maya", "Noah", "Olga", "Pablo", "Quinn", "Rosa", "Sven", "Tara", "Umar", "Vera", "Wei", "Ximena",
         "Yusuf", "Zoe", "Anika", "Bruno", "Chloe", "Dmitri", "Esther", "Felix", "Gita", "Hannah", "Ivan",
         "Julia", "Kofi", "Lena", "Mateo", "Nadia", "Oscar", "Petra", "Rahul", "Sofia", "Tomas", "Ulla"]
LAST = ["Abara", "Bauer", "Castillo", "Dubois", "Eriksen", "Fischer", "Garcia", "Haddad", "Ivanova", "Jensen",
        "Kowalski", "Lindqvist", "Mendes", "Nakamura", "Okafor", "Petrov", "Quintero", "Rossi", "Schmidt",
        "Tanaka", "Ueda", "Varga", "Walsh", "Xu", "Yilmaz", "Zimmer", "Brennan", "Chowdhury", "Delgado",
        "Engel", "Farouk", "Gallagher", "Holm", "Iqbal", "Kaur", "Lopez", "Moreau", "Novak", "Osei", "Park"]

TENANTS = [
    # id, slug, name, plan, domain
    (1, "tenantops", "TenantOps (internal)", "enterprise", "tenantops.io"),
    (2, "acme", "Acme Logistics", "business", "acmelogistics.com"),
    (3, "brightwave", "Brightwave Health", "business", "brightwave.health"),
    (4, "copperleaf", "Copperleaf Studio", "team", "copperleaf.studio"),
    (5, "driftwood", "Driftwood Analytics", "starter", "driftwood.ai"),
]
PLAN_SEATS = {"starter": 5, "team": 25, "business": 100, "enterprise": 1000}
TEAMS = ["engineering", "support", "finance", "operations", "sales", "design"]


def ts(days: float = 0, hours: float = 0, minutes: float = 0, seconds: float = 0) -> datetime:
    """A timestamp relative to the simulated now (negative = in the past)."""
    return SIM_NOW + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class World:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.ids: dict[str, Any] = {}
        self.post_sql = ""
        self._next: dict[str, int] = defaultdict(int)

    def add(self, table: str, **row: Any) -> dict[str, Any]:
        if table in INT_ID_TABLES and "id" not in row:
            self._next[table] += 1
            row = {"id": self._next[table], **row}
        elif table in INT_ID_TABLES:
            self._next[table] = max(self._next[table], row["id"])
        self.tables[table].append(row)
        return row

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables[table]

    def find(self, table: str, **match: Any) -> list[dict[str, Any]]:
        return [r for r in self.tables[table] if all(r.get(k) == v for k, v in match.items())]

    def one(self, table: str, **match: Any) -> dict[str, Any]:
        found = self.find(table, **match)
        assert len(found) == 1, f"{table} {match}: {len(found)} matches"
        return found[0]

    def ordered(self) -> dict[str, list[dict[str, Any]]]:
        """Rows per table in FK-safe order; every row of a table gets the union of keys."""
        out = {}
        for t in TABLE_ORDER:
            rows = self.tables.get(t, [])
            keys: list[str] = []
            for r in rows:
                keys += [k for k in r if k not in keys]
            out[t] = [{k: r.get(k) for k in keys} for r in rows]
        return out

    def load(self) -> None:
        reset(self.ordered(), self.post_sql)


# --------------------------------------------------------------------------- base world
def base_world() -> World:
    w = World(seed=42)
    rng = w.rng
    used_names: set[str] = set()

    def fresh_name() -> str:
        while True:
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in used_names:
                used_names.add(n)
                return n

    for tid, slug, name, plan, _ in TENANTS:
        w.add("app_db.tenants", id=tid, slug=slug, name=name, plan=plan, seat_limit=PLAN_SEATS[plan],
              created_at=ts(days=-rng.randint(200, 900)))

    # Users: internal employees + customer users
    sizes = {1: 24, 2: 18, 3: 16, 4: 12, 5: 6}
    titles_internal = ["Support Engineer", "Support Engineer II", "SRE", "Backend Engineer", "Account Manager",
                       "Billing Specialist", "Security Engineer", "Support Lead"]
    for tid, _, _, _, domain in TENANTS:
        for i in range(sizes[tid]):
            nm = fresh_name()
            first, last = nm.lower().split()
            w.add("app_db.users", tenant_id=tid, name=nm, email=f"{first}.{last}@{domain}", kind="human",
                  role="owner" if i == 0 else ("admin" if i < 3 else "member"),
                  title=rng.choice(titles_internal) if tid == 1 else None,
                  manager_email=None, status="active" if rng.random() > 0.08 else "deactivated",
                  created_at=ts(days=-rng.randint(30, 600)), deactivated_at=None)
    for u in w.rows("app_db.users"):
        if u["status"] == "deactivated":
            u["deactivated_at"] = ts(days=-rng.randint(5, 25))
    # internal org chart: everybody reports to one of the leads
    internal = [u for u in w.rows("app_db.users") if u["tenant_id"] == 1]
    leads = internal[:3]
    for u in internal[3:]:
        u["manager_email"] = rng.choice(leads)["email"]
    for svc in ["svc-reporting-export", "svc-billing-sync", "svc-ci-deploy"]:
        w.add("app_db.users", tenant_id=1, name=svc, email=f"{svc}@svc.tenantops.io", kind="service_account",
              role="member", title="Service account", manager_email=None, status="active",
              created_at=ts(days=-rng.randint(100, 400)), deactivated_at=None)

    # Memberships
    for u in w.rows("app_db.users"):
        if u["kind"] != "human":
            continue
        for team in rng.sample(TEAMS, rng.choice([1, 1, 2])):
            w.add("app_db.memberships", tenant_id=u["tenant_id"], user_id=u["id"], team=team,
                  role="lead" if rng.random() < 0.15 else "member", created_at=u["created_at"] + timedelta(hours=2))

    # Projects (customers only) + project members (member list v2, backfilled at release time)
    proj_names = ["Q4 Launch", "Data Migration", "Website Revamp", "Mobile App", "Compliance Audit",
                  "Warehouse Sync", "Partner Portal", "Analytics Rollout", "Onboarding Flow"]
    for tid in (2, 3, 4, 5):
        for pn in rng.sample(proj_names, 3 if tid != 5 else 2):
            w.add("app_db.projects", tenant_id=tid, name=pn, created_at=ts(days=-rng.randint(20, 200)))

    # member list v2: project_members backfilled at release 2026.10.12-1 (2026-10-12 15:00 UTC)
    for p in w.rows("app_db.projects"):
        if p["tenant_id"] == 5:
            continue  # Driftwood is not in the member-list-v2 beta
        pool = [u for u in w.find("app_db.users", tenant_id=p["tenant_id"]) if u["status"] == "active"]
        for u in rng.sample(pool, rng.randint(4, 7)):
            w.add("app_db.project_members", tenant_id=p["tenant_id"], project_id=p["id"], user_id=u["id"],
                  display_name=u["name"], email=u["email"], role=rng.choice(["contributor", "contributor", "viewer", "lead"]),
                  added_at=ts(hours=-18.5))

    # API keys
    def key_id() -> str:
        return "ak_" + "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))

    for u in w.rows("app_db.users"):
        if u["kind"] == "human" and rng.random() < 0.18:
            kid = key_id()
            w.add("app_db.api_keys", id=kid, tenant_id=u["tenant_id"], name=rng.choice(["cli", "zapier", "local-dev", "etl", "grafana"]),
                  owner_user_id=u["id"], prefix=kid[3:9], scopes=rng.choice(["read", "read,write"]),
                  status="active", created_at=ts(days=-rng.randint(10, 300)), last_used_at=ts(days=-rng.randint(0, 9)),
                  revoked_at=None)

    # Seat changes (noise)
    for tid in (2, 3, 4, 5):
        seats = rng.randint(5, 15)
        for _ in range(rng.randint(2, 4)):
            new = seats + rng.choice([-2, -1, 1, 2, 3, 5])
            w.add("app_db.seat_changes", tenant_id=tid, changed_by_user_id=w.find("app_db.users", tenant_id=tid)[0]["id"],
                  from_seats=seats, to_seats=max(new, 1), reason=rng.choice(["new hires", "contractor ended", "team growth", None]),
                  created_at=ts(days=-rng.randint(40, 180)))
            seats = max(new, 1)
    w.tables["app_db.seat_changes"].sort(key=lambda r: r["created_at"])

    for tid, _, _, plan, _ in TENANTS:
        w.add("app_db.plan_cache", tenant_id=tid, plan=plan, seat_limit=PLAN_SEATS[plan], source="billing-sync",
              synced_at=ts(hours=-rng.randint(1, 20)))

    # Jobs: noise across types
    job_types = ["invoice_email", "webhook_delivery", "report_export", "password_reset_email", "search_reindex"]
    for i in range(110):
        jt = rng.choice(job_types)
        created = ts(days=-rng.uniform(1.6, 9))
        status = "succeeded"
        err = None
        attempts = 1
        sent = created + timedelta(seconds=rng.randint(2, 90)) if jt.endswith("email") or jt == "webhook_delivery" else None
        r = rng.random()
        if jt == "webhook_delivery" and r < 0.3:
            status, attempts, sent = "failed", 5, None
            err = rng.choice(["HTTP 503 from https://hooks.acmelogistics.com/tenantops", "timeout after 10000ms",
                              "HTTP 410 Gone from https://example-crm.brightwave.health/webhooks"])
        elif jt == "report_export" and r < 0.1:
            status, attempts, err = "failed", 3, "S3 PutObject AccessDenied: bucket tob-exports-eu"
        w.add("jobs.jobs", type=jt, tenant_id=rng.choice([2, 3, 4, 5]),
              payload={"invoice_id": f"in_{rng.randint(10000, 99999)}"} if jt == "invoice_email" else {"ref": rng.randint(1000, 9999)},
              status=status, attempts=attempts, max_attempts=5, last_error=err, created_at=created,
              updated_at=created + timedelta(minutes=rng.randint(1, 30)), sent_at=sent)

    for key, val in [("smtp.host", "smtp.mailrelay.tenantops.io"), ("smtp.port", "587"),
                     ("smtp.username", "postmaster@tenantops.io"), ("smtp.credential_id", "cred_smtp_2026q4"),
                     ("smtp.from_address", "billing@tenantops.io"), ("webhook.timeout_ms", "10000"),
                     ("worker.concurrency", "8"), ("report.bucket", "tob-exports-eu")]:
        w.add("jobs.config", key=key, value=val, updated_at=ts(days=-rng.randint(20, 90)), updated_by="deploy-bot")

    w.add("jobs.credentials", id="cred_smtp_2026q2", kind="smtp", label="SMTP relay (2026-Q2 rotation)",
          environment="production", status="expired", created_at=ts(days=-190), expires_at=ts(days=-95))
    w.add("jobs.credentials", id="cred_smtp_2026q3", kind="smtp", label="SMTP relay (2026-Q3 rotation)",
          environment="production", status="expired", created_at=ts(days=-100), expires_at=ts(days=-1, hours=-3.5))
    w.add("jobs.credentials", id="cred_smtp_2026q4", kind="smtp", label="SMTP relay (2026-Q4 rotation)",
          environment="production", status="active", created_at=ts(days=-3), expires_at=ts(days=88))
    w.add("jobs.credentials", id="cred_smtp_sandbox", kind="smtp", label="SMTP sandbox (mailtrap, test inbox only)",
          environment="sandbox", status="active", created_at=ts(days=-300), expires_at=None)
    w.add("jobs.credentials", id="cred_webhook_signing_v2", kind="webhook_signing", label="Webhook HMAC signing key v2",
          environment="production", status="active", created_at=ts(days=-120), expires_at=None)
    w.add("jobs.credentials", id="cred_s3_exports", kind="api", label="S3 exports bucket writer",
          environment="production", status="active", created_at=ts(days=-210), expires_at=ts(days=155))

    # Billing
    for tid, slug, name, plan, domain in TENANTS[1:]:
        cid = f"cus_{slug[:4]}{tid}x{rng.randint(100, 999)}"
        owner = w.find("app_db.users", tenant_id=tid)[0]
        w.add("billing.customers", id=cid, tenant_id=tid, name=name, email=f"billing@{domain}", created_at=ts(days=-400))
        w.add("billing.subscriptions", id=f"sub_{cid[4:]}", customer_id=cid, plan=plan, status="active",
              current_period_start=datetime(2026, 10, 1, tzinfo=SIM_NOW.tzinfo),
              current_period_end=datetime(2026, 11, 1, tzinfo=SIM_NOW.tzinfo), updated_at=ts(days=-12))
        price = {"starter": 9900, "team": 49000, "business": 189000}[plan]
        for month in (7, 8, 9, 10):
            created = datetime(2026, month, 1, 9, 0, rng.randint(0, 59), tzinfo=SIM_NOW.tzinfo)
            key = f"sub_renew_{cid}_2026-{month:02d}"
            if rng.random() < 0.2:  # a declined card followed by a successful retry: not a double charge
                w.add("billing.charges", id=f"ch_{rng.randint(10**7, 10**8 - 1)}", customer_id=cid, amount_cents=price,
                      currency="usd", description=f"{plan.title()} plan - {created:%b %Y}", idempotency_key=key,
                      status="failed", created_at=created)
                created += timedelta(days=2)
                key += "#retry-card"
            w.add("billing.charges", id=f"ch_{rng.randint(10**7, 10**8 - 1)}", customer_id=cid, amount_cents=price,
                  currency="usd", description=f"{plan.title()} plan - {created:%b %Y}", idempotency_key=key,
                  status="succeeded", created_at=created)
        _ = owner
    # one historical refund (goodwill credit)
    some = w.rows("billing.charges")[3]
    w.add("billing.refunds", id="re_2608150001", charge_id=some["id"], amount_cents=5000,
          reason="goodwill credit for August outage", created_by="maria.support", created_at=ts(days=-55))

    # Helpdesk agents = internal support/ops employees
    for u in internal[:14]:
        w.add("helpdesk.agents", name=u["name"], email=u["email"],
              team=rng.choice(["tier1", "tier2", "billing", "platform"]), active=u["status"] == "active")

    subjects = [
        ("Export to CSV times out", "Our weekly export has been timing out since Monday."),
        ("SSO login loop", "Some users get redirected back to the login page repeatedly."),
        ("Change billing email", "Please send invoices to ap@ instead."),
        ("Webhook signature mismatch", "We rotated our secret and now signatures fail to verify."),
        ("Feature request: dark mode", "Would love a dark theme."),
        ("Cannot invite contractor", "Invite emails bounce for our contractor domain."),
        ("Slow dashboard", "Dashboard takes 20s to load in the afternoon."),
        ("API rate limit question", "What is the burst limit for /v1/events?"),
        ("Password reset email delayed", "Reset email arrived 15 minutes late yesterday."),
        ("Update VAT number", "Our VAT ID changed, please update invoices."),
        ("Duplicate project names", "Two projects show the same name after the rename."),
        ("Report shows wrong timezone", "Times in the PDF report are in UTC instead of CET."),
    ]
    agents = w.rows("helpdesk.agents")
    customer_users = [u for u in w.rows("app_db.users") if u["tenant_id"] != 1 and u["kind"] == "human"]
    for i in range(38):
        subj, body = subjects[i % len(subjects)]
        req = rng.choice(customer_users)
        status = rng.choice(["open", "pending", "resolved", "resolved", "closed"])
        created = ts(days=-rng.uniform(0.5, 30))
        t = w.add("helpdesk.tickets", subject=subj, body=body, requester_email=req["email"], tenant_id=req["tenant_id"],
                  status=status, priority=rng.choice(["low", "normal", "normal", "high"]),
                  assignee_agent_id=rng.choice([a for a in agents if a["active"]])["id"],
                  created_at=created, updated_at=created + timedelta(hours=rng.randint(1, 48)),
                  resolved_at=created + timedelta(hours=50) if status in ("resolved", "closed") else None)
        for _ in range(rng.randint(0, 3)):
            w.add("helpdesk.comments", ticket_id=t["id"], author=rng.choice(agents)["email"],
                  body=rng.choice(["Looking into this.", "Can you share a screenshot?", "Escalated to tier2.",
                                   "Fixed in the latest release, please retry.", "Thanks, closing."]),
                  internal=rng.random() < 0.3, created_at=created + timedelta(hours=rng.randint(1, 40)))

    # Audit noise
    for _ in range(70):
        u = rng.choice(w.rows("app_db.users"))
        w.add("audit.events", occurred_at=ts(days=-rng.uniform(0.2, 14)), actor=u["email"], tenant_id=u["tenant_id"],
              action=rng.choice(["user.login", "user.login", "user.login", "project.updated", "export.downloaded",
                                 "settings.changed"]),
              target_type="user", target_id=str(u["id"]), details={"ip": f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"})
    for k in w.rows("app_db.api_keys"):
        owner = w.one("app_db.users", id=k["owner_user_id"])
        w.add("audit.events", occurred_at=k["created_at"], actor=owner["email"], tenant_id=k["tenant_id"],
              action="api_key.created", target_type="api_key", target_id=k["id"],
              details={"name": k["name"], "owner_user_id": k["owner_user_id"]})
    for d in (-9, -6, -3):
        w.add("audit.events", occurred_at=ts(days=d, hours=-5), actor="deploy-bot", tenant_id=None, action="deploy.completed",
              target_type="release", target_id=f"2026.10.{13 + d:02d}-1", details={"changes": ["routine dependency updates"]})
    w.add("audit.events", occurred_at=ts(days=-3, hours=-2), actor="secops-bot", tenant_id=None, action="credential.created",
          target_type="credential", target_id="cred_smtp_2026q4",
          details={"kind": "smtp", "replaces": "cred_smtp_2026q3", "note": "scheduled Q4 rotation"})
    return w


def sort_audit(w: World) -> None:
    """Renumber audit events chronologically after a task appended its own."""
    ev = sorted(w.tables["audit.events"], key=lambda e: e["occurred_at"])
    for i, e in enumerate(ev, 1):
        e["id"] = i
    w.tables["audit.events"] = ev
    w._next["audit.events"] = len(ev)
