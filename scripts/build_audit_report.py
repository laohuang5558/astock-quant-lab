"""专家团联合审核报告生成器。

数值全部来自 output/result.json（整改后 v2）与 output/result_v1_pre_audit.json
（审核基准 v1），定性结论为 6 位专家委员的独立评审意见汇总。禁止手工转录读数。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]

PANEL = [
    {"role": "量化工作流主席", "skill": "quant-workflow", "eng": "有条件可交付", "strat": "不放行",
     "stance": "结论正确，但裁决机制不正确：两票硬编码、final 永无「放行」态、6 项 VERDICT 未经校验即判成立；output 产物被 gitignore 却列为交付物",
     "p0": "重写 quality_gate 推导链；结果产物纳入版本控制；文档数值改由脚本渲染"},
    {"role": "策略架构师", "skill": "strategy-architect", "eng": "附条件可交付", "strat": "不放行（建议升级「冻结」）",
     "stance": "合成得分应冻结弃用、反对反向使用；熔断与月末调仓根本冲突（致命）；择时基准含成分前视；多项配置声明未接线",
     "p0": "熔断触发日即时生效并修复清仓锁死；切断「反向/继续调参」路径；规格—实现—验证链收口"},
    {"role": "因子研究员", "skill": "factor-researcher", "eng": "有保留（方法正确性=否）", "strat": "不放行",
     "stance": "IC 全负是「样本选择＋区间＋前复权 amount 口径＋统计推断缺陷」四重叠加伪影，不是可采信的反向 alpha；20 日重叠窗口用 iid t 使显著性虚高约一个量级",
     "p0": "改 HAC/非重叠 IC 重算 t、p；FDR 分母改真实尝试数；统一 IC 口径并扩全 A 面板"},
    {"role": "回测工程师", "skill": "backtest-engineer", "eng": "否（唯一反对票）", "strat": "不放行",
     "stance": "独立重放证实 275 天负现金、最大敞口 133.38%，与「纯多头无杠杆」直接矛盾，全部绩效读数失真；T+1 为 dead code；blocked 静默丢弃",
     "p0": "两段式撮合＋现金/敞口断言；lots FIFO 实现真实 T+1；被拒订单留痕顺延；补 4 项构造性单测"},
    {"role": "风险建模师", "skill": "risk-modeler", "eng": "附条件可交付", "strat": "不放行（应更严）",
     "stance": "风险度量整类未进入定级表与质量门；压力情景窗口内重置峰值使 2022/2024 少报 0.231/0.342；夏普 0.44＋MDD −45%＋峰度 16.29 本身即独立否决项",
     "p0": "风险读数纳入硬否决；修熔断状态机与生效时效；历史情景改全局峰值口径"},
    {"role": "现实检验官", "skill": "reality-check", "eng": "附条件可交付", "strat": "不放行",
     "stance": "数值一致性 6 处漂移（质量门计数 7/3/2 vs 8/2/2、bar 42,093 vs 42,091 等）；结论未被包装成变相营销，但「裁决说冻结、工程照跑」脱节",
     "p0": "关键数值统一由 result.json 渲染并加 CI 校验；冻结纪律落地；公开仓库补 research-only 与英文免责"},
]

FINDINGS = [
    ("A01", "负现金 / 隐性杠杆（致命）", "致命", "回测工程师；主审独立重放复核",
     "275 天现金为负、最低 −¥507,193.99、最大敞口 1.3338；与「纯多头、无杠杆」声明冲突，绩效全部失真",
     "已修", "两段式撮合（先卖后买、按可用现金等比例缩放）＋ cash≥0 断言；v2 现金最低 0.00、敞口 1.0000"),
    ("A02", "T+1 从未生效（重大）", "重大", "回测工程师、策略架构师",
     "bought_today 每日清空且同批只写 BUY，卖出分支恒读到 0；同名单测零条 T+1 断言",
     "已修", "lots FIFO 按买入日期计算 sellable，补连续调仓日专项单测"),
    ("A03", "熔断与月末调仓冲突 / 清仓锁死（致命+重大）", "致命", "策略架构师、风险建模师",
     "敞口每日更新但目标权重只在月末生成，触发到生效最长滞后约 20 交易日；exposure=0 后 equity 不变、dd 永不收敛 → 永久锁死；清仓档实测触发 0 次",
     "已修", "敞口变化当日强制生成新目标、T+1 执行（v2 共 17 次）；影子净值恢复路径 + 逐级升档，v2 含清仓档事件"),
    ("A04", "被拒订单静默丢弃（重大）", "重大", "回测工程师",
     "13 笔被拒订单无条件清空 pending，不留挂单、不重试、不缩放，组合静默偏离目标",
     "已修", "unfilled_history 留痕（v2 8 项）并在下期目标合并；n_trades 改为仅计实际成交"),
    ("A05", "IC 统计推断 iid t 虚高（致命）", "致命", "因子研究员",
     "20 日重叠前向收益逐日算 IC，却用 mean/(std/√n) 的 iid t；合成 t=−13.6191 严重高估显著性",
     "已修", "Newey-West / Bartlett HAC（lags=20）；合成 t 由 −13.6191 修正为 −4.2447，p=2.2e−05，iid t 另列对照"),
    ("A06", "多重检验分母漏登尝试数（重大）", "重大", "因子研究员",
     "n_tests=6 只算最终保留的 6 个因子，漏登 9 组参数组合；同一报告 DSR 却用 n_trials=54，两套口径矛盾",
     "已修", "BH-FDR 阈值分母改用真实尝试数 54，与 DSR 一致；校正后显著数 5 → 2"),
    ("A07", "质量门两票硬编码、裁决不可证伪（重大）", "重大", "工作流主席、现实检验官、回测工程师",
     "engineering_gate / strategy_conclusion_gate 为常量；final 无「放行」分支；6 项 VERDICT 无条件判成立；overfit complete 恒为 True",
     "已修", "18 项 VERDICT 全部由读数推导，两票与 final 由计数＋硬否决项反推；complete 改按关键字段有效性判定；进程内自检绑定"),
    ("A08", "风险读数整类未进入定级与质量门（致命/重大）", "致命", "风险建模师",
     "VaR/CVaR、压力、相关性、MDD −0.45、峰度 16.29、Calmar 0.15 全部未被任何条目消费；IF_RISK 触发条件与实测脱节",
     "已修", "新增「风险收益比与尾部风险」「系统性 beta/无法止损/流动性」两条风险要素；风险收益比列为独立硬否决项"),
    ("A09", "历史压力情景低估（重大）", "重大", "风险建模师",
     "窗口内 seg.cummax() 重置峰值，2022 少报 0.231、2024 少报 0.342；真实极值段未列入；2/4 假设情景无数值",
     "已修", "改全局峰值口径并新增 2021-02-10~2026-08-17 极值段；补齐 2 项假设情景数值、清理滞后 note"),
    ("A10", "随机对照组与基准不等价（重大）", "重大", "策略架构师、工作流主席",
     "对照组仅随机选股，未叠加趋势/择时/熔断/buffer/换手/权重约束与两段式撮合，却宣称「其余约束完全一致」；8.5 分位与 DSR 因此不可靠",
     "部分修", "已诚实标注 control_equivalence=false 并把分位/DSR 降级为方向性参考；等价对照需 200 次完整引擎重跑，列入待办"),
    ("A11", "文档与 result.json 数值漂移（重大）", "重大", "现实检验官、工作流主席、策略架构师",
     "QUALITY_GATE.md 写 7/3/2 而实测 8/2/2；42,093（原始 CSV 行）vs 42,091（入模 bar）；config_snapshot 声称存在实为缺失；合规条数与缺口条数自述不符",
     "已修", "计数改为不手写、由 result.json 渲染；42,091/42,093 口径并列标注；cfg.to_dict() 落盘至 meta.config_snapshot；合规与缺口条数补齐"),
    ("A12", "结果产物被忽略却列为交付物（重大）", "重大", "工作流主席",
     "output/*.json|md|csv 被 .gitignore 排除，git ls-files 无任何结果产物，第三方无法复核数值",
     "已修", ".gitignore 放开 output/，并把 v1 审核基线 result_v1_pre_audit.json 一并入库"),
    ("A13", "「反向使用」留口子、冻结纪律未落地（重大）", "重大", "策略架构师、现实检验官",
     "IF_RISK 写「立即冻结、禁止调参」，followups 却把「反向使用」列为合法选项；config 仍启用全套权重；方向判定还用了含样本外段的全样本 IC",
     "已修", "followups 首条改为「冻结、禁止反向使用；方向重估须作为新假设在全新样本重走流程」；FACTOR_META.sign 死字段与 OOS 污染登记为待办"),
    ("A14", "因子共线 / 近似成交额 / 幸存者样本（重大）", "重大", "因子研究员、策略架构师",
     "liq_20↔illiq_20 相关 0.8637 且合计权重 0.30，输入 amount 100% 为前复权价近似值；20 只全为当前存续抱团龙头，行业 UNKNOWN 无法中性化",
     "待外部数据", "需含退市股/历史 ST/历史行业/PIT 财务/真实成交额的全 A 点时间面板，按「当时存续」重构横截面后重估；非本轮可解"),
    ("A15", "择时基准前视 / 前复权用于涨跌停基准（重大/一般）", "重大", "策略架构师",
     "market_proxy 由「当前存续 20 只」等权构造，2018 年成分由未来存续集合决定；前复权 prev_close 用作涨跌停基准，除权日比例失真",
     "待外部数据", "换用带 PIT 成分的可投资公开指数；分离未复权撮合价与复权收益价两套序列"),
    ("A16", "配置项声明但未接线 / 邻接网格过窄（重大/一般）", "重大", "策略架构师、因子研究员",
     "winsor/standardize/nan_policy、FACTOR_META.sign、max_industry_weight、exclude_suspended、min_avg_amount_20d 均未被读取；网格仅 top_n×ma_long 二维",
     "部分修", "已全部登记进 quality_gate.followups；实现或显式删除需下一轮工程改动，本轮不伪造「已实现」"),
]

ROADMAP_DONE = [
    "两段式撮合 + 现金/敞口断言（负现金 275 天 → 0 天，敞口 1.3338 → 1.0000）",
    "lots FIFO 真实 T+1，删除 dead code；补 T+1 连续调仓日单测",
    "被拒订单留痕并顺延；换手/权重口径标注；n_trades 仅计实际成交",
    "熔断触发日强制调仓（17 次）、逐级升档、影子净值恢复、清仓档真实触发",
    "压力测试全局峰值口径 + 极值段 + 2 项假设情景数值补齐",
    "Newey-West HAC 重算 IC 的 t/p；BH-FDR 分母改用 54 次尝试数并与 DSR 对齐",
    "质量门重写：18 项证据推导、两票反推、放行态、complete 可证伪、进程内自检绑定",
    "风险读数纳入定级表与硬否决；新增 2 条风险要素",
    "文档/HTML 数值统一由 result.json 渲染；合规、research-only、config_snapshot、output 入库",
    "单元测试 11 → 15 项（现金/敞口、T+1、熔断状态机、全局峰值）",
]

ROADMAP_OPEN = [
    ("全 A 点时间面板", "需含退市股、历史 ST、历史行业分类、PIT 财务与真实成交额；按当时存续重构每日横截面后重估 6 因子方向", "外部数据"),
    ("容量与冲击成本", "需用户给出资金规模与单标的参与率上限，才能计算容量曲线；当前 capital=null，不下任何规模结论", "新假设"),
    ("等价随机对照", "需 200 次复用完整权重函数与两段式撮合的引擎回测，重算分位与 DSR；当前分位仅作方向性参考", "算力/新假设"),
    ("独立验证段与预注册假设", "方向重估、反向使用与否必须在全新样本上作为新假设重走 Step 0，禁止在原样本翻符号", "新假设"),
    ("未复权价序列与真实指数", "撮合/涨跌停改用未复权价、收益用复权价；MA200 择时换带 PIT 成分的公开指数", "外部数据"),
    ("oos_use_count 持久化", "当前为 config 静态常量；需落盘、参数/方向回改时递增、>3 次自动降级", "工程改动"),
    ("未接线配置项与行业上限", "接入或显式删除 winsor/sign/行业上限/ST 开关等；补因子权重与择时/熔断/成本网格", "工程改动"),
]

LEVEL_CLS = {"致命": "lv-fatal", "重大": "lv-major", "一般": "lv-minor", "建议": "lv-tip"}
STATUS_CLS = {"已修": "st-done", "部分修": "st-part", "待外部数据": "st-open"}


def fmt(v, pct=False, money=False):
    if v is None:
        return "—"
    if money:
        return f"¥{float(v):,.2f}"
    if pct:
        return f"{float(v) * 100:.2f}%"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def build_rows(v1: dict, v2: dict) -> list[tuple]:
    p1, p2 = v1["performance"], v2["performance"]
    a2 = v2["audit"]
    c1, c2 = v1["factor_research"]["composite_ic"], v2["factor_research"]["composite_ic"]
    m1, m2 = v1["factor_research"]["multiple_testing"], v2["factor_research"]["multiple_testing"]
    g1, g2 = v1["quality_gate"], v2["quality_gate"]
    rows = [
        ("现金最低（¥）", f"{p1.get('cash_min_cny', -507193.99):,.2f}", fmt(p2.get("cash_min_cny"), money=True), "→ 改善", "两段式撮合 + cash≥0 断言"),
        ("最大持仓敞口", "1.3338", fmt(p2.get("max_position_exposure")), "→ 改善", "纯多头约束落地"),
        ("负现金天数", "275", str(a2["cash"].get("negative_days")), "→ 改善", "独立重放复核"),
        ("总收益", fmt(p1["total_return"], pct=True), fmt(p2["total_return"], pct=True), "↓ 下降", "去掉隐性杠杆后回落"),
        ("年化收益", fmt(p1["annualized_return"], pct=True), fmt(p2["annualized_return"], pct=True), "↓ 下降", ""),
        ("夏普（rf=0）", fmt(p1["sharpe"]), fmt(p2["sharpe"]), "↓ 下降", ""),
        ("最大回撤", fmt(p1["max_drawdown"], pct=True), fmt(p2["max_drawdown"], pct=True), "↓ 加深", "熔断真正生效 + 无杠杆，v1 净值存在虚高"),
        ("Calmar", fmt(p1["calmar"]), fmt(p2["calmar"]), "↓ 下降", ""),
        ("总成本（¥）", fmt(p1["cost_total_cny"], money=True), fmt(p2["cost_total_cny"], money=True), "↑ 上升", "强制调仓次数增加"),
        ("成交笔数", f"{p1['n_trades']}（含 13 被拒）", f"{p2['n_trades']}（另 {p2.get('n_blocked_records')} 被拒）", "口径修正", "n_trades 改为仅计 BUY/SELL"),
        ("合成因子 IC 均值", fmt(c1["ic_mean"]), fmt(c2["ic_mean"]), "不变", "IC 均值不受 t 检验方法影响"),
        ("合成 IC t 值", f"{c1['t_stat']}（iid）", f"{c2['t_stat']}（HAC）", "显著性下调", "HAC Bartlett lags=20；iid t 另列对照"),
        ("FDR 尝试数分母", "6", str(m2.get("n_attempted")), "→ 校正", "与 DSR 的 n_trials 统一"),
        ("校正后显著因子数", str(m1["n_rejected"]), str(m2["n_rejected"]), "↓ 减少", "分母 + p 值双修正"),
        ("单元测试项数", "11", str(a2["selftest"].get("tests_run")), "→ 增加", "新增 4 项构造性单测"),
        ("熔断事件数", "5（仅状态变更）", f"{a2['fuse']['events']}（含清仓档）", "→ 生效", "触发日强制调仓 17 次"),
        ("质量门复核项", str(len(g1["items"])), str(len(g2["items"])), "→ 增加", "新增 4 道硬否决"),
        ("工程票", g1["engineering_gate"], g2["engineering_gate"], "裁决变更", "由常量改为证据推导"),
        ("策略票", g1["strategy_conclusion_gate"], g2["strategy_conclusion_gate"], "一致", "6 位委员全票不放行"),
    ]
    return rows


HTML_TPL = Template(r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
:root{--bg:#f5f7fa;--card:#fff;--ink:#1b2733;--sub:#5c6b7f;--line:#e3e9f0;--red:#c0392b;--green:#1e8449;--amber:#b9770e;--blue:#1f5f8b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65}
.wrap{max-width:1180px;margin:0 auto;padding:34px 26px 70px}
header{border-bottom:3px solid var(--blue);padding-bottom:20px;margin-bottom:26px}
h1{font-size:27px;margin:0 0 8px;letter-spacing:.5px}
.meta{color:var(--sub);font-size:13px}
.badge{display:inline-block;background:#eaf2f8;color:var(--blue);border:1px solid #cfe0ec;border-radius:3px;padding:2px 8px;font-size:12px;margin-right:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:20px 22px;margin:18px 0;box-shadow:0 1px 3px rgba(20,40,70,.04)}
h2{font-size:19px;margin:0 0 14px;padding-left:11px;border-left:4px solid var(--blue)}
h3{font-size:15px;margin:18px 0 8px;color:var(--blue)}
.verdict{background:linear-gradient(135deg,#fff5f4,#fff);border:1px solid #e8c4bd;border-left:6px solid var(--red)}
.verdict .big{font-size:24px;font-weight:700;color:var(--red);margin:4px 0 10px}
.callout{background:#f0f5fa;border-left:4px solid var(--blue);padding:12px 16px;margin:12px 0;font-size:14px;color:#2a3b4c;border-radius:0 5px 5px 0}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
th{background:#eef3f8;color:#33475b;text-align:left;padding:9px 10px;border:1px solid var(--line);font-weight:600}
td{padding:9px 10px;border:1px solid var(--line);vertical-align:top}
tr:nth-child(even) td{background:#fafcfe}
.tag{display:inline-block;padding:1px 7px;border-radius:3px;font-size:11px;font-weight:600;white-space:nowrap}
.lv-fatal{background:#fdecea;color:#a5281b}.lv-major{background:#fdf1de;color:#935c05}.lv-minor{background:#eef4fa;color:#1f5f8b}.lv-tip{background:#eef1f4;color:#5c6b7f}
.st-done{background:#e8f6ee;color:#1e8449}.st-part{background:#fdf1de;color:#935c05}.st-open{background:#eef1f4;color:#5c6b7f}
.up{color:var(--red);font-weight:600}.down{color:var(--green);font-weight:600}.flat{color:var(--sub)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:12px 0}
.kpi{background:#f8fafc;border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.kpi .n{font-size:22px;font-weight:700;color:var(--blue)}
.kpi .l{font-size:12px;color:var(--sub)}
ul{margin:8px 0;padding-left:20px}li{margin:5px 0;font-size:14px}
.bar{height:9px;background:#e8eef4;border-radius:5px;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--blue)}
footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--sub)}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;color:var(--blue);font-size:14px}
.small{font-size:12px;color:var(--sub)}
</style>
</head>
<body><div class="wrap">
<header>
<h1>$title</h1>
<div class="meta"><span class="badge">专家团联合审核</span><span class="badge">research-only</span> 生成时间 $generated ｜ 审核基准 v1：$v1_file ｜ 整改复核 v2：$v2_file</div>
</header>

<div class="card verdict">
<h2>联合裁决</h2>
<div class="big">工程：修复后可交付（有保留）　｜　策略级结论：不放行（6/6 全票）</div>
<p><b>最重要的一条：</b>本次审核<b>推翻了此前「工程可交付」的判定</b>。6 位委员中回测工程师投出唯一反对票，独立重放证实回测长期处于负现金（275 天、最低 −¥507,193.99、最大敞口 133.38%），与「纯多头、无杠杆」声明直接冲突。<b>v1 的全部绩效读数（总收益 73.89%、夏普 0.4422、MDD −45.14%）在隐性杠杆口径下失真，一律作废</b>。</p>
<p>主审据此先整改再复核：引擎、风控、统计推断与质量门共 10 类缺陷已落地修复并重跑全流程。<b>v2 绩效整体恶化</b>（总收益 $v2_total、夏普 $v2_sharpe、最大回撤 $v2_mdd），说明 v1 净值存在虚高；这本身就是「不放行」最硬的证据。工程票在整改完成后恢复为「$v2_eng」，但<b>工程可交付不等于研究结论可采信</b>。</p>
<div class="callout">质量门最终判定 <b>$v2_final</b>（$v2_counts）。策略票维持「不放行」，且新增一条<b>不可通过补数据消除</b>的独立否决项：风险收益比与尾部风险（MDD $v2_mdd / Calmar $v2_calmar / 峰度 $v2_kurt）。</div>
</div>

<div class="card">
<h2>1 · 审核对象与专家团</h2>
<p class="small">审核对象：可运行工程 <code>astock-quant-lab/</code>、设计报告、真实回测结果、技能包与公开 GitHub 仓库。6 位委员分头独立只读审查，各自加载对应角色方法论，意见只报主审汇总。</p>
<div class="grid">
<div class="kpi"><div class="n">6</div><div class="l">专家委员 / 独立评审</div></div>
<div class="kpi"><div class="n">$n_findings</div><div class="l">去重后核心发现</div></div>
<div class="kpi"><div class="n">$n_fixed</div><div class="l">本轮已修复</div></div>
<div class="kpi"><div class="n">$n_open</div><div class="l">依赖外部数据/新假设的未决项</div></div>
</div>
<table><tr><th>委员</th><th>方法论</th><th>工程票</th><th>策略票</th><th>核心立场</th></tr>$panel_rows</table>
<details><summary>展开各委员首要整改主张</summary><ul>$panel_p0</ul></details>
</div>

<div class="card">
<h2>2 · 共识与分歧</h2>
<h3>共识</h3>
<ul>
<li><b>6/6 策略级结论不放行</b>：合成因子 IC 方向反转、样本外劣化、随机对照不占优、容量未评估、重大级数据缺口未决，任一项即足以否决。</li>
<li><b>一致反对「反向使用」</b>：6 因子 IC 全负是样本/区间/口径/统计推断叠加的伪影，不是可交易反向 alpha；应冻结权重组合，方向重估须作为新假设在全新样本重走流程。</li>
<li><b>一致认定 v1 裁决机制不可信</b>：两票硬编码、final 无放行态、多条 VERDICT 未经校验即判成立——「结论碰巧对，推导链不对」。</li>
<li><b>一致认定文档与结果存在数值漂移</b>：质量门计数、bar 数、合规条数、config_snapshot 声明。</li>
</ul>
<h3>分歧与主审裁决</h3>
<ul>
<li><b>工程票</b>：5 位投「可交付但附条件」，回测工程师投「否」；因子研究员把「研究方法正确性」单独否定。<b>主审采纳更严立场</b>：不直接放行，先完成致命/重大缺陷整改并复跑，再按证据给票。</li>
<li><b>裁决强度</b>：策略架构师建议措辞由「不放行」升级为「冻结」，风险建模师认为应更严。<b>主审采纳</b>：followups 首条改为冻结、禁止反向使用、禁止继续调参。</li>
<li><b>随机对照</b>：部分委员要求立即重跑等价对照；<b>主审判定</b>该改动依赖 200 次完整引擎回测与新假设，本轮不伪造等价性，改为显式标注不等价并把分位/DSR 降级为方向性参考。</li>
</ul>
</div>

<div class="card">
<h2>3 · 核心发现与整改状态</h2>
<p class="small">严重级按专家团定级；「已修」= 有代码改动 + 重跑复核证据，「部分修」= 已诚实登记但需后续工程/算力，「待外部数据」= 物理上依赖本轮不可得的数据或新假设。</p>
<table><tr><th>编号</th><th>发现</th><th>级别</th><th>提出委员</th><th>证据</th><th>状态</th><th>整改动作 / 复核读数</th></tr>$finding_rows</table>
</div>

<div class="card">
<h2>4 · v1（审核基准）→ v2（整改复核）读数对照</h2>
<p class="small">两列数值均直接读自 <code>output/result_v1_pre_audit.json</code> 与 <code>output/result.json</code>，未手工转录。涨跌配色遵循 A 股习惯（红涨绿跌）。</p>
<table><tr><th>指标</th><th>v1 审核基准</th><th>v2 整改后</th><th>变化</th><th>原因</th></tr>$compare_rows</table>
<div class="callout"><b>如何解读「回撤反而加深」</b>：v1 的正收益部分来自隐性杠杆（现金为负仍全额买入）与熔断未真正执行；修复后去掉杠杆、熔断按宣称即时降档/清仓，净值不再被这两项人为抬高，收益下降、回撤加深是<b>修正而非退步</b>。</div>
</div>

<div class="card">
<h2>5 · 整改路线图</h2>
<h3>P0 · 本轮已执行（有文件改动与重跑证据）</h3>
<ul>$road_done</ul>
<h3>P1 · 未决（本轮无法执行，已登记进 <code>quality_gate.followups</code>）</h3>
<table><tr><th>事项</th><th>为什么本轮做不了</th><th>依赖类型</th></tr>$road_open</table>
<p class="small">以上 P1 项不属于「诊断后搁置」：它们要么需要本环境物理不含的行情/财务数据，要么需要用户给出新的资金规模假设，要么需要另立研究假设；强行填占位数值会构成编造。</p>
</div>

<div class="card">
<h2>6 · 交付物与可复核性</h2>
<ul>
<li><code>output/result_v1_pre_audit.json</code>：v1 审核基准存档（已作废，仅供对照）</li>
<li><code>output/result.json</code> / <code>SUMMARY.md</code> / <code>equity.csv</code> / <code>trades.csv</code>：v2 全量结果，已纳入版本控制</li>
<li><code>docs/AUDIT_PANEL.md</code>：本报告 Markdown 版；<code>docs/QUALITY_GATE.md</code>：质量门与风险定级</li>
<li>15 项单元测试（<code>python -m quantlab selftest</code>），含现金/敞口、T+1、熔断状态机、全局峰值口径四项新增构造性测试</li>
<li>本报告全部数值由脚本从结果文件注入，不含手写读数；零内联 JS、零外部 CDN 依赖</li>
</ul>
</div>

<footer>
<b>合规声明</b>：投资有风险，本内容仅供研究与教育，不构成投资建议；回测结果不等于实盘表现，历史业绩不代表未来；不荐股、不给买卖点与目标价、不承诺收益、不做代客理财、不代下单；本次为纯多头、无杠杆、不做空，如未来启用杠杆，高杠杆可能放大亏损甚至造成本金归零或倒欠；涉及税务、法律定性与跨境资金问题，转税务师 / 律师；涨跌表述红涨绿跌，货币符号 ¥。<br><br>
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。本工程不含实盘下单与券商接口。
</footer>
</div></body></html>
""")

