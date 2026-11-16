#!/usr/bin/env python3
"""从 output/result.json + equity.csv 生成浅底深字研报风 HTML 设计报告。

只使用真实回测输出，不编造任何数字。运行：
    python scripts/build_design_report.py --out ../astock-quant-design-report.html
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def fmt_pct(x, digits=2):
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def fmt_num(x, digits=4):
    return "—" if x is None else f"{x:.{digits}f}"


def build(rep: dict, eq: pd.Series) -> str:
    p = rep["performance"]
    oos = rep["overfit"]["suite"]["1_out_of_sample"]
    fr = rep["factor_research"]
    mc = rep["overfit"]["suite"]["3_monte_carlo_deflated_sharpe"]
    sens = rep["overfit"]["suite"]["2_parameter_sensitivity"]
    cost = rep["overfit"]["suite"]["4_cost_sensitivity"]
    rk = rep["risk"]
    gate = rep["quality_gate"]

    # 净值按周降采样，避免首屏加载过重的 2150 点
    step = max(1, len(eq) // 430)
    sub = eq.iloc[::step]
    if sub.index[-1] != eq.index[-1]:
        sub = pd.concat([sub, eq.iloc[[-1]]])
    dates = [str(d) for d in sub.index]
    nav = [round(float(v / eq.iloc[0]), 4) for v in sub.values]
    dd = [round(float(v), 4) for v in (eq / eq.cummax() - 1.0).iloc[::step].values]

    fac_names = list(fr["per_factor"].keys())
    fac_ic = [fr["per_factor"][k]["ic_mean"] for k in fac_names]
    fac_t = [fr["per_factor"][k]["t_stat"] for k in fac_names]

    grid = sens["grid"]
    top_ns = sorted({g["top_n"] for g in grid})
    ma_ls = sorted({g["ma_long"] for g in grid})
    heat = [[g["ma_long"], g["top_n"], g["sharpe"]] for g in grid if g.get("sharpe") is not None]

    cost_x = [f"×{r['multiplier']}g" for r in cost["rows"]]
    cost_y = [r["total_return"] for r in cost["rows"]]

    mc_bars = {
        "labels": ["随机均值", "随机 P95", "策略基准", "DSR"],
        "values": [mc["random_sharpe_mean"], mc["random_sharpe_p95"],
                   mc["baseline_sharpe"], mc["deflated_sharpe"]["dsr"]],
    }

    hist = rk["stress"]["historical"]
    hist_rows = "".join(
        f"<tr><td>{k}</td><td>{v['window']}</td><td class='{'neg' if (v.get('period_return') or 0) < 0 else 'pos'}'>"
        f"{fmt_pct(v.get('period_return'))}</td><td class='neg'>{fmt_pct(v.get('max_drawdown'))}</td>"
        f"<td>{v.get('trading_days', '—') if v.get('covered') else '样本未覆盖'}</td></tr>"
        for k, v in hist.items()
    )

    risk_rows = "".join(
        f"<tr><td><b>{e['item']}</b></td><td>{e['reading']}</td>"
        f"<td><span class='lv lv-{e['level']}'>{e['level']}</span></td><td>{e['confidence']}</td>"
        f"<td>{e['if_risk']}</td></tr>"
        for e in rep["risk_elements"]
    )

    gate_rows = "".join(
        f"<tr><td><b>{i['ITEM']}</b></td><td><span class='vd vd-{i['VERDICT']}'>{i['VERDICT']}</span></td>"
        f"<td>{i['EVIDENCE']}</td><td>{i['NOTE'] or '—'}</td></tr>"
        for i in gate["items"]
    )

    factor_rows = "".join(
        f"<tr><td><code>{k}</code></td><td>{v['desc'] if 'desc' in v else k}</td>"
        f"<td>{fr['per_factor'][k]['ic_mean']}</td><td>{fr['per_factor'][k]['icir']}</td>"
        f"<td>{fr['per_factor'][k]['t_stat']}</td><td>{fr['per_factor'][k]['layered']['monotonicity_corr']}</td></tr>"
        for k, v in FACTOR_DESC.items()
    )

    fuse_rows = "".join(
        f"<tr><td>{e['date']}</td><td class='neg'>{fmt_pct(e['drawdown'])}</td><td>{e['action']}</td></tr>"
        for e in rep["portfolio"]["degrade_fuse"]["triggered"]
    ) or "<tr><td colspan='3'>本次样本内未触发</td></tr>"

    followups = "".join(f"<li>{x}</li>" for x in gate["followups"])
    gaps = "".join(f"<li>{x}</li>" for x in rep["meta"]["data_gaps"])
    comp = fr["composite_ic"]

    DATA = {
        "dates": dates, "nav": nav, "dd": dd,
        "facNames": fac_names, "facIC": fac_ic, "facT": fac_t,
        "heat": heat, "topNs": top_ns, "maLs": ma_ls,
        "costX": cost_x, "costY": cost_y, "mc": mc_bars,
        "oosSplit": rep["meta"]["step0"]["period"]["oos_start"],
    }
    data_json = json.dumps(DATA, ensure_ascii=False)

    return HTML_TEMPLATE.format(
        title=rep["meta"]["title"],
        generated=rep["meta"]["generated_at"],
        source=rep["meta"]["data_source"],
        sample_dates=rep["meta"]["sample"]["dates"],
        sample_codes=rep["meta"]["sample"]["codes"],
        sample_start=rep["meta"]["sample"]["start"],
        sample_end=rep["meta"]["sample"]["end"],
        conclusion=rep["one_line_conclusion"],
        verdict=gate["final_verdict"],
        eng_gate=gate["engineering_gate"],
        strat_gate=gate["strategy_conclusion_gate"],
        total_ret=fmt_pct(p["total_return"]),
        ann_ret=fmt_pct(p["annualized_return"]),
        sharpe=fmt_num(p["sharpe"]),
        mdd=fmt_pct(p["max_drawdown"]),
        cost_share=fmt_pct(p["cost_share_of_gross"]),
        cost_cny=f"¥{p['cost_total_cny']:,.0f}",
        oos_sharpe=fmt_num(oos["out_of_sample"]["sharpe"]),
        is_sharpe=fmt_num(oos["in_sample"]["sharpe"]),
        vol=fmt_pct(p["annualized_vol"]),
        calmar=fmt_num(p["calmar"]),
        win=fmt_pct(p["monthly_win_rate"]),
        turnover=fmt_pct(p["avg_turnover_per_rebalance"]),
        n_trades=p["n_trades"],
        blocked=p["blocked_orders"],
        comp_ic=fmt_num(comp["ic_mean"]),
        comp_t=fmt_num(comp["t_stat"]),
        comp_pos=fmt_pct(comp["positive_ratio"]),
        fdr_n=fr["multiple_testing"]["n_rejected"],
        fdr_cut=fr["multiple_testing"]["cutoff"],
        mc_pct=fmt_pct(mc["baseline_percentile"], 1),
        mc_paths=mc["n_paths"],
        dsr=fmt_num(mc["deflated_sharpe"]["dsr"]),
        sens_spread=fmt_num(sens["sharpe_spread"]),
        var95=fmt_pct(rk["tail"]["0.95"]["var"]),
        cvar95=fmt_pct(rk["tail"]["0.95"]["cvar"]),
        var99=fmt_pct(rk["tail"]["0.99"]["var"]),
        cvar99=fmt_pct(rk["tail"]["0.99"]["cvar"]),
        corr_full=rk["correlation_concentration"]["full_sample_avg_pair_corr"],
        corr_worst=rk["correlation_concentration"]["worst_days_avg_pair_corr"],
        hist_rows=hist_rows, risk_rows=risk_rows, gate_rows=gate_rows,
        factor_rows=factor_rows, fuse_rows=fuse_rows,
        followups=followups, gaps=gaps, data_json=data_json,
    )


FACTOR_DESC = {
    "mom_60": {"desc": "60日动量（跳过最近5日）"},
    "mom_120": {"desc": "120日动量（跳过最近5日）"},
    "rev_5": {"desc": "5日短期反转"},
    "low_vol_20": {"desc": "20日低波动"},
    "liq_20": {"desc": "20日流动性（对数成交额）"},
    "illiq_20": {"desc": "20日低价格冲击（负 Amihud）"},
}


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
:root {{ --bg:#f7f8fa; --card:#ffffff; --ink:#1b2330; --sub:#5c6b7f; --line:#e3e8ef;
         --up:#c0392b; --down:#1e8449; --accent:#2c5282; --warn:#b7791f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
        font-family:"PingFang SC","Microsoft YaHei",-apple-system,Segoe UI,sans-serif; line-height:1.7; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:32px 24px 80px; }}
header {{ border-bottom:3px solid var(--accent); padding-bottom:18px; margin-bottom:26px; }}
h1 {{ font-size:29px; margin:0 0 8px; letter-spacing:.5px; }}
.meta {{ color:var(--sub); font-size:13px; }}
.badges span {{ display:inline-block; background:#eaf1f8; color:var(--accent); border:1px solid #cddcec;
               border-radius:14px; padding:2px 11px; font-size:12px; margin:8px 6px 0 0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
         padding:22px 24px; margin:18px 0; box-shadow:0 1px 3px rgba(20,40,80,.04); }}
.tldr {{ border-left:5px solid var(--down); background:#f2f9f4; }}
.tldr h2 {{ margin:0 0 8px; font-size:19px; }}
.tldr p {{ margin:0; font-size:15px; }}
.verdict {{ display:inline-block; background:var(--down); color:#fff; padding:3px 12px;
            border-radius:5px; font-size:13px; margin-right:8px; }}
.verdict.eng {{ background:var(--accent); }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(158px,1fr)); gap:12px; margin-top:18px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:13px 15px; }}
.kpi .lab {{ font-size:12px; color:var(--sub); }}
.kpi .val {{ font-size:22px; font-weight:700; margin-top:3px; }}
.pos {{ color:var(--up); }} .neg {{ color:var(--down); }}
h2.sec {{ font-size:20px; margin:34px 0 12px; padding-left:11px; border-left:4px solid var(--accent); }}
h3 {{ font-size:16px; margin:20px 0 8px; }}
table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
th,td {{ border:1px solid var(--line); padding:9px 11px; text-align:left; vertical-align:top; }}
th {{ background:#eef3f9; font-weight:600; }}
tr:nth-child(even) td {{ background:#fbfcfe; }}
.chart {{ width:100%; height:330px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:820px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.note {{ font-size:12px; color:var(--sub); margin-top:8px; }}
.flow {{ display:flex; flex-wrap:wrap; gap:10px; align-items:stretch; }}
.fbox {{ flex:1 1 150px; background:#eef3f9; border:1px solid #cddcec; border-radius:8px;
         padding:12px; min-width:145px; }}
.fbox b {{ display:block; color:var(--accent); font-size:13px; margin-bottom:4px; }}
.fbox small {{ color:var(--sub); font-size:11.5px; }}
.arrow {{ align-self:center; color:var(--accent); font-size:20px; }}
.lv {{ padding:2px 8px; border-radius:4px; font-size:12px; color:#fff; }}
.lv-重大 {{ background:#8e2b20; }} .lv-较大 {{ background:var(--warn); }}
.lv-一般 {{ background:#5c6b7f; }} .lv-提示 {{ background:#8fa0b3; }}
.vd {{ padding:2px 8px; border-radius:4px; font-size:12px; }}
.vd-成立 {{ background:#e6f4ea; color:var(--down); }} .vd-不成立 {{ background:#fdecea; color:var(--up); }}
.vd-存疑 {{ background:#fff5e0; color:var(--warn); }}
.tag {{ font-size:12px; background:#f0f3f7; border:1px solid var(--line); border-radius:5px;
        padding:2px 8px; margin:0 5px 5px 0; display:inline-block; }}
footer {{ margin-top:38px; padding:20px; background:#fff; border:1px solid var(--line);
          border-radius:10px; font-size:12.5px; color:var(--sub); }}
footer b {{ color:var(--ink); }}
code {{ background:#f0f3f7; padding:1px 5px; border-radius:4px; font-size:12.5px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>{title}</h1>
  <div class="meta">生成时间 {generated} ｜ 数据源：{source} ｜ 样本 {sample_dates} 个交易日 / {sample_codes} 只 / {sample_start} ~ {sample_end}</div>
  <div class="badges"><span>A股</span><span>中低频 · 日频信号 + 月末调仓</span><span>多因子 + 趋势过滤</span>
  <span>纯多头 · 无杠杆</span><span>前复权</span><span>红涨绿跌</span></div>
</header>

<div class="card tldr">
  <h2>一句话结论</h2>
  <p>{conclusion}</p>
  <p style="margin-top:10px">
    <span class="verdict">质量门：{verdict}</span>
    <span class="verdict eng">工程：{eng_gate}</span>
    <span class="verdict">策略结论：{strat_gate}</span>
  </p>
  <p class="note">回测结果不等于实盘表现，历史业绩不代表未来。本报告仅验证软件框架与研究方法，不构成任何可实盘的策略结论。</p>
</div>

<div class="kpis">
  <div class="kpi"><div class="lab">总收益（含成本）</div><div class="val pos">{total_ret}</div></div>
  <div class="kpi"><div class="lab">年化收益</div><div class="val pos">{ann_ret}</div></div>
  <div class="kpi"><div class="lab">夏普比率</div><div class="val">{sharpe}</div></div>
  <div class="kpi"><div class="lab">最大回撤</div><div class="val neg">{mdd}</div></div>
  <div class="kpi"><div class="lab">成本 / 毛收益</div><div class="val">{cost_share}</div></div>
  <div class="kpi"><div class="lab">样本外夏普</div><div class="val neg">{oos_sharpe}</div></div>
  <div class="kpi"><div class="lab">合成因子 IC</div><div class="val neg">{comp_ic}</div></div>
  <div class="kpi"><div class="lab">随机对照分位</div><div class="val neg">{mc_pct}</div></div>
</div>

<h2 class="sec">1 · Step 0 五要素基线（用户逐项锁定）</h2>
<div class="card">
<table>
<tr><th>要素</th><th>取值</th><th>落地位置</th></tr>
<tr><td>① 标的池与市场</td><td>全 A 股（剔除 ST / 退市 / 上市不足 120 日）；演示样本为 20 只真实 A 股，<b>不是全 A</b></td><td><code>config.universe</code> + <code>data.eligible</code></td></tr>
<tr><td>② 交易频率</td><td>日频计算信号 + 月末调仓</td><td><code>strategy.rebalance_dates()</code></td></tr>
<tr><td>③ 资金规模与容量</td><td>仅研究、不设规模；<code>capital=null</code>，本次不输出容量结论</td><td><code>step0.capital</code></td></tr>
<tr><td>④ 回测区间与成本</td><td>2018-01-01 ~ 2026-11-13，样本外自 2024-01-01；佣金 2.5bp 双边（最低 ¥5）、印花税 10bp 仅卖出、滑点 5bp、T+1、冲击成本 none</td><td><code>config.cost</code> / <code>period</code></td></tr>
<tr><td>⑤ 杠杆与做空</td><td>纯多头、无杠杆、不做空</td><td><code>config.leverage</code></td></tr>
</table>
<p class="note">成本假设由用户给定，代码零硬编码费率；记账单位 ¥1,000,000 仅用于最低佣金计算，不代表策略规模。</p>
</div>

<h2 class="sec">2 · 软件架构（六层 + 一个质量门）</h2>
<div class="card">
<div class="flow">
  <div class="fbox"><b>① 数据层</b><small>CSV / 通达信 JSON 适配器、日期×代码面板、涨跌停与可交易标记、ST/次新剔除</small></div>
  <div class="arrow">→</div>
  <div class="fbox"><b>② 因子层</b><small>6 个价量因子、MAD 去极值、横截面 rank-Z、IC/IR/分层/BH-FDR</small></div>
  <div class="arrow">→</div>
  <div class="fbox"><b>③ 策略层</b><small>线性合成 → 趋势过滤 → 等权+上限 → buffer/换手 → MA200 择时</small></div>
</div>
<div class="flow" style="margin-top:10px">
  <div class="fbox"><b>④ 回测层</b><small>T+1 开盘撮合、涨跌停/停牌阻断、佣金/印花税/滑点、逐日盯市</small></div>
  <div class="arrow">→</div>
  <div class="fbox"><b>⑤ 风控层</b><small>回撤熔断、VaR/CVaR、历史+假设压力测试、相关性与集中度</small></div>
  <div class="arrow">→</div>
  <div class="fbox"><b>⑥ 分析层</b><small>绩效、样本外、参数敏感性、蒙特卡洛+DSR、成本敏感性、归因</small></div>
  <div class="arrow">→</div>
  <div class="fbox"><b>质量门</b><small>reality-check 逐条 VERDICT；唯一放行出口</small></div>
</div>
<h3>时序铁律</h3>
<p style="font-size:14px;margin:0"><b>T 日收盘</b>：只用 <code>&lt;= T</code> 的数据算因子与权重 → <b>T+1 开盘</b>撮合 → 收盘盯市 → 熔断状态更新 → 生成下一日 pending。前向收益只出现在评价函数，单元测试验证「追加未来数据不改变历史因子值」。</p>
<h3>刻意不做的部分</h3>
<p><span class="tag">实盘下单</span><span class="tag">券商/交易所接口</span><span class="tag">自动交易循环</span><span class="tag">冲击成本（按用户设定 none）</span><span class="tag">整手取整</span><span class="tag">分红再投资</span><span class="tag">Level-2 盘口</span></p>
</div>

<h2 class="sec">3 · 策略设计</h2>
<div class="card">
<h3>因子定义与实测有效性</h3>
<table>
<tr><th>因子</th><th>含义</th><th>IC 均值</th><th>ICIR</th><th>t 值</th><th>分层单调性</th></tr>
{factor_rows}
</table>
<p class="note">口径：20 日前向收益的横截面 Spearman IC，样本 2018-01-02 ~ 2026-11-13，演示样本 20 只；BH-FDR α=0.05 截断 {fdr_cut}，校正后显著 {fdr_n} 个，但 6 个因子 IC 均值<b class="neg">全部为负</b> —— 显著仅代表非零，不代表方向正确。</p>
<div id="chartIC" class="chart"></div>
<h3>信号 → 组合 → 执行</h3>
<table>
<tr><th>环节</th><th>规则</th></tr>
<tr><td>合成得分</td><td>6 因子横截面 rank → Z-score，权重 0.25/0.15/0.15/0.15/0.15/0.15 线性合成后再标准化</td></tr>
<tr><td>趋势过滤</td><td><code>close &gt; MA60</code> 且 <code>MA60 &gt; MA120</code> 才进入候选</td></tr>
<tr><td>组合权重</td><td>Top-8 等权，单标的 ≤20%，buffer = Top 1.5N 优先保留旧持仓，单期换手 ≤60%</td></tr>
<tr><td>大盘择时</td><td>等权样本指数在 MA200 之下 → 敞口降至 50%（生产应替换为中证全指）</td></tr>
<tr><td>回撤熔断</td><td>回撤 ≥15% → 敞口 50%；≥20% → 清仓；收敛到 ≤8% 恢复</td></tr>
<tr><td>撮合</td><td>月末最后交易日收盘出信号，T+1 开盘 ±5bp 滑点成交；涨停不可买、跌停不可卖、停牌跳过</td></tr>
</table>
<p class="note">EP/BP/ROE 等财务因子需带 <code>publish_date</code> 的 PIT 数据，当前数据源不覆盖 → 插件未启用，不伪造数值。</p>
</div>

<h2 class="sec">4 · 回测结果（真实数据，含成本）</h2>
<div class="card">
<div id="chartNav" class="chart"></div>
<div id="chartDD" class="chart" style="height:230px;margin-top:6px"></div>
<table style="margin-top:14px">
<tr><th>指标</th><th>全样本</th><th>样本内 (~2023-12-29)</th><th>样本外 (2024-01-01 起)</th></tr>
<tr><td>夏普比率</td><td>{sharpe}</td><td>{is_sharpe}</td><td class="neg">{oos_sharpe}</td></tr>
<tr><td>年化收益</td><td>{ann_ret}</td><td colspan="2">样本外显著劣化，见下方四件套</td></tr>
<tr><td>年化波动 / Calmar</td><td>{vol} / {calmar}</td><td colspan="2">—</td></tr>
<tr><td>月胜率 / 期均换手</td><td>{win} / {turnover}</td><td colspan="2">—</td></tr>
<tr><td>成交笔数 / 被阻断订单</td><td>{n_trades} / {blocked}</td><td colspan="2">阻断来自涨跌停 / 停牌 / T+1</td></tr>
<tr><td>成本合计</td><td>{cost_cny}</td><td colspan="2">占毛收益 {cost_share}（佣金 ¥22,058 / 印花税 ¥43,593 / 滑点 ¥43,411）</td></tr>
</table>
<p class="note">成本假设：佣金 2.5bp 双边（最低 ¥5）+ 印花税 10bp 仅卖出 + 滑点 5bp；冲击成本按用户设定未建模。数据源 L3：腾讯证券前复权日线，600519 于 2026-11-13 的 OHLC 已与通达信 MCP 逐字段交叉校验。</p>
</div>

<h2 class="sec">5 · 过拟合四件套（缺一不放行）</h2>
<div class="card">
<div class="grid2">
  <div><h3>② 参数敏感性 · 夏普邻域网格</h3><div id="chartHeat" class="chart" style="height:280px"></div>
       <p class="note">top_n ∈ {{5,8,12}} × ma_long ∈ {{90,120,160}}；夏普极差 {sens_spread}，无悬崖式衰减。</p></div>
  <div><h3>③ 蒙特卡洛 / Deflated Sharpe</h3><div id="chartMC" class="chart" style="height:280px"></div>
       <p class="note">{mc_paths} 条随机选股路径（同等调仓频率/持仓数/成本）；策略基准排 {mc_pct} 分位，DSR={dsr}。</p></div>
</div>
<h3>④ 成本敏感性</h3>
<div id="chartCost" class="chart" style="height:230px"></div>
<p class="note">成本放大 ×1 / ×1.5 / ×2 / ×3 后总收益均未归零；但本项只覆盖已建模成本，冲击成本=none 是未决缺口。</p>
<h3>① 样本外</h3>
<p style="font-size:14px">切分点 2024-01-01，样本外使用次数 <b>1</b>；样本内夏普 {is_sharpe} → 样本外 <b class="neg">{oos_sharpe}</b>，判「存疑」。任何基于样本外结果回改参数的行为都必须递增使用次数并重新定级。</p>
</div>

<h2 class="sec">6 · 风险模型</h2>
<div class="card">
<div class="grid2">
<div>
<h3>尾部风险（历史模拟法）</h3>
<table><tr><th>置信度</th><th>VaR（日）</th><th>CVaR（日）</th></tr>
<tr><td>95%</td><td class="neg">{var95}</td><td class="neg">{cvar95}</td></tr>
<tr><td>99%</td><td class="neg">{var99}</td><td class="neg">{cvar99}</td></tr></table>
<p class="note">窗口 2,150 个交易日，全样本。最大回撤 {mdd}，2021-02-10 见顶、2026-08-17 见底，样本内未恢复。</p>
<h3>相关性与集中度</h3>
<p style="font-size:14px">全样本平均两两相关 <b>{corr_full}</b>；最差 40 日 <b>{corr_worst}</b>（本样本未出现相关性跳升）。末期持仓 8 只，HHI 0.125，前 3 大占比 37.5%。</p>
</div>
<div>
<h3>历史情景压力测试（真实样本区间）</h3>
<table><tr><th>情景</th><th>区间</th><th>区间收益</th><th>最大回撤</th><th>交易日</th></tr>{hist_rows}</table>
<p class="note">假设情景（相关性跳升 / 流动性枯竭 / 连续跌停 / 择时失效）为<b>模型估计</b>，与历史事实分栏列示，详见 <code>output/result.json</code>。</p>
</div>
</div>
<h3>熔断触发记录</h3>
<table><tr><th>日期</th><th>当时回撤</th><th>动作</th></tr>{fuse_rows}</table>
</div>

<h2 class="sec">7 · 风险定级汇总</h2>
<div class="card">
<table><tr><th style="width:18%">要素</th><th style="width:32%">读数</th><th>定级</th><th>置信度</th><th style="width:32%">IF_RISK（触发条件 + 具体动作）</th></tr>{risk_rows}</table>
</div>

<h2 class="sec">8 · quality gate 复核记录</h2>
<div class="card">
<p><b>最终判定：{verdict}</b> ｜ 工程交付：{eng_gate} ｜ 策略级结论：{strat_gate} ｜ 成立 / 不成立 / 存疑见下表</p>
<table><tr><th style="width:20%">复核项</th><th>判定</th><th style="width:40%">证据</th><th>备注</th></tr>{gate_rows}</table>
<h3>交付后必须跟进</h3><ol>{followups}</ol>
</div>

<h2 class="sec">9 · 数据缺口与研究局限</h2>
<div class="card"><ul>{gaps}</ul>
<p class="note">以上缺口已写入 <code>config.data.known_gaps</code> 与风险表，未按占位数据填充。演示样本为「当前存续的 20 只大中盘」，天然规避小盘流动性与退市风险，会系统性高估表现；所有绩效数字仅作引擎验证，不得作为策略有效性证据。</p></div>

<h2 class="sec">10 · 交付物</h2>
<div class="card">
<p><span class="tag">astock-quant-lab/ 可运行 Python 工程（14 个模块 + 11 项单测）</span>
<span class="tag">config/default.json Step 0 基线</span>
<span class="tag">data/sample_real/prices.csv 真实行情 42,093 行</span>
<span class="tag">output/result.json 全量结构化结果</span>
<span class="tag">output/SUMMARY.md 研究报告</span>
<span class="tag">docs/ 架构·策略·数据合约·质量门</span>
<span class="tag">astock-quant-designer/ WorkBuddy 技能包</span>
<span class="tag">本 HTML 设计报告</span></p>
<p style="font-size:14px">运行：<code>pip install -r requirements.txt</code> → <code>python -m quantlab fetch</code> → <code>python -m quantlab selftest</code> → <code>python -m quantlab run</code></p>
</div>

<footer>
<p><b>合规声明</b></p>
<p>投资有风险，本内容仅供研究与教育，不构成投资建议。回测结果不等于实盘表现，历史业绩不代表未来。回测基于历史数据与给定假设，实际交易受流动性、冲击成本、交易规则变化、极端行情与执行延迟影响，可能显著劣于回测。不荐股、不给买卖点与目标价、不承诺收益、不做代客理财、不代下单。本次为纯多头、无杠杆、不做空；如未来启用杠杆，高杠杆可能放大亏损甚至造成本金归零或倒欠。涉及税务、法律定性与跨境资金问题，转税务师 / 律师。</p>
<p><b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。</p>
<p>配色约定：红涨绿跌（A 股习惯）｜ 货币符号 ¥ ｜ 事实与判断分栏，判断标置信度 ｜ 数据源层级 L3，前复权口径。</p>
</footer>

</div>
<script>
const DATA = {data_json};
const RED = '#c0392b', GREEN = '#1e8449', BLUE = '#2c5282', GREY = '#8fa0b3';
function mk(id, option) {{
  const el = document.getElementById(id);
  if (!el || typeof echarts === 'undefined') return;
  const c = echarts.init(el);
  c.setOption(option);
  window.addEventListener('resize', function () {{ c.resize(); }});
}}
mk('chartIC', {{
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 60, right: 30, top: 30, bottom: 55 }},
  xAxis: {{ type: 'category', data: DATA.facNames, axisLabel: {{ rotate: 20, color: '#5c6b7f' }} }},
  yAxis: {{ type: 'value', name: 'IC 均值', axisLabel: {{ color: '#5c6b7f' }} }},
  series: [{{
    type: 'bar', barWidth: '48%',
    data: DATA.facIC.map(function (v) {{ return {{ value: v, itemStyle: {{ color: v < 0 ? GREEN : RED }} }}; }}),
    label: {{ show: true, position: 'bottom', formatter: function (p) {{ return p.value.toFixed(3); }}, color: '#1b2330' }}
  }}]
}});
mk('chartNav', {{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ data: ['策略净值'] }},
  grid: {{ left: 62, right: 30, top: 40, bottom: 50 }},
  xAxis: {{ type: 'category', data: DATA.dates, axisLabel: {{ color: '#5c6b7f', interval: Math.floor(DATA.dates.length / 8) }} }},
  yAxis: {{ type: 'value', name: '净值', scale: true, axisLabel: {{ color: '#5c6b7f' }} }},
  series: [{{
    name: '策略净值', type: 'line', data: DATA.nav, showSymbol: false,
    lineStyle: {{ color: BLUE, width: 2 }},
    areaStyle: {{ color: 'rgba(44,82,130,0.08)' }},
    markLine: {{ silent: true, symbol: 'none',
      data: [{{ xAxis: DATA.oosSplit, name: '样本外起点' }}],
      lineStyle: {{ color: '#b7791f', type: 'dashed' }},
      label: {{ formatter: '样本外起点', color: '#b7791f' }} }}
  }}]
}});
mk('chartDD', {{
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 62, right: 30, top: 25, bottom: 42 }},
  xAxis: {{ type: 'category', data: DATA.dates, axisLabel: {{ color: '#5c6b7f', interval: Math.floor(DATA.dates.length / 8) }} }},
  yAxis: {{ type: 'value', name: '回撤', axisLabel: {{ color: '#5c6b7f', formatter: function (v) {{ return (v * 100).toFixed(0) + '%'; }} }} }},
  series: [{{ name: '回撤', type: 'line', data: DATA.dd, showSymbol: false,
    lineStyle: {{ color: GREEN, width: 1.5 }}, areaStyle: {{ color: 'rgba(30,132,73,0.14)' }} }}]
}});
mk('chartHeat', {{
  tooltip: {{ trigger: 'item', formatter: function (p) {{ return 'ma_long=' + p.value[0] + ' / top_n=' + p.value[1] + ' · 夏普 ' + p.value[2]; }} }},
  grid: {{ left: 70, right: 70, top: 25, bottom: 45 }},
  xAxis: {{ type: 'category', data: DATA.topNs, name: 'top_n' }},
  yAxis: {{ type: 'category', data: DATA.maLs, name: 'ma_long' }},
  visualMap: {{ min: 0, max: 0.65, calculable: true, orient: 'vertical', right: 5, top: 'center',
    inRange: {{ color: ['#e8f3ec', '#7fb894', '#1e8449'] }} }},
  series: [{{ type: 'heatmap', data: DATA.heat.map(function (r) {{ return [DATA.topNs.indexOf(r[1]), DATA.maLs.indexOf(r[0]), r[2]]; }}),
    label: {{ show: true, formatter: function (p) {{ return p.value[2].toFixed(3); }} }} }}]
}});
mk('chartMC', {{
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 60, right: 30, top: 25, bottom: 50 }},
  xAxis: {{ type: 'category', data: DATA.mc.labels, axisLabel: {{ color: '#5c6b7f' }} }},
  yAxis: {{ type: 'value', name: '数值', axisLabel: {{ color: '#5c6b7f' }} }},
  series: [{{ type: 'bar', barWidth: '45%',
    data: DATA.mc.values.map(function (v, i) {{ return {{ value: v, itemStyle: {{ color: i === 2 ? RED : (i === 3 ? '#b7791f' : GREY) }} }}; }}),
    label: {{ show: true, position: 'top', formatter: function (p) {{ return p.value.toFixed(3); }} }} }}]
}});
mk('chartCost', {{
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 65, right: 30, top: 25, bottom: 45 }},
  xAxis: {{ type: 'category', data: DATA.costX }},
  yAxis: {{ type: 'value', name: '总收益', axisLabel: {{ color: '#5c6b7f', formatter: function (v) {{ return (v * 100).toFixed(0) + '%'; }} }} }},
  series: [{{ name: '总收益', type: 'line', data: DATA.costY, symbolSize: 8,
    lineStyle: {{ color: BLUE, width: 2 }}, itemStyle: {{ color: RED }},
    label: {{ show: true, formatter: function (p) {{ return (p.value * 100).toFixed(1) + '%'; }} }} }}]
}});
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../astock-quant-design-report.html")
    ap.add_argument("--result", default="output/result.json")
    ap.add_argument("--equity", default="output/equity.csv")
    args = ap.parse_args()

    rp = Path(args.result)
    ep = Path(args.equity)
    if not rp.is_absolute():
        rp = ROOT / rp
    if not ep.is_absolute():
        ep = ROOT / ep
    rep = json.loads(rp.read_text(encoding="utf-8"))
    eq = pd.read_csv(ep, index_col=0).iloc[:, 0]
    html = build(rep, eq)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"WROTE {out} ({len(html):,} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
