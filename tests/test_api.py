"""Tests voor de FastAPI-service (api/main.py)."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import core.storage as storage
from api.main import app


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "api.db")
    monkeypatch.delenv("SENTINEL_API_KEY", raising=False)
    storage.init_db()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_dataset():
    rng = np.random.default_rng(11)
    n = 90
    df = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D"),
        "value": np.clip(5 + rng.normal(0, 1.5, n), 0, None),
        "location_name": ["Alpha"] * n,
    })
    ds = storage.create_dataset("api-test", "", {})
    storage.insert_observations(ds, df)
    return ds


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_list_datasets(client, seeded_dataset):
    names = [d["name"] for d in client.get("/datasets").json()]
    assert "api-test" in names


def test_observations_paginated(client, seeded_dataset):
    r = client.get(f"/datasets/{seeded_dataset}/observations?limit=10").json()
    assert r["total"] == 90
    assert len(r["rows"]) == 10


def test_normbeeld_endpoint(client, seeded_dataset):
    r = client.get(f"/datasets/{seeded_dataset}/normbeeld?location=Alpha")
    assert r.status_code == 200
    body = r.json()
    assert body["location"] == "Alpha"
    assert body["upper_band"] >= body["lower_band"]
    assert len(body["forecast"]) == 14


def test_unknown_dataset_404(client):
    assert client.get("/datasets/999/normbeeld").status_code == 404


def test_invalid_aggregation_422(client, seeded_dataset):
    r = client.get(f"/datasets/{seeded_dataset}/alerts?aggregation=fout")
    assert r.status_code == 422


def test_api_key_enforced(monkeypatch, seeded_dataset):
    monkeypatch.setenv("SENTINEL_API_KEY", "geheim")
    with TestClient(app) as c:
        assert c.get("/datasets").status_code == 401
        assert c.get("/datasets",
                     headers={"X-API-Key": "fout"}).status_code == 401
        assert c.get("/datasets",
                     headers={"X-API-Key": "geheim"}).status_code == 200
        # /health blijft open (voor loadbalancer-checks)
        assert c.get("/health").status_code == 200
