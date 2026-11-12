#!/usr/bin/env python3
"""Run PLG Cloud API locally: python cloud/run.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / "cloud" / ".env")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("cloud.app.main:app", host="0.0.0.0", port=8787, reload=True)
