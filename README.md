# AStock Quant Lab · A股中低频多因子＋趋势策略研究框架

一套**可运行、可复现、可审计**的 A 股量化研究工程：从真实日线抓取、因子构造与检验、信号合成、事件驱动回测、过拟合四件套、风险模型，到质量门裁决，全链路一次命令跑通。

> **定位**：只做研究与回测，**不含实盘下单、不连接券商/交易所接口、不含自动交易循环**。
> **结论纪律**：本次交付的是软件工程与研究方法，**不构成任何策略级投资结论**。质量门判定见 `output/SUMMARY.md`。

---

## 快速开始

**想要一个能打开操作的系统？** 见 [docs/RUNNING.md](docs/RUNNING.md)：执行
`python scripts/build_web_snapshot.py && python apps/api/server.py`，
浏览器打开 `http://127.0.0.1:8000/` 即为 8 面板可交互控制台；
内网静态版在 `http://192.168.2.203:8888/workbuddy/astock-quant-lab/v3/app/`（零依赖，离线只读）。

```bash
pip install -r requirements.txt          # pandas / numpy / PyYAML / Flask

python -m quantlab fetch                 # 抓取 20 只真实 A 股前复权日线 (2018-01-01 ~ 2026-11-13)
python -m quantlab fetch --adjust none --out data/sample_real/prices_raw.csv  # 未复权价（涨跌停基准）
python -m quantlab selftest              # 25 项单元测试（含等价对照、预注册、双价格、真实数据跨源校验）
python -m quantlab run                   # 完整研究：因子/回测/四件套(含 200 次等价重跑)/风控/质量门
python -m quantlab run --quick           # 联调快速模式（跳过敏感性、蒙特卡洛与成本敏感性）
python -m quantlab preregister --text "事前假设全文"   # 写入并冻结预注册假设
python -m quantlab state                # 查看策略指纹与样本外使用次数
python -m quantlab verify --render       # 交付前一致性校验（数值指纹 / 标记 / 陈旧值）
```

产物写到 `output/`：

| 文件 | 内容 |
|---|---|
| `result.json` | 全量结构化结果（Step 1–4 + 质量门 + 审计段），可机读 |
| `SUMMARY.md` | 《量化策略研究报告》摘要版 |
| `equity.csv` / `trades.csv` | 逐日净值 / 逐笔成交（含被涨跌停、停牌、T+1 阻断的订单） |
| `verify.json` | 一致性校验结果（指纹、漂移清单） |
| `result_v1_pre_audit.json` / `result_v2_post_audit.json` | 审核前 / 审核整改后的固定基线存档 |
| `api_smoke.json` / `web_render_check.json` | 平台接口冒烟 / 前端双路径渲染校验结果 |

报告重建与 CI：`scripts/build_design_report.py`、`scripts/build_audit_report.py`、
`scripts/check_inline_js.py`、`scripts/smoke_api.py`、`scripts/validate_web_render.js`；
`.github/workflows/ci.yml` 会在每次推送自动跑
`selftest → run → 报告重建 → node --check → verify → 快照重建 → 接口冒烟 → 前端渲染校验`。
关键数值由 `docs/metrics.md` 自动渲染，禁止手工转录。

自定义配置：`python -m quantlab run --config config/my.json --data data/my.csv --out output/run2/`

---

## Step 0 · 五要素基线（用户逐项锁定，全部外置在 `config/default.json`）

| 要素 | 取值 |
|---|---|
| ① 标的池与市场 | 全 A 股（剔除 ST / 退市 / 上市不足 120 日）；**随仓库演示样本为 20 只真实 A 股，不是全 A** |
| ② 交易频率 | 日频计算信号 + 月末调仓（中低频） |
| ③ 资金规模与容量 | 仅研究、不设规模；`capital=null`，参与率与冲击成本为占位符，**本次不输出容量结论** |
| ④ 回测区间与成本 | 2018-01-01 ~ 2026-11-13，样本外自 2024-01-01；佣金万 2.5 双边（最低 ¥5）、印花税千 1 仅卖出、滑点 5bp、T+1、不建模冲击 |
| ⑤ 杠杆与做空 | 纯多头、无杠杆、不做空 |

成本假设**必填且不代用户决定**：改 `config/default.json` 的 `cost` 段即可，代码里没有任何硬编码费率。

---

## 架构（6 层 + 1 个质量门）

```
scripts/fetch_tencent.py        真实行情抓取（腾讯前复权日线）
        ↓ 标准数据合约 CSV
quantlab/data.py                ① 数据层：CSV / 通达信 JSON 适配器、面板化、涨跌停与可交易标记
        ↓
quantlab/factors.py             ② 因子层：6 个价量因子、MAD 去极值、横截面 rank-Z、行业中性化(可选)、IC/IR/分层/BH-FDR
        ↓
quantlab/strategy.py            ③ 策略层：线性合成 → 趋势过滤 → 等权+上限 → buffer/换手约束 → MA200 择时
        ↓  目标权重（T 日收盘）
quantlab/engine.py              ④ 回测层：T+1 开盘撮合、涨跌停/停牌阻断、佣金/印花税/滑点、逐日盯市
        ↓  净值 / 成交
quantlab/risk.py                ⑤ 风险层：回撤熔断、VaR/CVaR、历史+假设情景压力测试、相关性与集中度
quantlab/analytics.py           ⑥ 分析层：绩效、样本外、参数敏感性、蒙特卡洛与 Deflated Sharpe、成本敏感性、归因
        ↓
quantlab/report.py              汇总 + reality-check 质量门（唯一放行出口）
```

