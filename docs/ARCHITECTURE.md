# 架构设计（ARCHITECTURE）

## 1. 设计原则

1. **时间单向性**：任何信号只能读 `<= T` 的数据；撮合永远发生在 T+1。前向收益只允许出现在 `factors.forward_returns()`，且只被评价函数调用。
2. **参数外置**：Step 0 五要素、因子权重、成本、阈值全部在 `config/*.json`，代码中零硬编码费率。
3. **缺口显式**：数据不可得就登记 `config.data.known_gaps` 与风险表，不用占位数值冒充结论。
4. **研究与执行隔离**：不含下单、券商接口、自动交易循环；`engine.py` 的输出是净值/成交，不是订单路由。
5. **质量门唯一出口**：`report.quality_gate()` 给放行 VERDICT，其它模块不得自称「确认」；所有裁决项由真实读数、进程内自检与审计段推导，禁止硬编码票数。
6. **配置即契约**：`config/default.json` 中的每个开关都必须真正接线到代码，并由 `audit.config_wiring` 逐项声明；非法取值直接 `ValueError`，不静默回退默认值。
7. **数值单一来源**：绩效与统计读数只从 `output/result.json` 渲染，文档不得手写汇总数字；`metric-fingerprint` 把 HTML 报告与 `docs/metrics.md` 绑定到当前结果指纹。
8. **等价性可证伪**：随机对照必须复用同一权重函数与撮合引擎，并用真实 score 重跑断言逐日净值与成本一致，而不是靠文字声明「同等条件」。

## 2. 分层与接口

| 层 | 模块 | 输入 | 输出 | 关键不变量 |
|---|---|---|---|---|
| 数据 | `data.py` | 前复权 + 未复权长表 CSV / 通达信 JSON | `MarketData`（日期×代码面板）+ `wiring` 审计段 | 双价格口径分离；`eligible` 接线 ST/次新/停牌/最小成交额 |
| 因子 | `factors.py` | `MarketData`, cfg | `score` 面板 + 检验结果 | 只用 shift/rolling 向后窗口；HAC t、FDR 用尝试数 |
| 策略 | `strategy.py` | `score`, `MarketData`, cfg | `weights_fn(date) -> Series` | 权重和 ≤ 敞口；换手受限；行业上限不可行时留现金 |
| 编排 | `pipeline.py` | `MarketData`, cfg, `score_override` | 一次完整可复现运行 | 随机对照只替换 score，权重函数/择时/熔断/撮合完全一致 |
| 回测 | `engine.py` | `weights_fn`, 调仓日 | `BacktestResult` | 两段式先卖后买、lots FIFO T+1、逐日现金/敞口断言、blocked 留痕 |
| 风控 | `risk.py` | 净值/收益/权重 | 风险画像 + 熔断状态 + beta/容量 | 历史情景=事实，假设情景=模型估计，分栏；历史情景用全局峰值 |
| 分析 | `analytics.py` | 上述全部 | 绩效 + 过拟合四件套 + 归因 | 缺项写 None + 原因；蒙特卡洛为真跑引擎的等价随机对照 |
| 状态 | `state.py` | cfg 指纹 | `state/research_state.json` | 预注册冻结、参数变更自动递增样本外使用次数、超限降级 |
| 报告 | `report.py` | 上述全部 | `result.json` / `SUMMARY.md` | 质量门裁决 + 合规声明 + `audit` 审计段 |
| 校验 | `fingerprint.py`, `verify.py` | `result.json` + 交付文档 | `output/verify.json` / `docs/metrics.md` | 指纹一致、`[[m:path]]` 逐项核对、历史作废值零出现 |
| CI | `.github/workflows/ci.yml` | 仓库 | 构建产物 | selftest → run → 报告重建 → `node --check` → verify |

## 3. 时序图

```
T-1 收盘 ──┐
T   收盘 ──┼──> 因子面板(<=T) ──> 趋势过滤 ──> 合成得分 ──> 目标权重 w_T
T+1 开盘 ──┴──> 撮合 w_T（涨跌停/停牌检查 + 成本） ──> 持仓/现金 ──> 收盘盯市 ──> 熔断状态更新
```

`engine.run()` 的一次循环严格按「执行昨日 pending → 盯市 → 在**月末调仓日或熔断敞口变化日**生成下一交易日 pending」顺序，避免同日自成交与未来函数。熔断触发当日会强制生成一次减仓目标，T+1 开盘执行（专家团审核整改后新增，v1 存在最长约 20 个交易日的滞后）。

执行 pending 时采用**两段式撮合**：先按当日可卖份额（`lots` 中 `buy_date < 当日` 的批次，FIFO）卖出释放现金，再按可用现金等比例缩放买入，每个交易日结束时断言 `cash >= -1e-6` 且 `exposure <= 1.0 + 1e-6`，并写入 `cash_history` / `exposure_history`。被涨跌停、停牌或 T+1 拒绝的订单不静默丢弃，而是登记 `unfilled_history` 并在下一个 pending 日按 element-wise max 重试。逐日断言与「纯多头、无杠杆」硬约束由单测覆盖。

## 4. 扩展点

