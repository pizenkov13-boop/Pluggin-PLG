"""Global reward/ranker snapshots from cloud beat ratings.

This is intentionally simple for v1: no heavyweight training service yet. We
aggregate liked-vs-disliked feature means into directional weights that can be
used for dashboards and, later, prompt/ranker priors.
"""

from __future__ import annotations

from typing import Any

from supabase import Client

_MIN_GLOBAL_SAMPLES = 20


def _numeric_features(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _mean(rows: list[dict[str, float]], key: str) -> float:
    vals = [row[key] for row in rows if key in row]
    return sum(vals) / len(vals) if vals else 0.0


def build_global_reward_snapshot(
    client: Client,
    *,
    genre: str | None = None,
    min_samples: int = _MIN_GLOBAL_SAMPLES,
) -> dict[str, Any]:
    query = (
        client.table("beat_ratings")
        .select("rating,genre,features")
        .limit(5000)
    )
    if genre:
        query = query.eq("genre", genre)
    resp = query.execute()
    rows = resp.data or []

    positives = [_numeric_features(row.get("features")) for row in rows if int(row.get("rating") or 0) > 0]
    negatives = [_numeric_features(row.get("features")) for row in rows if int(row.get("rating") or 0) < 0]
    keys = sorted({key for row in positives + negatives for key in row})

    # Which style-DNA progressions win likes (clients bias the corpus pick).
    prog_counts: dict[str, list[int]] = {}
    for row in rows:
        feats = row.get("features")
        name = feats.get("dna_progression") if isinstance(feats, dict) else None
        rating = int(row.get("rating") or 0)
        if not name or not rating:
            continue
        bucket = prog_counts.setdefault(str(name), [0, 0])
        bucket[0 if rating > 0 else 1] += 1
    progression_stats = {
        name: {
            "up": up,
            "down": down,
            "votes": up + down,
            "win_rate": round(up / (up + down), 4),
        }
        for name, (up, down) in prog_counts.items()
        if up + down > 0
    }

    weights: dict[str, float] = {}
    for key in keys:
        weights[key] = round(_mean(positives, key) - _mean(negatives, key), 5)

    top_positive = [
        {"feature": key, "weight": weight}
        for key, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True)
        if weight > 0
    ][:12]
    top_negative = [
        {"feature": key, "weight": weight}
        for key, weight in sorted(weights.items(), key=lambda item: item[1])
        if weight < 0
    ][:12]

    samples = len(rows)
    thumbs_up = len(positives)
    thumbs_down = len(negatives)
    model_ready = samples >= min_samples and thumbs_up > 0 and thumbs_down > 0
    snapshot = {
        "scope": "genre" if genre else "global",
        "genre": genre,
        "samples": samples,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "model_ready": model_ready,
        "feature_weights": weights,
        "top_positive_features": top_positive,
        "top_negative_features": top_negative,
        "progression_stats": progression_stats,
        "min_samples": min_samples,
    }

    # Keep a historical trail for offline analysis. Failure should not block the
    # API response because the live aggregate is already computed above.
    try:
        client.table("beat_ranker_snapshots").insert(
            {
                "scope": snapshot["scope"],
                "genre": genre,
                "samples": samples,
                "thumbs_up": thumbs_up,
                "thumbs_down": thumbs_down,
                "model_ready": model_ready,
                "feature_weights": weights,
                "top_positive_features": top_positive,
            }
        ).execute()
    except Exception:
        pass
    return {"ok": True, **snapshot}
