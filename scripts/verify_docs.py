#!/usr/bin/env python3
"""交付前一致性校验薄封装（也可直接用 python -m quantlab verify）。

    python scripts/verify_docs.py --render
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantlab.verify import verify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true", help="先重新渲染 docs/metrics.md")
    args = ap.parse_args()
    out = verify(render=args.render)
    print(json.dumps({k: out[k] for k in ("ok", "fingerprint", "metric_paths",
                                          "issues", "warnings")}, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