| 想换什么 | 改哪里 | 注意 |
|---|---|---|
| 数据源 | 实现 `load_prices()` 同签名适配器，或 `from_tdx_kline_json()` | 必须给出 `publish_date` 才能启用财务因子 |
| 因子 | `factors.raw_factors()` 增字典项 + `config.factors.weights` | 每新增因子必须递增 `validation.trials_factor_count` |
| 加权方式 | `strategy.build_weight_function()` | IC 加权 / 最优化为插件位，需重做多重检验校正 |
| 组合优化 | `strategy._cap_weights()` / `_cap_industry_weights()` | 行业约束需真实 PIT 行业分类；约束不可行时留现金，不再归一化推回超限 |
| 撮合 | `engine.BacktestEngine._run` 内的成交价与可成交判断 | 改 VWAP/集合竞价需重跑成本敏感性；成交价切换未复权口径需除权除息流水 |
| 冲击成本 | `cost.impact_model`、`capacity.*` | 当前 `none`；接入前需给资金规模与参与率，`capital=null` 时容量显式 `not_evaluated` |
| 基准 | `strategy.market_proxy()` | 由 `strategy.market_timing.proxy` 驱动，非法值报错；生产环境应替换为中证全指/万得全A |
| 随机对照 | `pipeline.run_backtest(score_override=...)` | 只允许替换 score，任何跳过权重函数/撮合的「简化对照」都会破坏等价性 |
| 一致性校验 | `fingerprint.METRIC_PATHS`、`verify.STALE_PATHS` | 新增关键读数须同步进 `docs/metrics.md`（由 `verify --render` 生成） |

## 5. 明确未建模（登记为风险，不假装已处理）

冲击成本、整手取整（份额允许小数）、分红再投资、集合竞价撮合、退市清算、融券与杠杆、Level-2 盘口、交易规则历史变更（如注册制后的涨跌幅调整）、停牌复牌的现金处理。

v3 追加登记：**除权除息/分红送转流水缺失**，因此成交价与盯市仍使用前复权价，仅涨跌停基准切到未复权价；这不是「双价格已闭环」，质量门按「存疑」定级。**全 A 点时间成分面板、PIT 财务、交易所真实成交额**缺失，行业约束与容量测算因此受限，均写入 `config.data.known_gaps` 与 `quality_gate.followups`。

## 6. 可复现性

- 固定 `config/default.json` + 固定数据文件 + 固定随机种子（蒙特卡洛 `9000+i`）→ 结果可复现。
- `output/result.json` 的 `meta.config_snapshot` 由 `cfg.to_dict()` 落盘，连同数据源、样本区间，可追溯每个读数对应的配置（v1 文档曾声称存在但实际未写入，专家团审核后已补齐）。
- `state/research_state.json` 以 SHA-256 固化预注册假设与策略参数指纹；参数变动会自动递增样本外使用次数并写入 history，超限即降级，杜绝「用样本外反复调参」。
- 单元测试 `python -m quantlab selftest` 覆盖无未来函数、涨跌停、成本、统计口径、真实数据跨源校验，以及 v3 的等价对照、预注册、配置接线、容量/beta、双价格回退。
- `python -m quantlab verify` 做交付前三重校验：HTML/`docs/metrics.md` 指纹一致、`[[m:path]]` 标记逐项核对、历史作废值零出现；CI 中同一套流程自动执行。

## 7. v3 关键链路

| 链路 | 入口 | 关键约束 |
|---|---|---|
| 等价随机对照 | `pipeline.run_backtest(md, cfg, score_override=...)` → `analytics.monte_carlo()` | 先用真实 score 重跑断言逐日净值与成本一致（`control_equivalence`），再真跑 `mc_paths` 次完整引擎；不等价则分位与 DSR 直接置空 |
| 预注册 | `python -m quantlab preregister --text ...` → `state.sync_run()` | 冻结后需 `--force` 才能改写；指纹只 hash `leverage/cost/factors/strategy/risk/period`，新增检验维度不被误记为调参 |
| 双价格 | `scripts/fetch_tencent.py --adjust none` → `data.MarketData` | 未复权覆盖率 ≥50% 时用于涨跌停基准，否则显式回退 `qfq_only__raw_coverage_too_low`；成交价仍前复权 |
| 一致性 CI | `python -m quantlab verify --render` | 数值变动后必须重跑完整流水线并重建报告，否则指纹不匹配即报错 |

## 8. 质量门裁决链

`report.quality_gate()` 是唯一放行出口。判据来自：进程内自检结果、逐日现金/敞口断言、因子 IC 的 HAC t 与 BH-FDR（分母用尝试数）、样本外使用次数、等价随机对照显著性、参数邻域共识、风险收益比与尾部风险、配置接线审计、双价格口径分离。策略级结论由 `blocking` 判据推导，任何一条硬否决成立即「不放行」；工程票与策略票分开给出，禁止互相顶替。

所有当前读数见 `output/result.json` 与 `docs/metrics.md`；本架构文档不转录绩效数字。

## 9. 合规边界

research-only：不含实盘下单、券商接口、自动交易循环；`engine.py` 的输出是净值与成交记录，不是订单路由。回测结果不等于实盘表现，历史业绩不代表未来；不荐股、不给买卖点与目标价、不承诺收益、不代操作。
