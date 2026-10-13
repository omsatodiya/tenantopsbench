"""The root app. GET /catalog is the ONLY discovery entry point handed to the agent."""
from __future__ import annotations

from fastapi import FastAPI

from services import app_db, audit, billing, helpdesk, jobs

SERVICES = [
    ("app_db", "/app", app_db.app, "Multi-tenant product database: tenants, users, memberships, projects, API keys, RLS admin."),
    ("jobs", "/jobs", jobs.app, "Background job queue, worker configuration and integration credentials."),
    ("billing", "/billing", billing.app, "Customers, subscriptions, charges and refunds."),
    ("helpdesk", "/helpdesk", helpdesk.app, "Support tickets, comments and support agents."),
    ("audit", "/audit", audit.app, "Append-only audit log and security notifications."),
]


def create_app() -> FastAPI:
    root = FastAPI(title="TenantOps internal platform", docs_url=None, redoc_url=None, openapi_url=None)

    @root.get("/catalog")
    def catalog():
        return {
            "description": "Internal operator APIs. Each service publishes an OpenAPI 3 spec describing its endpoints.",
            "services": [
                {"name": name, "base_path": prefix, "description": desc, "openapi": f"{prefix}/openapi.json"}
                for name, prefix, _, desc in SERVICES
            ],
        }

    for _, prefix, sub, _ in SERVICES:
        root.mount(prefix, sub)
    return root


app = create_app()