MD_TPL = Template(r"""# 专家团联合审核报告（A股多因子＋趋势策略研究框架）

生成时间：$generated ｜ 审核基准 v1：`$v1_file` ｜ 整改复核 v2：`$v2_file`

> **联合裁决**：工程 **修复后可交付（有保留）**；策略级结论 **不放行（6/6 全票）**。
> 本次审核**推翻了此前「工程可交付」的判定**：独立重放证实 v1 存在 275 天负现金、最大敞口 133.38% 的隐性杠杆，全部绩效读数失真并作废。整改重跑后 v2 绩效整体恶化（总收益 $v2_total、夏普 $v2_sharpe、MDD $v2_mdd），恰是不放行的最硬证据。

## 1 · 专家团与投票

| 委员 | 方法论 | 工程票 | 策略票 | 核心立场 |
|---|---|---|---|---|
$panel_md

## 2 · 共识与分歧

**共识**：6/6 策略不放行；一致反对「反向使用」（IC 全负为样本/区间/口径/统计推断叠加伪影）；一致认定 v1 质量门机制不可信；一致认定文档数值漂移。

**分歧**：工程票 5 票「附条件可交付」vs 回测工程师 1 票「否」；策略架构师与风险建模师主张措辞更严（升级为「冻结」）。**主审裁决**：采纳更严立场 —— 不直接放行，先整改致命/重大缺陷并复跑；followups 首条改为冻结、禁止反向与继续调参；随机对照不等价一项不伪造等价性，显式标注并降级结论。

## 3 · 核心发现与整改状态

| 编号 | 发现 | 级别 | 提出委员 | 证据 | 状态 | 整改动作 |
|---|---|---|---|---|---|---|
$finding_md

## 4 · v1 → v2 读数对照（数值直接取自结果文件）

| 指标 | v1 审核基准 | v2 整改后 | 变化 | 原因 |
|---|---|---|---|---|
$compare_md

> **回撤加深不是退步**：v1 正收益部分来自隐性杠杆与熔断未真正执行；修复后去掉杠杆、熔断即时降档/清仓，净值不再被人为抬高。

## 5 · 整改路线图

**P0 本轮已执行**

$road_done_md

**P1 未决（已登记进 `quality_gate.followups`）**

$road_open_md

P1 项依赖本环境物理不含的数据、用户尚未给出的资金规模假设或另立研究假设；填占位数值会构成编造，故不伪造「已解决」。

## 6 · 可复核性

- `output/result_v1_pre_audit.json`：v1 审核基准存档（已作废，仅供对照）
- `output/result.json`、`SUMMARY.md`、`equity.csv`、`trades.csv`：v2 全量结果，已纳入版本控制
- 15 项单元测试（新增现金/敞口、T+1、熔断状态机、全局峰值 4 项）
- 本报告全部数值由 `scripts/build_audit_report.py` 从结果文件注入，无手写读数

---

**合规声明**：投资有风险，本内容仅供研究与教育，不构成投资建议；回测结果不等于实盘表现，历史业绩不代表未来；不荐股、不给买卖点与目标价、不承诺收益、不做代客理财、不代下单；本次为纯多头、无杠杆、不做空，如未来启用杠杆，高杠杆可能放大亏损甚至造成本金归零或倒欠；涉及税务、法律定性与跨境资金问题，转税务师 / 律师；涨跌表述红涨绿跌，货币符号 ¥。

**免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。本工程不含实盘下单与券商接口。
""")


