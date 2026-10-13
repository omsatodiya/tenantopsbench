import pytest
from fastapi.testclient import TestClient

from services.catalog import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app, base_url="http://tenantops.internal") as c:
        yield c
