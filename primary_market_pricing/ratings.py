"""Portal-backed internal-rating lookup for primary-market results."""

from __future__ import annotations

from juyuan_update import config as portal_config
from juyuan_update.unified_excel import normalize_rating

_CACHE_MTIME_NS: int | None = None
_CACHE: dict[str, str] = {}


def _load_ratings() -> dict[str, str]:
    """按主体读取信评门户最新内评（每日更新任务自动同步的 portal_data 缓存）。"""
    from juyuan_update.neiping_portal_fetch import load_portal_data

    ratings = load_portal_data().get("ratings") or {}
    return {
        issuer: normalize_rating(rating)
        for issuer, rating in ratings.items()
        if normalize_rating(rating)
    }


def internal_rating_by_issuer() -> dict[str, str]:
    """Return the latest non-empty portal internal rating for each issuer."""
    global _CACHE_MTIME_NS, _CACHE
    path = portal_config.PORTAL_DATA_JSON
    mtime_ns = path.stat().st_mtime_ns if path.exists() else None
    if mtime_ns != _CACHE_MTIME_NS:
        _CACHE = _load_ratings()
        _CACHE_MTIME_NS = mtime_ns
    return _CACHE


def attach_internal_ratings(bonds: list[dict]) -> list[dict]:
    ratings = internal_rating_by_issuer()
    for bond in bonds:
        bond["internal_rating"] = ratings.get(str(bond.get("issuer") or "").strip(), "")
    return bonds


def rating_for_issuer(issuer: str) -> str:
    return internal_rating_by_issuer().get(str(issuer or "").strip(), "")
