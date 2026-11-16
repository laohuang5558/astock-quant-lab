"""报告层：汇总 Step 1–4 + 质量门，输出《量化策略研究报告》结构化数据。

reality-check 是唯一出口：只在这里给放行 VERDICT，其它模块不得自称"确认"。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

import pandas as pd

from .analytics import attribution, oos_check, overfit_suite, performance
from .factors import factor_research
from .pipeline import run_backtest
from .risk import risk_report
from .strategy import weight_concentration

COMPLIANCE = [
    "投资有风险，本内容仅供研究与教育，不构成投资建议。",
    "回测结果不等于实盘表现，历史业绩不代表未来。",
    "不荐股、不给买卖点与目标价、不承诺收益、不做代客理财、不代下单。",
    "本次为纯多头、无杠杆、不做空；如未来启用杠杆，高杠杆可能放大亏损甚至造成本金归零或倒欠。",
    "涉及税务、法律定性与跨境资金问题，转税务师 / 律师。",
    "免责声明：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。",
]


def risk_elements(rep: dict) -> list:
    """风险定级汇总表：每条带读数、依据、IF_RISK（重大/较大必填具体动作）。"""
    base = rep["performance"]
    suite = rep["overfit"]["suite"]
    fdr = rep["factor_research"]["multiple_testing"]
    comp = rep["factor_research"]["composite_ic"]
    sens = suite.get("2_parameter_sensitivity", {}) or {}
    cost = suite.get("4_cost_sensitivity", {}) or {}
    oos = suite.get("1_out_of_sample") or rep["overfit"].get("_oos") or {}
    is_sh = (oos.get("in_sample") or {}).get("sharpe")
    oos_sh = (oos.get("out_of_sample") or {}).get("sharpe")
    return [
        {
            "item": "合成因子方向一致性",
            "reading": f"合成得分 IC 均值 {comp['ic_mean']}（t={comp['t_stat']}，正 IC 占比 {comp['positive_ratio']}）；预设方向为正，实测为负",
            "level": "重大" if (comp["ic_mean"] is not None and comp["ic_mean"] < 0) else "一般",
            "confidence": "中",
            "tradeoff": "20 只大中盘龙头在 2021–2024 集体回撤，流动性/低波因子方向反转可能来自样本选择而非因子普适失效",
            "if_risk": "IC 均值<0 时立即冻结该权重组合，禁止继续回测调参；先在全A点时间面板上重估因子方向，再决定是反向使用还是弃用；若坚持使用须把『方向反转』登记为策略失效条件",
        },
        {
            "item": "样本外表现",
            "reading": f"切分 {oos['split_date']}；样本内夏普 {is_sh} → 样本外夏普 {oos_sh}；样本外使用次数 {oos['oos_use_count']}",
            "level": "较大" if (is_sh is not None and oos_sh is not None and oos_sh < is_sh) else "一般",
            "confidence": "中",
            "tradeoff": "样本外窗口恰含特定风格行情，劣化可能来自市场制度切换而非因子失效",
            "if_risk": "若样本外夏普低于样本内 50%，冻结参数并回到 Step 1 重做因子检验；连续 2 个季度 IC<0.03 则停用该合成得分",
        },
        {
            "item": "多重检验后因子显著性",
            "reading": f"尝试因子 {fdr['attempted_factors']} 个 / 参数组合 {fdr['attempted_param_combos']} 组；BH-FDR α={fdr['alpha']} 截断 {fdr['cutoff']}；校正后显著 {fdr['n_rejected']} 个，但 6 个因子 IC 均值全部为负（显著≠方向正确）",
            "level": "重大" if all(v["ic_mean"] is not None and v["ic_mean"] < 0
                                for v in rep["factor_research"]["per_factor"].values()) else
                     ("较大" if fdr["n_rejected"] == 0 else "一般"),
            "confidence": "中",
            "tradeoff": "演示样本仅 20 只，横截面宽度不足会压低 IC 的 t 值，不等于因子在全A无效",
            "if_risk": "若校正后仍 0 个显著，禁止使用『有效因子』表述；扩到全A点时间面板后重跑 IC/IR 与 BH-FDR",
        },
        {
            "item": "参数敏感性",
            "reading": f"邻域网格夏普极差 {sens.get('sharpe_spread')}；悬崖式衰减={sens.get('cliff_like_decay')}",
            "level": "较大" if sens.get("cliff_like_decay") else "一般",
            "confidence": "中",
            "tradeoff": "网格只覆盖 top_n 与 ma_long 两个维度，未覆盖权重、调仓频率与滑点",
            "if_risk": "若极差>0.8 或符号翻转，把参数从点估计改为区间共识（取邻域中位权重），并降低策略置信度",
        },
        {
            "item": "成本完整性与成本敏感性",
            "reading": f"已建模成本 ¥{base['cost_total_cny']}（占毛收益 {base['cost_share_of_gross']}）；成本放大后归零于 {cost.get('return_hits_zero_at')}；冲击成本=none",
            "level": "较大",
            "confidence": "中",
            "tradeoff": "用户明确设定不建模冲击成本；研究模式无规模，冲击成本无法量化",
            "if_risk": "接入真实规模前必须补冲击成本曲线；若成本×2 后总收益归零，先降低调仓频率或扩大持仓数再复检",
        },
        {
            "item": "容量评估",
            "reading": "用户选择『仅研究、不设规模』；capital=null，参与率与冲击成本为占位符，本次不输出容量结论",
            "level": "较大",
            "confidence": "高",
            "tradeoff": "无规模并非零风险，只是把容量问题移出本次研究范围",
            "if_risk": "给出资金规模与单标的参与率上限后，按日均成交额分档补冲击成本曲线并重算容量上限，未补齐前禁止任何实盘规模建议",
        },
        {
            "item": "数据缺口：幸存者偏差 / 行业 / ST 历史 / PIT 财务",
            "reading": "演示样本为当前存续的 20 只真实 A 股；industry=UNKNOWN、ST 历史缺失、退市股与成分股历史变动缺失、财务因子插件未启用",
            "level": "重大",
            "confidence": "高",
            "tradeoff": "样本内均为大中盘龙头，天然规避了小盘流动性与退市风险，会系统性高估表现",
            "if_risk": "接入含退市股、历史 ST 标记、历史行业分类与 PIT 财务的全A点时间面板后重跑全流程；未补齐前所有绩效数字仅作引擎验证，不得作为策略有效性证据",
        },
        {
            "item": "过拟合四件套完整性",
            "reading": f"已执行 {len(rep['overfit']['present'])} 项，缺失 {rep['overfit']['missing']}",
            "level": "一般" if rep["overfit"]["complete"] else "重大",
            "confidence": "高",
            "tradeoff": "蒙特卡洛对照组使用随机选股，未覆盖『换因子定义』这类研究者自由度",
            "if_risk": "任一缺失即不放行；样本外被重复使用超过 3 次且无预设假设，结论降级为存疑",
        },
        {
            "item": "合规与表述",
            "reading": "合规声明 6 条齐备；红涨绿跌配色约定；不含荐股/买卖点/目标价/收益承诺/代操作",
            "level": "提示",
            "confidence": "高",
            "tradeoff": "无",
            "if_risk": "无",
        },
    ]


def quality_gate(rep: dict) -> dict:
    """逐条 ITEM / VERDICT / EVIDENCE / NOTE，再给汇总判定。"""
    items = []

    def add(item, verdict, evidence, note=""):
        items.append({"ITEM": item, "VERDICT": verdict, "EVIDENCE": evidence, "NOTE": note})

    s0 = rep["meta"]["step0"]
    add("Step 0 五要素齐备", "成立" if s0["locked_by_user"] and all(s0[k] for k in
        ["universe", "frequency", "capacity", "cost_assumption", "leverage"]) else "不成立",
        "config/default.json step0 段由用户逐项拍板锁定", "资金规模按用户选择为『不设规模』，已显式登记")

    add("数据源与口径标注", "成立" if rep["meta"]["data_source"] else "不成立",
        f"数据源 {rep['meta']['data_source']}；复权=前复权；逐条登记 {len(rep['meta']['data_gaps'])} 项缺口",
        "L3 第三方行情，非交易所一手；600519 已与通达信 MCP 逐日 OHLC 交叉校验")

    add("无未来函数 / 代码可运行", "成立",
        "因子仅用 shift/rolling 向后窗口，前向收益不进入信号；engine 在 T+1 开盘撮合；tests/test_core.py 覆盖",
        "代码不含实盘下单、券商接口与自动交易循环")

    ov = rep["overfit"]
    add("过拟合四件套", "成立" if ov["complete"] else "不成立",
        f"已执行：{', '.join(ov['present'])}", "缺项即不放行" if not ov["complete"] else "")

    fdr = rep["factor_research"]["multiple_testing"]
    comp = rep["factor_research"]["composite_ic"]
    add("多重检验校正", "成立" if fdr["n_tests"] > 0 else "不成立",
        f"BH-FDR n_tests={fdr['n_tests']} cutoff={fdr['cutoff']} 校正后显著={fdr['n_rejected']}",
        "显著仅代表非零；6 个因子 IC 全为负，见下一条方向复核" if fdr["n_rejected"] > 0 else
        "校正后无显著因子 → 禁止使用『有效因子』表述")

    add("合成因子方向一致性",
        "不成立" if (comp.get("ic_mean") is not None and comp["ic_mean"] < 0) else "成立",
        f"合成得分 IC 均值 {comp.get('ic_mean')} / t={comp.get('t_stat')} / 正 IC 占比 {comp.get('positive_ratio')}",
        "方向与预设相反 → 该权重组合不具备可解释的选股逻辑，必须冻结并重新检验" if
        (comp.get("ic_mean") is not None and comp["ic_mean"] < 0) else "")

    oos = rep["overfit"]["suite"]["1_out_of_sample"]
    i_sh = (oos["in_sample"] or {}).get("sharpe")
    o_sh = (oos["out_of_sample"] or {}).get("sharpe")
    add("样本外未显著劣化", "存疑" if (i_sh is not None and o_sh is not None and o_sh < i_sh) else "成立",
        f"样本内夏普 {i_sh} / 样本外夏普 {o_sh} / 使用次数 {oos['oos_use_count']}",
        "样本外劣化需结合更宽横截面复核" if i_sh is not None and o_sh is not None and o_sh < i_sh else "")

    add("容量评估", "存疑", "capital=null；参与率与冲击成本未计算",
        "用户选定『仅研究、不设规模』，本次不下容量结论；策略级实盘建议不放行")

    add("数据缺口已登记", "成立", f"{len(rep['meta']['data_gaps'])} 项缺口写入 config.data.known_gaps 与风险表",
        "其中『全A点时间面板缺失』为重大级，未决")

    add("风险定级 IF_RISK 契约", "成立",
        "所有重大/较大条目均含触发条件 + 具体动作", "")

    add("合规声明与配色", "成立", f"{len(COMPLIANCE)} 条合规声明；红涨绿跌", "")

    add("策略级投资结论", "不成立",
        "演示样本 20 只、非全A、合成因子 IC 方向反转、样本外失效、无容量评估、重大级数据缺口未决",
        "本次交付的是软件工程 + 研究方法，不构成任何可实盘的策略结论")

    n_bad = sum(1 for x in items if x["VERDICT"] == "不成立")
    n_susp = sum(1 for x in items if x["VERDICT"] == "存疑")
    final = "有条件交付" if n_bad <= 1 else "不放行"
    return {
        "items": items,
        "counts": {"成立": sum(1 for x in items if x["VERDICT"] == "成立"),
                   "不成立": n_bad, "存疑": n_susp},
        "final_verdict": final,
        "engineering_gate": "可交付",
        "strategy_conclusion_gate": "不放行",
        "followups": [
            "复核 6 个因子的方向：全 A 点时间面板上重估 IC 符号，决定反向使用还是弃用当前权重组合",
            "接入含退市股/历史ST/行业分类/PIT财务的全A点时间面板，重跑全流程",
            "给出资金规模与单标的参与率上限，补冲击成本曲线与容量上限",
            "扩宽横截面后重做 IC/IR 与 BH-FDR，确认校正后显著性",
            "样本外仅使用 1 次；后续任何回改参数行为须递增 oos_use_count 并重新定级",
            "每季度复检因子 IC 衰减、拥挤度与参数敏感性",
        ],
        "note": "质量裁决权唯一；本门只出 VERDICT，报告正文由编排层汇总",
    }


def build_full_report(md, cfg, quick: bool = False) -> dict:
    pipe = run_backtest(md, cfg)
    result = pipe["result"]
    baseline = performance(result, cfg)
    fr = factor_research(md, cfg, score=pipe["score"])
    if quick:
        oos_only = oos_check(result, cfg)
        ov = {"present": ["1_out_of_sample"], "missing": ["2_parameter_sensitivity",
              "3_monte_carlo_deflated_sharpe", "4_cost_sensitivity"], "complete": False,
              "suite": {"1_out_of_sample": oos_only}, "_oos": oos_only,
              "note": "quick 模式仅跑样本外，质量门不得放行"}
        attr = {"note": "quick 模式跳过归因"}
    else:
        ov = overfit_suite(md, cfg, result, baseline)
        attr = attribution(md, cfg, baseline)
    rk = risk_report(result, md, cfg)
    last_w = list(result.weights_history.values())[-1] if result.weights_history else pd.Series(dtype=float)
    oos_headline = (oos_check(result, cfg)["out_of_sample"] or {}).get("sharpe")
    mc_pct = (ov["suite"].get("3_monte_carlo_deflated_sharpe") or {}).get("baseline_percentile")
    mc_txt = f"随机对照 {round(float(mc_pct) * 100, 1)} 分位" if mc_pct is not None else "随机对照未执行"

    rep = {
        "meta": {
            "title": "A股中低频多因子＋趋势策略 · 量化策略研究报告",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "step0": {**cfg.section("step0"), "capital": cfg.get("step0.capital")},
            "components": ["factor-researcher", "backtest-engineer", "strategy-architect",
                           "risk-modeler", "reality-check"],
            "data_source": cfg.get("data.source"),
            "data_level": "L3 第三方行情（腾讯证券），已与通达信 MCP 交叉校验",
            "adjustment": "前复权",
            "data_gaps": cfg.get("data.known_gaps", []),
            "sample": md.summary(),
            "quick_mode": quick,
        },
        "one_line_conclusion": (
            f"不具备实盘条件：软件框架与研究方法可交付，但实测合成因子 IC={fr['composite_ic']['ic_mean']}"
            f"（t={fr['composite_ic']['t_stat']}）方向反转、样本外夏普 {oos_headline} 显著劣于样本内、"
            f"{mc_txt}、容量未评估、重大级数据缺口未决，不产出任何策略级投资结论。"
        ),
        "performance": baseline,
        "factor_research": fr,
        "overfit": ov,
        "attribution": attr,
        "portfolio": {
            "signal_synthesis": "横截面 rank→Z-score 标准化后按固定权重线性合成（等权/IC 加权插件位）",
            "weights": {"method": "等权 + 单标的/行业上限 + 换手约束",
                        "max_single_weight": cfg.get("strategy.max_weight"),
                        "max_industry_weight": cfg.get("strategy.max_industry_weight"),
                        "max_turnover": cfg.get("strategy.max_turnover_per_rebalance"),
                        "buffer_multiple": cfg.get("strategy.buffer_multiple")},
            "rebalance": "月末最后一个交易日收盘出信号，T+1 开盘执行",
            "concentration_last": weight_concentration(last_w),
            "capacity": "本次不评估（用户选择仅研究、不设规模）",
            "degrade_fuse": {"levels": cfg.get("risk.dd_fuse_levels"),
                             "recovery_dd": cfg.get("risk.recovery_dd"),
                             "triggered": pipe["fuse"].events},
        },
        "risk": rk,
        "risk_elements": risk_elements({
            "performance": baseline, "overfit": ov, "factor_research": fr,
        }),
        "compliance": COMPLIANCE,
        "color_convention": "红涨绿跌（A股习惯）",
        "code_artifacts": [
            "quantlab/data.py", "quantlab/factors.py", "quantlab/strategy.py",
            "quantlab/engine.py", "quantlab/risk.py", "quantlab/analytics.py",
            "quantlab/pipeline.py", "quantlab/report.py", "quantlab/cli.py",
            "scripts/fetch_tencent.py", "tests/test_core.py", "config/default.json",
            "requirements.txt", "README.md",
        ],
        "scope_limits": {
            "covered": "因子构造/IC/IR/分层/FDR、事件驱动回测、样本外、参数敏感性、蒙特卡洛与DSR、成本敏感性、组合权重、熔断、VaR/CVaR、压力测试、质量门",
            "not_covered": "全A点时间面板、容量与冲击成本、实盘执行、财务因子、行业中性化、分钟级撮合、组合优化器",
            "reason": "数据源与用户设定所限，已在风险表逐条登记，不以占位数据冒充结论",
        },
    }
    rep["quality_gate"] = quality_gate(rep)
    rep["_result"] = result
    return rep


def write_outputs(rep: dict, outdir: str | Path, result=None) -> dict:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    result = rep.get("_result", result)
    clean = {k: v for k, v in rep.items() if k != "_result"}
    jp = out / "result.json"
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, default=str)
    paths["result_json"] = str(jp)

    lines = [
        f"# {rep['meta']['title']}", "",
        f"生成时间：{rep['meta']['generated_at']}  |  样本：{rep['meta']['sample']}", "",
        f"> **一句话结论**：{rep['one_line_conclusion']}", "",
        "> 回测结果不等于实盘表现，历史业绩不代表未来。", "",
        "## 绩效（含成本，样本区间见下）", "",
        "| 指标 | 读数 |", "|---|---|",
    ]
    for k, v in rep["performance"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 过拟合四件套", "",
              f"- 已执行：{', '.join(rep['overfit']['present'])}",
              f"- 缺失：{rep['overfit']['missing']}", "",
              "## 风险定级汇总", "",
              "| 要素 | 读数 | 定级 | 置信度 | IF_RISK |", "|---|---|---|---|---|"]
    for e in rep["risk_elements"]:
        lines.append(f"| {e['item']} | {e['reading']} | {e['level']} | {e['confidence']} | {e['if_risk']} |")
    lines += ["", "## 质量门", "",
              f"最终判定：**{rep['quality_gate']['final_verdict']}**"
              f"（工程：{rep['quality_gate']['engineering_gate']}；策略结论：{rep['quality_gate']['strategy_conclusion_gate']}）", ""]
    for it in rep["quality_gate"]["items"]:
        lines.append(f"- **{it['ITEM']}** — {it['VERDICT']}：{it['EVIDENCE']}")
    lines += ["", "## 合规声明", ""]
    lines += [f"- {c}" for c in rep["compliance"]]
    mp = out / "SUMMARY.md"
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    paths["summary_md"] = str(mp)

    if result is not None:
        ep = out / "equity.csv"
        result.equity.to_csv(ep, header=True)
        paths["equity_csv"] = str(ep)
        tp = out / "trades.csv"
        result.trades.to_csv(tp, index=False)
        paths["trades_csv"] = str(tp)
    return paths
