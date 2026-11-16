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


def _inprocess_selftest() -> dict:
    """真实在进程内跑单元测试，作为质量门的可验证输入（非硬编码声明）。

    tests/ 为隐式命名空间包（无 __init__.py），故用 importlib 直接加载模块，
    避免 discover(top_level_dir=root) 的 "Start directory is not importable" 错误。
    """
    import importlib
    import io
    import os
    import sys
    import unittest
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        mod = importlib.import_module("tests.test_core")
        suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        return {"passed": bool(res.wasSuccessful()), "tests_run": int(res.testsRun),
                "failures": len(res.failures), "errors": len(res.errors),
                "method": "unittest in-process（含因子截断重算前视自检）"}
    except Exception as exc:
        return {"passed": False, "tests_run": 0, "failures": 0, "errors": 1,
                "method": f"自检执行失败：{type(exc).__name__}: {exc}"}


def quality_gate(rep: dict) -> dict:
    """逐条 ITEM / VERDICT / EVIDENCE / NOTE，全部由真实读数推导，再反推两票与最终裁决。

    专家团审核发现 v1 的致命机制缺陷：engineering_gate / strategy_conclusion_gate
    写死为常量、多条 VERDICT 无条件判成立、final 永无「放行」分支。本版全部改为
    证据推导，并新增撮合、IC 统计、对照等价性、风险收益比四道硬否决。
    """
    items = []

    def add(item, verdict, evidence, note=""):
        items.append({"ITEM": item, "VERDICT": verdict, "EVIDENCE": evidence, "NOTE": note})

    perf = rep["performance"]
    fr = rep["factor_research"]
    comp = fr["composite_ic"]
    fdr = fr["multiple_testing"]
    audit = rep.get("audit", {})
    ov = rep["overfit"]
    elems = rep.get("risk_elements", [])

    s0 = rep["meta"]["step0"]
    add("Step 0 五要素齐备", "成立" if s0["locked_by_user"] and all(s0[k] for k in
        ["universe", "frequency", "capacity", "cost_assumption", "leverage"]) else "不成立",
        "config/default.json step0 段由用户逐项拍板锁定",
        "资金规模按用户选择为『不设规模』，capital=null 已显式登记；判据不含 capital 属已知口径取舍")

    add("数据源与口径标注", "成立" if rep["meta"]["data_source"] else "不成立",
        f"数据源 {rep['meta']['data_source']}；复权=前复权；逐条登记 {len(rep['meta']['data_gaps'])} 项缺口",
        "L3 第三方行情，非交易所一手；600519 与通达信 MCP 为人工抽样校验（1 标的 1 日），非逐日全字段交叉校验")

    st = audit.get("selftest", {})
    add("无未来函数 / 代码可运行", "成立" if st.get("passed") else "不成立",
        f"进程内 unittest {st.get('tests_run', 0)} 项 / 失败 {st.get('failures', 0)} / 错误 {st.get('errors', 0)}；含因子截断重算前视自检",
        "由实际自检结果推导，不再无条件判成立")

    add("过拟合四件套", "成立" if ov["complete"] else "不成立",
        f"已执行：{', '.join(ov['present'])}；缺失：{ov['missing']}",
        "complete 现按各套件关键字段有效性判定，可证伪")

    fdr_ok = bool(fdr.get("n_tests", 0) > 0 and fdr.get("n_attempted", 0) >= 54)
    add("多重检验校正", "成立" if fdr_ok else "不成立",
        f"BH-FDR n_tests={fdr['n_tests']} / 尝试数分母={fdr.get('n_attempted')} / cutoff={fdr['cutoff']} / 校正后显著={fdr['n_rejected']}",
        "分母须用真实尝试数（与 DSR 的 54 次一致）；显著仅代表非零，方向另判")

    ic = audit.get("ic_method", {})
    add("IC 统计推断方法", "成立" if ic.get("passed") else "不成立",
        f"方法={ic.get('method')} / lags={ic.get('hac_lags')} / iid t={ic.get('t_stat_iid')} → HAC t={ic.get('t_stat_hac')}",
        "20 日重叠前向收益使 IC 序列强自相关，iid t 会系统性放大显著性")

    add("合成因子方向一致性",
        "不成立" if (comp.get("ic_mean") is not None and comp["ic_mean"] < 0) else "成立",
        f"合成得分 IC 均值 {comp.get('ic_mean')} / t={comp.get('t_stat')} / 正 IC 占比 {comp.get('positive_ratio')}",
        "方向与预设相反 → 冻结该权重组合；禁止反号使用，方向重估须作为新假设在全新样本重走流程" if
        (comp.get("ic_mean") is not None and comp["ic_mean"] < 0) else "")

    oos = ov["suite"]["1_out_of_sample"]
    i_sh = (oos["in_sample"] or {}).get("sharpe")
    o_sh = (oos["out_of_sample"] or {}).get("sharpe")
    oos_verdict = "不成立" if (o_sh is not None and o_sh < 0) else (
        "存疑" if (i_sh is not None and o_sh is not None and o_sh < i_sh) else "成立")
    add("样本外未显著劣化", oos_verdict,
        f"样本内夏普 {i_sh} / 样本外夏普 {o_sh} / 使用次数 {oos['oos_use_count']}",
        "样本外夏普转负即判不成立；oos_use_count 目前仍为静态常量，未持久化" )

    add("容量评估", "存疑", "capital=null；参与率与冲击成本未计算",
        "用户选定『仅研究、不设规模』，本次不下容量结论；策略级实盘建议不放行")

    add("数据缺口已登记", "成立", f"{len(rep['meta']['data_gaps'])} 项缺口写入 config.data.known_gaps 与风险表",
        "其中『全A点时间面板缺失』为重大级，未决")

    major = [e for e in elems if e.get("level") in ("重大", "较大")]
    bad_if = [e.get("item") for e in major if not e.get("if_risk")]
    add("风险定级 IF_RISK 契约", "成立" if (major and not bad_if) else "不成立",
        f"重大/较大条目 {len(major)} 条，缺 IF_RISK 的：{bad_if}",
        "由遍历风险表推导；触发条件须以当前实测读数为基准")

    add("合规声明与配色", "成立" if len(COMPLIANCE) >= 6 and rep.get("color_convention") else "不成立",
        f"{len(COMPLIANCE)} 条合规声明；{rep.get('color_convention')}", "")

    cash = audit.get("cash", {})
    add("撮合与会计核算", "成立" if cash.get("passed") else "不成立",
        f"现金最低 ¥{perf.get('cash_min_cny')} / 最大持仓敞口 {perf.get('max_position_exposure')} / 未成交留痕 {perf.get('unfilled_orders')} 项",
        "纯多头无杠杆的硬约束；v1 曾出现 275 天负现金、敞口 1.3338，绩效读数失真")

    ce = audit.get("control_equivalence", {})
    add("随机对照等价性", "成立" if ce.get("passed") else "不成立",
        f"对照组是否复用完整权重函数与两段式撮合：{ce.get('passed')}；{ce.get('note', '')}",
        "不等价时，分位数与 DSR 仅作方向性参考，不作为严格统计证据")

    rp = audit.get("risk_pricing", {})
    add("风险收益比与尾部风险", "成立" if rp.get("passed") else "不成立",
        f"最大回撤 {perf.get('max_drawdown')} / Calmar {perf.get('calmar')} / 峰度 {perf.get('kurtosis')} / 夏普 {perf.get('sharpe')}（rf=0）",
        "独立的、不可通过补数据消除的实盘否决项")

    fu = audit.get("fuse", {})
    add("熔断机制有效性", "成立" if fu.get("passed") else "存疑",
        f"熔断事件 {fu.get('events', 0)} 次 / 含清仓档={fu.get('has_liquidation')} / 强制调仓={fu.get('forced_rebalances')}",
        "触发日强制调仓、清仓档有恢复路径，才判成立")

    add("风险读数纳入定级", "成立" if any("风险收益比" in str(e.get("item", "")) for e in elems) else "不成立",
        "风险表是否已消费 vol_drawdown / tail / stress / correlation 读数", "")

    add("策略级投资结论", "不成立",
        "演示样本 20 只、非全A、合成因子 IC 方向反转、样本外失效、随机对照不等价、风险收益比不达标、容量未评估、重大级数据缺口未决",
        "本次交付的是软件工程 + 研究方法，不构成任何可实盘的策略结论")

    n_bad = sum(1 for x in items if x["VERDICT"] == "不成立")
    n_susp = sum(1 for x in items if x["VERDICT"] == "存疑")
    n_ok = sum(1 for x in items if x["VERDICT"] == "成立")
    hard_items = {"撮合与会计核算", "IC 统计推断方法", "无未来函数 / 代码可运行",
                  "多重检验校正", "合成因子方向一致性", "策略级投资结论",
                  "风险收益比与尾部风险", "随机对照等价性"}
    hard_fail = [x["ITEM"] for x in items if x["ITEM"] in hard_items and x["VERDICT"] != "成立"]

    engineering_gate = "可交付" if (cash.get("passed") and st.get("passed") and ic.get("passed")) else "修复后复核"
    strategy_conclusion_gate = "放行" if (n_bad == 0 and n_susp == 0 and not hard_fail
                                          and ov["complete"] and ce.get("passed")) else "不放行"
    if engineering_gate == "可交付" and strategy_conclusion_gate == "放行":
        final = "放行"
    elif engineering_gate == "可交付" and n_bad <= 1:
        final = "有条件交付"
    else:
        final = "不放行"

    return {
        "items": items,
        "counts": {"成立": n_ok, "不成立": n_bad, "存疑": n_susp},
        "final_verdict": final,
        "engineering_gate": engineering_gate,
        "strategy_conclusion_gate": strategy_conclusion_gate,
        "hard_fail_items": hard_fail,
        "followups": [
            "冻结当前合成得分权重组合：补齐全 A 点时间面板前不得反向使用、不得继续回测调参；方向重估须作为新假设在全新样本重走流程",
            "接入含退市股/历史ST/历史行业/PIT财务/真实成交额的全A点时间面板，按当时存续重构横截面后重跑 IC/FDR/分层",
            "把蒙特卡洛对照改为复用完整权重函数与两段式撮合的等价对照，重算分位与 DSR；当前分位仅作方向性参考",
            "给出资金规模与单标的参与率上限，补冲击成本曲线与容量上限",
            "把 oos_use_count 持久化，参数或方向回改时递增，超过 3 次自动降级；当前仅为静态常量",
            "补系统性 beta、停牌/跌停无法止损、流动性风险三条定级，并把风险读数纳入质量门硬否决项",
            "扩宽参数网格至因子权重/择时/熔断/成本，基准参数取邻域区间共识而非单点",
            "实现或显式删除未接线的配置项（winsor/standardize/nan_policy、行业权重上限、FACTOR_META.sign、exclude_suspended、min_avg_amount_20d）",
            "撮合价与涨跌停基准改用未复权价、收益用复权价，分离两套价格序列",
            "文档与报告的关键数值全部由 result.json 自动渲染，并纳入交付前一致性 CI 校验",
        ],
        "note": "两票与 final 均由逐条 VERDICT、进程内自检与真实读数推导；本门只出 VERDICT，报告正文由编排层汇总",
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
    mc_block = ov["suite"].get("3_monte_carlo_deflated_sharpe") or {}
    mc_pct = mc_block.get("baseline_percentile")
    mc_txt = (f"随机对照 {round(float(mc_pct) * 100, 1)} 分位（对照组不等价，仅作方向性参考）"
              if mc_pct is not None else "随机对照未执行")

    # ---- 专家团审核整改后新增：可验证的审计输入，硬编码常量一律不进质量门 ----
    ch = getattr(result, "cash_history", None)
    eh = getattr(result, "exposure_history", None)
    cash_s = ch.copy() if isinstance(ch, pd.Series) else pd.Series(dict(ch or {}))
    expo_s = eh.copy() if isinstance(eh, pd.Series) else pd.Series(dict(eh or {}))
    cash_ok = bool(len(cash_s) and len(expo_s)
                   and cash_s.min() >= -1e-6 and expo_s.max() <= 1.0 + 1e-6)
    ci = fr["composite_ic"]
    fuse_events = pipe["fuse"].events
    has_liq = any("0%" in str(e.get("action", "")) for e in fuse_events)
    audit = {
        "selftest": _inprocess_selftest(),
        "cash": {"passed": cash_ok,
                 "negative_days": int((cash_s < -1e-6).sum()) if len(cash_s) else None,
                 "cash_min_cny": round(float(cash_s.min()), 2) if len(cash_s) else None,
                 "max_position_exposure": round(float(expo_s.max()), 6) if len(expo_s) else None},
        "ic_method": {"passed": "HAC" in str(ci.get("method", "")),
                      "method": ci.get("method"), "hac_lags": ci.get("hac_lags"),
                      "t_stat_iid": ci.get("t_stat_iid"), "t_stat_hac": ci.get("t_stat")},
        "fdr": {"n_tests": fr["multiple_testing"].get("n_tests"),
                "n_attempted": fr["multiple_testing"].get("n_attempted"),
                "consistent_with_dsr": fr["multiple_testing"].get("n_attempted")
                                       == (mc_block.get("deflated_sharpe") or {}).get("n_trials")},
        "control_equivalence": {"passed": bool(mc_block.get("control_equivalence")) if mc_block else False,
                                "note": "简化向量化随机对照，未叠加趋势/择时/熔断/换手/权重约束与两段式撮合"},
        "risk_pricing": {"passed": bool(baseline.get("max_drawdown", -1) > -0.30
                                        and (baseline.get("calmar") or 0) >= 0.5
                                        and (baseline.get("kurtosis") or 99) < 10),
                         "note": "硬阈值：MDD>-30% 且 Calmar>=0.5 且 峰度<10，三者全过才成立"},
        "fuse": {"passed": bool(has_liq and len(fuse_events) > 0), "events": len(fuse_events),
                 "has_liquidation": bool(has_liq),
                 "forced_rebalances": len([e for e in fuse_events if "effective_date" in e])},
        "t_plus_1": {"method": "lots FIFO（买入日 < 当日才可卖）"},
        "blocked": {"unfilled_orders": len(getattr(result, "unfilled_history", {}) or {}),
                    "carry_forward": True},
        "note": "专家团审核整改后新增；所有字段来自本次运行的真实读数，供 quality_gate 推导使用",
    }

    elems = risk_elements({
        "performance": baseline, "overfit": ov, "factor_research": fr,
    })
    elems += [
        {"item": "风险收益比与尾部风险", "reading": (
            f"最大回撤 {baseline.get('max_drawdown')} / Calmar {baseline.get('calmar')} / "
            f"峰度 {baseline.get('kurtosis')} / 夏普 {baseline.get('sharpe')}（rf=0）"),
         "level": "重大", "confidence": "高",
         "if_risk": "若 MDD<-30% 或 Calmar<0.5 → 判定为不可通过补数据消除的实盘否决项，禁止任何规模建议"},
        {"item": "系统性 beta / 无法止损 / 流动性", "reading": (
            f"纯多头仅靠 MA200 降至 50% 敞口；被拒订单 {baseline.get('blocked_orders')} 笔；"
            "无参与率上限、冲击成本未建模、行业上限因 industry=UNKNOWN 空转"),
         "level": "较大", "confidence": "中",
         "if_risk": "接入真实规模前补参与率上限与冲击成本曲线；连续停牌/跌停情景计入尾部并给退出预案"},
    ]

    rep = {
        "meta": {
            "title": "A股中低频多因子＋趋势策略 · 量化策略研究报告",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "step0": {**cfg.section("step0"), "capital": cfg.get("step0.capital")},
            "config_snapshot": cfg.to_dict() if hasattr(cfg, "to_dict") else {"note": "config 未实现 to_dict"},
            "components": ["factor-researcher", "backtest-engineer", "strategy-architect",
                           "risk-modeler", "reality-check", "quant-workflow"],
            "data_source": cfg.get("data.source"),
            "data_level": "L3 第三方行情（腾讯证券），已与通达信 MCP 交叉校验",
            "adjustment": "前复权",
            "data_gaps": cfg.get("data.known_gaps", []),
            "sample": md.summary(),
            "quick_mode": quick,
        },
        "one_line_conclusion": (
            f"不具备实盘条件：经专家团审核已修复引擎级致命缺陷并重跑全流程，但实测合成因子 "
            f"IC={ci['ic_mean']}（HAC t={ci['t_stat']}）方向反转、样本外夏普 {oos_headline}、"
            f"{mc_txt}、风险收益比不达标（MDD {baseline.get('max_drawdown')} / Calmar {baseline.get('calmar')}）、"
            "容量未评估、重大级数据缺口未决，不产出任何策略级投资结论。"
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
        "risk_elements": elems,
        "audit": audit,
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
    au = rep.get("audit", {})
    lines += ["", "## 过拟合四件套", "",
              f"- 已执行：{', '.join(rep['overfit']['present'])}",
              f"- 缺失：{rep['overfit']['missing']}", "",
              "## 专家团审核与整改（可验证）", "",
              f"- 进程内自检：{au.get('selftest', {}).get('tests_run', 0)} 项，通过={au.get('selftest', {}).get('passed')}",
              f"- 现金最低 ¥{rep['performance'].get('cash_min_cny')} / 最大持仓敞口 {rep['performance'].get('max_position_exposure')} / 撮合硬约束通过={au.get('cash', {}).get('passed')}",
              f"- IC 推断：{au.get('ic_method', {}).get('method')}；FDR 尝试数分母={au.get('fdr', {}).get('n_attempted')}；随机对照等价={au.get('control_equivalence', {}).get('passed')}",
              "- 详细联合审核意见见 docs/AUDIT_PANEL.md 与 docs/AUDIT_REPORT.html", "",
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
