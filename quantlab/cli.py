"""命令行入口：fetch / run / selftest。

    python -m quantlab fetch
    python -m quantlab selftest
    python -m quantlab run --quick
    python -m quantlab run --config config/default.json --out output/
"""
from __future__ import annotations

import argparse
import runpy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


def cmd_fetch(args) -> int:
    script = ROOT / "scripts" / "fetch_tencent.py"
    sys.argv = [str(script), "--out", args.out, "--start", args.start, "--end", args.end]
    runpy.run_path(str(script), run_name="__main__")
    return 0


def cmd_run(args) -> int:
    from .config import Config
    from .data import MarketData, load_prices
    from .report import build_full_report, write_outputs

    cfg = Config.load(args.config)
    data_path = _resolve(args.data or cfg.get("data.prices_csv"))
    if not data_path.exists():
        print(f"缺少数据文件：{data_path}\n先执行：python -m quantlab fetch", file=sys.stderr)
        return 2
    md = MarketData(load_prices(data_path), cfg)
    print(f"[data] {md.summary()}", flush=True)
    rep = build_full_report(md, cfg, quick=args.quick)
    paths = write_outputs(rep, _resolve(args.out or cfg.get("output.dir")))
    p = rep["performance"]
    print(f"[perf] 总收益={p['total_return']} 年化={p['annualized_return']} "
          f"夏普={p['sharpe']} 最大回撤={p['max_drawdown']} 成本=¥{p['cost_total_cny']}", flush=True)
    g = rep["quality_gate"]
    print(f"[gate] 最终判定={g['final_verdict']} "
          f"(工程={g['engineering_gate']} / 策略结论={g['strategy_conclusion_gate']})", flush=True)
    for k, v in paths.items():
        print(f"[out] {k}: {v}")
    return 0


def cmd_selftest(args) -> int:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if res.wasSuccessful() else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quantlab", description="A股中低频多因子＋趋势策略研究框架")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="抓取真实 A 股前复权日线")
    f.add_argument("--out", default="data/sample_real/prices.csv")
    f.add_argument("--start", default="2018-01-01")
    f.add_argument("--end", default="2026-11-13")
    f.set_defaults(func=cmd_fetch)

    r = sub.add_parser("run", help="跑完整研究与回测")
    r.add_argument("--config", default="config/default.json")
    r.add_argument("--data", default=None)
    r.add_argument("--out", default=None)
    r.add_argument("--quick", action="store_true", help="跳过敏感性/蒙特卡洛/成本敏感性（联调用）")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("selftest", help="运行单元测试")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
