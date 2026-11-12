"""Localized strings for READ_ME_IMBA.txt."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from plg_paths import bundle_dir

_I18N_FILE = bundle_dir() / "assets" / "mix_blueprint_i18n.json"


@lru_cache(maxsize=1)
def _packs() -> dict[str, dict[str, str]]:
    if not _I18N_FILE.is_file():
        return {}
    raw = json.loads(_I18N_FILE.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def blueprint_text(locale: str | None, msg_key: str, **params: Any) -> str:
    # NB: the message-id param is ``msg_key`` (not ``key``) so a template can take
    # a ``key=`` param — e.g. step_key_lock formats the musical key {key}.
    loc = (locale or "en").lower()
    pack = _packs().get(loc) or _packs().get("en") or {}
    template = pack.get(msg_key) or (_packs().get("en") or {}).get(msg_key, msg_key)
    try:
        return str(template).format(**params)
    except (KeyError, ValueError):
        return str(template)
