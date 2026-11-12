"""CLAP text↔audio scoring for sample selection (optional, heavy).

Lazy-loads laion-clap on first use. Disabled when the package is missing,
``PLG_USE_CLAP=0``, or inference fails. Embeddings are cached per file
(path + mtime + size) so large libraries are not re-encoded every beat.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np

from plg_paths import user_data_dir

logger = logging.getLogger("plg.clap")

CACHE_FILE = user_data_dir() / "clap_audio_embeddings.json"

# Role suffix so CLAP knows which instrument we are matching.
TRACK_CLAP_ROLE: dict[str, str] = {
    "kick": "kick drum one shot",
    "snare": "snare drum one shot",
    "snare_layer": "snare rimshot one shot",
    "clap": "hand clap one shot",
    "sub_808": "808 bass sub one shot",
    "hi_hats": "hi hat cymbal one shot",
    "melody_lead": "melody synth loop one shot",
}

_MODEL_LOCK = threading.Lock()
_model: Any = None
_import_failed = False
_text_cache: dict[str, np.ndarray] = {}

# Cosine similarity ~0.05–0.45 for reasonable matches; scale to int bonus.
CLAP_BONUS_SCALE = 55


def _env_flag(name: str, default: str = "auto") -> str:
    return os.environ.get(name, default).strip().lower()


def clap_available() -> bool:
    """True if laion-clap imports and PLG_USE_CLAP is not explicitly off."""
    flag = _env_flag("PLG_USE_CLAP", "auto")
    if flag in ("0", "false", "no", "off"):
        return False
    global _import_failed
    if _import_failed:
        return False
    try:
        import laion_clap  # noqa: F401
    except ImportError:
        _import_failed = True
        return False
    return True


def use_clap() -> bool:
    return clap_available()


def build_clap_query(prompt: str = "", style: str = "", track: str = "") -> str:
    """Natural-language query for CLAP text encoder."""
    role = TRACK_CLAP_ROLE.get(track, f"{track.replace('_', ' ')} one shot")
    bits = [b.strip() for b in (prompt, style) if b and b.strip()]
    if bits:
        return f"{' '.join(bits)} {role}"
    return role


def _amodel() -> str:
    return "HTSAT-base" if _env_flag("PLG_CLAP_MODEL", "base") != "tiny" else "HTSAT-tiny"


def _resolve_device() -> str:
    pref = _env_flag("PLG_CLAP_DEVICE", "auto")
    if pref == "cuda":
        return "cuda:0"
    if pref == "cpu":
        return "cpu"
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _get_model() -> Any:
    global _model, _import_failed
    if _model is not None:
        return _model
    if not clap_available():
        raise RuntimeError("CLAP not available")
    with _MODEL_LOCK:
        if _model is not None:
            return _model
        import laion_clap

        device = _resolve_device()
        logger.info("Loading CLAP model (%s) on %s — first run may download weights", _amodel(), device)
        instance = laion_clap.CLAP_Module(
            enable_fusion=False,
            device=device,
            amodel=_amodel(),
            tmodel="roberta",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            instance.load_ckpt(verbose=False)
        _model = instance
        logger.info("CLAP ready")
        return _model


def _load_disk_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_disk_cache(data: dict[str, Any]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _cache_key(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return f"{path.resolve()}:{int(st.st_mtime)}:{st.st_size}"


def _cosine_bonus(audio_emb: np.ndarray, text_emb: np.ndarray) -> int:
    a = np.asarray(audio_emb, dtype=np.float64).ravel()
    t = np.asarray(text_emb, dtype=np.float64).ravel()
    na = float(np.linalg.norm(a))
    nt = float(np.linalg.norm(t))
    if na < 1e-9 or nt < 1e-9:
        return 0
    sim = float(np.dot(a, t) / (na * nt))
    return int(round(max(0.0, sim) * CLAP_BONUS_SCALE))


def get_text_embedding(prompt: str = "", style: str = "", track: str = "") -> np.ndarray | None:
    if not use_clap():
        return None
    query = build_clap_query(prompt, style, track)
    if query in _text_cache:
        return _text_cache[query]
    try:
        model = _get_model()
        emb = model.get_text_embedding([query], use_tensor=False)[0]
        _text_cache[query] = emb
        return emb
    except Exception as exc:
        logger.warning("CLAP text embedding failed: %s", exc)
        return None


def score_paths(
    paths: list[Path],
    text_emb: np.ndarray,
) -> dict[str, int]:
    """Return path-key → CLAP bonus for each readable audio file."""
    if not paths or text_emb is None:
        return {}
    try:
        model = _get_model()
    except Exception as exc:
        logger.warning("CLAP model load failed: %s", exc)
        return {}

    disk = _load_disk_cache()
    dirty = False
    bonuses: dict[str, int] = {}
    to_infer: list[Path] = []
    key_by_path: dict[str, str] = {}

    for path in paths:
        key = _cache_key(path)
        if key is None:
            continue
        resolved = str(path.resolve())
        key_by_path[resolved] = key
        cached = disk.get(key)
        if cached is not None:
            bonuses[resolved] = _cosine_bonus(np.asarray(cached), text_emb)
        else:
            to_infer.append(path)

    if to_infer:
        readable = [p for p in to_infer if p.is_file() and p.stat().st_size > 44]
        if readable:
            try:
                embeds = model.get_audio_embedding_from_filelist(
                    [str(p) for p in readable],
                    use_tensor=False,
                )
                for path, emb in zip(readable, embeds):
                    resolved = str(path.resolve())
                    ck = key_by_path.get(resolved)
                    if ck:
                        disk[ck] = emb.tolist()
                        dirty = True
                    bonuses[resolved] = _cosine_bonus(emb, text_emb)
            except Exception as exc:
                logger.warning("CLAP audio batch failed, trying one-by-one: %s", exc)
                for path in readable:
                    try:
                        emb = model.get_audio_embedding_from_filelist(
                            [str(path)],
                            use_tensor=False,
                        )[0]
                        resolved = str(path.resolve())
                        ck = key_by_path.get(resolved)
                        if ck:
                            disk[ck] = emb.tolist()
                            dirty = True
                        bonuses[resolved] = _cosine_bonus(emb, text_emb)
                    except Exception as inner:
                        logger.debug("CLAP skip %s: %s", path.name, inner)

    if dirty:
        _save_disk_cache(disk)
    return bonuses


def clap_bonus_for_path(
    path: Path,
    *,
    prompt: str = "",
    style: str = "",
    track: str = "",
    text_emb: np.ndarray | None = None,
) -> int:
    if text_emb is None:
        text_emb = get_text_embedding(prompt, style, track)
    scores = score_paths([path], text_emb)
    return scores.get(str(path.resolve()), 0)