**关键时序**：T 日收盘用 `<= T` 的数据算因子与权重 → T+1 开盘撮合。前向收益 `forward_returns()` 只出现在评价函数里，代码层面与信号生成隔离；单元测试 `test_factors_use_only_past_information` 验证「追加未来数据不改变历史因子值」。

---

## 策略规格（摘要）

**合成得分**：6 个因子横截面 rank → Z-score，按固定权重线性合成后再次标准化。

| 因子 | 公式 | 权重 |
|---|---|---|
| `mom_60` | `close.shift(5)/close.shift(65)-1` | 0.25 |
| `mom_120` | `close.shift(5)/close.shift(125)-1` | 0.15 |
| `rev_5` | `-(close/close.shift(5)-1)` | 0.15 |
| `low_vol_20` | `-std(ret,20)` | 0.15 |
| `liq_20` | `log(mean(amount,20))` | 0.15 |
| `illiq_20` | `-mean(|ret|/amount,20)` | 0.15 |

**过滤与调仓**：`close > MA60 且 MA60 > MA120` → 取合成得分 Top-N（演示样本 N=8）→ 等权 + 单标的 ≤20% → 保留 buffer（Top 1.5N 内旧持仓优先）→ 单期换手 ≤60% → 月末最后交易日收盘出信号、T+1 开盘执行。

**择时与熔断**：等权样本指数在 MA200 之下，敞口降至 50%；净值回撤 ≥15% 敞口降至 50%，≥20% 清仓，回撤收敛到 ≤8% 才恢复。

**财务因子留桩**：EP / BP / ROE 需要带 `publish_date` 的点时间（PIT）财务数据，当前数据源不覆盖 → 插件未启用，不伪造数值。

详细规格见 `docs/STRATEGY_SPEC.md`，数据口径与缺口见 `docs/DATA_CONTRACT.md`。

---

## 数据源与交叉校验

- **主数据源**：腾讯证券公开前复权日线 `web.ifzq.gtimg.cn`（L3 第三方，非交易所一手）。原始 CSV 42,093 行，面板化清洗后入模 **42,091 根 bar** / 20 只 / 2018-01-02～2026-11-13。数字以 `output/result.json` 的 `meta.sample.bars` 为准。
- **交叉校验**：通达信 MCP `tdx_kline` 的 600519 在 **2026-11-13** 的 OHLC 为 `1404.00 / 1437.69 / 1394.00 / 1435.26`，与腾讯接口逐字段一致；该校验已固化为单元测试 `test_cross_source_ohlc_600519`。
- **成交额**为近似值 `volume(手) × 100 × (H+L+C)/3`，不是交易所真实成交额；仅用于流动性类因子的横截面排序。
- **双价格口径**：`prices.csv` 为前复权（收益与盯市），`prices_raw.csv` 为未复权（涨跌停基准）。前复权序列会因未来除权重算历史价，用它乘涨跌停比例会在除权日产生虚假信号，故 `can_buy/can_sell` 用未复权前收与开盘判定；覆盖率不足时自动回退并登记。**成交价仍用前复权价**，因为缺除权除息流水，该残余缺口登记在 `quality_gate`「双价格口径分离」项（判为存疑，不称已完全解决）。

**已登记的数据缺口**（不得静默忽略）：行业分类缺失 → 行业中性化与行业权重上限本次不生效；历史 ST 标记、退市股与成分股历史变动缺失 → 存在幸存者偏差；PIT 财务缺失 → 价值/质量因子未启用；真实成交额与除权除息流水缺失 → 容量与成交价口径不可闭环。

**开发闭环**：专家团审核后的 v3 改动（等价随机对照、预注册与样本外计数持久化、配置项接线、7 轴邻域共识、beta/容量定级、双价格、一致性 CI）见 `docs/DEVELOPMENT_v3.md`。

---

## 研究用途声明（research-only / Not investment advice）

本仓库为**研究方法与软件工程演示**，不含实盘下单、券商接口或自动交易循环；所有绩效读数仅用于引擎验证，**不构成策略有效性证据，也不构成投资建议**。质量门最终判定为「不放行」，专家团联合审核意见见 `docs/AUDIT_PANEL.md` 与 `docs/AUDIT_REPORT.html`。

> For international readers: this is a **research-only** backtesting framework. Backtested results are not live performance and must not be construed as investment advice. No brokerage integration, no order execution.

---

## 合规

- 投资有风险，本内容仅供研究与教育，不构成投资建议。
- 回测结果不等于实盘表现，历史业绩不代表未来。
- 不荐股、不给买卖点与目标价、不承诺收益、不做代客理财、不代下单。
- 本次为纯多头、无杠杆、不做空；如未来启用杠杆，高杠杆可能放大亏损甚至造成本金归零或倒欠。
- 涉及税务、法律定性与跨境资金问题，转税务师 / 律师。
- 涨跌表述遵循 A 股习惯：**红涨绿跌**；货币符号 ¥。
- 如未来接入实盘执行层，需自行承担合规与风险责任；本仓库不含执行层。

> **免责声明**：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。

MIT License.
