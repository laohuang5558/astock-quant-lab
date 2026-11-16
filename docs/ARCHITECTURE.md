# 架构设计（ARCHITECTURE）

## 1. 设计原则

1. **时间单向性**：任何信号只能读 `<= T` 的数据；撮合永远发生在 T+1。前向收益只允许出现在 `factors.forward_returns()`，且只被评价函数调用。
2. **参数外置**：Step 0 五要素、因子权重、成本、阈值全部在 `config/*.json`，代码中零硬编码费率。
3. **缺口显式**：数据不可得就登记 `config.data.known_gaps` 与风险表，不用占位数值冒充结论。
4. **研究与执行隔离**：不含下单、券商接口、自动交易循环；`engine.py` 的输出是净值/成交，不是订单路由。
5. **质量门唯一出口**：`report.quality_gate()` 给放行 VERDICT，其它模块不得自称「确认」。

## 2. 分层与接口

| 层 | 模块 | 输入 | 输出 | 关键不变量 |
|---|---|---|---|---|
| 数据 | `data.py` | 长表 CSV / 通达信 JSON | `MarketData`（日期×代码面板） | 复权口径一致；`eligible` 含 ST/次新/停牌过滤 |
| 因子 | `factors.py` | `MarketData`, cfg | `score` 面板 + 检验结果 | 只用 shift/rolling 向后窗口 |
| 策略 | `strategy.py` | `score`, `MarketData`, cfg | `weights_fn(date) -> Series` | 权重和 ≤ 敞口；换手受限 |
| 回测 | `engine.py` | `weights_fn`, 调仓日 | `BacktestResult` | T+1、涨跌停、停牌、成本逐笔记账 |
| 风控 | `risk.py` | 净值/收益/权重 | 风险画像 + 熔断状态 | 历史情景=事实，假设情景=模型估计，分栏 |
| 分析 | `analytics.py` | 上述全部 | 绩效 + 过拟合四件套 + 归因 | 缺项写 None + 原因 |
| 报告 | `report.py` | 上述全部 | `result.json` / `SUMMARY.md` | 质量门裁决 + 合规声明 |

## 3. 时序图

```
T-1 收盘 ──┐
T   收盘 ──┼──> 因子面板(<=T) ──> 趋势过滤 ──> 合成得分 ──> 目标权重 w_T
T+1 开盘 ──┴──> 撮合 w_T（涨跌停/停牌检查 + 成本） ──> 持仓/现金 ──> 收盘盯市 ──> 熔断状态更新
```

`engine.run()` 的一次循环严格按「执行昨日 pending → 盯市 → 生成明日 pending」顺序，避免同日自成交与未来函数。

## 4. 扩展点

| 想换什么 | 改哪里 | 注意 |
|---|---|---|
| 数据源 | 实现 `load_prices()` 同签名适配器，或 `from_tdx_kline_json()` | 必须给出 `publish_date` 才能启用财务因子 |
| 因子 | `factors.raw_factors()` 增字典项 + `config.factors.weights` | 每新增因子必须递增 `validation.trials_factor_count` |
| 加权方式 | `strategy.build_weight_function()` | IC 加权 / 最优化为插件位，需重做多重检验校正 |
| 组合优化 | `strategy._cap_weights()` | 行业约束需真实行业分类 |
| 撮合 | `engine.BacktestEngine._run` 内的成交价与可成交判断 | 改 VWAP/集合竞价需重跑成本敏感性 |
| 冲击成本 | `cost.impact_model` | 当前 `none`；接入前需给资金规模与参与率 |
| 基准 | `strategy.market_proxy()` | 生产环境应替换为中证全指/万得全A |

## 5. 明确未建模（登记为风险，不假装已处理）

冲击成本、整手取整（份额允许小数）、分红再投资、集合竞价撮合、退市清算、融券与杠杆、Level-2 盘口、交易规则历史变更（如注册制后的涨跌幅调整）、停牌复牌的现金处理。

## 6. 可复现性

- 固定 `config/default.json` + 固定数据文件 + 固定随机种子（蒙特卡洛 `9000+i`）→ 结果可复现。
- `output/result.json` 内含 `config_snapshot` 与数据源、样本区间，可追溯每个读数。
- 单元测试 `python -m quantlab selftest` 覆盖无未来函数、涨跌停、成本、统计口径与真实数据跨源校验。
