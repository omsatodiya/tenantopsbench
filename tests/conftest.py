import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
# Tests drop and reseed every schema: point them at a dedicated database when one is configured,
# so `tob validate` never clobbers a benchmark run in progress.
for _var in ("TOB_DATABASE_URL", "TOB_TENANT_DATABASE_URL"):
    if os.environ.get(_var.replace("TOB_", "TOB_TEST_")):
        os.environ[_var] = os.environ[_var.replace("TOB_", "TOB_TEST_")]

import pytest
from fastapi.testclient import TestClient

from services.catalog import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app, base_url="http://tenantops.internal") as c:
        yield c
