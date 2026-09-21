"""Deterministic fair-spread model. All yields %, all model signals basis points."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median


@dataclass(frozen=True)
class Parameters:
    window: int = 5
    half_life: float = 2.0
    quote_weight: float = 0.65
    trade_weight: float = 0.75
    volume_cap_bp: float = 2.0
    trend_cap_bp: float = 2.0
    peer_cap_bp: float = 2.0
    total_cap_bp: float = 20.0
    tolerance_bp: float = 1.0


def finite(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def clip(x, bound):
    return max(-bound, min(bound, x))


def weighted_median(values, weights):
    points = sorted((float(v), float(w)) for v, w in zip(values, weights)
                    if finite(v) is not None and w > 0)
    if not points:
        return None
    total, acc = sum(w for _, w in points), 0.0
    for value, weight in points:
        acc += weight
        if acc >= total / 2:
            return value


def volume_imbalance(bid, offer):
    # Numeric zero accompanying '--' is handled upstream as unknown.
    if bid is None or offer is None or bid < 0 or offer < 0 or bid + offer <= 0:
        return None
    # Log size prevents a single enormous quote dominating the model.
    b, o = math.log1p(min(bid, 100000)), math.log1p(min(offer, 100000))
    return (o - b) / (o + b)


def evidence(features, base, p, mode="joint"):
    """Return own signal and quality. Rows already converted to historical spread."""
    quotes = features.get("quotes", []) if mode != "trade" else []
    trades = features.get("trades", []) if mode != "quote" else []
    contributions = {}
    qweights, qvalues, pressures = [], [], []
    quote_days = set()
    mids = []
    for q in quotes:
        age = q["age"]
        b, o = q.get("bid"), q.get("ofr")
        if b is None and o is None:
            continue
        quote_days.add(q["day"])
        w = 2 ** (-age / p.half_life) * q.get("freshness", 1)
        if b is not None and o is not None:
            width = max(0, b - o)
            center = (b + o) / 2
            w *= 1 / (1 + width / 10)
            mids.append((age, q.get("last_mid") if q.get("last_mid") is not None else center))
        elif o is not None:
            center = max(base, o)  # offer above fair yield is a soft lower bound
            w *= 0.65
        else:
            center = min(base, b)  # bid below fair yield is a soft upper bound
            w *= 0.65
        qvalues.append(center)
        qweights.append(w)
        im = q.get("imbalance")
        if im is not None:
            pressures.append((im, w))
    qc = weighted_median(qvalues, qweights)
    qquality = min(1.0, sum(qweights) / 2.0) if qc is not None else 0
    if qc is not None:
        contributions["quote_level"] = p.quote_weight * qquality * clip(qc - base, p.total_cap_bp)
        if pressures:
            contributions["volume"] = p.volume_cap_bp * sum(v*w for v,w in pressures) / sum(w for _,w in pressures) * qquality
        # Offer-only paths also contain useful sentiment; only distinct trading days count.
        trend_points = sorted((q["age"], q.get("ofr") if q.get("ofr") is not None else q.get("bid")) for q in quotes)
        if len(trend_points) >= 2 and trend_points[-1][0] > trend_points[0][0]:
            slope = (trend_points[0][1] - trend_points[-1][1]) / (trend_points[-1][0] - trend_points[0][0])
            contributions["quote_trend"] = clip(slope, p.trend_cap_bp) * qquality
        growth=[]
        for side,sign in (("ofr",1),("bid",-1)):
            sizes=sorted((q["age"],q.get(side+"_volume")) for q in quotes if q.get(side+"_volume") is not None and q.get(side+"_volume")>0)
            if len(sizes)>=2 and sizes[-1][0]>sizes[0][0]:
                growth.append(sign*math.log(sizes[0][1]/sizes[-1][1])/math.log(2))
        if growth:
            contributions["size_trend"]=clip(sum(growth),1)*p.volume_cap_bp*.5*qquality
    tvalues, tweights = [], []
    for t in trades:
        tvalues.append(t["spread"])
        tweights.append(2 ** (-t["age"] / p.half_life) * min(1.0, math.log1p(t["num"]) / math.log(6)))
    tc = weighted_median(tvalues, tweights)
    tquality = min(1.0, sum(tweights) / 2.0) if tc is not None else 0
    if tc is not None:
        contributions["trade_level"] = p.trade_weight * tquality * clip(tc - base, p.total_cap_bp)
        tp = sorted((t["age"], t["spread"]) for t in trades)
        if len(tp) > 1 and tp[-1][0] > tp[0][0]:
            contributions["trade_trend"] = clip((tp[0][1]-tp[-1][1])/(tp[-1][0]-tp[0][0]), p.trend_cap_bp)*tquality
    # Convexly combine independent level estimates; don't double the same market signal.
    if qc is not None and tc is not None:
        denom = p.quote_weight*qquality + p.trade_weight*tquality
        confidence = max(p.quote_weight*qquality, p.trade_weight*tquality)
        contributions["quote_level"] *= confidence / denom
        contributions["trade_level"] *= confidence / denom
        for key in ("quote_trend", "trade_trend"):
            if key in contributions:
                contributions[key] *= 0.5
    return {"signal": sum(contributions.values()), "components": contributions,
            "quality": max(qquality, tquality), "quote_days": len(quote_days),
            "offer_only_days":sum(q.get("ofr") is not None and q.get("bid") is None for q in quotes),
            "trade_days": len(trades), "quote_center": qc, "trade_center": tc,
            "latest_mid": min(mids)[1] if mids else None,
            "latest_trade": min(trades, key=lambda x: x["age"])["spread"] if trades else None,
            "has_evidence": bool(qvalues or tvalues)}


def predict(base, own, peer_signal, p):
    if not own["has_evidence"]:
        return None, {}
    parts = dict(own["components"])
    if peer_signal is not None:
        parts["issuer"] = clip(peer_signal * 0.25, p.peer_cap_bp) * own["quality"]
    raw = sum(parts.values())
    delta = clip(raw, p.total_cap_bp)
    parts["cap_adjustment"] = delta - raw
    return base + delta, parts


def realization(base, target, future, path, tolerance=1.0):
    delta, actual = target - base, future - base
    active = abs(delta) >= tolerance
    direction = 1 if delta > 0 else -1
    return {"predicted_delta_bp": delta, "actual_delta_bp": actual,
            "error_bp": abs(future-target), "baseline_error_bp": abs(actual),
            "convergence_bp": abs(delta)-abs(future-target),
            "direction_correct": float(delta*actual > 0) if active else None,
            "target_reached": float(direction*(future-target) >= -tolerance) if active else None,
            "touched": float(any(direction*(x-target) >= -tolerance for x in path)) if active and path else None,
            "max_adverse_bp": max([0.0]+[-direction*(x-base) for x in path]) if active and path else None}
