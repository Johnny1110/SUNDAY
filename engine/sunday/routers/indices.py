"""/api/indices — external macro/crypto indices (req 4).

Token-free read of the risk-weather panel. Values are TTL-cached in ``indices.py``,
so agent fan-out doesn't hammer the free upstreams.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import indices

router = APIRouter(prefix="/api/indices", tags=["indices"])


@router.get("")
def all_indices() -> dict:
    """Snapshot of every index (crypto F&G/dominance + VIX/DXY/SPX/NDX/US10Y/Gold/Brent oil)."""
    return {"items": indices.get_all()}


@router.get("/{key}")
def one_index(key: str) -> dict:
    """One index by key (e.g. fear-greed, vix, dxy, spx, ndx, us10y, gold, oil)."""
    try:
        return indices.get_index(key)
    except KeyError:
        raise HTTPException(404, f"unknown index '{key}'; valid: {', '.join(indices.INDEX_KEYS)}")
