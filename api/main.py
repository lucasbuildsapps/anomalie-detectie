"""SENTINEL Analysis API.

Start lokaal:
    uvicorn api.main:app --reload --port 8000

Authenticatie: zet env-var SENTINEEL_API_KEY (of SENTINEL_API_KEY) en stuur
'X-API-Key' mee. Zonder geconfigureerde key is de API open (lokaal dev) —
zelfde model als de Streamlit-app. Achter de reverse proxy geldt daarnaast
de SSO-laag.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader

from core import storage
from core.logging_setup import get_logger
from core.normbeeld import (
    AGGREGATIONS,
    compute_all_normbeelds,
    compute_normbeeld,
    detect_recent_alerts,
)

logger = get_logger("api")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    storage.init_db()
    yield


app = FastAPI(
    title="SENTINEL Analysis API",
    version="0.9.0",
    description="Normbeeld- en afwijkingsanalyse als service over core/.",
    lifespan=_lifespan,
)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _configured_key() -> str | None:
    return (os.environ.get("SENTINEL_API_KEY")
            or os.environ.get("SENTINEEL_API_KEY"))


def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    configured = _configured_key()
    if configured and key != configured:
        raise HTTPException(status_code=401, detail="Ongeldige of ontbrekende API-key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/datasets", dependencies=[Depends(require_api_key)])
def list_datasets() -> list[dict]:
    return storage.list_datasets()


def _load_or_404(dataset_id: int) -> pd.DataFrame:
    if not any(d["id"] == dataset_id for d in storage.list_datasets()):
        raise HTTPException(status_code=404, detail="Dataset niet gevonden")
    return storage.load_observations(dataset_id)


@app.get("/datasets/{dataset_id}/observations",
         dependencies=[Depends(require_api_key)])
def get_observations(dataset_id: int, limit: int = Query(1000, le=100_000),
                     offset: int = 0) -> dict:
    df = _load_or_404(dataset_id)
    page = df.iloc[offset:offset + limit]
    return {
        "total": len(df),
        "offset": offset,
        "rows": page.to_dict("records"),
    }


@app.get("/datasets/{dataset_id}/normbeeld",
         dependencies=[Depends(require_api_key)])
def get_normbeeld(
    dataset_id: int,
    location: str | None = None,
    horizon: int = Query(14, ge=1, le=60),
    aggregation: str = "daily",
    select: str = Query("heuristic", pattern="^(heuristic|backtest)$"),
) -> dict:
    if aggregation not in AGGREGATIONS:
        raise HTTPException(status_code=422,
                            detail=f"aggregation moet één zijn van {list(AGGREGATIONS)}")
    df = _load_or_404(dataset_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="Dataset is leeg")
    nb = compute_normbeeld(df, location=location, horizon_days=horizon,
                           aggregation=aggregation, select=select)
    if nb is None:
        raise HTTPException(status_code=404,
                            detail="Te weinig data voor een normbeeld")
    return {
        "location": nb.location,
        "aggregation": nb.aggregation,
        "expected_value": nb.expected_value,
        "lower_band": nb.lower_band,
        "upper_band": nb.upper_band,
        "confidence": nb.confidence,
        "pattern_description": nb.pattern_description,
        "methods_used": nb.methods_used,
        "backtest_scores": nb.backtest_scores,
        "band_alpha": nb.band_alpha,
        "band_coverage": nb.band_coverage,
        "widening_source": nb.widening_source,
        "n_history_periods": nb.n_history_periods,
        "historical": nb.historical.assign(
            date=nb.historical["date"].astype(str)
        ).to_dict("records"),
        "forecast": nb.forecast.assign(
            date=nb.forecast["date"].astype(str)
        ).to_dict("records"),
    }


@app.get("/datasets/{dataset_id}/alerts",
         dependencies=[Depends(require_api_key)])
def get_alerts(dataset_id: int, aggregation: str = "daily",
               horizon: int = Query(14, ge=1, le=60)) -> list[dict]:
    if aggregation not in AGGREGATIONS:
        raise HTTPException(status_code=422,
                            detail=f"aggregation moet één zijn van {list(AGGREGATIONS)}")
    df = _load_or_404(dataset_id)
    if df.empty:
        return []
    normbeelds = compute_all_normbeelds(df, horizon_days=horizon,
                                        aggregation=aggregation)
    return detect_recent_alerts(normbeelds, aggregation=aggregation)


@app.get("/audit", dependencies=[Depends(require_api_key)])
def get_audit(limit: int = Query(200, le=1000)) -> list[dict]:
    return [
        {**row, "ts": str(row["ts"])} for row in storage.list_audit(limit)
    ]
