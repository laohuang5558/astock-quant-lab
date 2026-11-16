#!/usr/bin/env python3
"""提取 HTML 中的内联 <script> 并交给 node --check 做语法校验。

    python scripts/check_inline_js.py ../astock-quant-design-report.html ../astock-quant-audit-report.html
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

BLOCK = re.compile(r"<script(?![^>]*src=)[^>]*>([\s\S]*?)</script>", re.I)


def main() -> int:
    files = sys.argv[1:]
    if not files:
        print("usage: check_inline_js.py <report.html> [...]", file=sys.stderr)
        return 2
    rc = 0
    for rel in files:
        p = Path(rel)
        if not p.exists():
            print(f"MISSING {rel}")
            rc = 1
            continue
        blocks = BLOCK.findall(p.read_text(encoding="utf-8"))
        js = "\n".join(blocks)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(js)
            tmp = f.name
        try:
            out = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
            if out.returncode == 0:
                print(f"OK {rel} (inline_js_blocks={len(blocks)}, chars={len(js)})")
            else:
                print(f"FAIL {rel}: {out.stderr.strip()[:500]}")
                rc = 1
        except FileNotFoundError:
            print("WARN node 不可用，跳过 JS 语法校验（CI 环境应安装 node）")
        finally:
            Path(tmp).unlink(missing_ok=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
