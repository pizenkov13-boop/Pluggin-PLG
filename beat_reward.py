"""Learn beat preferences from local 👍/👎 ratings (lightweight sklearn ranker).

Ratings append to ``user_data/beat_ratings.jsonl``. After enough samples the
model retrains and ``beat_quality.evaluate`` blends proxy score with the
learned reward prediction.

Env:
    PLG_USE_BEAT_REWARD=1|0|auto   (auto = on when sklearn is installed)
    PLG_BEAT_REWARD_WEIGHT=0.35    blend weight for reward vs proxy (0–1)
    PLG_BEAT_REWARD_MIN_SAMPLES=6  minimum ratings before model is used
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pattern_features import FEATURE_ORDER, features_vector
from plg_paths import user_data_dir

logger = logging.getLogger("plg.beat_reward")

_MIN_SAMPLES_DEFAULT = 6
_MODEL_FILE = "beat_reward.pkl"
_LOG_FILE = "beat_ratings.jsonl"


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def reward_enabled() -> bool:
    flag = _env_flag("PLG_USE_BEAT_REWARD", "auto")
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return _sklearn_available()
    return _sklearn_available()


def reward_weight() -> float:
    try:
        return max(0.0, min(1.0, float(os.environ.get("PLG_BEAT_REWARD_WEIGHT", "0.35"))))
    except ValueError:
        return 0.35


def min_train_samples() -> int:
    try:
        return max(4, int(os.environ.get("PLG_BEAT_REWARD_MIN_SAMPLES", str(_MIN_SAMPLES_DEFAULT))))
    except ValueError:
        return _MIN_SAMPLES_DEFAULT


def _sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401

        return True
    except ImportError:
        return False


def _data_dir() -> Path:
    path = user_data_dir() / "beat_reward"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ratings_log_path() -> Path:
    return _data_dir() / _LOG_FILE


def model_path() -> Path:
    return _data_dir() / _MODEL_FILE


def load_ratings() -> list[dict[str, Any]]:
    path = ratings_log_path()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("rating") in (1, -1):
            rows.append(row)
    return rows


def _append_rating(row: dict[str, Any]) -> None:
    path = ratings_log_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _save_model(model: Any, meta: dict[str, Any]) -> None:
    try:
        import joblib
    except ImportError:
        from sklearn.externals import joblib  # type: ignore[import-not-found,no-redef]

    payload = {"model": model, "meta": meta, "features": list(FEATURE_ORDER)}
    joblib.dump(payload, model_path())


def _load_model() -> tuple[Any, dict[str, Any]] | None:
    path = model_path()
    if not path.is_file():
        return None
    try:
        import joblib
    except ImportError:
        from sklearn.externals import joblib  # type: ignore[import-not-found,no-redef]
    try:
        payload = joblib.load(path)
    except Exception as exc:
        logger.warning("Could not load beat reward model: %s", exc)
        return None
    if not isinstance(payload, dict) or "model" not in payload:
        return None
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return payload["model"], meta


def train_model() -> dict[str, Any] | None:
    """Retrain logistic ranker from the ratings log. Returns training meta or None."""
    if not _sklearn_available():
        return None

    rows = load_ratings()
    if len(rows) < min_train_samples():
        return None

    from sklearn.linear_model import LogisticRegression
    import numpy as np

    x_rows: list[list[float]] = []
    y_rows: list[int] = []
    for row in rows:
        feats = row.get("features")
        if not isinstance(feats, list) or len(feats) != len(FEATURE_ORDER):
            continue
        x_rows.append([float(v) for v in feats])
        y_rows.append(1 if int(row["rating"]) > 0 else 0)

    if len(x_rows) < min_train_samples():
        return None
    if len(set(y_rows)) < 2:
        logger.info("Beat reward: need both 👍 and 👎 before training")
        return None

    x = np.array(x_rows, dtype=float)
    y = np.array(y_rows, dtype=int)
    model = LogisticRegression(max_iter=800, class_weight="balanced")
    model.fit(x, y)
    acc = float(model.score(x, y))
    meta = {
        "samples": len(x_rows),
        "accuracy_in_sample": round(acc, 3),
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_model(model, meta)
    logger.info("Beat reward model trained on %s samples (acc=%.2f)", len(x_rows), acc)
    return meta


# ---------------------------------------------------------------------------
# Fleet reward — every user's 👍/👎 teaches every client. The cloud aggregates
# ratings into per-genre feature weights (beat_ranker); clients cache them and
# use them to guide best-of-N selection from DAY ONE, before the local model
# has enough personal ratings. Personal model always wins once trained.
# ---------------------------------------------------------------------------

_FLEET_FILE = "fleet_reward.json"
_FLEET_TTL_S = 24 * 3600
_FLEET_NEG_TTL_S = 2 * 3600  # offline / no cloud → retry sooner, never hammer


def fleet_reward_enabled() -> bool:
    return _env_flag("PLG_FLEET_REWARD", "auto") not in ("0", "false", "no", "off")


def _fleet_cache_path() -> Path:
    return _data_dir() / _FLEET_FILE


def _load_fleet_cache() -> dict[str, Any]:
    path = _fleet_cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_fleet_cache(cache: dict[str, Any]) -> None:
    try:
        _fleet_cache_path().write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def fleet_snapshot(genre: str | None = None) -> dict[str, Any] | None:
    """Cached global reward snapshot for a genre (or global scope)."""
    if not fleet_reward_enabled():
        return None
    key = genre or "_global"
    cache = _load_fleet_cache()
    entry = cache.get(key)
    now = datetime.now(timezone.utc).timestamp()
    if isinstance(entry, dict):
        age = now - float(entry.get("fetched_at") or 0)
        snap = entry.get("snapshot")
        if snap is not None and age < _FLEET_TTL_S:
            return snap if isinstance(snap, dict) else None
        if snap is None and age < _FLEET_NEG_TTL_S:
            return None

    snapshot: dict[str, Any] | None = None
    try:
        import plg_cloud

        raw = plg_cloud.cloud_reward_status(genre)
        if isinstance(raw, dict) and raw.get("ok"):
            snapshot = raw
    except Exception as exc:  # noqa: BLE001 — offline must never break scoring
        logger.debug("Fleet reward fetch failed (%s)", exc)

    cache[key] = {"fetched_at": now, "snapshot": snapshot}
    _save_fleet_cache(cache)
    return snapshot


def global_feature_weights(genre: str | None) -> dict[str, float] | None:
    for scope in (genre, None):
        snap = fleet_snapshot(scope)
        if not isinstance(snap, dict) or not snap.get("model_ready"):
            continue
        weights = snap.get("feature_weights")
        if isinstance(weights, dict):
            numeric = {}
            for name, value in weights.items():
                try:
                    numeric[str(name)] = float(value)
                except (TypeError, ValueError):
                    continue
            if numeric:
                return numeric
        if scope == genre and genre is not None:
            continue
    return None


def global_progression_stats(genre: str | None) -> dict[str, dict[str, Any]]:
    """Per-progression win stats from the fleet ({name: {up, down, votes, win_rate}})."""
    for scope in (genre, None):
        snap = fleet_snapshot(scope)
        stats = (snap or {}).get("progression_stats")
        if isinstance(stats, dict) and stats:
            return {str(k): v for k, v in stats.items() if isinstance(v, dict)}
    return {}


def _fleet_score(pattern: dict[str, Any]) -> float | None:
    """Cosine of the beat's features against the fleet's liked-direction, 0–100."""
    genre = str(
        (pattern.get("plg_quality") or {}).get("genre")
        or (pattern.get("plg_variety") or {}).get("genre")
        or ""
    ) or None
    weights = global_feature_weights(genre)
    if not weights:
        return None
    vec = features_vector(pattern)
    w = [weights.get(name, 0.0) for name in FEATURE_ORDER]
    dot = sum(a * b for a, b in zip(vec, w))
    norm_v = sum(a * a for a in vec) ** 0.5
    norm_w = sum(b * b for b in w) ** 0.5
    if norm_v <= 0 or norm_w <= 0:
        return None
    return round(max(0.0, min(100.0, 50.0 + 50.0 * dot / (norm_v * norm_w))), 2)


def predict_reward_score(pattern: dict[str, Any]) -> float | None:
    """0–100 reward score: personal model when trained, else the fleet prior."""
    if reward_enabled():
        loaded = _load_model()
        if loaded is not None:
            model, meta = loaded
            if int(meta.get("samples", 0)) >= min_train_samples():
                import numpy as np

                vec = np.array([features_vector(pattern)], dtype=float)
                try:
                    if hasattr(model, "predict_proba"):
                        prob = float(model.predict_proba(vec)[0][1])
                    else:
                        prob = float(model.predict(vec)[0])
                    return round(max(0.0, min(100.0, prob * 100.0)), 2)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Beat reward predict failed: %s", exc)

    return _fleet_score(pattern) if fleet_reward_enabled() else None


def record_rating(pattern: dict[str, Any], rating: int) -> dict[str, Any]:
    """Persist one rating and retrain when enough data is available."""
    rating = 1 if int(rating) > 0 else -1
    beat_id = str(pattern.get("plg_beat_id") or "")
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "beat_id": beat_id,
        "rating": rating,
        "bpm": pattern.get("bpm"),
        "style": pattern.get("style"),
        "genre": (pattern.get("plg_quality") or {}).get("genre"),
        "proxy_score": (pattern.get("plg_quality") or {}).get("score"),
        "prompt": str(pattern.get("user_prompt") or "")[:240],
        "sample_picks": dict(pattern.get("plg_sample_picks") or {}),
        "features": features_vector(pattern),
    }
    _append_rating(row)
    total = len(load_ratings())
    train_meta = train_model()
    return {
        "rating": rating,
        "total_ratings": total,
        "model_trained": train_meta is not None,
        "model_samples": (train_meta or {}).get("samples"),
    }


def taste_profile() -> dict[str, Any]:
    """Aggregate local 👍/👎 history for UI hints."""
    rows = load_ratings()
    thumbs_up = sum(1 for row in rows if int(row.get("rating", 0)) > 0)
    thumbs_down = sum(1 for row in rows if int(row.get("rating", 0)) < 0)
    genre_scores: dict[str, int] = {}
    for row in rows:
        if int(row.get("rating", 0)) <= 0:
            continue
        genre = str(row.get("genre") or "").strip()
        if not genre:
            style = str(row.get("style") or "")
            if style:
                genre = style.split()[0].lower()
        if genre:
            genre_scores[genre] = genre_scores.get(genre, 0) + 1

    favorite_genre = None
    if genre_scores:
        favorite_genre = max(genre_scores, key=genre_scores.get)  # type: ignore[arg-type]

    minimum = min_train_samples()
    total = len(rows)
    return {
        "total": total,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "favorite_genre": favorite_genre,
        "favorite_genres": [
            {"genre": name, "count": count}
            for name, count in sorted(genre_scores.items(), key=lambda item: -item[1])[:3]
        ],
        "min_samples": minimum,
        "progress": min(1.0, total / minimum) if minimum else 0.0,
        "needs_both": thumbs_up > 0 and thumbs_down > 0,
    }


def model_status() -> dict[str, Any]:
    loaded = _load_model()
    meta = loaded[1] if loaded else {}
    taste = taste_profile()
    return {
        "enabled": reward_enabled(),
        "ratings": taste["total"],
        "thumbs_up": taste["thumbs_up"],
        "thumbs_down": taste["thumbs_down"],
        "min_samples": taste["min_samples"],
        "progress": taste["progress"],
        "favorite_genre": taste["favorite_genre"],
        "favorite_genres": taste["favorite_genres"],
        "model_ready": loaded is not None and int(meta.get("samples", 0)) >= min_train_samples(),
        "model_samples": meta.get("samples"),
        "trained_at": meta.get("trained_at"),
        "needs_both_polarities": not taste["needs_both"] and taste["total"] >= 2,
    }
