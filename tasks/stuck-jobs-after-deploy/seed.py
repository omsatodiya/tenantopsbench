"""stuck-jobs-after-deploy: a payload schema change dead-lettered report_export jobs enqueued before the deploy."""
from datetime import timedelta

from services.seedlib import World, base_world, sort_audit, ts

DEPLOY_AT = ts(hours=-6.5)  # 2026-10-13 03:00 UTC
SCHEMA_ERR = "PayloadValidationError: report_export v2 requires field 'date_range' {from, to}; unexpected fields: from, to"


def v1(rng, report_id):
    start = ts(days=-rng.randint(8, 30)).date()
    return {"report_id": report_id, "format": rng.choice(["csv", "xlsx", "pdf"]),
            "from": start.isoformat(), "to": (start + timedelta(days=7)).isoformat()}


def to_v2(p):
    return {"report_id": p["report_id"], "format": p["format"], "date_range": {"from": p["from"], "to": p["to"]}}


def build() -> World:
    w = base_world()
    rng = w.rng
    rng.seed(5005)

    w.add("audit.events", occurred_at=DEPLOY_AT, actor="deploy-bot", tenant_id=None, action="deploy.completed",
          target_type="release", target_id="2026.10.13-1",
          details={"changes": ["report_export worker: payload schema v2. The flat `from`/`to` fields are replaced by "
                               "`date_range: {from, to}`; unknown fields are rejected.",
                               "scheduler enqueues v2 payloads from this release on"],
                   "notes": "Jobs already enqueued before the deploy were not migrated."})

    # Nightly scheduled reports enqueued before the deploy, picked up after it: all dead-lettered.
    affected = []
    for i in range(9):
        t = rng.choice([2, 3, 4])
        created = DEPLOY_AT - timedelta(minutes=rng.randint(5, 55))
        j = w.add("jobs.jobs", type="report_export", tenant_id=t, payload=v1(rng, f"rpt_{rng.randint(1000, 9999)}"),
                  status="dead", attempts=5, max_attempts=5, last_error=SCHEMA_ERR, created_at=created,
                  updated_at=DEPLOY_AT + timedelta(minutes=rng.randint(5, 50)), sent_at=None)
        affected.append(j["id"])
    # Enqueued after the deploy with v2 payloads: fine. These show the agent the new shape.
    for i in range(6):
        t = rng.choice([2, 3, 4, 5])
        created = DEPLOY_AT + timedelta(minutes=rng.randint(20, 300))
        w.add("jobs.jobs", type="report_export", tenant_id=t, payload=to_v2(v1(rng, f"rpt_{rng.randint(1000, 9999)}")),
              status="succeeded", attempts=1, max_attempts=5, last_error=None, created_at=created,
              updated_at=created + timedelta(minutes=2), sent_at=None)
    # Distractors: dead jobs with unrelated causes.
    others = []
    j = w.add("jobs.jobs", type="report_export", tenant_id=3, payload=v1(rng, "rpt_4410"), status="dead", attempts=5,
              max_attempts=5, last_error="S3 PutObject AccessDenied: bucket tob-exports-eu (customer bucket policy)",
              created_at=ts(days=-5), updated_at=ts(days=-5, hours=1), sent_at=None)
    others.append(j["id"])
    for i in range(4):
        created = ts(hours=-rng.uniform(1, 30))
        j = w.add("jobs.jobs", type="webhook_delivery", tenant_id=rng.choice([2, 3]), payload={"event": "report.ready"},
                  status="dead", attempts=5, max_attempts=5,
                  last_error="HTTP 410 Gone from https://example-crm.brightwave.health/webhooks (endpoint removed)",
                  created_at=created, updated_at=created + timedelta(hours=1), sent_at=None)
        others.append(j["id"])
    j = w.add("jobs.jobs", type="search_reindex", tenant_id=2, payload={"index": "projects"}, status="dead", attempts=5,
              max_attempts=5, last_error="IndexTooLarge: projects index exceeds 2GB shard limit",
              created_at=ts(hours=-9), updated_at=ts(hours=-8), sent_at=None)
    others.append(j["id"])

    requester = w.find("app_db.users", tenant_id=3)[1]
    tk = w.add("helpdesk.tickets", subject="Scheduled reports missing this morning",
               body="Our weekly scheduled reports did not arrive this morning. They have worked for months.",
               requester_email=requester["email"], tenant_id=3, status="open", priority="high", assignee_agent_id=None,
               created_at=ts(hours=-1), updated_at=ts(hours=-1), resolved_at=None)
    sort_audit(w)
    w.ids.update(ticket_id=tk["id"], requester=requester["email"], affected=affected, other_dead=others)
    return w