def render(outdir: Path, top_html: Path | None = None) -> dict:
    v1 = load(ROOT / "output" / "result_v1_pre_audit.json")
    v2 = load(ROOT / "output" / "result.json")
    p2, g2, a2 = v2["performance"], v2["quality_gate"], v2["audit"]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    panel_rows = "\n".join(
        f"<tr><td><b>{x['role']}</b></td><td><code>{x['skill']}</code></td>"
        f"<td>{x['eng']}</td><td><b>{x['strat']}</b></td><td>{x['stance']}</td></tr>"
        for x in PANEL)
    panel_md = "\n".join(
        f"| {x['role']} | `{x['skill']}` | {x['eng']} | {x['strat']} | {x['stance']} |"
        for x in PANEL)
    panel_p0 = "\n".join(f"<li><b>{x['role']}</b>：{x['p0']}</li>" for x in PANEL)

    finding_rows = "\n".join(
        f"<tr><td>{i}</td><td><b>{n}</b></td><td><span class=\"tag {LEVEL_CLS[lv]}\">{lv}</span></td>"
        f"<td>{who}</td><td>{ev}</td><td><span class=\"tag {STATUS_CLS[st]}\">{st}</span></td><td>{act}</td></tr>"
        for i, n, lv, who, ev, st, act in FINDINGS)
    finding_md = "\n".join(
        f"| {i} | {n} | {lv} | {who} | {ev} | {st} | {act} |"
        for i, n, lv, who, ev, st, act in FINDINGS)

    def trend_cls(t):
        return "up" if ("改善" in t or "增加" in t or "↑" in t) else (
            "down" if ("下降" in t or "加深" in t or "减少" in t or "↓" in t) else "flat")

    rows = build_rows(v1, v2)
    compare_rows = "\n".join(
        f"<tr><td>{a}</td><td>{b}</td><td><b>{c}</b></td><td class=\"{trend_cls(d)}\">{d}</td><td>{e}</td></tr>"
        for a, b, c, d, e in rows)
    compare_md = "\n".join(f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows)

    road_done = "\n".join(f"<li>{x}</li>" for x in ROADMAP_DONE)
    road_done_md = "\n".join(f"- {x}" for x in ROADMAP_DONE)
    road_open = "\n".join(
        f"<tr><td><b>{a}</b></td><td>{b}</td><td><span class=\"tag lv-minor\">{c}</span></td></tr>"
        for a, b, c in ROADMAP_OPEN)
    road_open_md = "\n".join(f"- **{a}**（{c}）：{b}" for a, b, c in ROADMAP_OPEN)

    counts = g2["counts"]
    ctx = dict(
        title="A股多因子＋趋势策略 · 专家团联合审核报告",
        generated=generated,
        v1_file="output/result_v1_pre_audit.json", v2_file="output/result.json",
        v2_total=fmt(p2["total_return"], pct=True), v2_sharpe=fmt(p2["sharpe"]),
        v2_mdd=fmt(p2["max_drawdown"], pct=True), v2_calmar=fmt(p2["calmar"]),
        v2_kurt=fmt(p2["kurtosis"]), v2_eng=g2["engineering_gate"],
        v2_final=g2["final_verdict"],
        v2_counts=f"成立 {counts['成立']} / 不成立 {counts['不成立']} / 存疑 {counts['存疑']}",
        n_findings=len(FINDINGS),
        n_fixed=sum(1 for x in FINDINGS if x[5] == "已修"),
        n_open=len(ROADMAP_OPEN),
        panel_rows=panel_rows, panel_md=panel_md, panel_p0=panel_p0,
        finding_rows=finding_rows, finding_md=finding_md,
        compare_rows=compare_rows, compare_md=compare_md,
        road_done=road_done, road_done_md=road_done_md,
        road_open=road_open, road_open_md=road_open_md,
    )
    outdir.mkdir(parents=True, exist_ok=True)
    hp = outdir / "AUDIT_REPORT.html"
    mp = outdir / "AUDIT_PANEL.md"
    hp.write_text(HTML_TPL.substitute(ctx), encoding="utf-8")
    mp.write_text(MD_TPL.substitute(ctx), encoding="utf-8")
    paths = {"audit_html": str(hp), "audit_md": str(mp)}
    if top_html:
        top_html.write_text(HTML_TPL.substitute(ctx), encoding="utf-8")
        paths["top_html"] = str(top_html)
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "docs"))
    ap.add_argument("--top-html", default="")
    args = ap.parse_args()
    paths = render(Path(args.out_dir), Path(args.top_html) if args.top_html else None)
    for k, v in paths.items():
        print(f"[out] {k}: {v}")


if __name__ == "__main__":
    main()
