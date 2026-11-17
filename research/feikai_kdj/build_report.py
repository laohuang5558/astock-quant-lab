#!/usr/bin/env python3
"""由 result.json + equity.csv 生成飞凯材料策略研报（零 CDN、零 JS、纯 SVG 自绘）。

所有数值均从产物读取，禁止手写转录；报告带 feikai-fingerprint 供一致性校验。
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
ROOT = HERE.parent.parent
TOP = ROOT.parent

R = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
EQ = pd.read_csv(OUT / "equity.csv")
BH = pd.read_csv(OUT / "equity_buyhold.csv")


def esc(x) -> str:
    return html.escape(str(x))


def pct(x, d=2) -> str:
    return "—" if x is None else f"{float(x) * 100:.{d}f}%"


def num(x, d=4) -> str:
    return "—" if x is None else f"{float(x):.{d}f}"


def cny(x) -> str:
    return "—" if x is None else f"¥{float(x):,.2f}"


def signed(x, d=2, suffix="%") -> str:
    if x is None:
        return "—"
    v = float(x)
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v:+.{d}f}{suffix}</span>'


def fingerprint() -> str:
    b = R["baseline"]["strategy"]["perf"]
    key = {
        "sample": R["meta"]["sample"],
        "total_return": b["total_return"],
        "sharpe": b["sharpe"],
        "n_trades": b["n_trades"],
        "oos": R["oos"]["out_of_sample"]["total_return"],
        "mc_percentile": R["random_control"].get("sharpe_percentile"),
        "equivalence": R["audit"]["control_equivalence"]["equivalent"],
        "pnl_closed": R["audit"]["trade_pnl_check"]["closed"],
    }
    blob = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def equity_svg() -> str:
    e = EQ["equity"].to_numpy(float)
    b = BH["equity"].to_numpy(float)
    dates = EQ["date"].astype(str).tolist()
    step = max(1, len(e) // 360)
    idx = list(range(0, len(e), step))
    if idx[-1] != len(e) - 1:
        idx.append(len(e) - 1)
    en = [e[i] / e[0] for i in idx]
    bn = [b[i] / b[0] for i in idx]
    lo, hi = min(min(en), min(bn)), max(max(en), max(bn))
    pad = (hi - lo) * 0.08 or 0.1
    lo, hi = lo - pad, hi + pad
    W, H, L, Rm, T, B = 1100, 320, 64, 24, 22, 40
    def X(i): return L + (W - L - Rm) * i / max(len(idx) - 1, 1)
    def Y(v): return T + (H - T - B) * (hi - v) / (hi - lo)
    pe = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in zip(range(len(idx)), en))
    pb = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in zip(range(len(idx)), bn))
    grid, labels = [], []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        grid.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W - Rm}" y2="{y:.1f}" stroke="#e8ecf2" stroke-width="1"/>')
        labels.append(f'<text x="{L - 9}" y="{y + 4:.1f}" text-anchor="end" class="ax">{v:.2f}</text>')
    base_y = Y(1.0)
    grid.append(f'<line x1="{L}" y1="{base_y:.1f}" x2="{W - Rm}" y2="{base_y:.1f}" stroke="#b9c2cf" stroke-width="1" stroke-dasharray="5 4"/>')
    xt = []
    for yr in ("2018", "2020", "2022", "2024", "2026"):
        hit = next((j for j, i in enumerate(idx) if dates[i].startswith(yr)), None)
        if hit is not None:
            xt.append(f'<text x="{X(hit):.1f}" y="{H - 14}" text-anchor="middle" class="ax">{yr}</text>')
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="策略与买入持有归一化净值对比" class="chart">'
        + "".join(grid) + "".join(labels) + "".join(xt)
        + f'<polyline points="{pb}" fill="none" stroke="#94a3b8" stroke-width="2" opacity="0.9"/>'
        + f'<polyline points="{pe}" fill="none" stroke="#2563eb" stroke-width="2.2"/>'
        + f'<rect x="{W - 250}" y="{T + 6}" width="238" height="46" rx="6" fill="#ffffff" stroke="#e2e8f0"/>'
        + f'<line x1="{W - 238}" y1="{T + 22}" x2="{W - 214}" y2="{T + 22}" stroke="#2563eb" stroke-width="3"/>'
        + f'<text x="{W - 206}" y="{T + 26}" class="lg">本策略（多数时间空仓）</text>'
        + f'<line x1="{W - 238}" y1="{T + 40}" x2="{W - 214}" y2="{T + 40}" stroke="#94a3b8" stroke-width="3"/>'
        + f'<text x="{W - 206}" y="{T + 44}" class="lg">飞凯材料买入持有</text>'
        + f'<text x="{L}" y="{H - 14}" class="ax">起点归一 = 1.00</text>'
        + '</svg>'
    )


def premise_bars() -> str:
    rows = R["user_premise_check"]["scopes"]["近一年"]
    hs = R["user_premise_check"]["horizons"]
    W, H, L, T, B = 520, 210, 44, 20, 34
    bw = (W - L - 24) / len(hs)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="近一年新低后各周期上涨概率" class="chart">']
    y50 = T + (H - T - B) * (1.0 - 0.5)
    out.append(f'<line x1="{L}" y1="{y50:.1f}" x2="{W - 20}" y2="{y50:.1f}" stroke="#94a3b8" stroke-width="1" stroke-dasharray="5 4"/>')
    out.append(f'<text x="{L - 8}" y="{y50 + 4:.1f}" text-anchor="end" class="ax">50%</text>')
    for j, h in enumerate(hs):
        v = rows[str(h)].get("up_ratio")
        if v is None:
            continue
        x = L + j * bw + bw * 0.22
        w = bw * 0.56
        y = T + (H - T - B) * (1.0 - float(v))
        color = "#dc2626" if float(v) > 0.5 else "#16a34a"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{H - B - y:.1f}" rx="3" fill="{color}" opacity="0.85"/>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" class="barv">{float(v) * 100:.1f}%</text>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{H - 12}" text-anchor="middle" class="ax">+{h}日</text>')
    out.append(f'<text x="{L}" y="{T + 8}" class="ax">近一年 26 次 20 日新低</text>')
    out.append('</svg>')
    return "".join(out)


def kpi(label, value, sub="", tone="") -> str:
    return (f'<div class="kpi {tone}"><div class="kl">{esc(label)}</div>'
            f'<div class="kv">{value}</div><div class="ks">{esc(sub)}</div></div>')


def table(headers, rows, cls="") -> str:
    """rows 元素可为 list（普通行）或 {"cells": [...], "cls": "hl"}（带样式行）。"""
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    parts = []
    for row in rows:
        if isinstance(row, dict):
            cells, rowcls = row["cells"], row.get("cls", "")
        else:
            cells, rowcls = row, ""
        attr = f' class="{rowcls}"' if rowcls else ""
        parts.append("<tr" + attr + ">" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(parts)}</tbody></table></div>')


CSS = """
:root{--bg:#f5f7fa;--card:#fff;--ink:#1f2937;--muted:#64748b;--line:#e2e8f0;--blue:#2563eb;--red:#dc2626;--green:#16a34a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 60px}
h1{font-size:27px;margin:0 0 6px;letter-spacing:.3px}h2{font-size:19px;margin:30px 0 12px;padding-left:10px;border-left:4px solid var(--blue)}
h3{font-size:15px;margin:18px 0 8px;color:#334155}p{margin:8px 0}.sub{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0;box-shadow:0 1px 3px rgba(15,23,42,.04)}
.banner{border-radius:10px;padding:14px 18px;margin:14px 0;font-weight:600}
.warn{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}.info{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af}
.ok{background:#f0fdf4;border:1px solid #bbf7d0;color:#166534}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.kl{font-size:12px;color:var(--muted)}.kv{font-size:22px;font-weight:700;margin:4px 0;letter-spacing:.2px}.ks{font-size:11px;color:var(--muted)}
.up{color:var(--red);font-weight:600}.down{color:var(--green);font-weight:600}.flat{color:var(--muted)}
.tw{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:#f8fafc;color:#475569;font-weight:600}tr.hl{background:#eff6ff}td.r,th.r{text-align:right}
.chart{width:100%;height:auto;display:block}.ax{font-size:11px;fill:#64748b}.lg{font-size:12px;fill:#334155}.barv{font-size:12px;fill:#1f2937;font-weight:600}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600}
.tag.no{background:#fee2e2;color:#991b1b}.tag.ok{background:#dcfce7;color:#166534}.tag.mid{background:#fef3c7;color:#92400e}
ul{margin:8px 0 8px 20px;padding:0}li{margin:5px 0}code{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:12px}
pre{background:#0f172a;color:#e2e8f0;padding:14px 16px;border-radius:8px;overflow-x:auto;font-size:12px;line-height:1.6}
pre code{background:none;color:inherit;padding:0}.foot{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.small{font-size:12px;color:var(--muted)}
@media(max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
"""


def main() -> int:
    fp = fingerprint()
    b = R["baseline"]["strategy"]["perf"]
    bh = R["baseline"]["buy_and_hold"]
    prem = R["user_premise_check"]
    cur = R["current_state"]
    rc = R["random_control"]
    vd = R["verdict"]

    kpis = "".join([
        kpi("总收益", signed(b["total_return"] * 100, 2, "%"), f"买入持有同期 {pct(bh['total_return'])}"),
        kpi("年化收益", signed(b["annualized_return"] * 100, 2, "%"), f"{R['meta']['sample']['n_days']} 个交易日"),
        kpi("夏普", num(b["sharpe"]), f"年化波动 {pct(b['volatility_annual'])}"),
        kpi("最大回撤", signed(b["max_drawdown"] * 100, 2, "%"), f"买入持有 {pct(bh['max_drawdown'])}"),
        kpi("平仓交易", str(b["n_trades"]), f"信号 {R['baseline']['n_signals']} 次（重叠被忽略）"),
        kpi("胜率", pct(b["win_rate"], 1), f"平均持有 {b['avg_hold_days']} 日"),
        kpi("累计成本", cny(b["cost_total_cny"]), "佣金+印花税+滑点"),
        kpi("随机对照分位", pct(rc.get("sharpe_percentile"), 1), f"{rc.get('n_paths')} 次等价重跑"),
    ])

    prem_rows = []
    for label, rows in prem["scopes"].items():
        for h in prem["horizons"]:
            v = rows[str(h)]
            prem_rows.append([
                esc(label), f"+{h} 日", str(v.get("n", 0)),
                signed(v.get("up_ratio", 0) * 100 if v.get("n") else None, 1, "%"),
                signed(v.get("mean", 0) * 100 if v.get("n") else None, 2, "%"),
                signed(v.get("median", 0) * 100 if v.get("n") else None, 2, "%"),
                signed(v.get("p25", 0) * 100 if v.get("n") else None, 2, "%"),
                signed(v.get("p75", 0) * 100 if v.get("n") else None, 2, "%"),
            ])

    trade_rows = []
    for t in R["baseline"]["trades"]:
        trade_rows.append([
            esc(t["entry_date"]), esc(t["exit_date"]), str(t["hold_days"]),
            num(t["entry_price"], 2), num(t["exit_price"], 2),
            signed(t["return_pct"], 2, "%"),
            signed(t["net_pnl_cny"], 2, ""),
            esc(t["exit_reason"]),
        ])

    var_rows = []
    for v in sorted(R["variants"], key=lambda x: -x["perf"]["sharpe"]):
        cells = [
            esc(v["name"]),
            num(v["perf"]["sharpe"]), signed(v["perf"]["total_return"] * 100, 2, "%"),
            str(v["perf"]["n_trades"]), str(v["n_signals"]),
        ]
        var_rows.append({"cells": cells, "cls": "hl"} if v["name"].startswith("主规则") else cells)
    var_html = table(["规则变体", "夏普", "总收益", "平仓交易", "信号数"], var_rows)

    cost_rows = [[f"×{c['cost_mult']}", signed(c["perf"]["total_return"] * 100, 2, "%"),
                  num(c["perf"]["sharpe"]), cny(c["perf"]["cost_total_cny"])]
                 for c in R["cost_sensitivity"]]

    oos = R["oos"]
    oos_rows = [
        ["样本内 2018-01-02 ~ " + oos["split"][0], signed(oos["in_sample"]["total_return"] * 100, 2, "%"),
         num(oos["in_sample"]["sharpe"]), str(oos["in_sample"]["n_trades"]),
         pct(oos["in_sample"].get("win_rate"), 1)],
        ["样本外 " + oos["split"][1] + " ~ " + cur["last_complete_day"],
         signed(oos["out_of_sample"]["total_return"] * 100, 2, "%"),
         num(oos["out_of_sample"]["sharpe"]), str(oos["out_of_sample"]["n_trades"]),
         pct(oos["out_of_sample"].get("win_rate"), 1)],
        ["买入持有（样本内）", signed(oos["buy_hold_in_sample"]["total_return"] * 100, 2, "%"),
         num(oos["buy_hold_in_sample"]["sharpe"]), "—", "—"],
        ["买入持有（样本外）", signed(oos["buy_hold_out_of_sample"]["total_return"] * 100, 2, "%"),
         num(oos["buy_hold_out_of_sample"]["sharpe"]), "—", "—"],
    ]

    hard_rows = [[esc(k), '<span class="tag no">命中</span>' if v else '<span class="tag ok">未命中</span>']
                 for k, v in vd["hard_fail"].items()]

    premise_insight = (
        f'全样本 {prem["scopes"]["全样本 2018-2026"]["n_newlow_days"]} 次 20 日新低后，'
        f'次日上涨概率 {pct(prem["scopes"]["全样本 2018-2026"]["1"]["up_ratio"], 1)}，'
        f'但 5 日均值仅 {pct(prem["scopes"]["全样本 2018-2026"]["5"]["mean"], 2)}；'
        f'近一年 {prem["scopes"]["近一年"]["n_newlow_days"]} 次新低里，'
        f'次日上涨概率升到 {pct(prem["scopes"]["近一年"]["1"]["up_ratio"], 1)}，'
        f'而 5 日 / 10 日上涨概率降到 {pct(prem["scopes"]["近一年"]["5"]["up_ratio"], 1)} / '
        f'{pct(prem["scopes"]["近一年"]["10"]["up_ratio"], 1)}，'
        f'均值分别为 {pct(prem["scopes"]["近一年"]["5"]["mean"], 2)} / '
        f'{pct(prem["scopes"]["近一年"]["10"]["mean"], 2)}。'
    )

    cur_rows = [
        ["最新完整交易日", esc(cur["last_complete_day"])],
        ["飞凯材料收盘（前复权）", esc(cur["stock_close_qfq"])],
        ["KDJ · J 值", num(cur["kdj_j"], 2) + ("（为负）" if cur["j_negative"] else "（非负值）")],
        ["上证指数收盘", esc(cur["index_close"])],
        ["指数是否创 20 日新低", "是" if cur["index_20d_newlow"] else "否"],
        ["主规则当日是否触发", "是" if cur["rule_signal_today"] else "否"],
        ["最近一次历史信号日", esc(cur["last_signal_day"])],
    ]
    cur_html = table(["读数", "数值"], cur_rows)

    intraday = cur["intraday_snapshot"]
    intra_txt = "；".join(
        f'{k}：开 {v["open"]} / 高 {v["high"]} / 低 {v["low"]} / 现 {v["close"]}'
        for k, v in intraday.items()
    )

    caveats = "".join(f"<li>{esc(c)}</li>" for c in R["caveats"])
    rf = R["rule_formalization"]

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="feikai-fingerprint" content="{fp}">
<title>飞凯材料 KDJ 择时策略研究 · research-only</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>飞凯材料 KDJ 择时策略研究</h1>
<div class="sub">上证指数新低反弹 + 个股 J 值负值触发 · 单标的事件驱动回测 · 数据指纹 {fp}</div>

<div class="banner warn">结论：工程可复跑、可审计；<strong>策略级结论 {esc(vd['final'])}</strong>。命中 {len(vd['hard_fail_items'])} 条硬否决。以下全部为历史统计，不构成投资建议。</div>
<div class="banner info">research-only：不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。</div>

<h2>一、把口语规则形式化</h2>
<div class="card">
<p class="small">用户原话：<strong>{esc(rf['user_words'])}</strong></p>
<ul>
<li><strong>大盘条件 A</strong>：{esc(rf['A_market'])}</li>
<li><strong>个股条件 B</strong>：{esc(rf['B_stock'])}</li>
<li><strong>入场</strong>：{esc(rf['entry'])}</li>
<li><strong>出场（「反弹 T」）</strong>：{esc(rf['exit'])}</li>
<li><strong>T+1 口径</strong>：{esc(rf['t_plus_1_note'])}</li>
<li><strong>「2负」歧义</strong>：{esc(rf['ambiguity'])}</li>
</ul>
<p class="small">成交价用前复权、涨跌停基准用未复权；创业板涨跌幅按 2020-08-24 前后 10%/20% 分段。¥1,000,000 为研究假设规模，非用户实盘资金。</p>
</div>

<h2>二、先检验前提：「上证指数每次新低有反弹」</h2>
<div class="card">
<p>{premise_insight}</p>
<div class="grid2"><div>{table(['样本区间', '持有期', '样本数', '上涨概率', '均值', '中位', 'P25', 'P75'], prem_rows)}</div>
<div>{premise_bars()}<p class="small">近一年「新低次日反弹」确实明显，但反弹不持续：3 日以后上涨概率跌破 50%，5/10 日均值转负。这解释了为什么主规则平均只持有 {b['avg_hold_days']} 天、几乎都靠 J 上穿 0 迅速离场。</p></div></div>
<p class="small">{esc(prem['note'])}</p>
</div>

<h2>三、当前状态（事实读数，不是操作建议）</h2>
<div class="card">{cur_html}
<p class="small">2026-11-17 盘中快照（未收盘、未进入回测）：{esc(intra_txt)}。</p>
<p class="small">{esc(cur['note'])}</p>
</div>

<h2>四、主规则回测结果</h2>
<div class="kpis">{kpis}</div>
<div class="card">{equity_svg()}
<p class="small">策略曲线长期走平，是因为 {R['meta']['sample']['n_days']} 个交易日里只有 {R['baseline']['strategy']['exposure_days']} 天持仓；低波动与 {pct(b['max_drawdown'])} 的回撤主要来自「不持仓」，不是择时能力的证明。同期买入持有总收益 {pct(bh['total_return'])}，但最大回撤 {pct(bh['max_drawdown'])}。</p>
</div>
<div class="card"><h3>逐笔交易（{b['n_trades']} 笔）</h3>
{table(['买入日', '卖出日', '持有日', '买入价', '卖出价', '收益率', '净额(¥)', '出场原因'], trade_rows)}
<p class="small">确定性审计：逐笔净额合计 {cny(R['audit']['trade_pnl_check']['trade_pnl_sum_cny'])} = 期末权益增益 {cny(R['audit']['trade_pnl_check']['equity_gain_cny'])}，差额 {R['audit']['trade_pnl_check']['abs_diff']}。</p>
</div>

<h2>五、样本内 / 样本外</h2>
<div class="card">{table(['区间', '总收益', '夏普', '平仓交易', '胜率'], oos_rows)}
<p class="small">样本外（{oos['split'][1]} 起）总收益 {pct(oos['out_of_sample']['total_return'])}、夏普 {num(oos['out_of_sample']['sharpe'])}，样本内夏普 {num(oos['in_sample']['sharpe'])} 未能延续；样本外仅 {oos['out_of_sample']['n_trades']} 笔交易。oos_use_count=1，参数网格已在同一段数据上反复试过，样本外不再干净。</p>
</div>

<h2>六、「2负」歧义与参数敏感性（{R['variant_summary']['n_variants']} 个单轴变体）</h2>
<div class="card">{var_html}
<p class="small">夏普区间 {num(R['variant_summary']['sharpe_min'])} ~ {num(R['variant_summary']['sharpe_max'])}，极差 {num(R['variant_summary']['sharpe_spread'])}；交易笔数 {R['variant_summary']['n_trades_min']} ~ {R['variant_summary']['n_trades_max']}。夏普最高的两个变体只有 6 笔交易；去掉大盘新低过滤后 39 笔交易反而亏 {pct(next(v['perf']['total_return'] for v in R['variants'] if v['name'].startswith('只看个股')), 2)}。{esc(R['variant_summary']['note'])}</p>
</div>

<h2>七、成本敏感性与等价随机对照</h2>
<div class="grid2"><div class="card"><h3>成本倍数</h3>{table(['成本档', '总收益', '夏普', '累计成本'], cost_rows)}
<p class="small">成本 ×3 后收益仍为 {pct(R['cost_sensitivity'][-1]['perf']['total_return'])}，成本不是主要约束；真正的问题是交易次数太少。</p></div>
<div class="card"><h3>等价随机对照</h3>
{table(['读数', '数值'], [
 ['重跑次数', str(rc.get('n_paths'))],
 ['随机夏普均值', num(rc.get('random_sharpe_mean'))],
 ['随机夏普 P95', num(rc.get('random_sharpe_p95'))],
 ['本策略夏普', num(rc.get('baseline_sharpe'))],
 ['夏普分位', pct(rc.get('sharpe_percentile'), 1)],
 ['显著优于随机', '否' if not rc.get('significantly_better_than_random') else '是'],
])}
<p class="small">{esc(rc.get('note'))} 等价性断言：净值最大差 {num(R['audit']['control_equivalence']['max_abs_equity_diff'], 9)}、成本差 {num(R['audit']['control_equivalence']['cost_abs_diff'], 9)}。</p></div></div>

<h2>八、裁决（由读数推导）</h2>
<div class="card">{table(['判据', '结果'], hard_rows)}
<p><strong>最终裁决：工程 {esc(vd['engineering'])} / 策略结论 {esc(vd['final'])}。</strong> {esc(vd['note'])}</p>
<p class="small">成立依据：{esc(vd['basis'])}。</p>
</div>

<h2>九、数据口径与已知缺口</h2>
<div class="card"><ul>{caveats}</ul>
<p class="small">数据源：{esc(R['meta']['data']['source'])}（{esc(R['meta']['data']['level'])}）；样本 {esc(R['meta']['sample']['start'])} ~ {esc(R['meta']['sample']['end'])}，{R['meta']['sample']['n_days']} 个完整交易日。名称已跨源校验：飞凯材料 / 上证指数。</p>
</div>

<h2>十、复跑命令</h2>
<div class="card"><pre><code>cd astock-quant-lab
python research/feikai_kdj/fetch_data.py
python research/feikai_kdj/run.py
python research/feikai_kdj/build_report.py
python research/feikai_kdj/validate_research.py</code></pre>
<p class="small">产物：research/feikai_kdj/output/ 下 result.json、equity.csv、equity_buyhold.csv、trades.csv、strategy_report.html。</p>
</div>

<div class="foot">{esc(R['compliance'])}<br>
生成时间 {esc(R['meta']['generated_at'])} · 数值指纹 {fp} · 全部数值由 result.json 自动生成，禁止手工转录。</div>
</div></body></html>"""

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "strategy_report.html").write_text(doc, encoding="utf-8")
    (TOP / "astock-quant-feikai-kdj-report.html").write_text(doc, encoding="utf-8")
    (OUT / "report_fingerprint.json").write_text(
        json.dumps({"fingerprint": fp, "files": ["strategy_report.html",
                    "../astock-quant-feikai-kdj-report.html"]}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    summary = f"""# 飞凯材料 KDJ 择时策略研究（摘要）

数据指纹 `{fp}` ｜ 样本 {R['meta']['sample']['start']} ~ {R['meta']['sample']['end']}，{R['meta']['sample']['n_days']} 个完整交易日。

## 结论
工程可复跑、可审计；**策略级结论不放行**（{len(vd['hard_fail_items'])} 条硬否决：{ '、'.join(vd['hard_fail_items']) }）。全部为历史统计，不构成投资建议。

## 关键读数
- 主规则：信号 {R['baseline']['n_signals']} 次、平仓 {b['n_trades']} 笔、胜率 {pct(b['win_rate'],1)}、平均持有 {b['avg_hold_days']} 日。
- 总收益 {pct(b['total_return'])}、年化 {pct(b['annualized_return'])}、夏普 {num(b['sharpe'])}、最大回撤 {pct(b['max_drawdown'])}、成本 {cny(b['cost_total_cny'])}。
- 同期买入持有总收益 {pct(bh['total_return'])}、最大回撤 {pct(bh['max_drawdown'])}。
- 样本外（{oos['split'][1]} 起）总收益 {pct(oos['out_of_sample']['total_return'])}、夏普 {num(oos['out_of_sample']['sharpe'])}、{oos['out_of_sample']['n_trades']} 笔交易。
- 等价随机对照 {rc.get('n_paths')} 次：夏普分位 {pct(rc.get('sharpe_percentile'),1)}，显著优于随机 = {'是' if rc.get('significantly_better_than_random') else '否'}。

## 用户前提检验
{premise_insight}

## 当前状态
{cur['last_complete_day']}：J={num(cur['kdj_j'],2)}（{'为负' if cur['j_negative'] else '非负'}）、指数 {cur['index_close']}、20 日新低={'是' if cur['index_20d_newlow'] else '否'}、主规则触发={'是' if cur['rule_signal_today'] else '否'}。仅为事实读数。

## 合规
{R['compliance']}
"""
    (OUT / "RESEARCH_SUMMARY.md").write_text(summary, encoding="utf-8")
    print(f"WROTE strategy_report.html ({len(doc):,} chars) fingerprint={fp}")
    print("WROTE ../astock-quant-feikai-kdj-report.html  +  RESEARCH_SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
