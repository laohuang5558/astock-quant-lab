#!/usr/bin/env python3
"""抓取真实 A 股前复权日线，生成标准数据合约 CSV。

数据源：腾讯证券公开行情接口 web.ifzq.gtimg.cn （非交易所一手数据，L3）
返回字段顺序：[date, open, close, high, low, volume(手), ...]
成交额用 volume x 100 x (H+L+C)/3 近似，非交易所真实成交额，口径见 docs/DATA_CONTRACT.md。

仅用于研究与回测，不含任何实盘下单能力。
用法：
    python scripts/fetch_tencent.py --out data/sample_real/prices.csv
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://gu.qq.com/"}

# 演示样本：20 只真实 A 股，跨行业，全部为当前非 ST 的大中盘标的
DEFAULT_CODES = [
    "600519", "601318", "600036", "000001", "000858",
    "002594", "300750", "600030", "601899", "600276",
    "000333", "002415", "600887", "601012", "300059",
    "002230", "600309", "601166", "000651", "688981",
]


def prefix(code: str) -> str:
    return "sh" if code.startswith(("6", "9")) else "sz"


def fetch_one(code: str, start: str, end: str, retries: int = 3) -> list[dict]:
    """按 2 年一段分页，避免单次返回的条数上限截断历史。"""
    out: dict[str, dict] = {}
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1, 2):
        seg_start = max(start, f"{y}-01-01")
        seg_end = min(end, f"{y + 1}-12-31")
        param = f"{prefix(code)}{code},day,{seg_start},{seg_end},800,qfq"
        url = ENDPOINT + "?" + urllib.parse.urlencode({"param": param})
        payload = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    payload = json.load(resp)
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(1.0 + attempt)
        key = f"{prefix(code)}{code}"
        node = (payload.get("data") or {}).get(key) or {}
        bars = node.get("qfqday") or node.get("day") or []
        for b in bars:
            if len(b) < 6:
                continue
            try:
                d, o, c, h, l, v = b[0], float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])
            except (TypeError, ValueError):
                continue
            typical = (h + l + c) / 3.0
            out[str(d)] = {
                "date": str(d), "code": code, "open": o, "high": h, "low": l,
                "close": c, "volume": v, "amount": round(v * 100.0 * typical, 2),
                "is_st": 0, "industry": "UNKNOWN",
            }
    return [out[k] for k in sorted(out)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sample_real/prices.csv")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2026-11-13")
    ap.add_argument("--codes", nargs="*", default=DEFAULT_CODES)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, code in enumerate(args.codes, 1):
        try:
            got = fetch_one(code, args.start, args.end)
            rows.extend(got)
            print(f"[{i:>2}/{len(args.codes)}] {code}: {len(got)} bars", flush=True)
        except Exception as exc:
            print(f"[{i:>2}/{len(args.codes)}] {code}: FAILED {type(exc).__name__}: {exc}", flush=True)
        time.sleep(0.15)

    if not rows:
        raise SystemExit("未取到任何真实行情，已中止（禁止用合成数据冒充真实行情）")

    import csv
    fields = ["date", "code", "open", "high", "low", "close", "volume", "amount", "is_st", "industry"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE {out_path} rows={len(rows)} codes={len(set(r['code'] for r in rows))}")
    print("NOTE: amount 为近似值；industry/ST 历史为数据缺口，见 docs/DATA_CONTRACT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
