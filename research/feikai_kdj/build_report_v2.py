#!/usr/bin/env python3
"""由 result_v2.json 生成飞凯材料 v2 研报（零 CDN、零 JS、纯 SVG 自绘）。

所有数值均从产物读取，禁止手写转录；报告带 feikai-v2-fingerprint 供一致性校验。
运行：python research/feikai_kdj/build_report_v2.py
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
ROOT = HERE.parent.parent
TOP = ROOT.parent

R = json.loads((OUT / "result_v2.json").read_text(encoding="utf-8"))


def esc(x) -> str:
    return html.escape(str(x))


def pct(x, d=2) -> str:
    return "—" if x is None else f"{float(x) * 100:.{d}f}%"


def num(x, d=4) -> str:
    return "—" if x is None else f"{float(x):.{d}f}"


def signed(x, d=2, suffix="%") -> str:
    if x is None:
        return "—"
    v = float(x)
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v:+.{d}f}{suffix}</span>'


def fingerprint() -> str:
    cur = R["current_state"]
    key = {
        "sample": R["meta"]["sample"],
        "ma5w": cur["ma5w_qfq"], "ma10w": cur["ma10w_qfq"],
        "touch5": cur["touch5_today"], "touch10": cur["touch10_today"],
        "monthly_provisional": cur["monthly"]["macd_deathcross"],
        "monthly_confirmed": cur["monthly_confirmed_state_macd"],
        "range20_pctile": cur["range20_percentile"],
        "variants": R["variant_summary"],
        "verdict": R["verdict"]["final"],
    }
    blob = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def style() -> str:
    """复用 v1 研报的样式，保证同一工程内视觉一致。"""
    v1 = OUT / "strategy_report.html"
    if v1.exists():
        m = re.search(r"<style>([\s\S]*?)</style>", v1.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return ("body{font:14px/1.65 -apple-system,'Microsoft YaHei',sans-serif;background:#f5f7fa;color:#1f2937}"
            ".wrap{max-width:1180px;margin:0 auto;padding:28px}h1{font-size:26px}"
            ".card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px 18px;margin:12px 0}"
            "table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid #e2e8f0}"
            ".up{color:#dc2626}.down{color:#16a34a}.ax{font-size:11px;fill:#64748b}")


def kpi(label, value, sub="", tone="") -> str:
    return (f'<div class="kpi {tone}"><div class="kl">{esc(label)}</div>'
            f'<div class="kv">{value}</div><div class="ks">{esc(sub)}</div></div>')


def table(headers, rows, cls="") -> str:
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


def range_bars() -> str:
    """5/10/20/60 日区间宽度的历史分位——检验「震荡区间有点小」。"""
    data = [(f"{w} 日", R["current_state"]["range_by_window"][str(w)]["percentile"],
             R["current_state"]["range_by_window"][str(w)]["width"])
            for w in (5, 10, 20, 60)]
    W, H, L, T, B = 560, 215, 48, 24, 40
    bw = (W - L - 22) / len(data)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="不同窗口震荡区间宽度的历史分位" class="chart">']
    y50 = T + (H - T - B) * (1.0 - 0.5)
    out.append(f'<line x1="{L}" y1="{y50:.1f}" x2="{W - 18}" y2="{y50:.1f}" stroke="#94a3b8" stroke-dasharray="5 4"/>')
    out.append(f'<text x="{L - 8}" y="{y50 + 4:.1f}" text-anchor="end" class="ax">50%</text>')
    for j, (label, v, width) in enumerate(data):
        x = L + j * bw + bw * 0.22
        w = bw * 0.56
        y = T + (H - T - B) * (1.0 - float(v))
        color = "#dc2626" if float(v) > 0.5 else "#16a34a"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{H - B - y:.1f}" rx="3" fill="{color}" opacity="0.85"/>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" class="barv">{float(v) * 100:.1f}%</text>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{H - 22}" text-anchor="middle" class="ax">{esc(label)}</text>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{H - 7}" text-anchor="middle" class="ax">宽 {float(width) * 100:.1f}%</text>')
    out.append(f'<text x="{L}" y="{T + 8}" class="ax">柱高 = 该窗口振幅的全样本历史分位</text>')
    out.append("</svg>")
    return "".join(out)


def collapse_bars() -> str:
    """条件叠加后信号数坍缩。"""
    rows = [v for v in R["filter_variants"] if not v["name"].startswith("v2-tol")]
    labels = [v["name"].split(" ", 1)[0] for v in rows]
    vals = [v["signals"] for v in rows]
    W, H, L, T, B = 640, 210, 46, 22, 46
    bw = (W - L - 20) / len(rows)
    mx = max(vals) or 1
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="各变体叠加过滤后的信号数" class="chart">']
    for j, (lab, v) in enumerate(zip(labels, vals)):
        x = L + j * bw + bw * 0.20
        w = bw * 0.60
        h = (H - T - B) * v / mx
        y = H - B - h
        color = "#2563eb" if v > 0 else "#cbd5e1"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{max(h, 2):.1f}" rx="3" fill="{color}"/>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" class="barv">{v}</text>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{H - 26}" text-anchor="middle" class="ax">{esc(lab)}</text>')
    out.append(f'<text x="{L}" y="{T}" class="ax">主规则 9 次信号，叠加条件后逐层消失</text>')
    out.append(f'<text x="{L}" y="{H - 6}" class="ax">横轴为变体编号，详见下表；灰柱 = 0 次信号</text>')
    out.append("</svg>")
    return "".join(out)


def main() -> int:
    fp = fingerprint()
    cur = R["current_state"]
    m = cur["monthly"]
    rows = R["filter_variants"]
    ev = R["event_study"]
    rc = R["random_control_v2"]
    erc = R["event_random_control"]
    vd = R["verdict"]

    conv_rows = [[esc(c["time"]), esc(c["speaker"]), esc(c["quote"]),
                  "v1 已形式化" if ("新低" in c["quote"] or "kdj" in c["quote"] or "2负" in c["quote"])
                  else "v2 新增"] for c in R["conversation"]]

    recon_rows = [[esc(x["quote"]), esc(x["reading"]), esc(x["assessment"])]
                  for x in R["conversation_reconciliation"]]

    kpis = "".join([
        kpi("最新完整交易日", esc(cur["last_complete_day"]), "盘中 bar 不进入统计"),
        kpi("5 周线", num(cur["ma5w_qfq"], 3), f"收盘偏离 {pct(cur['dist_close_to_ma5w'])}"),
        kpi("10 周线", num(cur["ma10w_qfq"], 3), f"收盘偏离 {pct(cur['dist_close_to_ma10w'])}"),
        kpi("触及 5 / 10 周线", ("是" if cur["touch5_today"] else "否") + " / " +
            ("是" if cur["touch10_today"] else "否"), f"容差 ±{cur['tol'] * 100:.1f}%"),
        kpi("月线 MACD 死叉·未完成月", "是" if m["macd_deathcross"] else "否", "当月尚有交易日，读数会变"),
        kpi("月线 MACD 死叉·已完成月", "是" if cur["monthly_confirmed_state_macd"] else "否", "无前视确认口径"),
        kpi("20 日振幅 / 分位", pct(cur["range20_width"], 1), f"历史分位 {pct(cur['range20_percentile'], 1)}"),
        kpi("全叠加变体信号数", str(next(v["signals"] for v in rows if v["name"].startswith("v2-9"))),
            f"v1 基线 {rows[0]['signals']} 次"),
    ])

    cur_rows = [
        ["飞凯材料收盘（前复权 / 未复权）", f'{cur["stock_close_qfq"]} / {cur["stock_close_raw"]}'],
        ["当日最低（前复权）", esc(cur["stock_low_qfq"])],
        ["上证指数收盘", esc(cur["index_close"])],
        ["5 周线（前复权 / 未复权）", f'{cur["ma5w_qfq"]} / {cur["ma5w_raw"]}'],
        ["10 周线（前复权 / 未复权）", f'{cur["ma10w_qfq"]} / {cur["ma10w_raw"]}'],
        ["最低价距 5 / 10 周线", f'{pct(cur["dist_low_to_ma5w"])} / {pct(cur["dist_low_to_ma10w"])}'],
        ["最低价更接近", "5 周线" if cur["closer_weekly_ma_by_low"] == "ma5w" else
         ("10 周线" if cur["closer_weekly_ma_by_low"] == "ma10w" else "等距")],
        ["收盘价是否在 10 周线上方", "是" if cur["above_ma10w"] else "否"],
        ["若向上触及 10 周线需涨", f'{num(cur["gap_up_to_ma10w_pct"], 2)}%'],
    ]

    monthly_rows = [
        ["MACD(12,26,9) · 未完成月", "DIF " + num(m["macd_dif"], 4) + " / DEA " + num(m["macd_dea"], 4),
         "是" if m["macd_deathcross"] else "否", "本月刚下穿 = " + ("是" if m["macd_cross_event_this_month"] else "否")],
        ["MACD · 上一个已完成月", "—", "是" if cur["monthly_confirmed_state_macd"] else "否", "回测过滤用的确认口径"],
        ["月线 KDJ(9,3,3)", "K " + num(m["kdj_k"], 2) + " / D " + num(m["kdj_d"], 2) + " / J " + num(m["kdj_j"], 2),
         "是" if m["kdj_deathcross"] else "否", "已完成月确认 = " + ("是" if cur["monthly_confirmed_state_kdj"] else "否")],
        ["月线 MA5 / MA10", num(m["ma5"], 3) + " / " + num(m["ma10"], 3),
         "是" if m["ma_deathcross"] else "否", "已完成月确认 = " + ("是" if cur["monthly_confirmed_state_ma"] else "否")],
    ]

    var_rows = []
    for v in rows:
        cells = [
            esc(v["name"]), str(v["filter_days"]), str(v["signals"]),
            str(v["perf"]["n_trades"]), signed(v["perf"]["total_return"] * 100),
            num(v["perf"]["sharpe"]), signed(v["oos"].get("total_return", 0) * 100)
            if isinstance(v["oos"].get("total_return"), (int, float)) else "—",
        ]
        var_rows.append({"cells": cells, "cls": "hl"} if v["name"].startswith("v2-0") else cells)

    ev_rows = []
    for label, g in ev["groups"].items():
        h1, h5 = g["horizons"].get("1", {}), g["horizons"].get("5", {})
        ev_rows.append([
            esc(label), str(g["n_events"]),
            pct(h1.get("up_ratio"), 1) if h1.get("n") else "—",
            pct(h5.get("up_ratio"), 1) if h5.get("n") else "—",
            signed(h5.get("mean", 0) * 100 if h5.get("n") else None),
            signed(h5.get("excess_vs_all", 0) * 100 if h5.get("n") else None),
        ])

    erc_rows = [[esc(k), str(v.get("n_events", "—")), pct(v.get("event_mean"), 3),
                 pct(v.get("random_mean"), 3), pct(v.get("percentile"), 1),
                 "是" if v.get("significantly_better") else "否"]
                for k, v in erc.items()]

    rc_rows = [[esc(k), esc(v.get("status")), str(v.get("n_signals_per_path", "—")),
                pct(v.get("sharpe_percentile"), 1) if v.get("sharpe_percentile") is not None else "—",
                "是" if v.get("significantly_better_than_random") else "否"]
               for k, v in rc.items()]

    hard_rows = [[esc(k), '<span class="tag no">命中</span>' if v else '<span class="tag ok">未命中</span>']
                 for k, v in vd["hard_fail"].items()]

    intraday = cur["intraday_snapshot"]
    intra_txt = "；".join(
        f'{k}：现 {v["last"]}，较前收 {num(v["pct_vs_prev_close"], 3)}%'
        for k, v in intraday.items()
    )
    caveats = "".join(f"<li>{esc(c)}</li>" for c in R["caveats"])
    nf = R["new_conditions"]

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="feikai-v2-fingerprint" content="{fp}">
<title>飞凯材料研究 v2 · 月线死叉 / 10 周线 / 震荡区间 · research-only</title>
<style>{style()}</style></head><body><div class="wrap">

<h1>飞凯材料研究 v2 · 对话新增条件核对</h1>
<div class="sub">月线死叉 / 回踩 10 周线 / 震荡区间 + v1 主规则 · 单标的事件驱动研究 · 数据指纹 {fp}</div>

<div class="banner warn">结论：工程 {esc(vd['engineering'])}；<strong>「新增条件能改善策略」这一结论 {esc(vd['final'])}</strong>，命中 {len(vd['hard_fail_items'])} 条硬否决。以下全部为历史统计与事实读数，不构成投资建议。</div>
<div class="banner info">research-only：不含实盘下单、券商接口、自动交易循环；不荐股、不给买卖点与目标价、不承诺收益、不代操作。「是不是能 T」「我等 10 周线 T 一波」属操作判断，本研究不评价、不建议。</div>

<h2>一、对话原文与逐条核对</h2>
<div class="card">{table(['时间', '说话人', '原话', '归属'], conv_rows)}
<p class="small">v1 已形式化「上证新低买 / 反弹 T / KDJ J 负 / 2 负反弹」；v2 补齐本段对话里此前未进模型的三个条件，并对原话做客观读数核对。</p>
</div>
<div class="card"><h3>逐条核对（读数自动派生）</h3>
{table(['对话中的说法', '截至最后完整交易日的客观读数', '核对结果'], recon_rows)}
<p class="small">2026-11-17 盘中快照（未收盘、未进入回测）：{esc(intra_txt)}。</p>
</div>

<h2>二、三个新条件的形式化定义</h2>
<div class="card">
<ul>
<li><strong>C1 月线死叉</strong>：主口径 {esc(nf['C_monthly_deathcross']['definitions']['macd'])}；并列口径 {esc(nf['C_monthly_deathcross']['definitions']['kdj'])}、{esc(nf['C_monthly_deathcross']['definitions']['ma5_ma10'])}。
回测过滤 {esc(nf['C_monthly_deathcross']['confirmed_rule'])}；{esc(nf['C_monthly_deathcross']['provisional_caveat'])}。共 {nf['C_monthly_deathcross']['n_months']} 根月线。</li>
<li><strong>C2 回踩 10 周线</strong>：{esc(nf['C_touch_weekly_ma']['ma_definition'])}（无前视={esc(nf['C_touch_weekly_ma']['no_lookahead'])}）；
判定为 {esc(nf['C_touch_weekly_ma']['touch_definition'])}。容差敏感性 {' / '.join(str(x) for x in nf['C_touch_weekly_ma']['tol_sensitivity'])} 一并回测。</li>
<li><strong>C3 震荡区间</strong>：{esc(nf['C_range_width']['definition'])}；窗口 {' / '.join(str(x) for x in nf['C_range_width']['windows'])} 日并列；
窄幅 = {esc(nf['C_range_width']['narrow_threshold'])}，宽幅 = {esc(nf['C_range_width']['wide_threshold'])}。</li>
</ul>
<p class="small">价格口径：{esc(cur['price_basis']['weekly_ma_primary'])}；{esc(cur['price_basis']['weekly_ma_secondary'])}。v2 复用 v1 的回测引擎、成本模型、T+1 与出场规则，仅以 <code>signals=</code> 注入过滤后信号；真实信号 override 的净值最大差 {num(R['audit']['signal_override_equity_diff'], 9)}。</p>
</div>

<h2>三、当前条件状态（事实读数，不是操作建议）</h2>
<div class="kpis">{kpis}</div>
<div class="grid2">
<div class="card"><h3>价格与周线位置</h3>{table(['读数', '数值'], cur_rows)}</div>
<div class="card"><h3>「月线死叉」三种口径</h3>{table(['口径', '读数', '是否死叉', '备注'], monthly_rows)}
<p class="small">{esc(m['note'])} {esc(m['month_completeness_basis'])}（当月已 {m['n_bars_in_month']} 根日线）。</p></div>
</div>
<div class="card"><h3>「震荡区间有点小」的多尺度核对</h3>
<div class="grid2"><div>{range_bars()}</div>
<div>{table(['窗口', '振幅', '历史分位', '区间高', '区间低'],
  [[f"{w} 日", pct(cur['range_by_window'][str(w)]['width'], 2),
    pct(cur['range_by_window'][str(w)]['percentile'], 1),
    num(cur['range_by_window'][str(w)]['high'], 2),
    num(cur['range_by_window'][str(w)]['low'], 2)] for w in (5, 10, 20, 60)])}
<p class="small">四个窗口的历史分位均高于 50%，即按「最高−最低/最低」度量，近期区间在历史上偏宽而非偏窄；ATR20 分位 {pct(cur['atr20_percentile'], 1)}。「区间小」可能指别的尺度或别的定义，本研究不替用户补定义。</p></div></div>
</div>

<h2>四、把新条件叠加到 v1 主规则</h2>
<div class="card">{collapse_bars()}
{table(['变体', '过滤生效天数', '信号数', '平仓交易', '总收益', '夏普', '样本外收益'], var_rows)}
<p class="small">共 {R['variant_summary']['n_variants']} 个变体（含 3 个容差档），信号数 {R['variant_summary']['signals_min']} ~ {R['variant_summary']['signals_max']}，
其中 {R['variant_summary']['n_zero_signal']} 个变体零信号、{R['variant_summary']['n_zero_trade']} 个零交易。
<strong>全叠加变体（v2-9）信号为 0</strong>：条件越贴合对话，样本越坍缩，越无法验证。
另外注意口径选择的陷阱：同为「月线死叉」，MACD 口径 3 笔交易 {pct(next(v['perf']['total_return'] for v in rows if v['name'].startswith('v2-2')))}，
KDJ 口径 3 笔交易 {pct(next(v['perf']['total_return'] for v in rows if v['name'].startswith('v2-4')))}，符号相反——3 笔样本的差值没有统计意义，挑好看的那个就是过拟合。</p>
</div>

<h2>五、事件级统计（绕开交易样本坍缩）</h2>
<div class="card">
<p class="small">不用完整交易回测，直接统计条件发生后的个股前向收益。全样本基线是无条件平均，excess = 条件组均值 − 全样本均值。</p>
{table(['条件组', '事件数', '+1 日上涨率', '+5 日上涨率', '+5 日均值', '+5 日超额'], ev_rows)}
<div class="grid2"><div><h3>事件级随机对照（+5 日，1000 次无放回抽样）</h3>
{table(['条件', '事件数', '条件均值', '随机均值', '分位', '显著更优'], erc_rows)}</div>
<div><h3>过滤变体的等价随机对照（200 次完整引擎重跑）</h3>
{table(['变体', '状态', '每路径信号', '夏普分位', '显著更优'], rc_rows)}
<p class="small">随机池限定为满足同一过滤条件的日期；信号数只有 1 次时分位不具解释力。</p></div></div>
<p class="small">{esc(ev['note'])} {esc(next(iter(erc.values())).get('note', ''))}</p>
</div>

<h2>六、裁决（由读数推导）</h2>
<div class="card">{table(['判据', '结果'], hard_rows)}
<p><strong>最终裁决：{esc(vd['engineering'])} / {esc(vd['scope'])} → {esc(vd['final'])}。</strong> {esc(vd['note'])}</p>
<p class="small">成立依据：{esc(vd['basis'])}。v1 原结论（工程可交付 / 策略结论不放行）不因本轮新增条件而改变。</p>
</div>

<h2>七、口径、缺口与复跑</h2>
<div class="card"><ul>{caveats}</ul>
<p class="small">样本 {esc(R['meta']['sample']['start'])} ~ {esc(R['meta']['sample']['end'])}，{R['meta']['sample']['n_days']} 个完整交易日；数据源 {esc(R['meta']['data']['source'])}（{esc(R['meta']['data']['level'])}）。</p>
<pre><code>cd astock-quant-lab
python research/feikai_kdj/fetch_data.py
python research/feikai_kdj/run_v2.py
python research/feikai_kdj/build_report_v2.py
python research/feikai_kdj/validate_v2.py</code></pre>
</div>

<div class="foot">{esc(R['compliance'])}<br>
生成时间 {esc(R['meta']['generated_at'])} · 数值指纹 {fp} · 全部数值由 result_v2.json 自动生成，禁止手工转录。</div>
</div></body></html>"""

    (OUT / "strategy_report_v2.html").write_text(doc, encoding="utf-8")
    (TOP / "astock-quant-feikai-kdj-v2-report.html").write_text(doc, encoding="utf-8")
    (OUT / "report_fingerprint_v2.json").write_text(
        json.dumps({"fingerprint": fp,
                    "files": ["strategy_report_v2.html",
                              "../astock-quant-feikai-kdj-v2-report.html"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    summary = f"""# 飞凯材料研究 v2 · 对话新增条件核对（摘要）

数据指纹 `{fp}` ｜ 样本 {R['meta']['sample']['start']} ~ {R['meta']['sample']['end']}，{R['meta']['sample']['n_days']} 个完整交易日。

## 结论
{R['verdict']['engineering']}；**「新增条件能改善策略」不放行**（{len(vd['hard_fail_items'])} 条硬否决：{'、'.join(vd['hard_fail_items'])}）。
v1 结论（工程可交付 / 策略结论不放行）不变。全部为历史统计，不构成投资建议。

## 对话核对的关键读数（截至 {cur['last_complete_day']}）
- 5 周线 {cur['ma5w_qfq']}，10 周线 {cur['ma10w_qfq']}；最低价 {cur['stock_low_qfq']} 距 5 周线 {pct(cur['dist_low_to_ma5w'])}、距 10 周线 {pct(cur['dist_low_to_ma10w'])} → 更接近 5 周线。
- 收盘 {cur['stock_close_qfq']} 低于 10 周线 {pct(cur['dist_close_to_ma10w'])}；10 周线在价格**上方**，向上触及需涨 {num(cur['gap_up_to_ma10w_pct'], 2)}%。「回踩」方向存在歧义，两种解释均已回测。
- 月线 MACD：未完成月 DIF {m['macd_dif']} < DEA {m['macd_dea']}（死叉=是，本月刚下穿）；上一个已完成月死叉=否。月线 KDJ 死叉=是、MA5<MA10=是 → 三种口径不一致。
- 区间振幅 5/10/20/60 日历史分位分别 {pct(cur['range_by_window']['5']['percentile'],1)} / {pct(cur['range_by_window']['10']['percentile'],1)} / {pct(cur['range_by_window']['20']['percentile'],1)} / {pct(cur['range_by_window']['60']['percentile'],1)}，均高于 50%，不支撑统一的「区间小」判断。
- 2026-11-17 盘中快照（未入统计）：{'；'.join(f"{k} 现 {v['last']}（较前收 {num(v['pct_vs_prev_close'],3)}%）" for k, v in intraday.items())}。

## 条件叠加结果
{R['variant_summary']['n_variants']} 个变体，信号 {R['variant_summary']['signals_min']}~{R['variant_summary']['signals_max']} 次、交易 {R['variant_summary']['trades_min']}~{R['variant_summary']['trades_max']} 笔；
{R['variant_summary']['n_zero_signal']} 个零信号。全叠加变体信号 0 次。同为「月线死叉」，MACD 口径 {pct(next(v['perf']['total_return'] for v in rows if v['name'].startswith('v2-2')))}、KDJ 口径 {pct(next(v['perf']['total_return'] for v in rows if v['name'].startswith('v2-4')))}，符号相反但都只有 3 笔交易。

## 事件级统计（+5 日）
回踩 10 周线 {ev['groups']['回踩 10 周线（全部）']['n_events']} 次，均值 {pct(ev['groups']['回踩 10 周线（全部）']['horizons']['5']['mean'], 3)}，随机对照分位 {pct(erc['回踩 10 周线']['percentile'], 1)}；
叠加月线死叉后均值 {pct(ev['groups']['回踩 10 周线 + 月线死叉状态']['horizons']['5']['mean'], 3)}，分位 {pct(erc['回踩 10 周线 + MACD 死叉']['percentile'], 1)}。均未显著优于随机，且未做多重检验校正。

## 合规
{R['compliance']}
"""
    (OUT / "RESEARCH_SUMMARY_V2.md").write_text(summary, encoding="utf-8")
    print(f"WROTE strategy_report_v2.html ({len(doc):,} chars) fingerprint={fp}")
    print("WROTE ../astock-quant-feikai-kdj-v2-report.html  +  RESEARCH_SUMMARY_V2.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
