#!/usr/bin/env python3
"""Write the build_flavor.json of a tester build: ``make_build_flavor.py DAYS OUT_FILE``.

``DAYS`` counts from today; 0 is valid for today only, a negative number makes an already expired build (for
checking the expiry message). ``build_plg.bat tester`` calls this and hands the file to PLG.spec, which puts it
into the tester exe alone (``app_config.build_flavor`` reads it)."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path


def flavor(days: int, today: date | None = None) -> dict[str, str]:
    today = today or date.today()
    return {"flavor": "tester", "built": today.isoformat(), "expires": (today + timedelta(days=days)).isoformat()}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        days = int(argv[1])
    except ValueError:
        print(f"DAYS must be a whole number, got {argv[1]!r}")
        return 2
    out = Path(argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    data = flavor(days)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"tester build: valid until {data['expires']} ({days} days from {data['built']}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
